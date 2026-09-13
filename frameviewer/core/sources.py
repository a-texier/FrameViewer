# -*- coding: utf-8 -*-
"""Sources d'entree : abstraction commune "sequence de frames indexee"
(voir docs/sources.md). Trois implementations concretes (video,
sequence d'images, SPECIALIZED) + une source virtuelle (blend fusion)."""
import os

import cv2
import numpy as np

from .io_utils import imread_unicode
from .feature_registry import operation_for_path
from .pipeline import blend_frames, render_frame


# formats YUV bruts supportes : id -> (libelle, octets par pixel)
YUV_FORMATS = {
    "gray8":    ("Gris 8-bit (Y seul)",             1.0),
    "gray16le": ("Gris 16-bit little-endian (Y seul)", 2.0),
    "i420":     ("YUV 4:2:0 planaire (I420 / IYUV)", 1.5),
    "nv12":     ("YUV 4:2:0 semi-planaire (NV12)",   1.5),
    "yuyv":     ("YUV 4:2:2 entrelace (YUYV / YUY2)", 2.0),
    "uyvy":     ("YUV 4:2:2 entrelace (UYVY)",        2.0),
}


def yuv_frame_bytes(width, height, fmt):
    """Taille (octets) d'une frame brute, ou 0 si le format est inconnu."""
    spec = YUV_FORMATS.get(fmt)
    if spec is None:
        return 0
    return int(round(int(width) * int(height) * spec[1]))


def decode_yuv(data, width, height, fmt):
    """Decode une frame YUV/gris brute -> ndarray BGR (formats YUV) ou 2D gris
    (formats gray). `data` = bytes ou ndarray uint8. None si taille insuffisante
    ou format inconnu (le pipeline gere ensuite 2D gris comme 3D BGR)."""
    need = yuv_frame_bytes(width, height, fmt)
    if need <= 0:
        return None
    buf = data if isinstance(data, np.ndarray) else np.frombuffer(data, dtype=np.uint8)
    if buf.size < need:
        return None
    buf = buf[:need]
    w, h = int(width), int(height)
    if fmt == "gray8":
        return buf.reshape(h, w)
    if fmt == "gray16le":
        return buf.view("<u2").reshape(h, w)
    if fmt == "i420":
        return cv2.cvtColor(buf.reshape(h * 3 // 2, w), cv2.COLOR_YUV2BGR_I420)
    if fmt == "nv12":
        return cv2.cvtColor(buf.reshape(h * 3 // 2, w), cv2.COLOR_YUV2BGR_NV12)
    if fmt == "yuyv":
        return cv2.cvtColor(buf.reshape(h, w, 2), cv2.COLOR_YUV2BGR_YUYV)
    if fmt == "uyvy":
        return cv2.cvtColor(buf.reshape(h, w, 2), cv2.COLOR_YUV2BGR_UYVY)
    return None

class VideoSource:
    is_raw = False

    def __init__(self, path):
        self.path = path
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise IOError(f"OpenCV ne parvient pas a ouvrir:\n{path}")
        n = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self._count = n if n > 0 else 0
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        self._fps = float(fps) if fps and fps > 0 else 25.0
        self._next = 0
        # certains conteneurs/codecs (flux web, VFR, GOP longs) n'ont pas d'index
        # d'images fiable : CAP_PROP_POS_FRAMES + read() renvoie ok=False sur un
        # seek arbitraire (scrub) alors que la lecture sequentielle marche. Une
        # fois ce cas detecte, on bascule en decodage sequentiel (rembobinage +
        # grab jusqu'a idx), seul acces fiable.
        self._seek_unreliable = False

    @property
    def count(self):
        return self._count

    @property
    def fps(self):
        return self._fps

    def _learn_count(self, idx):
        self._next = idx + 1
        if self._count == 0:
            self._count = idx + 1  # longueur inconnue: on l'apprend au fil de l'eau

    def _get_sequential(self, idx):
        """Decode sequentiellement jusqu'a idx (index natif non fiable). En
        avant depuis la position courante, avec rembobinage seulement si on
        recule."""
        if idx < self._next:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self._next = 0
        frame = None
        while self._next <= idx:
            ok, f = self.cap.read()
            if not ok:
                return None
            frame = f
            self._next += 1
        if self._count == 0:
            self._count = idx + 1
        return frame

    def get(self, idx):
        if idx < 0:
            idx = 0
        if self._seek_unreliable:
            return self._get_sequential(idx)
        # lecture sequentielle (cas rapide de la lecture normale).
        if idx == self._next:
            ok, frame = self.cap.read()
            if ok:
                self._learn_count(idx)
                return frame
            return self._get_sequential(idx)
        # acces aleatoire : on tente d'abord le seek natif.
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        self._next = idx
        ok, frame = self.cap.read()
        if ok:
            self._learn_count(idx)
            return frame
        # seek natif casse pour ce flux : on memorise et on decode en sequentiel.
        self._seek_unreliable = True
        return self._get_sequential(idx)

    def close(self):
        try:
            self.cap.release()
        except Exception:
            pass

    @property
    def name(self):
        return os.path.basename(self.path)

    @property
    def directory(self):
        return os.path.dirname(os.path.abspath(self.path))

    @property
    def stem(self):
        return os.path.splitext(os.path.basename(self.path))[0]


class ImageSequenceSource:
    is_raw = False

    def __init__(self, paths, label="sequence", directory=None, fps=12.0):
        self.paths = list(paths)
        if not self.paths:
            raise IOError("Sequence d'images vide.")
        self._fps = fps
        self._label = label
        self._dir = directory or os.path.dirname(os.path.abspath(self.paths[0]))

    @property
    def count(self):
        return len(self.paths)

    @property
    def fps(self):
        return self._fps

    def get(self, idx):
        if idx < 0 or idx >= len(self.paths):
            return None
        return imread_unicode(self.paths[idx], cv2.IMREAD_UNCHANGED)

    def close(self):
        pass

    @property
    def name(self):
        return self._label

    @property
    def directory(self):
        return self._dir

    @property
    def stem(self):
        return self._label


class YuvSource:
    """Sequence de fichiers YUV/gris BRUTS, un fichier = une frame. Le brut n'a
    pas d'en-tete : la geometrie (largeur/hauteur) et le format sont fournis par
    l'utilisateur (dialogue d'import) et memorises ici. Chaque frame est lue et
    decodee a la demande (get), comme une sequence d'images."""
    is_raw = False

    def __init__(self, paths, width, height, fmt, label="yuv",
                 directory=None, fps=12.0):
        self.paths = list(paths)
        if not self.paths:
            raise IOError("Sequence YUV vide.")
        self.width = int(width)
        self.height = int(height)
        self.fmt = fmt
        self._fps = fps
        self._label = label
        self._dir = directory or os.path.dirname(os.path.abspath(self.paths[0]))

    @property
    def count(self):
        return len(self.paths)

    @property
    def fps(self):
        return self._fps

    def get(self, idx):
        if idx < 0 or idx >= len(self.paths):
            return None
        try:
            data = np.fromfile(self.paths[idx], dtype=np.uint8)
        except OSError:
            return None
        return decode_yuv(data, self.width, self.height, self.fmt)

    def close(self):
        pass

    @property
    def name(self):
        return self._label

    @property
    def directory(self):
        return self._dir

    @property
    def stem(self):
        return self._label


class SpecializedSource:
    """Source SPECIALIZED (acces aleatoire). Frames natives (souvent uint16 IR)."""
    is_raw = True

    def __init__(self, path):
        open_source = operation_for_path(path, "open_source", kind="sequence_format")
        if open_source is None:
            raise IOError("Module specialized_reader indisponible.")
        self.path = path
        self.seq = open_source(path)
        self._fps = 25.0

    @property
    def count(self):
        return self.seq.count

    @property
    def fps(self):
        return self._fps

    def get(self, idx):
        return self.seq.get(idx)

    def close(self):
        try:
            self.seq.close()
        except Exception:
            pass

    @property
    def name(self):
        return os.path.basename(self.path)

    @property
    def directory(self):
        return os.path.dirname(os.path.abspath(self.path))

    @property
    def stem(self):
        return os.path.splitext(os.path.basename(self.path))[0]


def describe_source(src):
    """Description humaine du type de source (rappelée dans les popups
    d'export « Extraire »/« Convertir » : vidéo / séquence / SPECIALIZED, résolution,
    type de données, nb de frames, fps)."""
    if src is None:
        return "Aucune source"
    if isinstance(src, SpecializedSource):
        kind = "SPECIALIZED (données brutes)"
    elif isinstance(src, VideoSource):
        kind = "Vidéo"
    elif isinstance(src, ImageSequenceSource):
        kind = "Sequence d'images"
    elif isinstance(src, YuvSource):
        kind = f"Séquence YUV brute ({src.fmt})"
    else:
        kind = type(src).__name__
    extra = []
    if isinstance(src, VideoSource):
        ext = os.path.splitext(src.path)[1].upper().lstrip(".")
        if ext:
            extra.append(ext)
    elif isinstance(src, ImageSequenceSource) and src.paths:
        ext = os.path.splitext(src.paths[0])[1].upper().lstrip(".")
        if ext:
            extra.append(ext)
    try:
        f0 = src.get(0)
        if f0 is not None:
            h, w = f0.shape[:2]
            ch = f0.shape[2] if f0.ndim == 3 else 1
            extra.append(f"{w}×{h}")
            extra.append(f"{f0.dtype} / {ch}ch")
    except Exception:
        pass
    extra.append(f"{src.count} frames")
    extra.append(f"{src.fps:.1f} fps")
    return kind + ("  —  " + " · ".join(extra) if extra else "")



class FusionSource:
    """Source virtuelle : blend alpha de deux sources rendues, frame par frame.
    Sert à extraire (mp4/png/specialized) le résultat de la vue fusion comme une source
    classique. Capture les sources et leurs fenêtres au moment de la création."""

    def __init__(self, src_a, rp_a, src_b, rp_b, lut_data, cube_lut, cube_size,
                 full_range, filters, alpha, mode="alpha"):
        self._a = src_a
        self._b = src_b
        self._rpa = rp_a           # (lo, hi) de la vue A
        self._rpb = rp_b
        self._lut = lut_data
        self._cube = cube_lut
        self._csize = cube_size
        self._full = full_range
        self._filters = filters
        self._alpha = float(alpha)
        self._mode = mode
        na = src_a.count if src_a else 0
        nb = src_b.count if src_b else 0
        self._count = min(na, nb) if (na and nb) else max(na, nb)
        self.fps = getattr(src_a, "fps", 25.0) or 25.0
        self.name = "Fusion (A⊕B)"

    @property
    def count(self):
        return self._count

    @property
    def directory(self):
        return getattr(self._a, "directory", "")

    @property
    def stem(self):
        return "fusion"

    def _rend(self, src, rp, idx):
        raw = src.get(idx) if src else None
        if raw is None:
            return None
        bgr = render_frame(raw, rp[0], rp[1], self._lut, self._cube,
                           self._csize, self._full, self._filters)
        if bgr.ndim == 2:
            bgr = cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGR)
        return bgr

    def get(self, idx):
        a = self._rend(self._a, self._rpa, idx)
        b = self._rend(self._b, self._rpb, idx)
        return blend_frames(a, b, self._alpha, self._mode)

    def close(self):
        pass
