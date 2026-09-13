from __future__ import annotations

import cv2
import numpy as np
import pytest

from frameviewer.core.io_utils import (
    fmt_time,
    imread_unicode,
    imwrite_unicode,
    list_files,
    list_images,
    natural_sort,
)
from frameviewer.core.pipeline import (
    apply_cube,
    apply_filters,
    auto_window,
    auto_window_sigma,
    blend_frames,
    load_cube,
    render_frame,
    to_intensity,
    window8,
)
from frameviewer.core.plugin_render import alpha_over, composite_patches


def test_unicode_image_roundtrip_and_directory_scan(tmp_path):
    image = np.arange(60, dtype=np.uint8).reshape(4, 5, 3)
    first = tmp_path / "image_10.png"
    second = tmp_path / "image_2_accent.png"
    assert imwrite_unicode(first, image)
    assert imwrite_unicode(second, image + 1)
    (tmp_path / "ignore.bin").write_bytes(b"x")
    loaded = imread_unicode(second, cv2.IMREAD_UNCHANGED)
    assert np.array_equal(loaded, image + 1)
    assert [p.split("\\")[-1] for p in list_images(tmp_path)] == [
        "image_2_accent.png", "image_10.png"
    ]
    assert list_files(tmp_path, {".bin"}) == [str(tmp_path / "ignore.bin")]


def test_missing_or_invalid_image_is_non_fatal(tmp_path):
    assert imread_unicode(tmp_path / "missing.png") is None
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    assert imread_unicode(bad) is None


@pytest.mark.parametrize(
    ("values", "expected"),
    [(["f10", "f2", "F1"], ["F1", "f2", "f10"]), ([], [])],
)
def test_natural_sort(values, expected):
    assert natural_sort(values) == expected


@pytest.mark.parametrize(("seconds", "label"), [(-1, "00:00"), (0, "00:00"), (61.8, "01:01")])
def test_time_format(seconds, label):
    assert fmt_time(seconds) == label


def test_windowing_and_intensity_preserve_contracts():
    raw = np.array([[-10, 0, 50, 100, 200]], dtype=np.int16)
    assert window8(raw, 0, 100).tolist() == [[0, 0, 127, 255, 255]]
    assert window8(raw, 5, 5).dtype == np.uint8
    bgr = np.zeros((2, 3, 3), np.uint8)
    bgr[:, :, 2] = 255
    intensity = to_intensity(bgr)
    assert intensity.shape == (2, 3) and intensity.dtype == np.uint8
    assert to_intensity(np.ones((2, 3), np.uint16)).dtype == np.uint16


def test_automatic_windows_handle_empty_constant_and_outliers():
    assert auto_window(np.array([])) == (0.0, 1.0)
    assert auto_window_sigma(np.array([])) == (0.0, 1.0)
    lo, hi = auto_window(np.full((4, 4), 7))
    assert lo == 7 and hi == 8
    lo, hi = auto_window_sigma(np.arange(100))
    assert 0 <= lo < hi <= 99


def test_cube_parser_and_identity_application(tmp_path):
    cube = tmp_path / "identity.cube"
    rows = ["LUT_3D_SIZE 2"]
    for blue in (0.0, 1.0):
        for green in (0.0, 1.0):
            for red in (0.0, 1.0):
                rows.append(f"{red} {green} {blue}")
    cube.write_text("\n".join(rows), encoding="utf-8")
    lut, size = load_cube(cube)
    image = np.array([[[0, 0, 0], [255, 128, 32]]], np.uint8)
    output = apply_cube(image, lut, size)
    assert np.max(np.abs(output.astype(int) - image.astype(int))) <= 1
    cube.write_text("LUT_3D_SIZE 2\n0 0 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="incoherentes"):
        load_cube(cube)


@pytest.mark.parametrize("flag", ["median", "smooth", "clahe", "sharpen", "edges", "invert"])
def test_each_filter_returns_display_ready_frame(flag):
    image = np.tile(np.arange(32, dtype=np.uint8), (32, 1))
    image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    output = apply_filters(image, {flag: True})
    assert output.shape == image.shape
    assert output.dtype == np.uint8
    assert output.flags.c_contiguous


def test_render_frame_color_gray_colormap_and_filter_paths():
    color = np.zeros((8, 9, 3), np.uint8)
    color[:, :, 1] = 90
    preserved = render_frame(color, 0, 255, None)
    assert np.array_equal(preserved, color)
    assert preserved.flags.c_contiguous
    raw16 = np.arange(72, dtype=np.uint16).reshape(8, 9) * 100
    gray = render_frame(raw16, 0, 7100, "gray", full_range=(0, 7100))
    mapped = render_frame(raw16, 0, 7100, cv2.COLORMAP_TURBO, full_range=(0, 7100))
    inverted = render_frame(raw16, 0, 7100, "gray", full_range=(0, 7100), filters={"invert": True})
    assert gray.shape == mapped.shape == inverted.shape == (8, 9, 3)
    assert not np.array_equal(gray, mapped)
    assert np.array_equal(inverted, 255 - gray)


@pytest.mark.parametrize("mode", ["alpha", "add_ab", "add_ba", "diff", "damier", "max", "mult", "wipe"])
def test_all_blend_modes_have_stable_shape_and_dtype(mode):
    a = np.full((24, 32, 3), 30, np.uint8)
    b = np.full((12, 16), 180, np.uint8)
    output = blend_frames(a, b, 0.25, mode)
    assert output.shape == a.shape and output.dtype == np.uint8


def test_blend_none_contracts():
    image = np.ones((2, 2, 3), np.uint8)
    assert blend_frames(None, None) is None
    assert blend_frames(image, None) is image
    assert blend_frames(None, image) is image


def test_alpha_overlay_crops_and_does_not_mutate_base():
    base = np.zeros((4, 5, 3), np.uint8)
    overlay = np.zeros((8, 8, 4), np.uint8)
    overlay[:, :, 2] = 200
    overlay[:, :, 3] = 128
    output = alpha_over(base, overlay)
    assert np.count_nonzero(base) == 0
    assert output.shape == base.shape
    assert 99 <= int(output[0, 0, 2]) <= 101
    assert alpha_over(base, np.zeros((2, 2, 3), np.uint8)) is base


@pytest.mark.parametrize("corner", ["tl", "tr", "bl", "br"])
def test_patch_composition_places_each_corner(corner):
    base = np.zeros((20, 30, 3), np.uint8)
    patch = np.full((4, 6, 3), 255, np.uint8)
    output = composite_patches(base, [{"image": patch, "corner": corner, "margin": 2}])
    assert int(output.sum()) == 4 * 6 * 3 * 255
    assert int(base.sum()) == 0
