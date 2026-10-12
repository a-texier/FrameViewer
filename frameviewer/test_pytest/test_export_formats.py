"""Conversion : format puis options selon la donnee d'entree."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from frameviewer.core.export_formats import (
    ExportSpec,
    ImageFolderWriter,
    Mp4Writer,
    SpecializedWriter,
    convert_frame,
    depth_reason,
    find_ffmpeg,
    mapping_choices,
    native_depth_allowed,
    to_8bit,
)
from frameviewer.core.sources import ImageSequenceSource
from frameviewer.ui.export_jobs import run_sync

needs_ffmpeg = pytest.mark.skipif(find_ffmpeg() is None, reason="ffmpeg not installed")
needs_sequence_format = pytest.mark.skipif(
    SpecializedWriter is None, reason="optional sequence format not shipped"
)


def _ramp16(h=6, w=8):
    return (np.arange(h * w, dtype=np.uint16).reshape(h, w) * 1000).astype(np.uint16)


# --- choix proposes ---------------------------------------------------------------


def test_eight_bit_input_never_offers_a_deeper_output():
    for fmt in ("png", "tiff", "jpg", "specialized", "mp4"):
        assert not native_depth_allowed(fmt, np.uint8)
    assert depth_reason("png", np.uint8) == "Entrée 8 bits : la sortie reste en 8 bits."


def test_deep_input_keeps_its_depth_only_where_the_format_can_store_it():
    assert native_depth_allowed("png", np.uint16)
    assert native_depth_allowed("tiff", np.uint16)
    assert native_depth_allowed("specialized", np.uint16)
    assert not native_depth_allowed("jpg", np.uint16)
    assert not native_depth_allowed("mp4", np.uint16)
    # PNG ne sait pas stocker de flottants, TIFF si
    assert not native_depth_allowed("png", np.float32)
    assert native_depth_allowed("tiff", np.float32)


def test_full_range_mapping_is_not_offered_for_floats():
    assert "full" in [k for k, _ in mapping_choices(np.uint16)]
    assert "full" not in [k for k, _ in mapping_choices(np.float32)]


# --- conversion 8 bits ------------------------------------------------------------


def test_eight_bit_rules():
    raw = _ramp16()
    minmax = to_8bit(raw, "minmax")
    assert minmax.dtype == np.uint8
    assert int(minmax.min()) == 0 and int(minmax.max()) == 255

    full = to_8bit(raw, "full")
    assert int(full.max()) == int(47000 * 255 / 65535)  # pas d'etirement

    custom = to_8bit(raw, "custom", (0, 10000))
    assert int(custom[0, 0]) == 0 and int(custom[0, 1]) > 0
    assert int(custom.max()) == 255  # tout ce qui depasse 10000 sature

    sigma = to_8bit(raw, "sigma")
    assert sigma.dtype == np.uint8 and sigma.shape == raw.shape


def test_eight_bit_input_passes_through_unchanged():
    raw = np.random.default_rng(0).integers(0, 255, (5, 7, 3), dtype=np.uint8)
    assert to_8bit(raw, "minmax") is raw


def test_deep_color_keeps_its_channels_in_8_bits():
    raw = np.zeros((4, 5, 3), np.uint16)
    raw[..., 2] = 60000
    out = to_8bit(raw, "full")
    assert out.shape == (4, 5, 3) and int(out[0, 0, 2]) > 200 and int(out[0, 0, 0]) == 0


def test_native_png_squeezes_single_channel_and_clips_wide_integers():
    raw = np.full((3, 4, 1), 70000, np.uint32)
    out = convert_frame(raw, ExportSpec(fmt="png", depth="native"))
    assert out.shape == (3, 4) and out.dtype == np.uint16 and int(out.max()) == 65535


def test_jpeg_and_mp4_are_always_eight_bit():
    assert ExportSpec(fmt="jpg", depth="native").eight_bit
    assert ExportSpec(fmt="mp4", depth="native").eight_bit
    assert not ExportSpec(fmt="tiff", depth="native").eight_bit


# --- ecrivains --------------------------------------------------------------------


def test_png_16_bit_round_trip(tmp_path):
    writer = ImageFolderWriter(str(tmp_path), "png")
    writer.write(0, _ramp16())
    back = cv2.imread(str(tmp_path / "frame_00000.png"), cv2.IMREAD_UNCHANGED)
    assert back.dtype == np.uint16 and int(back.max()) == 47000


def test_jpeg_quality_changes_the_file_size(tmp_path):
    img = np.random.default_rng(1).integers(0, 255, (64, 64, 3), dtype=np.uint8)
    sizes = {}
    for quality in (10, 95):
        folder = tmp_path / f"q{quality}"
        folder.mkdir()
        ImageFolderWriter(str(folder), "jpg", jpeg_quality=quality).write(0, img)
        sizes[quality] = (folder / "frame_00000.jpg").stat().st_size
    assert sizes[10] < sizes[95]


def test_mpeg4_writer_accepts_gray_frames_of_changing_size(tmp_path):
    out = tmp_path / "a.mp4"
    writer = Mp4Writer(str(out), ExportSpec(fmt="mp4", encoder="mp4v", fps=10))
    writer.write(0, np.zeros((32, 48), np.uint8))
    writer.write(1, np.zeros((30, 40), np.uint8))  # ROI suivie : taille variable
    writer.close()
    cap = cv2.VideoCapture(str(out))
    assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 2
    cap.release()


@needs_ffmpeg
@pytest.mark.parametrize("encoder", ["h264", "h265"])
def test_ffmpeg_encoders_write_odd_sized_frames(tmp_path, encoder):
    out = tmp_path / f"{encoder}.mp4"
    spec = ExportSpec(fmt="mp4", encoder=encoder, crf=23, speed="veryfast", fps=10)
    writer = Mp4Writer(str(out), spec)
    for index in range(3):
        writer.write(index, np.full((241, 321, 3), index * 60, np.uint8))
    writer.close()
    cap = cv2.VideoCapture(str(out))
    ok, frame = cap.read()
    cap.release()
    assert ok and frame.shape[:2] == (242, 322)  # complete en dimensions paires


@needs_ffmpeg
def test_cancelled_ffmpeg_export_stops_the_encoder(tmp_path):
    writer = Mp4Writer(str(tmp_path / "x.mp4"), ExportSpec(fmt="mp4", encoder="h264"))
    writer.write(0, np.zeros((16, 16, 3), np.uint8))
    proc = writer._proc
    writer.abort()
    assert proc.poll() is not None
    writer.close()  # sans effet apres abort


# --- panneau d'options --------------------------------------------------------------


def _panel(sample, **kwargs):
    from frameviewer.ui.export_format_panel import ExportFormatPanel

    panel = ExportFormatPanel(sample, **kwargs)
    return panel, panel._form


def test_panel_for_8_bit_input_offers_only_8_bits(qapp):
    panel, form = _panel(np.zeros((4, 4), np.uint8))
    panel.set_format("png")
    assert not panel._depth.isEnabled()
    assert not form.isRowVisible(panel._mapping)
    assert form.isRowVisible(panel._as_viewed)
    assert panel.spec().depth == "8"


def test_panel_for_16_bit_input_asks_how_to_reach_8_bits(qapp):
    panel, form = _panel(_ramp16(), window=(100.0, 9000.0))
    panel.set_format("png")
    assert panel._depth.isEnabled()
    panel._depth.setCurrentIndex(panel._depth.findData("8"))
    assert form.isRowVisible(panel._mapping)
    assert not form.isRowVisible(panel._range_box)
    panel._mapping.setCurrentIndex(panel._mapping.findData("custom"))
    assert form.isRowVisible(panel._range_box)
    assert panel.spec().custom_range == (100.0, 9000.0)  # pre-rempli avec la vue
    panel._as_viewed.setChecked(True)  # le rendu de la vue remplace la regle
    assert not form.isRowVisible(panel._mapping)
    panel._depth.setCurrentIndex(panel._depth.findData("native"))
    assert not form.isRowVisible(panel._as_viewed)
    spec = panel.spec()
    assert spec.depth == "native" and not spec.renders_view


def test_panel_shows_format_specific_options(qapp):
    panel, form = _panel(_ramp16())
    panel.set_format("jpg")
    assert form.isRowVisible(panel._jpeg_quality)
    assert not panel._depth.isEnabled() and panel.spec().eight_bit
    assert form.isRowVisible(panel._mapping)
    panel.set_format("mp4")
    assert form.isRowVisible(panel._encoder) and form.isRowVisible(panel._fps)
    assert not form.isRowVisible(panel._jpeg_quality)
    assert not form.isRowVisible(panel._crf)  # MPEG-4 OpenCV : qualite fixe


@needs_ffmpeg
def test_panel_offers_quality_for_ffmpeg_encoders(qapp):
    panel, form = _panel(np.zeros((4, 4, 3), np.uint8))
    panel.set_format("mp4")
    panel._encoder.setCurrentIndex(panel._encoder.findData("h264"))
    assert form.isRowVisible(panel._crf) and form.isRowVisible(panel._speed)
    spec = panel.spec()
    assert spec.encoder == "h264" and spec.crf == 18


def test_panel_hides_stream_copy_unless_allowed(qapp):
    panel, _form = _panel(np.zeros((4, 4, 3), np.uint8))
    assert panel._encoder.findData("copy") < 0
    assert panel._fmt.findData("copy") < 0


def test_panel_for_float_input(qapp):
    panel, _form = _panel(np.zeros((4, 4), np.float32))
    panel.set_format("png")
    assert not panel._depth.isEnabled()
    panel.set_format("tiff")
    assert panel._depth.isEnabled()
    assert panel._mapping.findData("full") < 0


# --- dialogues ----------------------------------------------------------------------


def _sequence16(folder, count=3):
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for index in range(count):
        path = folder / f"g_{index:02d}.png"
        cv2.imwrite(str(path), _ramp16() + index)
        paths.append(str(path))
    return ImageSequenceSource(paths, directory=str(folder))


@pytest.mark.parametrize(
    ("spec", "suffix", "dtype"),
    [
        (ExportSpec(fmt="png", depth="native"), ".png", np.uint16),
        (ExportSpec(fmt="tiff", depth="native"), ".tiff", np.uint16),
        (ExportSpec(fmt="png", depth="8", mapping="minmax"), ".png", np.uint8),
        (ExportSpec(fmt="jpg", jpeg_quality=80), ".jpg", np.uint8),
    ],
)
def test_clip_dialog_writes_the_chosen_depth(qapp, tmp_path, spec, suffix, dtype):
    from frameviewer.ui.export_dialogs import ClipExtractDialog
    from frameviewer.ui.main_window import MainWindow

    window = MainWindow()
    try:
        window.set_source(_sequence16(tmp_path / "src"))
        dialog = ClipExtractDialog(window, 0, 2, has_in_out=False)
        out = tmp_path / "out"
        out.mkdir()
        run_sync(dialog._job_for(spec, str(out), 0, 2))
        files = sorted(out.glob("*" + suffix))
        assert len(files) == 3
        img = cv2.imread(str(files[0]), cv2.IMREAD_UNCHANGED)
        assert img.dtype == dtype
        dialog.close()
    finally:
        window.close()


@needs_sequence_format
def test_roi_dialog_writes_raw_crop_to_sequence_file(qapp, tmp_path):
    from frameviewer.core.sources import SpecializedSource
    from frameviewer.ui.export_dialogs import RoiConvertDialog
    from frameviewer.ui.main_window import MainWindow

    window = MainWindow()
    try:
        window.set_source(_sequence16(tmp_path / "src"))
        window._roi = (1, 2, 4, 3)
        dialog = RoiConvertDialog(window)
        out = tmp_path / "roi.specialized"
        spec = ExportSpec(fmt="specialized", depth="native")
        run_sync(dialog._job_write(spec, str(out), range(3), prefix="roi"))
        frame = SpecializedSource(str(out)).get(0)
        assert frame.shape[:2] == (3, 4) and frame.dtype == np.uint16
        assert int(frame[0, 0]) == int(_ramp16()[2, 1])
        dialog.close()
    finally:
        window.close()
