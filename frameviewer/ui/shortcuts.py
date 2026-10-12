# -*- coding: utf-8 -*-
"""Raccourcis clavier personnalisables.

Chaque action a une ou plusieurs touches (texte QKeySequence portable). Les
choix de l'utilisateur vivent dans settings.ini, section [shortcuts] ; une
action absente garde ses touches par defaut.

Deux familles :
- "nav" : lues dans MainWindow.keyPressEvent (fleches, espace...). Ce ne sont
  pas des QShortcut, qui seraient captes par les champs de saisie.
- "global" : QShortcut de fenetre (Ctrl+O, F11...).
"""

from PySide6 import QtGui, QtWidgets
from PySide6.QtCore import Qt

SETTINGS_GROUP = "shortcuts"

# (id, libelle, touches par defaut, famille)
SHORTCUT_ACTIONS = (
    ("step_back", "Frame précédente", ("Left",), "nav"),
    ("step_forward", "Frame suivante", ("Right",), "nav"),
    ("play_pause", "Lecture / pause", ("Space",), "nav"),
    ("first_frame", "Première frame", ("Home",), "nav"),
    ("last_frame", "Dernière frame", ("End",), "nav"),
    ("zoom_in", "Zoom avant", ("+", "="), "nav"),
    ("zoom_out", "Zoom arrière", ("-",), "nav"),
    ("open_file", "Ouvrir un fichier", ("Ctrl+O",), "global"),
    ("open_folder", "Ouvrir un dossier", ("Ctrl+Shift+O",), "global"),
    ("save_clicks", "Enregistrer les clics sous...", ("Ctrl+S",), "global"),
    ("undo_click", "Annuler le dernier clic", ("Ctrl+Z",), "global"),
    ("rotate", "Rotation 90° horaire", ("Ctrl+R",), "global"),
    ("capture", "Capturer la frame courante", ("Ctrl+E",), "global"),
    ("copy_frame", "Copier la frame dans le presse-papiers", ("Ctrl+C",), "global"),
    ("view_only", "Vue plein cadre", ("F11",), "global"),
    ("panel_hist", "Panneau Hist / Calque", ("Ctrl+H",), "global"),
    ("panel_plugins", "Panneau Plugins", ("Ctrl+P",), "global"),
    ("panel_params", "Panneau Paramètres", ("Ctrl+Shift+P",), "global"),
    ("toggle_tools", "Outils TI", ("Ctrl+T",), "global"),
    ("toggle_history", "Historique des sources", ("Ctrl+Shift+H",), "global"),
    ("help", "Aide", ("F1",), "global"),
)

_DEFAULTS = {aid: list(keys) for aid, _label, keys, _kind in SHORTCUT_ACTIONS}
NAV_ACTIONS = tuple(aid for aid, _l, _k, kind in SHORTCUT_ACTIONS if kind == "nav")
GLOBAL_ACTIONS = tuple(aid for aid, _l, _k, kind in SHORTCUT_ACTIONS if kind == "global")


def default_shortcuts():
    return {aid: list(keys) for aid, keys in _DEFAULTS.items()}


def load_shortcuts(settings):
    """Touches effectives : defauts surcharges par settings.ini."""
    mapping = default_shortcuts()
    settings.beginGroup(SETTINGS_GROUP)
    try:
        for aid in mapping:
            raw = settings.value(aid, None)
            if raw is None:
                continue
            keys = [raw] if isinstance(raw, str) else list(raw)
            mapping[aid] = [k for k in (str(x).strip() for x in keys) if k]
    finally:
        settings.endGroup()
    return mapping


def save_shortcuts(settings, mapping):
    """N'ecrit que les actions modifiees : un defaut futur reste applique aux autres."""
    settings.beginGroup(SETTINGS_GROUP)
    try:
        settings.remove("")
        for aid, keys in mapping.items():
            if aid in _DEFAULTS and list(keys) != _DEFAULTS[aid]:
                settings.setValue(aid, list(keys))
    finally:
        settings.endGroup()


def _combination(text):
    seq = QtGui.QKeySequence(text, QtGui.QKeySequence.PortableText)
    return seq[0] if seq.count() else None


_IGNORED_MODIFIERS = Qt.KeypadModifier


def event_matches(event, keys):
    """True si l'evenement clavier correspond a l'une des touches.

    Une touche sans modificateur ignore les modificateurs de l'evenement
    (comportement historique : Maj+fleche avancait aussi d'une frame, et
    "+" s'obtient avec Maj sur certains claviers).
    """
    mods = event.modifiers() & ~_IGNORED_MODIFIERS
    for text in keys:
        combo = _combination(text)
        if combo is None or combo.key() != event.key():
            continue
        want = combo.keyboardModifiers() & ~_IGNORED_MODIFIERS
        if want == Qt.NoModifier or want == mods:
            return True
    return False


def keys_label(keys):
    """Affichage lisible (langue du systeme) d'une liste de touches."""
    out = []
    for text in keys:
        seq = QtGui.QKeySequence(text, QtGui.QKeySequence.PortableText)
        out.append(seq.toString(QtGui.QKeySequence.NativeText) or text)
    return " / ".join(out)


def conflicts(mapping):
    """{touche: [actions]} pour chaque touche attribuee a plusieurs actions."""
    seen = {}
    for aid, keys in mapping.items():
        for text in keys:
            norm = QtGui.QKeySequence(text, QtGui.QKeySequence.PortableText).toString(
                QtGui.QKeySequence.PortableText
            )
            seen.setdefault(norm, []).append(aid)
    return {k: v for k, v in seen.items() if len(v) > 1}


class ShortcutsDialog(QtWidgets.QDialog):
    """Edition des raccourcis : une touche principale par action."""

    def __init__(self, mapping, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Raccourcis clavier")
        self.setMinimumWidth(460)
        self._mapping = {aid: list(keys) for aid, keys in mapping.items()}
        self._editors = {}

        lay = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel(
            "Cliquez dans un champ puis tapez la nouvelle touche. "
            "Effacer remet la touche par défaut de l'action."
        )
        hint.setWordWrap(True)
        lay.addWidget(hint)

        grid = QtWidgets.QGridLayout()
        for row, (aid, label, _defaults, _kind) in enumerate(SHORTCUT_ACTIONS):
            grid.addWidget(QtWidgets.QLabel(label), row, 0)
            edit = QtWidgets.QKeySequenceEdit()
            edit.setObjectName(f"shortcut_{aid}")
            keys = self._mapping.get(aid) or _DEFAULTS[aid]
            edit.setKeySequence(QtGui.QKeySequence(keys[0], QtGui.QKeySequence.PortableText))
            edit.setMaximumSequenceLength(1)
            edit.keySequenceChanged.connect(lambda _s, a=aid: self._on_edited(a))
            grid.addWidget(edit, row, 1)
            reset = QtWidgets.QPushButton("Défaut")
            reset.clicked.connect(lambda _c=False, a=aid: self._reset_one(a))
            grid.addWidget(reset, row, 2)
            self._editors[aid] = edit
        lay.addLayout(grid)

        self._warn = QtWidgets.QLabel("")
        self._warn.setWordWrap(True)
        self._warn.setStyleSheet("color:#e05555;")
        lay.addWidget(self._warn)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok
            | QtWidgets.QDialogButtonBox.Cancel
            | QtWidgets.QDialogButtonBox.RestoreDefaults
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        btns.button(QtWidgets.QDialogButtonBox.RestoreDefaults).clicked.connect(self._reset_all)
        self._ok = btns.button(QtWidgets.QDialogButtonBox.Ok)
        lay.addWidget(btns)
        self._check_conflicts()

    def _on_edited(self, aid):
        text = self._editors[aid].keySequence().toString(QtGui.QKeySequence.PortableText)
        if text:
            # une touche choisie remplace toutes les variantes par defaut
            self._mapping[aid] = [text]
        else:
            self._mapping[aid] = list(_DEFAULTS[aid])
        self._check_conflicts()

    def _reset_one(self, aid):
        self._mapping[aid] = list(_DEFAULTS[aid])
        edit = self._editors[aid]
        edit.blockSignals(True)
        edit.setKeySequence(QtGui.QKeySequence(_DEFAULTS[aid][0], QtGui.QKeySequence.PortableText))
        edit.blockSignals(False)
        self._check_conflicts()

    def _reset_all(self):
        for aid in self._editors:
            self._reset_one(aid)

    def _check_conflicts(self):
        clash = conflicts(self._mapping)
        labels = {aid: label for aid, label, _d, _k in SHORTCUT_ACTIONS}
        if clash:
            lines = [
                f"{key} : " + ", ".join(labels[a] for a in aids) for key, aids in clash.items()
            ]
            self._warn.setText("Touche utilisée plusieurs fois :\n" + "\n".join(lines))
        else:
            self._warn.setText("")
        self._ok.setEnabled(not clash)

    def mapping(self):
        return {aid: list(keys) for aid, keys in self._mapping.items()}


def install_global_shortcuts(window, mapping, handlers):
    """Cree les QShortcut des actions "global" ; renvoie la liste a garder.

    ``handlers`` : {action_id: callable}. Les anciens QShortcut doivent etre
    supprimes par l'appelant (deleteLater) avant un nouvel appel.
    """
    created = []
    for aid in GLOBAL_ACTIONS:
        fn = handlers.get(aid)
        if fn is None:
            continue
        for text in mapping.get(aid) or _DEFAULTS[aid]:
            seq = QtGui.QKeySequence(text, QtGui.QKeySequence.PortableText)
            if seq.isEmpty():
                continue
            sc = QtGui.QShortcut(seq, window)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(fn)
            created.append(sc)
    return created


__all__ = [
    "SHORTCUT_ACTIONS",
    "NAV_ACTIONS",
    "GLOBAL_ACTIONS",
    "ShortcutsDialog",
    "conflicts",
    "default_shortcuts",
    "event_matches",
    "install_global_shortcuts",
    "keys_label",
    "load_shortcuts",
    "save_shortcuts",
]
