# -*- coding: utf-8 -*-
"""Indexed media-source abstractions used by the rendering pipeline.

Concrete implementations cover video, image sequences, raw streams and
optional formats. A virtual source renders two inputs as a blend.
"""
import os

import cv2
import numpy as np

from .io_utils import imread_unicode
from .feature_registry import operation_for_path
from .pipeline import blend_frames, render_frame


# Supported raw YUV layouts: identifier -> (display label, bytes per pixel).
YUV_FORMATS = {
    "gray8":    ("Gris 8-bit (Y seul)",             1.0),
    "gray16le": ("Gris 16-bit little-endian (Y seul)", 2.0),
    "i420":     ("YUV 4:2:0 planaire (I420 / IYUV)", 1.5),
    "nv12":     ("YUV 4:2:0 semi-planaire (NV12)",   1.5),
    "yuyv":     ("YUV 4:2:2 entrelace (YUYV / YUY2)", 2.0),
    "uyvy":     ("YUV 4:2:2 entrelace (UYVY)",        2.0),
}


def yuv_frame_bytes(width, height, fmt):
    """Return the byte size of one raw frame, or zero for an unknown layout."""
    spec = YUV_FORMATS.get(fmt)
    if spec is None:
        return 0
    return int(round(int(width) * int(height) * spec[1]))


def decode_yuv(data, width, height, fmt):
    """Decode raw YUV or grayscale bytes into a BGR or 2D NumPy array.

    Return ``None`` for insufficient data or an unknown layout.
    """
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
        # Some containers and codecs do not expose a reliable frame index.
        # Once random access fails, use sequential decoding with rewind as the
        # reliable fallback.
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
        """Decode sequentially to ``idx``, rewinding only when moving backward."""
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
        # Fast path for ordinary sequential playback.
        if idx == self._next:
            ok, frame = self.cap.read()
            if ok:
                self._learn_count(idx)
                return frame
            return self._get_sequential(idx)
        # Try native random access first.
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        self._next = idx
        ok, frame = self.cap.read()
        if ok:
            self._learn_count(idx)
            return frame
        # Remember that native seeking is broken for this stream.
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
    """Raw YUV/grayscale file sequence with one file per frame.

    Raw media has no header, so the import dialog supplies geometry and layout.
    Frames are read and decoded on demand.
    """
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
    """Random-access optional sequence source preserving native frame types."""
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
    """Return a human-readable source description for export dialogs."""
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
    """Virtual source that blends two rendered sources frame by frame.

    It exposes the composition as a regular exportable source and captures the
    source display windows when created.
    """

    def __init__(self, src_a, rp_a, src_b, rp_b, lut_data, cube_lut, cube_size,
                 full_range, filters, alpha, mode="alpha"):
        self._a = src_a
        self._b = src_b
        self._rpa = rp_a           # Display window for source A.
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
