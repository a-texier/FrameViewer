# -*- coding: utf-8 -*-
"""Composition des "vignettes" plugin (hook FrameViewerPlugin.render_patch)
dans un coin de la frame -- UNE seule implementation, partagee entre
l'affichage live et l'export (voir MainWindow._display /
_export_frame_as_viewed), pour ne pas reproduire le doublon QPainter/cv2
deja connu de ce depot pour les overlays SIDECAR (voir docs/overlays.md)."""
import cv2
import numpy as np

_CORNERS = ("tl", "tr", "bl", "br")


def alpha_over(bgr, bgra):
    """Compose un overlay BGRA (canal alpha) par-dessus une image BGR, au
    niveau pixel -- utilise pour les overlays plugin "code" (hook
    render_overlay). Le resultat est une COPIE de `bgr` (jamais un buffer
    partage). `bgra` est recadre aux dimensions de `bgr` si besoin (un plugin
    qui renvoie une taille legerement differente ne casse pas l'affichage).
    Renvoie `bgr` tel quel si `bgra` est vide/None."""
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
    """`patches` : liste de dicts {"image": ndarray BGR/gris, "corner":
    "tl"|"tr"|"bl"|"br", "margin": int}. Renvoie une copie de `bgr` avec
    les patches incrustes (ou `bgr` tel quel si `patches` est vide)."""
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
