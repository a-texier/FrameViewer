# -*- coding: utf-8 -*-
"""Frame rendering pipeline: 3D LUTs, windowing, filters, color maps and blend.

Frames enter and leave this module as NumPy arrays. See
``docs/rendering-pipeline.md`` for the complete contract.
"""

import cv2
import numpy as np

from frameviewer.core.constants import SIGNED_DIFF_LUT


def signed_diff_colormap(gray8):
    """Diverging red -> black -> blue map, with zero mapped to black."""
    anchors_x = np.array([0, 102, 127, 128, 153, 255], dtype=np.float32)
    # BGR anchors approximate matplotlib coolwarm_r, with a hard black center.
    anchors_bgr = np.array(
        [
            [38, 4, 180],
            [173, 196, 238],
            [0, 0, 0],
            [0, 0, 0],
            [234, 202, 179],
            [192, 76, 59],
        ],
        dtype=np.float32,
    )
    values = np.arange(256, dtype=np.float32)
    lut = np.empty((256, 3), dtype=np.uint8)
    for channel in range(3):
        lut[:, channel] = np.clip(np.interp(values, anchors_x, anchors_bgr[:, channel]), 0, 255)
    return lut[np.asarray(gray8, dtype=np.uint8)]


def load_cube(path):
    """Parse a 3D ``.cube`` LUT and return ``(lut, size)``.

    In the file order, red changes fastest, followed by green and blue.
    Therefore ``reshape(size, size, size, 3)`` produces ``lut[b, g, r]``.
    """
    size = None
    data = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            key = parts[0].upper()
            if key == "LUT_3D_SIZE":
                size = int(parts[1])
            elif key == "LUT_1D_SIZE":
                raise ValueError("LUT 1D non supportee (fournis une LUT 3D .cube).")
            elif key in ("TITLE", "DOMAIN_MIN", "DOMAIN_MAX", "LUT_3D_INPUT_RANGE"):
                continue
            else:
                try:
                    data.append((float(parts[0]), float(parts[1]), float(parts[2])))
                except (ValueError, IndexError):
                    continue
    if size is None:
        raise ValueError("LUT_3D_SIZE introuvable dans le fichier .cube.")
    arr = np.asarray(data, dtype=np.float32)
    if arr.shape[0] != size**3:
        raise ValueError(f"Donnees .cube incoherentes ({arr.shape[0]} != {size**3}).")
    lut = arr.reshape(size, size, size, 3)  # [b, g, r, rgb]
    return lut, size


def apply_cube(frame_bgr, lut, size):
    """Apply a display-only 3D LUT using trilinear interpolation."""
    rgb = frame_bgr[..., ::-1].astype(np.float32) / 255.0
    c = rgb * (size - 1)
    i0 = np.floor(c).astype(np.int32)
    i1 = np.minimum(i0 + 1, size - 1)
    f = c - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    r1, g1, b1 = i1[..., 0], i1[..., 1], i1[..., 2]
    fr = f[..., 0:1]
    fg = f[..., 1:2]
    fb = f[..., 2:3]

    def L(rr, gg, bb):
        return lut[bb, gg, rr]

    c00 = L(r0, g0, b0) * (1 - fr) + L(r1, g0, b0) * fr
    c10 = L(r0, g1, b0) * (1 - fr) + L(r1, g1, b0) * fr
    c01 = L(r0, g0, b1) * (1 - fr) + L(r1, g0, b1) * fr
    c11 = L(r0, g1, b1) * (1 - fr) + L(r1, g1, b1) * fr
    c0 = c00 * (1 - fg) + c10 * fg
    c1 = c01 * (1 - fg) + c11 * fg
    out = c0 * (1 - fb) + c1 * fb
    out = np.clip(out * 255.0, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(out[..., ::-1])  # RGB to BGR


# Rendering pipeline shared by the GUI and conversion workers.


def _finite_values(arr, dtype):
    """Flatten ``arr`` and drop NaN/inf, which would poison min/max/percentiles."""
    g = np.asarray(arr, dtype=dtype).ravel()
    if np.issubdtype(np.asarray(arr).dtype, np.floating):
        g = g[np.isfinite(g)]
    return g


def finite_range(arr):
    """Return ``(min, max)`` over finite values, ``(0.0, 1.0)`` if there are none."""
    g = _finite_values(arr, np.float64)
    if g.size == 0:
        return 0.0, 1.0
    return float(g.min()), float(g.max())


def window8(arr, lo, hi):
    """Map ``[lo, hi]`` linearly to ``[0, 255]``.

    NaN pixels render black, +inf white and -inf black (float IR data).
    """
    lo, hi = float(lo), float(hi)
    if not np.isfinite(lo):
        lo = 0.0
    if not np.isfinite(hi) or hi <= lo:
        hi = lo + 1.0
    out = (arr.astype(np.float32) - lo) * (255.0 / (hi - lo))
    if np.issubdtype(arr.dtype, np.floating):
        out = np.nan_to_num(out, nan=0.0, posinf=255.0, neginf=0.0)
    return np.clip(out, 0, 255).astype(np.uint8)


def to_intensity(raw):
    """Reduce a native array to 2D intensity while preserving its range."""
    if raw.ndim == 2:
        return raw
    if raw.shape[2] == 1:
        return raw[:, :, 0]
    if raw.shape[2] == 4:  # Drop alpha before conversion.
        raw = raw[:, :, :3]
    if raw.dtype == np.uint8:
        return cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
    return raw.astype(np.float32).mean(axis=2)


def auto_window(inten, pg=0.35, pd=0.35):
    """Compute an automatic window with low/high percentile saturation."""
    g = _finite_values(inten, np.float32)
    if g.size == 0:
        return 0.0, 1.0
    pg = max(0.0, min(49.0, float(pg)))
    pd = max(0.0, min(49.0, float(pd)))
    lo = float(np.percentile(g, pg))
    hi = float(np.percentile(g, 100.0 - pd))
    if hi <= lo:
        lo, hi = float(g.min()), float(g.max())
    if hi <= lo:
        hi = lo + 1.0
    return lo, hi


def auto_window_sigma(inten, n_sigma=3.0):
    """Compute a mean +/- N-sigma window clipped to the observed range."""
    g = _finite_values(inten, np.float64)
    if g.size == 0:
        return 0.0, 1.0
    mu = float(g.mean())
    sigma = float(g.std())
    lo = max(float(g.min()), mu - n_sigma * sigma)
    hi = min(float(g.max()), mu + n_sigma * sigma)
    if hi <= lo:
        hi = lo + 1.0
    return lo, hi


def sigma_window_u8(raw, n_sigma=3.0):
    """Intensity of ``raw`` windowed to mean +/- ``n_sigma`` sigma, as uint8.

    Same window as the "Auto" histogram mode, computed on this frame only.
    """
    inten = to_intensity(raw)
    lo, hi = auto_window_sigma(inten, n_sigma)
    return window8(inten, lo, hi)


def native_intensity(raw):
    """Single-channel intensity that keeps the native sample type.

    ``to_intensity`` averages multi-channel integer data in float32; this
    rounds it back to the source integer type so a 16-bit color frame stays
    16-bit instead of being squeezed to 8 bits. Floats are kept as is.
    """
    inten = to_intensity(raw)
    if np.issubdtype(raw.dtype, np.integer) and not np.issubdtype(inten.dtype, np.integer):
        info = np.iinfo(raw.dtype)
        inten = np.clip(np.rint(inten), info.min, info.max).astype(raw.dtype)
    return inten


def apply_filters(bgr, flags):
    """Apply lightweight inspection filters to a uint8 BGR image.

    ``flags`` is a boolean mapping for median, smooth, CLAHE, sharpen, edges,
    and invert. Operations run in that order.
    """
    if not flags:
        return bgr
    img = bgr
    if flags.get("median"):
        img = cv2.medianBlur(img, 3)
    if flags.get("smooth"):
        img = cv2.GaussianBlur(img, (3, 3), 0)
    if flags.get("clahe"):
        # Apply contrast-limited adaptive histogram equalization to luminance
        # so colors produced by a LUT remain stable.
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        if img.ndim == 3:
            lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
            lab[:, :, 0] = clahe.apply(lab[:, :, 0])
            img = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
        else:
            img = clahe.apply(img)
    if flags.get("sharpen"):
        k = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], np.float32)
        img = cv2.filter2D(img, -1, k)
    if flags.get("edges"):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        mag = np.clip(cv2.magnitude(gx, gy), 0, 255).astype(np.uint8)
        img = cv2.cvtColor(mag, cv2.COLOR_GRAY2BGR)
    if flags.get("invert"):
        img = 255 - img
    return np.ascontiguousarray(img)


def render_frame(
    raw, lo, hi, lut_data, cube_lut=None, cube_size=0, full_range=(0, 255), filters=None
):
    """Render a native frame as uint8 BGR ready for display or export."""
    if lut_data == SIGNED_DIFF_LUT:
        amplitude = max(abs(float(lo)), abs(float(hi)), 1e-6)
        lo, hi = -amplitude, amplitude
    # Normalize BGRA to BGR by dropping alpha.
    if raw.ndim == 3 and raw.shape[2] == 4:
        raw = raw[:, :, :3]
    is_color_u8 = raw.ndim == 3 and raw.shape[2] == 3 and raw.dtype == np.uint8
    is_color_other = raw.ndim == 3 and raw.shape[2] == 3 and not is_color_u8
    fr0, fr1 = full_range
    engaged = not (lo <= fr0 + 1e-6 and hi >= fr1 - 1e-6)
    if lut_data is None and (is_color_u8 or is_color_other):
        # Preserve color for 3-channel arrays and window each channel.
        base = window8(raw, lo, hi) if (engaged or is_color_other) else np.ascontiguousarray(raw)
    else:
        inten = to_intensity(raw)
        g8 = window8(inten, lo, hi)
        if lut_data is None or lut_data == "gray":
            base = cv2.cvtColor(g8, cv2.COLOR_GRAY2BGR)
        elif lut_data == SIGNED_DIFF_LUT:
            base = signed_diff_colormap(g8)
        else:
            base = cv2.applyColorMap(g8, int(lut_data))
    if cube_lut is not None:
        try:
            base = apply_cube(base, cube_lut, cube_size)
        except Exception:
            pass
    if filters:
        base = apply_filters(base, filters)
    return np.ascontiguousarray(base)


# -------------------------------- blending ---------------------------------

BLEND_MODES = [
    ("alpha", "Alpha (fondu)"),
    ("add_ab", "Addition pondérée (A + %·B)"),
    ("add_ba", "Addition pondérée (B + %·A)"),
    ("diff", "Différence |A−B|"),
    ("damier", "Damier"),
    ("wipe", "Rideau A/B (poignée à glisser)"),
    ("max", "Maximum (A, B)"),
    ("mult", "Multiplication A·B"),
]


def blend_frames(a, b, alpha=0.5, mode="alpha"):
    """Blend two same-sized BGR images using ``mode`` and alpha in ``[0, 1]``."""
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    if a.ndim == 2:
        a = cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)
    if b.ndim == 2:
        b = cv2.cvtColor(b, cv2.COLOR_GRAY2BGR)
    if b.shape[:2] != a.shape[:2]:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    if mode == "diff":
        return cv2.absdiff(a, b)
    if mode == "max":
        return np.maximum(a, b)
    if mode == "mult":
        return ((a.astype(np.uint16) * b.astype(np.uint16)) // 255).astype(np.uint8)
    if mode == "damier":
        h, w = a.shape[:2]
        tile = max(8, min(h, w) // 12)
        yy, xx = np.indices((h, w))
        mask = ((xx // tile) + (yy // tile)) % 2 == 0
        out = b.copy()
        out[mask] = a[mask]
        return out
    if mode == "wipe":
        h, w = a.shape[:2]
        cut = int(round(alpha * w))
        out = b.copy()
        if cut > 0:
            out[:, :cut] = a[:, :cut]
        return out
    if mode == "add_ab":
        # A + alpha*B, clamped to 0..255.
        return cv2.addWeighted(a, 1.0, b, alpha, 0.0)
    if mode == "add_ba":
        # B + alpha*A, clamped to 0..255.
        return cv2.addWeighted(b, 1.0, a, alpha, 0.0)
    return cv2.addWeighted(a, 1.0 - alpha, b, alpha, 0.0)  # Default alpha blend.
