# -*- coding: utf-8 -*-
"""Dialogue d'import YUV/gris brut : le format brut n'a pas d'en-tete, donc on
demande largeur, hauteur et format. Validation en direct contre la taille du
premier fichier (octets par frame vs taille reelle) pour aider a trouver la
bonne geometrie."""
from PySide6 import QtWidgets

from frameviewer.core.sources import YUV_FORMATS, yuv_frame_bytes


class YuvImportDialog(QtWidgets.QDialog):
    def __init__(self, parent, file_size, width=0, height=0, fmt="gray8"):
        super().__init__(parent)
        self.setWindowTitle("Import YUV / gris brut")
        self._file_size = int(file_size)

        form = QtWidgets.QFormLayout()
        self._w = QtWidgets.QSpinBox()
        self._w.setRange(1, 100000)
        self._w.setValue(int(width) if width else 1280)
        self._h = QtWidgets.QSpinBox()
        self._h.setRange(1, 100000)
        self._h.setValue(int(height) if height else 720)
        self._fmt = QtWidgets.QComboBox()
        for fid, (label, _bpp) in YUV_FORMATS.items():
            self._fmt.addItem(label, fid)
        i = self._fmt.findData(fmt)
        if i >= 0:
            self._fmt.setCurrentIndex(i)
        form.addRow("Largeur", self._w)
        form.addRow("Hauteur", self._h)
        form.addRow("Format", self._fmt)

        self._info = QtWidgets.QLabel("")
        self._info.setWordWrap(True)

        vbox = QtWidgets.QVBoxLayout(self)
        vbox.addLayout(form)
        vbox.addWidget(self._info)
        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        self._ok = btns.button(QtWidgets.QDialogButtonBox.Ok)
        vbox.addWidget(btns)

        for w in (self._w, self._h):
            w.valueChanged.connect(self._revalidate)
        self._fmt.currentIndexChanged.connect(self._revalidate)
        self._revalidate()

    def _revalidate(self):
        """Compare octets/frame a la taille du fichier ; guide vers reste=0."""
        w, h = self._w.value(), self._h.value()
        fmt = self._fmt.currentData()
        fb = yuv_frame_bytes(w, h, fmt)
        if fb <= 0:
            self._info.setText("Format invalide.")
            self._ok.setEnabled(False)
            return
        n, rest = divmod(self._file_size, fb)
        ok = (n >= 1)
        if rest == 0 and n == 1:
            txt = (f"OK : {fb} octets/frame = la taille du fichier "
                   f"({self._file_size}). 1 frame par fichier.")
            color = "#3ad06a"
        elif rest == 0 and n > 1:
            txt = (f"Attention : {fb} octets/frame -> le fichier contient {n} "
                   f"frames. En mode 1 frame/fichier, seule la 1re est lue.")
            color = "#e0a030"
        else:
            txt = (f"Ne correspond pas : {fb} octets/frame, fichier = "
                   f"{self._file_size} octets (reste {rest}). Ajustez "
                   f"largeur/hauteur/format jusqu'a reste = 0.")
            color = "#e05555"
        self._info.setStyleSheet(f"color:{color}; font-size:11px;")
        self._info.setText(txt)
        self._ok.setEnabled(ok)

    def result_params(self):
        """-> (largeur:int, hauteur:int, format:str)."""
        return self._w.value(), self._h.value(), self._fmt.currentData()
