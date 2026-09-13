# -*- coding: utf-8 -*-
"""Compact editor for linked multi-view starting frames."""

from PySide6 import QtCore, QtWidgets


_LAYOUTS = {
    "h2": (1, 2, ("Gauche", "Droite")),
    "v2": (2, 1, ("Haut", "Bas")),
    "h3": (1, 3, ("Gauche", "Centre", "Droite")),
    "q4": (2, 2, ("Haut gauche", "Haut droite", "Bas gauche", "Bas droite")),
    "fusion": (1, 2, ("Source A", "Source B")),
}


class LinkViewsDialog(QtWidgets.QDialog):
    def __init__(self, parent, mode, sources, starts, active):
        super().__init__(parent)
        self.setWindowTitle("Lier les vues")
        self.setModal(True)
        self.setMinimumWidth(430)
        self._spins = []

        layout = QtWidgets.QVBoxLayout(self)
        self.enabled = QtWidgets.QCheckBox("Liaison active")
        self.enabled.setObjectName("link_views_enabled")
        self.enabled.setChecked(bool(active))
        layout.addWidget(self.enabled)

        if mode == "temporal":
            info = QtWidgets.QLabel(
                "Le mode Temporel utilise deja N pour definir l'ecart entre i-N et i.\n"
                "La liaison synchronise ici uniquement le zoom et le deplacement.")
            info.setWordWrap(True)
            layout.addWidget(info)
        else:
            info = QtWidgets.QLabel(
                "Chaque valeur est la frame de depart du slot. Ensuite, la navigation\n"
                "conserve exactement ces ecarts entre les vues.")
            info.setWordWrap(True)
            layout.addWidget(info)
            _rows, cols, labels = _LAYOUTS.get(mode, (1, len(sources), tuple()))
            grid = QtWidgets.QGridLayout()
            grid.setSpacing(8)
            for index, source in enumerate(sources):
                cell = QtWidgets.QFrame()
                cell.setObjectName("linkFrameCell")
                cell.setStyleSheet(
                    "QFrame#linkFrameCell{border:1px solid #555;background:#202027;"
                    "border-radius:4px;padding:5px;}")
                form = QtWidgets.QVBoxLayout(cell)
                form.setContentsMargins(7, 6, 7, 6)
                label = labels[index] if index < len(labels) else f"Vue {index + 1}"
                title = QtWidgets.QLabel(f"[{index + 1}] {label}")
                title.setStyleSheet("font-weight:bold;color:#ff9a3d;")
                name = source.get("name") or "Aucune source"
                source_label = QtWidgets.QLabel(name)
                source_label.setWordWrap(True)
                source_label.setStyleSheet("color:#aaa;font-size:10px;")
                spin = QtWidgets.QSpinBox()
                spin.setObjectName(f"link_start_frame_{index}")
                spin.setRange(0, 1_000_000)
                spin.setKeyboardTracking(False)
                spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.UpDownArrows)
                spin.setFocusPolicy(QtCore.Qt.StrongFocus)
                spin.setValue(int(starts[index]) if index < len(starts) else 0)
                count = source.get("count")
                if count:
                    spin.setMaximum(max(0, int(count) - 1))
                    spin.setToolTip(f"Frames valides : 0 a {int(count) - 1}")
                form.addWidget(title)
                form.addWidget(source_label)
                form.addWidget(spin)
                grid.addWidget(cell, index // cols, index % cols)
                self._spins.append(spin)
            layout.addLayout(grid)

        self.enabled.toggled.connect(self._set_fields_enabled)
        reset = QtWidgets.QPushButton("Reinitialiser")
        reset.setObjectName("link_reset")
        reset.clicked.connect(self._reset)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Apply | QtWidgets.QDialogButtonBox.Cancel)
        apply_button = buttons.button(QtWidgets.QDialogButtonBox.Apply)
        apply_button.setText("Appliquer")
        apply_button.setObjectName("link_apply")
        # ApplyRole n'emet pas QDialogButtonBox.accepted. Le branchement
        # historique sur `accepted` laissait donc le dialogue ouvert et
        # n'appliquait jamais les valeurs.
        apply_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(reset)
        row.addStretch(1)
        row.addWidget(buttons)
        layout.addLayout(row)
        self._set_fields_enabled(self.enabled.isChecked())

    def _reset(self):
        for spin in self._spins:
            spin.setValue(0)

    def _set_fields_enabled(self, enabled):
        for spin in self._spins:
            spin.setEnabled(bool(enabled))

    def values(self):
        return self.enabled.isChecked(), [spin.value() for spin in self._spins]
