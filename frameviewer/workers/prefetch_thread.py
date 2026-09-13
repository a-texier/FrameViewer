# -*- coding: utf-8 -*-
"""Precharge N frames en avance depuis une source image/SPECIALIZED (tache de fond,
reduit les saccades en lecture reseau SSH)."""
import cv2
from PySide6 import QtCore

class _PrefetchThread(QtCore.QThread):
    """Thread de fond : charge N frames en avance depuis une source image/SPECIALIZED."""
    # Signal(dict) sous PySide6 6.8 peut convertir silencieusement le payload
    # Python en dictionnaire vide lors du passage inter-thread. Signal(object)
    # conserve les ndarray du cache et leurs indices sans marshaling Qt.
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
