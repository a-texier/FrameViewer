from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from PySide6 import QtTest

from frameviewer.core.io_utils import imwrite_unicode
from frameviewer.core.sources import (
    FusionSource,
    ImageSequenceSource,
    VideoSource,
    YuvSource,
    decode_yuv,
    describe_source,
    yuv_frame_bytes,
)
from frameviewer.workers.annot_load_worker import AnnotFolderLoadWorker
from frameviewer.workers.convert_worker import ConvertWorker
from frameviewer.workers.prefetch_thread import _PrefetchThread


def _images(folder: Path, count=4, shape=(24, 32)) -> list[str]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for index in range(count):
        path = folder / f"frame_{index:03d}.png"
        image = np.full((*shape, 3), index * 40, np.uint8)
        assert imwrite_unicode(path, image)
        paths.append(str(path))
    return paths


@pytest.mark.parametrize(
    ("fmt", "factor"),
    [("gray8", 1.0), ("gray16le", 2.0), ("i420", 1.5), ("nv12", 1.5),
     ("yuyv", 2.0), ("uyvy", 2.0)],
)
def test_raw_frame_byte_sizes(fmt, factor):
    assert yuv_frame_bytes(8, 6, fmt) == int(8 * 6 * factor)
    assert yuv_frame_bytes(8, 6, "unknown") == 0


def test_gray_raw_decoders_and_short_buffer():
    gray8 = np.arange(12, dtype=np.uint8)
    assert np.array_equal(decode_yuv(gray8, 4, 3, "gray8"), gray8.reshape(3, 4))
    gray16 = np.arange(12, dtype="<u2")
    decoded = decode_yuv(gray16.view(np.uint8), 4, 3, "gray16le")
    assert decoded.dtype == np.uint16 and np.array_equal(decoded, gray16.reshape(3, 4))
    assert decode_yuv(b"short", 8, 8, "nv12") is None
    assert decode_yuv(b"anything", 1, 1, "unknown") is None


def test_image_sequence_properties_bounds_and_pixels(tmp_path):
    paths = _images(tmp_path / "images")
    source = ImageSequenceSource(paths, label="Traffic", fps=15.0)
    assert source.count == 4 and source.fps == 15.0
    assert source.name == source.stem == "Traffic"
    assert Path(source.directory) == tmp_path / "images"
    assert int(source.get(2)[0, 0, 0]) == 80
    assert source.get(-1) is None and source.get(4) is None
    with pytest.raises(IOError, match="vide"):
        ImageSequenceSource([])


def test_raw_file_sequence_reads_frames_on_demand(tmp_path):
    first = tmp_path / "a.raw"
    second = tmp_path / "b.raw"
    np.arange(12, dtype=np.uint8).tofile(first)
    np.arange(12, dtype=np.uint8)[::-1].tofile(second)
    source = YuvSource([str(first), str(second)], 4, 3, "gray8", label="raw")
    assert source.count == 2 and source.get(0).shape == (3, 4)
    assert source.get(3) is None


def test_video_source_random_and_sequential_access(tmp_path):
    path = tmp_path / "clip.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 12.0, (32, 24))
    assert writer.isOpened()
    for index in range(5):
        writer.write(np.full((24, 32, 3), index * 40, np.uint8))
    writer.release()
    source = VideoSource(str(path))
    try:
        assert source.count == 5
        assert source.get(0).shape == (24, 32, 3)
        assert source.get(3) is not None
        assert source.get(1) is not None
        assert source.fps == pytest.approx(12.0, rel=0.1)
    finally:
        source.close()


def test_fusion_source_count_resize_and_blend(tmp_path):
    left = ImageSequenceSource(_images(tmp_path / "left", count=3, shape=(20, 30)))
    right = ImageSequenceSource(_images(tmp_path / "right", count=2, shape=(10, 15)))
    fusion = FusionSource(
        left, (0, 255), right, (0, 255), None, None, 0,
        (0, 255), {}, 0.5, "alpha",
    )
    assert fusion.count == 2
    assert fusion.get(1).shape == (20, 30, 3)
    assert "Fusion" in fusion.name


def test_describe_source_contains_operational_metadata(tmp_path):
    source = ImageSequenceSource(_images(tmp_path / "images", count=2), fps=9.5)
    description = describe_source(source)
    assert "Sequence" in description
    assert "32" in description and "24" in description
    assert "2 frames" in description and "9.5 fps" in description
    assert describe_source(None) == "Aucune source"


def test_prefetch_worker_scales_and_skips_missing(qapp):
    class Source:
        def get(self, index):
            return None if index == 2 else np.full((20, 30, 3), index, np.uint8)

    worker = _PrefetchThread(Source(), [0, 1, 2], 0.5)
    spy = QtTest.QSignalSpy(worker.done)
    worker.start()
    assert worker.wait(5000)
    qapp.processEvents()
    assert spy.count() == 1
    cache = spy.at(0)[0]
    assert set(cache) == {0, 1}
    assert cache[1].shape == (10, 15, 3)


def test_annotation_worker_transports_full_python_object(qapp, data_dir):
    worker = AnnotFolderLoadWorker(str(data_dir / "annotations_yolo"), 1920, 1080)
    done = QtTest.QSignalSpy(worker.done)
    failed = QtTest.QSignalSpy(worker.failed)
    worker.start()
    assert worker.wait(5000)
    qapp.processEvents()
    assert failed.count() == 0 and done.count() == 1
    payload = done.at(0)[0]
    assert len(payload) == 10 and len(payload["frame_05000"]) > 0


@pytest.mark.parametrize("mode", ["png8", "png16"])
def test_conversion_worker_writes_all_image_frames(qapp, tmp_path, mode):
    paths = _images(tmp_path / "input", count=3)
    output = tmp_path / mode
    worker = ConvertWorker(
        "images", paths, str(output), mode, 0, 255, None, None, 0,
        1, (0, 255), filters={}, fps=12.0,
    )
    done = QtTest.QSignalSpy(worker.finished_ok)
    failed = QtTest.QSignalSpy(worker.failed)
    worker.start()
    assert worker.wait(10000)
    qapp.processEvents()
    assert failed.count() == 0 and done.count() == 1
    assert done.at(0)[1] == 3
    produced = sorted(output.glob("*.png"))
    assert len(produced) == 3
    read_flag = cv2.IMREAD_UNCHANGED
    assert cv2.imread(str(produced[0]), read_flag) is not None


def test_conversion_worker_reports_unknown_source(qapp, tmp_path):
    worker = ConvertWorker(
        "unknown", [], str(tmp_path / "out"), "png8", 0, 255, None,
        None, 0, 1, (0, 255), filters={},
    )
    failed = QtTest.QSignalSpy(worker.failed)
    worker.start()
    assert worker.wait(5000)
    qapp.processEvents()
    assert failed.count() == 1 and "inconnu" in failed.at(0)[0]
