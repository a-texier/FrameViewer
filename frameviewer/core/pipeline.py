# -*- coding: utf-8 -*-
"""Pipeline de rendu d'une frame : LUT .cube, fenetrage 16->8 bits, filtres
FIJI, colormaps, blend fusion. Aucune frame ne quitte ce module autrement
qu'en ndarray (voir docs/rendering-pipeline.md)."""
import cv2
import numpy as np

def load_cube(path):
    """Parse un LUT 3D .cube. Retourne (lut, size).

    Ordre .cube: l'indice rouge varie le plus vite, puis vert, puis bleu.
    reshape(size,size,size,3) donne donc lut[b, g, r].
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
    if arr.shape[0] != size ** 3:
        raise ValueError(f"Donnees .cube incoherentes ({arr.shape[0]} != {size**3}).")
    lut = arr.reshape(size, size, size, 3)  # [b, g, r, rgb]
    return lut, size


def apply_cube(frame_bgr, lut, size):
    """Applique une LUT 3D (.cube) par interpolation trilineaire. Visuel seulement."""
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
    return np.ascontiguousarray(out[..., ::-1])  # -> BGR


# --- pipeline de rendu (partage GUI + worker de conversion) ----------------

def window8(arr, lo, hi):
    """Fenetrage lineaire [lo,hi] -> [0,255] (style FIJI Brightness/Contrast)."""
    if hi <= lo:
        hi = lo + 1.0
    out = (arr.astype(np.float32) - float(lo)) * (255.0 / (float(hi) - float(lo)))
    return np.clip(out, 0, 255).astype(np.uint8)


def to_intensity(raw):
    """Reduit un tableau natif en 2D d'intensite (conserve la dynamique)."""
    if raw.ndim == 2:
        return raw
    if raw.shape[2] == 1:
        return raw[:, :, 0]
    if raw.shape[2] == 4:       # BGRA -> BGR avant conversion
        raw = raw[:, :, :3]
    if raw.dtype == np.uint8:
        return cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
    return raw.astype(np.float32).mean(axis=2)


def auto_window(inten, pg=0.35, pd=0.35):
    """Fenetre auto par saturation: pg% en bas, pd% en haut (facon FIJI)."""
    g = np.asarray(inten, dtype=np.float32).ravel()
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
    """Fenetre auto 3-sigma : mean +/- n_sigma*std, borne aux min/max reels."""
    g = np.asarray(inten, dtype=np.float64).ravel()
    if g.size == 0:
        return 0.0, 1.0
    mu = float(g.mean())
    sigma = float(g.std())
    lo = max(float(g.min()), mu - n_sigma * sigma)
    hi = min(float(g.max()), mu + n_sigma * sigma)
    if hi <= lo:
        hi = lo + 1.0
    return lo, hi


def apply_filters(bgr, flags):
    """Filtres rapides facon FIJI sur une image BGR uint8.

    flags: dict de bool {median, smooth, clahe, sharpen, edges, invert}.
    Ordre: median -> smooth -> clahe -> sharpen (nettete) -> edges (bords) -> invert.
    """
    if not flags:
        return bgr
    img = bgr
    if flags.get("median"):
        img = cv2.medianBlur(img, 3)
    if flags.get("smooth"):
        img = cv2.GaussianBlur(img, (3, 3), 0)
    if flags.get("clahe"):
        # Egalisation d'histogramme adaptative a contraste limite (FIJI "Enhance
        # Local Contrast / CLAHE"). On l'applique sur la luminance pour preserver
        # les couleurs d'une LUT.
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


def render_frame(raw, lo, hi, lut_data, cube_lut=None, cube_size=0,
                 full_range=(0, 255), filters=None):
    """raw (natif) -> image BGR uint8 prete a afficher / enregistrer."""
    # Normalise : BGRA -> BGR (supprime canal alpha)
    if raw.ndim == 3 and raw.shape[2] == 4:
        raw = raw[:, :, :3]
    is_color_u8 = (raw.ndim == 3 and raw.shape[2] == 3 and raw.dtype == np.uint8)
    is_color_other = (raw.ndim == 3 and raw.shape[2] == 3 and not is_color_u8)
    fr0, fr1 = full_range
    engaged = not (lo <= fr0 + 1e-6 and hi >= fr1 - 1e-6)
    if lut_data is None and (is_color_u8 or is_color_other):
        # Preserve couleur (uint8 ou uint16/float 3ch) : fenetre par canal
        base = (window8(raw, lo, hi)
                if (engaged or is_color_other)
                else np.ascontiguousarray(raw))
    else:
        inten = to_intensity(raw)
        g8 = window8(inten, lo, hi)
        if lut_data is None or lut_data == "gray":
            base = cv2.cvtColor(g8, cv2.COLOR_GRAY2BGR)
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


# --------------------------- fusion / blend --------------------------------

BLEND_MODES = [
    ("alpha",  "Alpha (fondu)"),
    ("add_ab", "Addition pondérée (A + %·B)"),
    ("add_ba", "Addition pondérée (B + %·A)"),
    ("diff",   "Différence |A−B|"),
    ("damier", "Damier"),
]


def blend_frames(a, b, alpha=0.5, mode="alpha"):
    """Fusionne deux images BGR de même taille selon `mode`. alpha ∈ [0,1]."""
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
        mask = (((xx // tile) + (yy // tile)) % 2 == 0)
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
        # A + (alpha·100 %)·B, écrêté (clamp) sur dépassement 0..255
        return cv2.addWeighted(a, 1.0, b, alpha, 0.0)
    if mode == "add_ba":
        # B + (alpha·100 %)·A, écrêté (clamp) sur dépassement 0..255
        return cv2.addWeighted(b, 1.0, a, alpha, 0.0)
    return cv2.addWeighted(a, 1.0 - alpha, b, alpha, 0.0)  # alpha (défaut)


