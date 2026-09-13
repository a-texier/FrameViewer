# -*- coding: utf-8 -*-
"""CsvColumnMapperDialog : associe des roles semantiques (ex. "frame_id",
"x1"...) aux colonnes REELLES d'un CSV dont les noms varient d'un fichier a
l'autre. Outil generique, reutilisable par n'importe quel plugin via
PluginAPI.map_csv_columns() -- voir docs/plugins.md."""
from PySide6 import QtWidgets


class CsvColumnMapperDialog(QtWidgets.QDialog):

    def __init__(self, parent, header, roles, sample_rows=None,
                optional_roles=None, title="Associer les colonnes du CSV"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        self._header = list(header)
        optional_roles = list(optional_roles or [])
        self._optional = set(optional_roles)
        # `roles` (obligatoires) + `optional_roles` (peuvent rester "(aucune)")
        # doivent TOUS apparaitre dans le formulaire, dans un ordre stable --
        # ne garder que les obligatoires ici oubliait les optionnels du
        # champ de saisie ; iterer un set aurait rendu l'ordre aleatoire.
        self._roles = list(roles) + [r for r in optional_roles if r not in roles]
        self._combos = {}

        vbox = QtWidgets.QVBoxLayout(self)
        info = QtWidgets.QLabel(
            "Indique quelle colonne du fichier correspond à chaque "
            "information attendue (les noms de colonnes peuvent varier "
            "d'un fichier à l'autre -- ce choix sera mémorisé pour ce "
            "fichier).")
        info.setWordWrap(True)
        vbox.addWidget(info)

        if sample_rows:
            vbox.addWidget(QtWidgets.QLabel("<b>Aperçu du fichier</b> :"))
            table = QtWidgets.QTableWidget(len(sample_rows), len(self._header))
            table.setHorizontalHeaderLabels(self._header)
            table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            table.setMaximumHeight(120)
            for r, row in enumerate(sample_rows):
                for c, val in enumerate(row):
                    if c < len(self._header):
                        table.setItem(r, c, QtWidgets.QTableWidgetItem(str(val)))
            table.resizeColumnsToContents()
            vbox.addWidget(table)

        form = QtWidgets.QFormLayout()
        options = ["(aucune)"] + self._header
        for role in self._roles:
            combo = QtWidgets.QComboBox()
            combo.addItems(options)
            guess = self._guess_column(role)
            if guess is not None:
                combo.setCurrentIndex(options.index(guess))
            elif role not in self._optional and self._header:
                combo.setCurrentIndex(1)
            label = role + ("" if role in self._optional else " *")
            form.addRow(label, combo)
            self._combos[role] = combo
        vbox.addLayout(form)

        note = QtWidgets.QLabel("* obligatoire")
        note.setStyleSheet("color:#888; font-size:11px;")
        vbox.addWidget(note)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        vbox.addWidget(btns)

    def _guess_column(self, role):
        """Pre-selectionne une colonne dont le nom ressemble au role
        (insensible a la casse/underscores) -- simple confort, l'utilisateur
        garde la main pour corriger."""
        norm_role = role.lower().replace("_", "").replace(" ", "")
        for col in self._header:
            norm_col = col.lower().replace("_", "").replace(" ", "")
            if norm_col == norm_role:
                return col
        return None

    def _on_accept(self):
        missing = [r for r in self._roles
                  if r not in self._optional
                  and self._combos[r].currentIndex() == 0]
        if missing:
            QtWidgets.QMessageBox.warning(
                self, "Colonnes manquantes",
                "Associe une colonne pour : " + ", ".join(missing))
            return
        self.accept()

    def result_mapping(self):
        out = {}
        for role, combo in self._combos.items():
            out[role] = None if combo.currentIndex() == 0 else combo.currentText()
        return out
