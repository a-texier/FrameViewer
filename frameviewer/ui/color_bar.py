# -*- coding: utf-8 -*-
"""Echelle de couleur (degrade de la LUT courante) sous l'histogramme."""
import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

class ColorBar(QtWidgets.QWidget):
    """Échelle de couleur : dégradé de la LUT courante, bornes lo/hi affichées."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self._lut = None      # code colormap OpenCV ou None (gris)
        self._lo = 0.0
        self._hi = 255.0

    def set_lut(self, lut_code):
        self._lut = lut_code
        self.update()

    def set_range(self, lo, hi):
        self._lo, self._hi = float(lo), float(hi)
        self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        w, h = self.width(), self.height()
        bar_h = 14
        ramp = np.linspace(0, 255, max(2, w)).astype(np.uint8).reshape(1, -1)
        if self._lut is None or self._lut == "gray":
            bgr = cv2.cvtColor(ramp, cv2.COLOR_GRAY2BGR)
        else:
            try:
                bgr = cv2.applyColorMap(ramp, int(self._lut))
            except Exception:
                bgr = cv2.cvtColor(ramp, cv2.COLOR_GRAY2BGR)
        rgb = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        img = QtGui.QImage(rgb.data, rgb.shape[1], 1, rgb.strides[0],
                           QtGui.QImage.Format_RGB888)
        p.drawImage(QtCore.QRect(0, 0, w, bar_h), img)
        p.setPen(QtGui.QColor(60, 60, 68))
        p.drawRect(0, 0, w - 1, bar_h)
        p.setPen(QtGui.QColor(170, 170, 180))
        f = p.font(); f.setPointSize(8); p.setFont(f)
        mid = (self._lo + self._hi) / 2.0
        p.drawText(QtCore.QRectF(1, bar_h, w, h - bar_h), Qt.AlignLeft,
                   f"{self._lo:.0f}")
        p.drawText(QtCore.QRectF(0, bar_h, w, h - bar_h), Qt.AlignCenter,
                   f"{mid:.0f}")
        p.drawText(QtCore.QRectF(0, bar_h, w - 1, h - bar_h), Qt.AlignRight,
                   f"{self._hi:.0f}")
