# -*- coding: utf-8 -*-
"""Panneau de selection (ROI) : mini-histogramme du crop + statistiques."""
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from frameviewer.ui.i18n import set_ui_pair, set_ui_text, ui_text

class MiniHist(QtWidgets.QWidget):
    """Petit histogramme (sans poignees) pour la zone selectionnee, avec
    axes : X = niveau de gris (min..max du crop), Y = nombre de pixels."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(88)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.counts = None
        self._lo = 0.0
        self._hi = 255.0

    def set_data(self, inten):
        try:
            g = np.asarray(inten).ravel()
            if g.size == 0:
                self.counts = None
            else:
                lo = float(g.min())
                hi = float(g.max())
                if hi <= lo:
                    hi = lo + 1.0
                self._lo, self._hi = lo, hi
                self.counts, _ = np.histogram(g, bins=64, range=(lo, hi))
        except Exception:
            self.counts = None
        self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(24, 24, 28))
        ml, mr, mb, mt = 28, 4, 13, 4
        w = max(1, self.width() - ml - mr)
        base = self.height() - mb
        mx = 1.0
        if self.counts is not None and len(self.counts):
            c = self.counts.astype(np.float32)
            mx = float(c.max()) if c.max() > 0 else 1.0
            nb = len(c)
            p.setPen(Qt.NoPen)
            p.setBrush(QtGui.QColor(120, 160, 210))
            for i in range(nb):
                bh = (c[i] / mx) * (base - mt - 2)
                x0 = ml + i / nb * w
                x1 = ml + (i + 1) / nb * w
                p.drawRect(QtCore.QRectF(x0, base - bh, max(1.0, x1 - x0), bh))
        # axe Y (comptes) : ligne + graduations 0 / max
        p.setPen(QtGui.QPen(QtGui.QColor(80, 80, 90), 1))
        p.drawLine(QtCore.QLineF(ml, mt, ml, base))
        p.drawLine(QtCore.QLineF(ml, base, ml + w, base))
        font = p.font(); font.setPixelSize(8); p.setFont(font)
        p.setPen(QtGui.QColor(150, 150, 160))
        p.drawText(QtCore.QRectF(0, mt - 2, ml - 3, 11), Qt.AlignRight, f"{mx:.0f}")
        p.drawText(QtCore.QRectF(0, base - 6, ml - 3, 11), Qt.AlignRight, "0")
        # axe X (niveau de gris) : bornes min/max du crop
        p.drawText(QtCore.QRectF(ml, base + 1, w, 11), Qt.AlignLeft, f"{self._lo:.0f}")
        p.drawText(QtCore.QRectF(ml, base + 1, w, 11), Qt.AlignRight, f"{self._hi:.0f}")


class RoiPanel(QtWidgets.QWidget):
    """Zoom du crop + mini histogramme + statistiques de la selection."""
    convertRequested = QtCore.Signal()   # bouton "Convertir" (export ROI sur la séquence)

    def _tr(self, text):
        return ui_text(self, text)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._raw_pm = None  # pixmap brut; rescale dans resizeEvent

        self.convert_btn = QtWidgets.QPushButton("Convertir…")
        self.convert_btn.setToolTip(
            "Exporte cette ROI (fixe, ou suivant une boîte .ver si un suivi\n"
            "est actif) sur toute la séquence : MP4 / SPECIALIZED / dossier PNG,\n"
            "avec option heatmap des histogrammes.")
        self.convert_btn.clicked.connect(self.convertRequested.emit)

        self.crop_label = QtWidgets.QLabel("(pas de selection)")
        # Hauteur FIXE : évite la boucle de rétroaction (le pixmap redimensionne le
        # label -> relayout -> resize -> rescale...) qui faisait « grossir/scintiller »
        # le crop dans le QScrollArea. Politique verticale Fixed + hauteur fixe.
        self.crop_label.setFixedHeight(230)
        self.crop_label.setMinimumWidth(150)
        self.crop_label.setAlignment(Qt.AlignCenter)
        self.crop_label.setStyleSheet("background:#161618; border:1px solid #3a3a42; color:#888;")
        self.crop_label.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed)
        self.mini = MiniHist()
        self.stats = QtWidgets.QLabel("Active l'outil 'Selection / ROI' puis\n"
                                      "trace un rectangle sur l'image.")
        self.stats.setWordWrap(True)
        self.stats.setTextInteractionFlags(Qt.TextSelectableByMouse)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self.convert_btn)
        lay.addWidget(self.crop_label, 1)
        lay.addWidget(QtWidgets.QLabel("Histogramme du crop:"))
        lay.addWidget(self.mini)
        lay.addWidget(self.stats)

    def sizeHint(self):
        return QtCore.QSize(260, 460)

    def minimumSizeHint(self):
        return QtCore.QSize(180, 300)

    def clear(self):
        self._raw_pm = None
        self.crop_label.setPixmap(QtGui.QPixmap())
        set_ui_text(self.crop_label, "(pas de selection)")
        self.mini.counts = None
        self.mini.update()
        set_ui_text(
            self.stats,
            "Active l'outil 'Selection / ROI' puis\ntrace un rectangle sur l'image.")

    def _rescale_crop(self):
        if self._raw_pm is None:
            return
        sz = self.crop_label.size()
        if sz.isEmpty() or sz.width() < 4 or sz.height() < 4:
            return
        pm = self._raw_pm.scaled(sz, Qt.KeepAspectRatio, Qt.FastTransformation)
        # blockSignals empeche le setPixmap de declencher un updateGeometry
        self.crop_label.blockSignals(True)
        self.crop_label.setPixmap(pm)
        self.crop_label.blockSignals(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale_crop()

    def update_crop(self, bgr_crop, inten_crop):
        if bgr_crop is None or bgr_crop.size == 0:
            self.clear()
            return
        bgr = np.ascontiguousarray(bgr_crop)
        if bgr.ndim == 2:
            h, w = bgr.shape
            qimg = QtGui.QImage(bgr.data, w, h, bgr.strides[0], QtGui.QImage.Format_Grayscale8)
        else:
            h, w = bgr.shape[:2]
            qimg = QtGui.QImage(bgr.data, w, h, bgr.strides[0], QtGui.QImage.Format_BGR888)
        # Stocker le pixmap natif; le rescale se fait dans resizeEvent et ici
        self._raw_pm = QtGui.QPixmap.fromImage(qimg).copy()
        self._rescale_crop()

        self.mini.set_data(inten_crop)
        g = np.asarray(inten_crop, dtype=np.float64).ravel()
        if g.size:
            french = (
                f"{w} x {h} px   surface {w*h} px²\n"
                f"min {g.min():.1f}    max {g.max():.1f}\n"
                f"moyenne {g.mean():.2f}    σ {g.std():.2f}\n"
                f"mediane {np.median(g):.1f}"
            )
            english = (
                f"{w} x {h} px   area {w*h} px²\n"
                f"min {g.min():.1f}    max {g.max():.1f}\n"
                f"mean {g.mean():.2f}    σ {g.std():.2f}\n"
                f"median {np.median(g):.1f}"
            )
            set_ui_pair(self.stats, french, english)
        else:
            set_ui_text(self.stats, "Selection vide.")


# ------------------------------ affichage ---------------------------------
