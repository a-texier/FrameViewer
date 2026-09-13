# -*- coding: utf-8 -*-
"""Histogramme facon FIJI/ImageJ : trace + poignees lo/hi, modes Auto /
Min-Max / Pleine plage / Custom."""
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from frameviewer.core.pipeline import auto_window

class HistCanvas(QtWidgets.QWidget):
    """Trace l'histogramme + 2 poignees (lo/hi) deplacables a la souris."""
    rangeChanged = QtCore.Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(132)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.dmin, self.dmax = 0.0, 255.0
        self.lo, self.hi = 0.0, 255.0
        self.counts = None
        self.log = False
        self._drag = None

    def _m(self):
        return self._ml()

    def _ml(self):
        return 34   # marge gauche : place pour les labels de l'axe Y (comptes)

    def _mr(self):
        return 8

    def _mb(self):
        return 28   # marge basse : ticks domaine (min/max) + fenêtre (lo/hi)

    def _xval(self, v):
        ml, mr = self._ml(), self._mr()
        w = max(1, self.width() - ml - mr)
        if self.dmax <= self.dmin:
            return ml
        return ml + (v - self.dmin) / (self.dmax - self.dmin) * w

    def _valx(self, x):
        ml, mr = self._ml(), self._mr()
        w = max(1, self.width() - ml - mr)
        return self.dmin + (x - ml) / w * (self.dmax - self.dmin)

    def set_domain(self, dmin, dmax):
        self.dmin, self.dmax = float(dmin), float(dmax)
        self.update()

    def set_window(self, lo, hi):
        self.lo, self.hi = float(lo), float(hi)
        self.update()

    def set_counts(self, counts):
        self.counts = counts
        self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(28, 28, 32))
        ml, mr = self._ml(), self._mr()
        w = self.width() - ml - mr
        base = self.height() - self._mb()
        mx = 1.0
        if self.counts is not None and len(self.counts) > 0:
            c = self.counts.astype(np.float32)
            if self.log:
                c = np.log1p(c)
            mx = float(c.max()) if c.max() > 0 else 1.0
            nb = len(c)
            p.setPen(Qt.NoPen)
            p.setBrush(QtGui.QColor(120, 120, 140))
            for i in range(nb):
                bh = (c[i] / mx) * (base - 4)
                x0 = ml + i / nb * w
                x1 = ml + (i + 1) / nb * w
                p.drawRect(QtCore.QRectF(x0, base - bh, max(1.0, x1 - x0), bh))
        # ── axe Y (comptes) : ligne + 3 graduations (0 / mi / max) ──
        p.setPen(QtGui.QPen(QtGui.QColor(90, 90, 100), 1))
        p.drawLine(QtCore.QLineF(ml, 0, ml, base))
        p.setPen(QtGui.QColor(150, 150, 160))
        font = p.font(); font.setPixelSize(9); p.setFont(font)
        for frac in (0.0, 0.5, 1.0):
            y = base - frac * (base - 4)
            p.setPen(QtGui.QPen(QtGui.QColor(60, 60, 68), 1, Qt.DotLine))
            p.drawLine(QtCore.QLineF(ml, y, ml + w, y))
            val = frac * mx
            txt = f"{val:.0f}" if not self.log else f"{np.expm1(val):.0f}"
            p.setPen(QtGui.QColor(150, 150, 160))
            p.drawText(QtCore.QRectF(0, y - 6, ml - 3, 12), Qt.AlignRight | Qt.AlignVCenter, txt)
        # zones hors fenetre assombries
        xlo = self._xval(self.lo)
        xhi = self._xval(self.hi)
        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QColor(0, 0, 0, 115))
        p.drawRect(QtCore.QRectF(ml, 0, max(0.0, xlo - ml), base))
        p.drawRect(QtCore.QRectF(xhi, 0, max(0.0, ml + w - xhi), base))
        # droite de transfert lo->0 .. hi->255
        p.setPen(QtGui.QPen(QtGui.QColor(90, 200, 255), 1.5))
        p.drawLine(QtCore.QLineF(xlo, base, xhi, 4))
        # poignees
        for x, col in ((xlo, QtGui.QColor(90, 200, 255)), (xhi, QtGui.QColor(255, 170, 80))):
            p.setPen(QtGui.QPen(col, 2))
            p.drawLine(QtCore.QLineF(x, 0, x, base))
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRect(QtCore.QRectF(x - 3, base - 6, 6, 6))
        # ── axe X : bornes du domaine (min/max) puis fenêtre courante (lo/hi) ──
        p.setPen(QtGui.QPen(QtGui.QColor(90, 90, 100), 1))
        p.drawLine(QtCore.QLineF(ml, base, ml + w, base))
        p.setPen(QtGui.QColor(120, 120, 130))
        p.drawText(QtCore.QRectF(ml, base + 1, w, 12), Qt.AlignLeft, f"{self.dmin:.0f}")
        p.drawText(QtCore.QRectF(ml, base + 1, w, 12), Qt.AlignRight, f"{self.dmax:.0f}")
        p.setPen(QtGui.QColor(200, 200, 210))
        p.drawText(QtCore.QRectF(ml, base + 13, w, 14), Qt.AlignLeft, f"lo {self.lo:.0f}")
        p.drawText(QtCore.QRectF(ml, base + 13, w, 14), Qt.AlignRight, f"hi {self.hi:.0f}")

    def mousePressEvent(self, e):
        x = e.position().x()
        self._drag = "lo" if abs(x - self._xval(self.lo)) <= abs(x - self._xval(self.hi)) else "hi"
        self._apply(x)

    def mouseMoveEvent(self, e):
        if self._drag:
            self._apply(e.position().x())

    def mouseReleaseEvent(self, _):
        self._drag = None

    def _apply(self, x):
        v = max(self.dmin, min(self.dmax, self._valx(x)))
        if self._drag == "lo":
            self.lo = min(v, self.hi - 1e-6)
        else:
            self.hi = max(v, self.lo + 1e-6)
        self.update()
        self.rangeChanged.emit(self.lo, self.hi)


class HistogramLUT(QtWidgets.QWidget):
    """Panneau type FIJI Brightness/Contrast: histogramme + reglages de fenetre."""
    windowChanged = QtCore.Signal()
    perFrameChanged = QtCore.Signal()  # mode par-frame change (boutons radio)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last = None
        self.canvas = HistCanvas()
        self.canvas.rangeChanged.connect(self._from_canvas)

        self.lo_spin = QtWidgets.QDoubleSpinBox()
        self.hi_spin = QtWidgets.QDoubleSpinBox()
        for s in (self.lo_spin, self.hi_spin):
            # Bornes provisoires (0..255, cas 8 bits le plus courant) : évite
            # d'aller en négatif avant qu'une source ne soit chargée
            # (set_data_range() les resserre ensuite sur le domaine réel).
            s.setRange(0.0, 255.0)
            s.setDecimals(0)
            s.setKeyboardTracking(False)
            # flèches natives ▲▼ retirées : remplacées par des boutons -/+
            # explicites (moins ambigus) juste à côté du champ.
            s.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.lo_spin.valueChanged.connect(self._from_spin)
        self.hi_spin.valueChanged.connect(self._from_spin)

        def _step_btn(txt, tip):
            b = QtWidgets.QToolButton()
            b.setText(txt)
            b.setToolTip(tip)
            b.setFixedSize(22, 22)
            b.setFocusPolicy(Qt.NoFocus)
            b.setAutoRepeat(True)
            b.setStyleSheet(
                "QToolButton{background:#26262e;color:#ddd;border:1px solid #444;"
                "border-radius:3px;font-size:13px;font-weight:bold;}"
                "QToolButton:hover{border-color:#5aafff;color:#fff;}"
                "QToolButton:pressed{background:#333340;}")
            return b

        self.lo_minus = _step_btn("−", "Diminuer le point noir")
        self.lo_plus  = _step_btn("+", "Augmenter le point noir")
        self.hi_minus = _step_btn("−", "Diminuer le point blanc")
        self.hi_plus  = _step_btn("+", "Augmenter le point blanc")
        self.lo_minus.clicked.connect(self.lo_spin.stepDown)
        self.lo_plus.clicked.connect(self.lo_spin.stepUp)
        self.hi_minus.clicked.connect(self.hi_spin.stepDown)
        self.hi_plus.clicked.connect(self.hi_spin.stepUp)

        # Boutons mode (radio-style): Auto / Min-Max / Pleine plage + Reset
        self.auto_btn = QtWidgets.QPushButton("Auto")
        self.auto_btn.setCheckable(True)
        self.minmax_btn = QtWidgets.QPushButton("Min/Max")
        self.minmax_btn.setCheckable(True)
        self.fullrange_btn = QtWidgets.QPushButton("Pleine plage")
        self.fullrange_btn.setCheckable(True)
        # "Custom" : meme type (radio-style) que les 3 autres. Le fixer fige
        # la fenetre lo/hi courante (poignees / spinbox Noir-Blanc) : aucun
        # des 3 autres modes n'etant coche, _display() ne recalcule plus rien
        # au changement de frame (voir per_frame_mode()).
        self.custom_btn = QtWidgets.QPushButton("Custom")
        self.custom_btn.setCheckable(True)
        self.auto_btn.clicked.connect(lambda: self._on_mode_clicked(self.auto_btn))
        self.minmax_btn.clicked.connect(lambda: self._on_mode_clicked(self.minmax_btn))
        self.fullrange_btn.clicked.connect(lambda: self._on_mode_clicked(self.fullrange_btn))
        self.custom_btn.clicked.connect(lambda: self._on_mode_clicked(self.custom_btn))
        for b in (self.auto_btn, self.minmax_btn, self.fullrange_btn, self.custom_btn):
            b.setFocusPolicy(Qt.NoFocus)
        self.log_chk = QtWidgets.QCheckBox("Echelle log")
        self.log_chk.toggled.connect(self._set_log)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self.canvas)

        row1 = QtWidgets.QHBoxLayout()
        row1.addWidget(QtWidgets.QLabel("Noir"))
        row1.addWidget(self.lo_minus)
        row1.addWidget(self.lo_spin, 1)
        row1.addWidget(self.lo_plus)
        row1.addSpacing(8)
        row1.addWidget(QtWidgets.QLabel("Blanc"))
        row1.addWidget(self.hi_minus)
        row1.addWidget(self.hi_spin, 1)
        row1.addWidget(self.hi_plus)
        lay.addLayout(row1)

        row2 = QtWidgets.QHBoxLayout()
        row2.addWidget(self.auto_btn)
        row2.addWidget(self.minmax_btn)
        row2.addWidget(self.fullrange_btn)
        row2.addWidget(self.custom_btn)
        lay.addLayout(row2)

        row4 = QtWidgets.QHBoxLayout()
        row4.addWidget(self.log_chk)
        row4.addStretch(1)
        lay.addLayout(row4)

    # --- API ---
    @property
    def lo(self):
        return self.canvas.lo

    @property
    def hi(self):
        return self.canvas.hi

    @property
    def full_range(self):
        return (self.canvas.dmin, self.canvas.dmax)

    def sat(self):
        # saturation fixe facon FIJI "Auto" (les anciens reglages sat. bas/haut
        # ont ete retires de l'IHM a la demande).
        return (0.35, 0.35)

    def per_frame_mode(self):
        if self.auto_btn.isChecked():      return "Auto"
        if self.minmax_btn.isChecked():    return "Min/Max"
        if self.fullrange_btn.isChecked(): return "Pleine plage"
        return "Custom"

    def set_mode_silent(self, mode):
        """Règle les boutons radio (Auto/Min-Max/Pleine plage/Custom) SANS
        recalcul de fenêtre ni émission de signal : sert à restaurer le mode
        propre à une vue lors d'un changement de vue actives, sans écraser la
        fenêtre (lo/hi) déjà restaurée juste avant par l'appelant."""
        mapping = {"Auto": self.auto_btn, "Min/Max": self.minmax_btn,
                   "Pleine plage": self.fullrange_btn, "Custom": self.custom_btn}
        target = mapping.get(mode)
        for b in (self.auto_btn, self.minmax_btn, self.fullrange_btn, self.custom_btn):
            b.blockSignals(True)
            b.setChecked(b is target)
            b.blockSignals(False)

    def _on_mode_clicked(self, btn):
        """Logique radio : btn reste coché, les autres se décoChent; applique
        immédiatement (sauf Custom, qui garde la fenêtre lo/hi telle quelle)."""
        btn.blockSignals(True)
        btn.setChecked(True)
        btn.blockSignals(False)
        for b in (self.auto_btn, self.minmax_btn, self.fullrange_btn, self.custom_btn):
            if b is not btn and b.isChecked():
                b.blockSignals(True)
                b.setChecked(False)
                b.blockSignals(False)
        # Application immédiate sur la frame courante
        if self._last is not None:
            if btn is self.auto_btn:
                lo, hi = auto_window(self._last, *self.sat())
                self.set_window(lo, hi)
            elif btn is self.minmax_btn:
                self.set_window(float(np.min(self._last)), float(np.max(self._last)))
        if btn is self.fullrange_btn:
            self.set_window(self.canvas.dmin, self.canvas.dmax)
        self.perFrameChanged.emit()

    def set_data_range(self, dmin, dmax):
        dmin = float(dmin)
        dmax = float(dmax)
        if dmax <= dmin:
            dmax = dmin + 1.0
        self.canvas.set_domain(dmin, dmax)
        dec = 3 if (dmax - dmin) <= 5 else 0
        step = (dmax - dmin) / 256.0 if dec else 1.0
        for s in (self.lo_spin, self.hi_spin):
            s.blockSignals(True)
            s.setDecimals(dec)
            s.setRange(dmin, dmax)
            s.setSingleStep(max(step, 10 ** (-dec) if dec else 1.0))
            s.blockSignals(False)

    def set_window(self, lo, hi, emit=True):
        lo = float(lo)
        hi = float(hi)
        if hi <= lo:
            hi = lo + 1.0
        self.canvas.set_window(lo, hi)
        for s, v in ((self.lo_spin, lo), (self.hi_spin, hi)):
            s.blockSignals(True)
            s.setValue(v)
            s.blockSignals(False)
        if emit:
            self.windowChanged.emit()

    def set_histogram(self, inten):
        self._last = inten
        dmin, dmax = self.canvas.dmin, self.canvas.dmax
        if dmax <= dmin:
            dmax = dmin + 1.0
        try:
            counts, _ = np.histogram(np.asarray(inten).ravel(), bins=128, range=(dmin, dmax))
            self.canvas.set_counts(counts)
        except Exception:
            self.canvas.set_counts(None)

    def _from_canvas(self, lo, hi):
        for s, v in ((self.lo_spin, lo), (self.hi_spin, hi)):
            s.blockSignals(True)
            s.setValue(v)
            s.blockSignals(False)
        self.windowChanged.emit()

    def _from_spin(self, *_):
        dmin, dmax = self.canvas.dmin, self.canvas.dmax
        lo = max(dmin, min(dmax, self.lo_spin.value()))
        hi = max(dmin, min(dmax, self.hi_spin.value()))
        if hi <= lo:
            hi = min(dmax, lo + 1.0)
        # re-sync l'affichage des spinbox si le clamp au domaine a changé la valeur
        for s, v in ((self.lo_spin, lo), (self.hi_spin, hi)):
            if abs(s.value() - v) > 1e-9:
                s.blockSignals(True)
                s.setValue(v)
                s.blockSignals(False)
        self.canvas.set_window(lo, hi)
        self.windowChanged.emit()

    def _set_log(self, on):
        self.canvas.log = bool(on)
        self.canvas.update()

    def do_auto(self):
        self.auto_btn.click()

    def do_minmax(self):
        self.minmax_btn.click()


# ------------- conversion (tout type -> tout autre type, en thread) -------------

