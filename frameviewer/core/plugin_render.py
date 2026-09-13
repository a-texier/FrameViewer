# -*- coding: utf-8 -*-
"""Composite plugin patches and full-frame overlays onto BGR frames.

The live display and export paths share this implementation so their output
remains identical. See ``MainWindow._display`` and
``_export_frame_as_viewed``.
"""
import cv2
import numpy as np

_CORNERS = ("tl", "tr", "bl", "br")


def alpha_over(bgr, bgra):
    """Alpha-composite a BGRA overlay over a BGR image.

    The result is a copy. The overlay is clipped to the base dimensions, and
    an empty or invalid overlay leaves the base image unchanged.
    """
    if bgr is None or bgra is None or getattr(bgra, "size", 0) == 0:
        return bgr
    if bgra.ndim != 3 or bgra.shape[2] != 4:
        return bgr
    H, W = bgr.shape[:2]
    h = min(H, bgra.shape[0])
    w = min(W, bgra.shape[1])
    if h <= 0 or w <= 0:
        return bgr
    out = bgr.copy()
    if out.ndim == 2:
        out = cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)
    roi = out[:h, :w].astype(np.float32)
    ov = bgra[:h, :w]
    a = (ov[:, :, 3:4].astype(np.float32)) / 255.0
    blended = ov[:, :, :3].astype(np.float32) * a + roi * (1.0 - a)
    out[:h, :w] = np.clip(blended, 0, 255).astype(np.uint8)
    return out


def composite_patches(bgr, patches, default_margin=8):
    """Composite corner patches and return a copy of ``bgr``.

    Each patch contains an image, a ``tl|tr|bl|br`` corner, and a margin.
    """
    if bgr is None or not patches:
        return bgr
    out = bgr.copy()
    H, W = out.shape[:2]
    for p in patches:
        img = p.get("image")
        if img is None or getattr(img, "size", 0) == 0:
            continue
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        ph, pw = img.shape[:2]
        pw, ph = min(pw, W), min(ph, H)
        if pw <= 0 or ph <= 0:
            continue
        img = img[:ph, :pw]
        corner = p.get("corner") if p.get("corner") in _CORNERS else "tr"
        m = int(p.get("margin", default_margin))
        if corner == "tl":
            x0, y0 = m, m
        elif corner == "bl":
            x0, y0 = m, H - ph - m
        elif corner == "br":
            x0, y0 = W - pw - m, H - ph - m
        else:  # "tr"
            x0, y0 = W - pw - m, m
        x0 = max(0, min(x0, W - pw))
        y0 = max(0, min(y0, H - ph))
        out[y0:y0 + ph, x0:x0 + pw] = img
    return out
