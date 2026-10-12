"""Regression tests for bugs found during the code audit.

Each test pins one behaviour that used to be wrong; the bug is described in
the test docstring.
"""

from __future__ import annotations

import json
import sys

import cv2
import numpy as np
import pytest
from PySide6 import QtTest

from frameviewer.core.annotation_convert import yolo_to_ver
from frameviewer.core.annotation_loader import pair_stems_to_positions
from frameviewer.core.export_formats import ExportSpec
from frameviewer.core.feature_registry import operation_for_kind
from frameviewer.core.pipeline import (
    auto_window,
    auto_window_sigma,
    finite_range,
    native_intensity,
    sigma_window_u8,
    window8,
)
from frameviewer.core.sources import ImageSequenceSource, SpecializedSource, VideoSource
from frameviewer.plugins.loader import LoadedPlugin, PluginLoader, _purge_sibling_modules
from frameviewer.plugins.manifest import load as load_manifest
from frameviewer.plugins.manifest import normalize
from frameviewer.workers.convert_worker import ConvertWorker
from frameviewer.ui.export_jobs import run_sync
from frameviewer.workers.prefetch_thread import _PrefetchThread

SEQUENCE_WRITER = operation_for_kind("sequence_format", "writer")
needs_sequence_format = pytest.mark.skipif(
    SEQUENCE_WRITER is None, reason="optional sequence format not shipped"
)


# --- float frames with NaN / inf ------------------------------------------


def _float_frame_with_holes():
    frame = np.linspace(0.0, 100.0, 64, dtype=np.float32).reshape(8, 8)
    frame[0, 0] = np.nan
    frame[0, 1] = np.inf
    frame[0, 2] = -np.inf
    return frame


def test_windowing_ignores_non_finite_pixels():
    """NaN used to poison min/max/percentiles and give a black image."""
    frame = _float_frame_with_holes()
    lo, hi = auto_window_sigma(frame)
    assert np.isfinite(lo) and np.isfinite(hi) and hi > lo
    assert all(np.isfinite(v) for v in auto_window(frame))
    assert finite_range(frame) == pytest.approx((float(frame[0, 3]), 100.0))
    out = window8(frame, lo, hi)
    assert out.dtype == np.uint8
    assert out[0, 0] == 0 and out[0, 1] == 255 and out[0, 2] == 0
    assert out.max() > 0


def test_finite_range_and_window_handle_degenerate_input():
    assert finite_range(np.full((2, 2), np.nan, np.float32)) == (0.0, 1.0)
    assert window8(np.zeros((2, 2), np.float32), np.nan, np.nan).dtype == np.uint8


def test_native_intensity_keeps_16_bit_color_and_sigma_gives_8_bit():
    """A 16-bit color frame used to be averaged to float then clipped to 0..255."""
    color16 = np.full((4, 5, 3), 40000, np.uint16)
    inten = native_intensity(color16)
    assert inten.dtype == np.uint16 and inten.shape == (4, 5)
    assert int(inten[0, 0]) == 40000
    eight = sigma_window_u8(np.arange(20, dtype=np.uint16).reshape(4, 5) * 1000)
    assert eight.dtype == np.uint8 and eight.shape == (4, 5)
    assert eight.min() == 0 and eight.max() == 255


# --- video frame count -------------------------------------------------------


def _write_clip(path, frames=5):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (32, 24))
    assert writer.isOpened()
    for index in range(frames):
        writer.write(np.full((24, 32, 3), index * 40, np.uint8))
    writer.release()


def test_video_overestimated_count_is_corrected_without_breaking_seek(tmp_path):
    """Reading past the real end used to flag seeking as broken forever."""
    path = tmp_path / "clip.avi"
    _write_clip(path)
    source = VideoSource(str(path))
    try:
        source._count = 9  # container announcing too many frames
        assert source.get(8) is None
        assert source.count == 5
        assert not source._seek_unreliable
        assert source.get(4) is not None and source.get(1) is not None
    finally:
        source.close()


def test_video_unknown_count_is_learned_while_stepping(tmp_path):
    """With no frame count the length stayed at 1 and navigation was stuck."""
    path = tmp_path / "clip.avi"
    _write_clip(path)
    assert VideoSource._count_by_grabbing(str(path)) == 5
    source = VideoSource(str(path))
    try:
        source._count, source._count_known = 0, False
        for index in range(5):
            assert source.get(index) is not None
            assert source.count > index
        assert source.get(5) is None
        assert source.count == 5
    finally:
        source.close()


# --- prefetch ----------------------------------------------------------------


class _ConstSource:
    def __init__(self, value):
        self.value = value

    def get(self, index):
        return np.full((4, 4, 3), self.value, np.uint8)


def test_stale_prefetch_result_is_dropped(qapp):
    """A late result from a stopped thread used to land in the new cache."""
    from frameviewer.ui.main_window import MainWindow

    window = MainWindow()
    try:
        stale = _PrefetchThread(_ConstSource(1), [5], 1.0)
        stale.done.connect(window._on_prefetch_done)
        window._prefetch_thread = None  # what _stop_prefetch leaves
        stale.start()
        assert stale.wait(5000)
        qapp.processEvents()
        assert 5 not in window._prefetch_cache

        current = _PrefetchThread(_ConstSource(2), [6], 1.0)
        current.done.connect(window._on_prefetch_done)
        window._prefetch_thread = current
        current.start()
        assert current.wait(5000)
        qapp.processEvents()
        assert int(window._prefetch_cache[6][0, 0, 0]) == 2
    finally:
        window.close()


# --- plugins -----------------------------------------------------------------


def test_malformed_manifest_falls_back_instead_of_raising(tmp_path):
    """A JSON list or odd input entries used to abort discovery of all plugins."""
    (tmp_path / "manifest.json").write_text("[1, 2]", encoding="utf-8")
    assert load_manifest(str(tmp_path))["inputs"] == []
    manifest = normalize(
        {"inputs": [None, 3, {"name": "CSV BBOX"}, "class", "ok_name"]}, str(tmp_path)
    )
    assert manifest["inputs"] == [{"name": "csv_bbox"}, {"name": "ok_name"}]
    assert normalize({"inputs": "not a list"}, str(tmp_path))["inputs"] == []


def test_sibling_purge_never_evicts_stdlib_modules(tmp_path):
    """A plugin helper named json.py used to evict the real json module."""
    (tmp_path / "json.py").write_text("X = 1\n", encoding="utf-8")
    real_json = sys.modules["json"]
    _purge_sibling_modules(str(tmp_path))
    assert sys.modules["json"] is real_json
    assert json.dumps([1]) == "[1]"


def test_plugin_calling_sys_exit_is_disabled_not_fatal(tmp_path):
    class Exiting:
        def get_overlays(self, _api, _frame):
            sys.exit(3)

    loader = PluginLoader(object())
    loaded = LoadedPlugin("exiting", str(tmp_path))
    loaded.instance = Exiting()
    loaded.enabled = True
    assert loader._safe_call(loaded, "get_overlays", None, 0, default=[]) == []
    assert not loaded.enabled


# --- annotations ---------------------------------------------------------------


def test_yolo_folder_converts_to_tracked_rows(data_dir, tmp_path):
    """Folder labels are keyed by stem; converting used to do stem + 1."""
    out = tmp_path / "out.ver"
    boxes, frames = yolo_to_ver(str(data_dir / "annotations_yolo"), str(out), 1920, 1080)
    assert boxes > 0 and frames == 10
    first_frames = {float(line.split("\t")[0]) for line in out.read_text().splitlines()}
    assert min(first_frames) == 1.0 and max(first_frames) == 10.0


def test_pair_stems_by_name_then_by_order():
    by_stem = {"b": ["B"], "a": ["A"]}
    assert pair_stems_to_positions(by_stem, ["x", "a", "b"]) == ({1: ["A"], 2: ["B"]}, 2)
    assert pair_stems_to_positions(by_stem, ["x"]) == ({0: ["A"]}, 0)
    assert pair_stems_to_positions(by_stem) == ({0: ["A"], 1: ["B"]}, 0)


# --- temporal subtraction ----------------------------------------------------------


def test_temporal_difference_uses_own_reader_and_matches_reduced_frames(qapp, tmp_path):
    """The reference frame used to be read on the playback decoder, and the
    subtraction silently turned off at a reduced reading resolution."""
    from frameviewer.ui.main_window import MainWindow

    paths = []
    for index in range(3):
        path = tmp_path / f"f{index}.png"
        cv2.imwrite(str(path), np.full((20, 30), index * 50, np.uint8))
        paths.append(str(path))
    window = MainWindow()
    try:
        window.set_source(ImageSequenceSource(paths, directory=str(tmp_path)))
        window._temp_sub_on, window._temp_sub_k = True, 1
        reduced = cv2.resize(window.source.get(2), (15, 10), interpolation=cv2.INTER_AREA)
        diff = window._temporal_difference(reduced, 2)
        assert diff.shape == (10, 15) and diff.dtype == np.float32
        assert float(diff.mean()) == pytest.approx(50.0, abs=1.0)
        assert window._temporal_reference_reader() is not window.source
    finally:
        window._temp_sub_on = False
        window.close()


# --- sequence-format export ------------------------------------------------------------


@needs_sequence_format
@pytest.mark.parametrize(("depth", "dtype"), [("native", np.uint16), ("8", np.uint8)])
def test_clip_export_offers_raw_or_sigma_8_bit(qapp, tmp_path, depth, dtype):
    """The 16-bit data must stay 16-bit unless 8 bits is explicitly chosen."""
    from frameviewer.ui.export_dialogs import ClipExtractDialog
    from frameviewer.ui.main_window import MainWindow

    paths = []
    for index in range(2):
        path = tmp_path / f"g{index}.png"
        cv2.imwrite(str(path), (np.arange(30, dtype=np.uint16).reshape(5, 6) + index) * 2000)
        paths.append(str(path))
    window = MainWindow()
    try:
        window.set_source(ImageSequenceSource(paths, directory=str(tmp_path)))
        dialog = ClipExtractDialog(window, 0, 1, has_in_out=False)
        out = tmp_path / f"clip_{depth}.specialized"
        spec = ExportSpec(fmt="specialized", depth=depth, mapping="sigma")
        run_sync(dialog._job_for(spec, str(out), 0, 1))
        dialog.close()
        written = SpecializedSource(str(out))
        frame = written.get(1)
        assert frame.dtype == dtype
        if depth == "native":
            assert int(frame.max()) == 30 * 2000
    finally:
        window.close()


@needs_sequence_format
def test_convert_worker_keeps_16_bit_color_in_sequence_export(qapp, tmp_path):
    path = tmp_path / "c.png"
    cv2.imwrite(str(path), np.full((4, 5, 3), 50000, np.uint16))
    out = tmp_path / "c.specialized"
    worker = ConvertWorker(
        "images", [str(path)], str(out), "specialized", 0, 1, None, None, 0, 1, (0, 65535)
    )
    failed = QtTest.QSignalSpy(worker.failed)
    worker.start()
    assert worker.wait(10000)
    qapp.processEvents()
    assert failed.count() == 0
    frame = SpecializedSource(str(out)).get(0)
    assert frame.dtype == np.uint16 and int(frame.max()) == 50000
