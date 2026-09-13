# -*- coding: utf-8 -*-
"""Export custom des calques .ver : barre de plage de frames a 2 poignees
(_RangeBar) + dialogue de reorganisation (eclater/fusionner les tracks)."""
import os

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

class _RangeBar(QtWidgets.QWidget):
    """Barre horizontale a 2 poignees glissables (bleu) pour choisir une
    plage [lo, hi] de frames — utilisee par LayerExportDialog."""
    rangeChanged = QtCore.Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._n = 1
        self._lo = 0
        self._hi = 0
        self._drag = None   # "lo" | "hi" | None
        self.setMinimumHeight(30)
        self.setMouseTracking(True)

    def set_range(self, n):
        self._n = max(1, n)
        self._lo = 0
        self._hi = self._n - 1
        self.update()

    def set_values(self, lo, hi):
        self._lo = max(0, min(int(lo), self._n - 1))
        self._hi = max(self._lo, min(int(hi), self._n - 1))
        self.update()

    def values(self):
        return self._lo, self._hi

    def _track_w(self):
        return max(1, self.width() - 16)

    def _x_of(self, v):
        return 8 + (v / max(1, self._n - 1)) * self._track_w()

    def _v_of(self, x):
        v = (x - 8) / self._track_w() * (self._n - 1)
        return int(round(max(0, min(self._n - 1, v))))

    @staticmethod
    def _evt_x(e):
        try:
            return e.position().x()
        except AttributeError:
            return e.x()

    def mousePressEvent(self, e):
        x = self._evt_x(e)
        xlo, xhi = self._x_of(self._lo), self._x_of(self._hi)
        self._drag = "lo" if abs(x - xlo) <= abs(x - xhi) else "hi"
        self._drag_to(x)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self._drag_to(self._evt_x(e))

    def mouseReleaseEvent(self, e):
        self._drag = None

    def _drag_to(self, x):
        v = self._v_of(x)
        if self._drag == "lo":
            self._lo = min(v, self._hi)
        else:
            self._hi = max(v, self._lo)
        self.update()
        self.rangeChanged.emit(self._lo, self._hi)

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        w = self.width()
        mid = self.height() // 2
        p.setPen(QtGui.QPen(QtGui.QColor(80, 80, 90), 3))
        p.drawLine(8, mid, w - 8, mid)
        xlo, xhi = self._x_of(self._lo), self._x_of(self._hi)
        p.setPen(QtGui.QPen(QtGui.QColor(90, 150, 220), 5))
        p.drawLine(int(xlo), mid, int(xhi), mid)
        for x in (xlo, xhi):
            p.setBrush(QtGui.QColor(90, 150, 220))
            p.setPen(QtGui.QPen(QtGui.QColor(20, 20, 25), 1))
            p.drawEllipse(QtCore.QPointF(x, mid), 7, 7)


class LayerExportDialog(QtWidgets.QDialog):
    """Export custom des calques .ver de la vue active : choisit une plage
    de frames (barre a 2 poignees) puis reorganise les tracks entre fichiers
    de sortie — eclater un .ver multi-tracks en plusieurs fichiers
    mono-track, ou fusionner plusieurs .ver en un seul multi-tracks."""

    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw
        self.setWindowTitle("Export custom des calques")
        self.setMinimumWidth(480)
        n = mw.source.count if mw.source else 1

        vbox = QtWidgets.QVBoxLayout(self)
        lbl = QtWidgets.QLabel(
            "Choisis la plage de frames à exporter, les tracks .ver à inclure, "
            "puis comment répartir ces tracks entre les fichiers de sortie.")
        lbl.setWordWrap(True)
        vbox.addWidget(lbl)

        grp_range = QtWidgets.QGroupBox("Plage de frames")
        rl = QtWidgets.QVBoxLayout(grp_range)
        self._bar = _RangeBar()
        self._bar.set_range(n)
        rl.addWidget(self._bar)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("De :"))
        self._sp_lo = QtWidgets.QSpinBox()
        self._sp_lo.setRange(0, max(0, n - 1))
        row.addWidget(self._sp_lo)
        row.addWidget(QtWidgets.QLabel("à :"))
        self._sp_hi = QtWidgets.QSpinBox()
        self._sp_hi.setRange(0, max(0, n - 1))
        self._sp_hi.setValue(max(0, n - 1))
        row.addWidget(self._sp_hi)
        row.addStretch(1)
        rl.addLayout(row)
        vbox.addWidget(grp_range)
        self._bar.rangeChanged.connect(self._on_bar_changed)
        self._sp_lo.valueChanged.connect(self._on_spin_changed)
        self._sp_hi.valueChanged.connect(self._on_spin_changed)

        grp_tr = QtWidgets.QGroupBox("Calques .ver (décoche ceux à exclure)")
        tl = QtWidgets.QVBoxLayout(grp_tr)
        self._tree = QtWidgets.QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setMaximumHeight(150)
        groups = {}   # fichier -> [track_id, ...]
        for L in mw._layers:
            # les calques issus d'un dossier YOLO (fmt "yolo") n'ont pas de
            # concept de track -- seule une classe par boite, sans identite
            # d'objet entre frames -- donc hors de propos ici.
            if L["kind"] == "ver" and L.get("fmt", "ver") == "ver":
                groups.setdefault(L.get("file") or L["name"], []).append(L["key"])
        for file, tracks in groups.items():
            parent = QtWidgets.QTreeWidgetItem(
                [f"📄 {file}  ({len(tracks)} track{'s' if len(tracks) > 1 else ''})"])
            parent.setFlags(parent.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            parent.setData(0, Qt.UserRole, file)
            parent.setCheckState(0, Qt.Checked)
            self._tree.addTopLevelItem(parent)
            for tid in tracks:
                child = QtWidgets.QTreeWidgetItem([f"track {tid}"])
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Checked)
                child.setData(0, Qt.UserRole, tid)
                parent.addChild(child)
            parent.setExpanded(True)
        tl.addWidget(self._tree)
        vbox.addWidget(grp_tr)
        if not groups:
            grp_tr.setEnabled(False)

        grp_mode = QtWidgets.QGroupBox("Répartition des fichiers de sortie")
        ml = QtWidgets.QVBoxLayout(grp_mode)
        self._rb_split = QtWidgets.QRadioButton(
            "Éclater : un fichier .ver PAR TRACK cochée (mono-track chacun)")
        self._rb_merge = QtWidgets.QRadioButton(
            "Fusionner : toutes les tracks cochées dans un seul fichier .ver")
        self._rb_split.setChecked(True)
        for rb in (self._rb_split, self._rb_merge):
            ml.addWidget(rb)
        vbox.addWidget(grp_mode)

        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        vbox.addWidget(self._status)

        row2 = QtWidgets.QHBoxLayout()
        go = QtWidgets.QPushButton("Exporter...")
        go.clicked.connect(self._export)
        row2.addWidget(go)
        row2.addStretch(1)
        close = QtWidgets.QPushButton("Fermer")
        close.clicked.connect(self.accept)
        row2.addWidget(close)
        vbox.addLayout(row2)

    def _on_bar_changed(self, lo, hi):
        for sp, v in ((self._sp_lo, lo), (self._sp_hi, hi)):
            sp.blockSignals(True)
            sp.setValue(v)
            sp.blockSignals(False)

    def _on_spin_changed(self, *_):
        lo, hi = self._sp_lo.value(), self._sp_hi.value()
        if hi < lo:
            hi = lo
            self._sp_hi.blockSignals(True)
            self._sp_hi.setValue(hi)
            self._sp_hi.blockSignals(False)
        self._bar.set_values(lo, hi)

    def _checked_tracks(self):
        """{fichier: [track_id, ...]} pour les tracks cochées."""
        out = {}
        for i in range(self._tree.topLevelItemCount()):
            parent = self._tree.topLevelItem(i)
            file = parent.data(0, Qt.UserRole)
            tids = [parent.child(j).data(0, Qt.UserRole)
                    for j in range(parent.childCount())
                    if parent.child(j).checkState(0) == Qt.Checked]
            if tids:
                out[file] = tids
        return out

    def _build_ver_lines(self, start, end, tids):
        """Lignes .ver (frame ré-indexée 0-based dans la plage) pour les
        tracks demandées, meme format que MainWindow._export_annot_slice."""
        mw = self._mw
        tids = set(tids)
        lines = []
        for frame_idx in range(start, end + 1):
            clip_idx = frame_idx - start
            for det in mw._annotations.get(frame_idx, []):
                track_id = det[5] if len(det) > 5 else 0
                if track_id not in tids:
                    continue
                x1, y1, x2, y2 = det[1], det[2], det[3], det[4]
                labels = det[6] if len(det) > 6 else ()
                parts = [f"{clip_idx + 1:.6e}", "1.000000e+00",
                         f"{x1:.6e}", f"{y1:.6e}", f"{x2:.6e}", f"{y2:.6e}",
                         f"{track_id:.6e}"]
                parts.extend(labels if labels else ["unknown"])
                lines.append("\t".join(parts))
        return lines

    def _export(self):
        lo, hi = self._sp_lo.value(), self._sp_hi.value()
        checked = self._checked_tracks()
        all_tids = [tid for tids in checked.values() for tid in tids]
        if not all_tids:
            self._status.setText("Coche au moins une track .ver.")
            return
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Dossier de destination pour les .ver...")
        if not folder:
            return
        if self._rb_merge.isChecked():
            outputs = {"merged": all_tids}
        else:
            outputs = {f"track_{tid}": [tid] for tid in all_tids}
        written = []
        for out_name, tids in outputs.items():
            lines = self._build_ver_lines(lo, hi, tids)
            if not lines:
                continue
            safe_name = "".join(c if (c.isalnum() or c in "._-") else "_"
                                for c in str(out_name))
            path = os.path.join(folder, f"{safe_name}.ver")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
            written.append(path)
        if written:
            self._status.setText(
                f"{len(written)} fichier(s) .ver écrits (frames {lo}→{hi}) → {folder}")
        else:
            self._status.setText(
                "Aucune annotation dans la plage/les tracks choisies.")
