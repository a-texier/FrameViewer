# -*- coding: utf-8 -*-
"""Prefetch upcoming frames to reduce latency for remote media sources."""
import cv2
from PySide6 import QtCore

class _PrefetchThread(QtCore.QThread):
    """Load selected frames into a cache from a background thread."""
    # PySide6 6.8 may silently turn Signal(dict) into an empty mapping across
    # threads. Signal(object) preserves NumPy arrays and their indices.
    done = QtCore.Signal(object)

    def __init__(self, source, indices, scale):
        super().__init__()
        self._src = source
        self._idx = list(indices)
        self._scale = scale
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        cache = {}
        for i in self._idx:
            if self._stop:
                return
            frame = self._src.get(i)
            if frame is None:
                continue
            s = self._scale
            if s < 1.0:
                h, w = frame.shape[:2]
                nh = max(1, int(round(h * s)))
                nw = max(1, int(round(w * s)))
                frame = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
            cache[i] = frame
        if not self._stop:
            self.done.emit(cache)
