# -*- coding: utf-8 -*-
"""Panneau Outils TI (facon FIJI) : ROI+FFT2D, profil de ligne, regle,
soustraction temporelle -- sections repliables."""
import math

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from frameviewer.ui.roi_panel import RoiPanel

class LineProfCanvas(QtWidgets.QWidget):
    """Graphique de profil de ligne (QPainter, sans matplotlib)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                           QtWidgets.QSizePolicy.Fixed)
        self.profile = None

    def set_profile(self, profile):
        self.profile = np.asarray(profile, dtype=np.float64) if profile is not None else None
        self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(24, 24, 28))
        if self.profile is None or len(self.profile) < 2:
            p.setPen(QtGui.QColor(90, 90, 100))
            p.drawText(self.rect(), Qt.AlignCenter, "Tracez une ligne sur l'image")
            return
        m, W, H = 8, self.width() - 16, self.height() - 30
        data = self.profile
        vmin, vmax = float(data.min()), float(data.max())
        if vmax <= vmin:
            vmax = vmin + 1.0
        n = len(data)
        sx = lambda i: m + i / (n - 1) * W
        sy = lambda v: m + H - (v - vmin) / (vmax - vmin) * H
        if vmin <= 0 <= vmax:
            p.setPen(QtGui.QPen(QtGui.QColor(60, 60, 75), 1, Qt.DashLine))
            p.drawLine(QtCore.QLineF(m, sy(0), m + W, sy(0)))
        path = QtGui.QPainterPath()
        path.moveTo(sx(0), sy(data[0]))
        for i in range(1, n):
            path.lineTo(sx(i), sy(data[i]))
        p.setPen(QtGui.QPen(QtGui.QColor(90, 200, 255), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(QtGui.QColor(130, 130, 145))
        base = m + H + 4
        p.drawText(QtCore.QRectF(m, base, W, 14), Qt.AlignLeft,  f"{vmin:.0f}")
        p.drawText(QtCore.QRectF(m, base, W, 14), Qt.AlignRight, f"{vmax:.0f}")
        p.drawText(QtCore.QRectF(m, m, W, 14), Qt.AlignRight, f"{n - 1} px")


# ──────────────────────────── section repliable ───────────────────
class CollapsibleSection(QtWidgets.QWidget):
    """En-tête cliquable (▾/▸) qui replie/déplie son contenu — gain de place."""

    def __init__(self, title, parent=None, expanded=False):
        super().__init__(parent)
        self._v = QtWidgets.QVBoxLayout(self)
        self._v.setContentsMargins(0, 0, 0, 0)
        self._v.setSpacing(0)
        self.header = QtWidgets.QToolButton()
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                  QtWidgets.QSizePolicy.Fixed)
        self.header.setStyleSheet(
            "QToolButton{border:none;background:#26262f;color:#cfd3dc;"
            "padding:5px 6px;font-weight:bold;text-align:left;border-radius:3px;}"
            "QToolButton:hover{background:#30303c;}")
        self.header.clicked.connect(self._toggle)
        self.body = QtWidgets.QWidget()
        self.body_l = QtWidgets.QVBoxLayout(self.body)
        self.body_l.setContentsMargins(6, 3, 4, 6)
        self.body_l.setSpacing(3)
        self._v.addWidget(self.header)
        self._v.addWidget(self.body)
        self.body.setVisible(expanded)

    def addWidget(self, w, stretch=0):
        self.body_l.addWidget(w, stretch)

    def addLayout(self, l):
        self.body_l.addLayout(l)

    def set_expanded(self, on):
        self.header.setChecked(on)
        self.header.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.body.setVisible(on)

    def _toggle(self):
        self.set_expanded(self.header.isChecked())


# ──────────────────────────── panneau Outils TI ───────────────────
class ToolsPanel(QtWidgets.QWidget):
    """Outils TI (style FIJI) : ROI+FFT2D, profil de ligne, règle, soustraction
    temporelle — chaque outil dans une section repliable (accordéon)."""
    modeRequested  = QtCore.Signal(str)       # "roi" | "line" | "ruler"
    tempSubChanged = QtCore.Signal(bool, int) # (enabled, k)
    fft2dRequested = QtCore.Signal()          # FFT 2D spatiale sur la ROI courante
    roiConvertRequested = QtCore.Signal()     # export ROI (fixe/suivie) sur la séquence

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(5)
        self._active_tool = "roi"

        # ── ROI : zoom crop + mini histogramme + statistiques + FFT 2D ──
        self._roi_sec = CollapsibleSection("ROI — sélection / mesures", expanded=True)
        self.roi_panel = RoiPanel()
        self.roi_panel.convertRequested.connect(self.roiConvertRequested.emit)
        self._roi_sec.addWidget(self.roi_panel, stretch=1)
        fft_row = QtWidgets.QHBoxLayout()
        self.fft2d_btn = QtWidgets.QPushButton("FFT 2D (spectre spatial)")
        self.fft2d_btn.setToolTip(
            "Transformée de Fourier 2D du crop ROI courant.\n"
            "Affiche le spectre de magnitude (log) — analyse de texture / périodicité.")
        self.fft2d_btn.clicked.connect(lambda: self.fft2dRequested.emit())
        fft_row.addWidget(self.fft2d_btn)
        self._roi_sec.addLayout(fft_row)
        self.fft2d_label = QtWidgets.QLabel("Spectre FFT 2D : tracer une ROI puis cliquer.")
        self.fft2d_label.setAlignment(Qt.AlignCenter)
        self.fft2d_label.setMinimumHeight(140)
        self.fft2d_label.setStyleSheet(
            "background:#161618; border:1px solid #3a3a42; color:#888; font-size:11px;")
        self._roi_sec.addWidget(self.fft2d_label)
        self.fft2d_info = QtWidgets.QLabel("─")
        self.fft2d_info.setStyleSheet("font-size:11px; color:#bbb;")
        self.fft2d_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._roi_sec.addWidget(self.fft2d_info)
        lay.addWidget(self._roi_sec)

        # ── profil de ligne ──
        self._line_sec = CollapsibleSection("Profil de ligne", expanded=False)
        self.prof_canvas = LineProfCanvas()
        self._line_sec.addWidget(self.prof_canvas)
        self.prof_stats = QtWidgets.QLabel("─")
        self.prof_stats.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.prof_stats.setStyleSheet("font-size:11px; color:#bbb;")
        self._line_sec.addWidget(self.prof_stats)
        self.prof_csv_btn = QtWidgets.QPushButton("Exporter CSV")
        self.prof_csv_btn.setEnabled(False)
        self.prof_csv_btn.clicked.connect(self._export_csv)
        self._line_sec.addWidget(self.prof_csv_btn)
        lay.addWidget(self._line_sec)

        # ── règle multipoint ──
        self._ruler_sec = CollapsibleSection("Règle — distance / angle", expanded=False)
        self.ruler_lbl = QtWidgets.QLabel(
            "Clic = ajouter un point\nClic droit = réinitialiser")
        self.ruler_lbl.setWordWrap(True)
        self.ruler_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.ruler_lbl.setStyleSheet("font-size:11px; color:#bbb;")
        self._ruler_sec.addWidget(self.ruler_lbl)
        lay.addWidget(self._ruler_sec)

        # accordéon : ouvrir une section = sélectionner l'outil correspondant
        self._tool_secs = {"roi": self._roi_sec, "line": self._line_sec,
                           "ruler": self._ruler_sec}
        for mode, sec in self._tool_secs.items():
            sec.header.clicked.connect(lambda _=False, m=mode: self._set_tool(m))

        # ── soustraction temporelle (repliable) ──
        self._sub_sec = CollapsibleSection("Soustraction temporelle", expanded=False)
        self.sub_chk = QtWidgets.QCheckBox("Activer  |frame N − frame N−k|")
        self.sub_chk.toggled.connect(self._emit_sub)
        self._sub_sec.addWidget(self.sub_chk)
        row_k = QtWidgets.QHBoxLayout()
        row_k.addWidget(QtWidgets.QLabel("k ="))
        self.sub_spin = QtWidgets.QSpinBox()
        self.sub_spin.setRange(1, 9999); self.sub_spin.setValue(1)
        self.sub_spin.setFocusPolicy(Qt.NoFocus)
        self.sub_spin.valueChanged.connect(self._emit_sub)
        row_k.addWidget(self.sub_spin); row_k.addStretch(1)
        self._sub_sec.addLayout(row_k)
        hint = QtWidgets.QLabel("Révèle mouvements et variations thermiques.")
        hint.setStyleSheet("color:#888; font-size:10px;")
        self._sub_sec.addWidget(hint)
        lay.addWidget(self._sub_sec)

        lay.addStretch(1)

        self._profile = None

    def _set_tool(self, mode):
        if mode not in self._tool_secs:
            return
        self._active_tool = mode
        for m, sec in self._tool_secs.items():
            sec.set_expanded(m == mode)
        self.modeRequested.emit(mode)

    def is_roi_active(self):
        return self._active_tool == "roi"

    def update_profile(self, profile):
        self._profile = profile
        self.prof_canvas.set_profile(profile)
        if profile is not None and len(profile) > 0:
            mn, mx, av = float(profile.min()), float(profile.max()), float(profile.mean())
            self.prof_stats.setText(
                f"N={len(profile)} px   min={mn:.1f}   max={mx:.1f}   moy={av:.2f}")
            self.prof_csv_btn.setEnabled(True)
        else:
            self.prof_stats.setText("─")
            self.prof_csv_btn.setEnabled(False)

    def update_ruler_pts(self, pts):
        """Met à jour l'affichage de la règle multipoint."""
        if not pts:
            self.ruler_lbl.setText(
                "Clic = ajouter un point\nClic droit = réinitialiser")
            return
        lines = [f"P{i+1} = ({x}, {y})" for i, (x, y) in enumerate(pts)]
        if len(pts) >= 2:
            total = sum(math.hypot(pts[i][0] - pts[i-1][0], pts[i][1] - pts[i-1][1])
                        for i in range(1, len(pts)))
            lines.append(f"Distance totale : {total:.2f} px")
            x1, y1 = pts[-2]; x2, y2 = pts[-1]
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
            lines.append(f"Angle dernier segment : {angle:.2f}°")
        self.ruler_lbl.setText("\n".join(lines))

    def update_ruler(self, x1, y1, x2, y2):
        self.update_ruler_pts([(x1, y1), (x2, y2)])

    def show_fft2d(self, qpix, info):
        """Affiche le spectre FFT 2D (QPixmap) + un libellé d'info."""
        if qpix is not None:
            self.fft2d_label.setPixmap(qpix)
            self.fft2d_info.setText(info or "─")
        else:
            self.fft2d_label.setPixmap(QtGui.QPixmap())
            self.fft2d_label.setText(info or "Spectre FFT 2D : tracer une ROI puis cliquer.")
            self.fft2d_info.setText("─")

    def _export_csv(self):
        if self._profile is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Exporter profil CSV", "profil_ligne.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("index,valeur\n")
                for i, v in enumerate(self._profile):
                    f.write(f"{i},{float(v):.4f}\n")
        except Exception as ex:
            QtWidgets.QMessageBox.warning(self, "Erreur CSV", str(ex))

    def _emit_sub(self, *_):
        self.tempSubChanged.emit(self.sub_chk.isChecked(), self.sub_spin.value())


# ──────────────────────────── spectre FFT ─────────────────────────
class FftCanvas(QtWidgets.QWidget):
    """Spectre FFT d'un signal ROI temporel (QPainter, sans matplotlib)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(110)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                           QtWidgets.QSizePolicy.Fixed)
        self.freqs = None
        self.psd = None
        self.peak_freq = None

    def set_spectrum(self, freqs, psd):
        self.freqs = np.asarray(freqs, dtype=np.float64) if freqs is not None else None
        self.psd   = np.asarray(psd,   dtype=np.float64) if psd   is not None else None
        self.peak_freq = float(self.freqs[int(np.argmax(self.psd))]) \
            if (self.freqs is not None and self.psd is not None and len(self.psd)) else None
        self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(24, 24, 28))
        if self.freqs is None or self.psd is None or len(self.psd) < 2:
            p.setPen(QtGui.QColor(90, 90, 100))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "Sélectionner une ROI puis cliquer sur Analyser FFT")
            return
        m, W, H = 8, self.width() - 16, self.height() - 30
        freqs, psd = self.freqs, self.psd
        vmax = float(psd.max()) or 1.0
        n = len(freqs)
        sx = lambda i: m + i / max(n - 1, 1) * W
        sy = lambda v: m + H - v / vmax * H
        # fond de pic
        if self.peak_freq is not None:
            pi = int(np.argmax(psd))
            p.setPen(QtGui.QPen(QtGui.QColor(255, 180, 0, 80), 2))
            p.drawLine(QtCore.QLineF(sx(pi), m, sx(pi), m + H))
        # remplissage
        area = QtGui.QPainterPath()
        area.moveTo(sx(0), m + H)
        for i in range(n):
            area.lineTo(sx(i), sy(psd[i]))
        area.lineTo(sx(n - 1), m + H)
        area.closeSubpath()
        p.setBrush(QtGui.QColor(50, 120, 200, 120))
        p.setPen(Qt.NoPen)
        p.drawPath(area)
        # contour
        outline = QtGui.QPainterPath()
        outline.moveTo(sx(0), sy(psd[0]))
        for i in range(1, n):
            outline.lineTo(sx(i), sy(psd[i]))
        p.setPen(QtGui.QPen(QtGui.QColor(90, 180, 255), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawPath(outline)
        # labels
        p.setPen(QtGui.QColor(130, 130, 145))
        base = m + H + 4
        p.drawText(QtCore.QRectF(m, base, W, 14), Qt.AlignLeft,
                   f"{freqs[0]:.2f} Hz")
        p.drawText(QtCore.QRectF(m, base, W, 14), Qt.AlignRight,
                   f"{freqs[-1]:.2f} Hz")
        if self.peak_freq is not None:
            p.setPen(QtGui.QColor(255, 200, 50))
            p.drawText(QtCore.QRectF(m, m, W, 14), Qt.AlignCenter,
                       f"Pic : {self.peak_freq:.4f} Hz")

