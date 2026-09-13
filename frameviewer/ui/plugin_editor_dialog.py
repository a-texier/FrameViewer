# -*- coding: utf-8 -*-
"""PluginEditorDialog : ecrire/modifier un plugin.py depuis l'app, avec une
liste des "objets" (methodes PluginAPI -- ce qu'on peut LIRE/FAIRE) et des
"actions/competences" (hooks FrameViewerPlugin -- ce qu'on peut DEFINIR)
disponibles depuis la classe de base, inseres par double-clic. Pas un
editeur visuel par noeuds (hors de portee ici) -- un editeur de texte
simple + une reference API navigable, pour rester dans l'app sans avoir a
ouvrir un IDE externe."""
import inspect
import os
import re

from PySide6 import QtWidgets

from frameviewer.plugins.api import FrameViewerPlugin, PluginAPI
from frameviewer.plugins.loader import (PLUGIN_FOLDER_PREFIX, PLUGIN_MODULE_FILE,
                                         plugins_root, plugins_roots)

_NEW_PLUGIN_TEMPLATE = '''# -*- coding: utf-8 -*-
"""Plugin FrameViewer -- decris ici, en une phrase, ce que fait ce plugin
(cette description s'affiche dans Parametres > Plugins).

Un plugin = cette classe (sous-classe de FrameViewerPlugin) exposee via
`PLUGIN = <classe>`. TOUS les hooks ci-dessous sont OPTIONNELS : garde ceux
qui te servent, supprime les autres (ils ont un comportement neutre par
defaut). Tu ne touches jamais l'interface directement : tout passe par
l'objet `api` (PluginAPI) -- survole ses methodes dans l'onglet de droite de
cet editeur (double-clic pour inserer un appel), ou vois docs/plugins.md.
"""
from frameviewer.plugins.api import FrameViewerPlugin


class {cls}(FrameViewerPlugin):
    # --- identite (affichee dans Parametres > Plugins) ---
    name = "{name}"            # identifiant lisible, unique
    description = ""            # une phrase : ce que fait le plugin

    # --- reglages du hook render_patch (vignette dans un coin de la vue) ---
    corner = "tr"              # coin : "tl" | "tr" | "bl" | "br"
    margin = 8                 # marge en pixels depuis le bord

    # ==================================================================
    # CYCLE DE VIE
    # ==================================================================
    def on_load(self, api):
        """Appele une fois au chargement/rechargement. Initialise ici ton
        etat (ex. self._rows = []), ou charge un fichier livre a cote du
        plugin via api.plugin_dir(). Ne bloque pas longtemps : appele au
        demarrage de l'app."""
        pass

    def on_unload(self, api):
        """Appele avant un rechargement. Libere ici ce que tu as ouvert
        (fichiers, ressources). Supprime cette methode si inutile."""
        pass

    # ==================================================================
    # DESSIN PAR FRAME
    # ==================================================================
    def get_overlays(self, api, frame_idx):
        """Formes a dessiner sur LA frame `frame_idx` (meme moteur que les
        calques SIDECAR : affichage live ET export). Renvoie une LISTE de dicts,
        [] s'il n'y a rien a dessiner pour cette frame. Un dict forme :

            {"type": "ligne_brisee"        # segment / polyligne ouverte
                   | "ligne_brisee_fermee" # polygone (ex. une boite)
                   | "points" | "croix"    # marqueurs ponctuels
                   | "ellipse",            # + "a","b" (demi-axes), "rotation"
             "points": [(x, y), ...],      # coordonnees IMAGE, en pixels
             "color": (r, g, b),           # 0..255, comme QColor (PAS du BGR)
             "thickness": 2,
             "text": {"label": "id 3", "x": 6, "y": -6, "size": 12}}  # option

        Contexte dispo : api.image_size(), api.current_frame_index(),
        api.ver_boxes(frame_idx), api.sidecar_graphs(frame_idx)."""
        return []

    def render_patch(self, api, frame_idx, size):
        """Mini-image BGR (numpy ndarray) incrustee dans un coin (voir
        `corner`/`margin`), ou None. `size` = (largeur, hauteur) indicatif.
        Pour une jauge, une boussole, un mini-histogramme... Renvoie None si
        tu n'utilises pas ce hook."""
        return None

    # ==================================================================
    # GLISSER-DEPOSER (fichiers que l'app ne sait pas ouvrir seule, ex. .csv)
    # ==================================================================
    def accepts_drop(self, api, path):
        """True si CE plugin veut traiter le fichier `path` glisse sur la
        fenetre (ex. return path.lower().endswith(".csv")). Le premier plugin
        actif qui accepte recoit on_drop. Renvoie False sinon."""
        return False

    def on_drop(self, api, path):
        """Appele si accepts_drop(api, path) a renvoye True. Charge le fichier
        (ex. self._rows = api.read_csv_dict(path)), stocke le resultat, puis
        api.request_repaint() pour rafraichir l'affichage."""
        pass

    # ==================================================================
    # INTERFACE (panneau lateral + boutons d'action)
    # ==================================================================
    def build_panel(self, api):
        """QWidget de reglages/apercu ajoute comme dock, ou None. Ex :
            from PySide6 import QtWidgets
            w = QtWidgets.QWidget(); lay = QtWidgets.QVBoxLayout(w)
            lay.addWidget(QtWidgets.QLabel("Reglages du plugin"))
            return w
        Renvoie None si pas de panneau."""
        return None

    def menu_actions(self, api):
        """Liste de (libelle, callback) -- des boutons listes dans le dialogue
        Plugins (section Actions). callback() est appele sans argument.
        Renvoie [] si aucune action."""
        return []


PLUGIN = {cls}
'''


def create_code_plugin_folder(slug):
    """Cree plugins/plugins_<slug>/plugin.py a partir du template complet
    (tous les hooks documentes) et renvoie le dossier cree. Le dossier ne
    doit pas exister prealablement (verifie par l'appelant)."""
    folder = os.path.join(plugins_root(), f"{PLUGIN_FOLDER_PREFIX}{slug}")
    os.makedirs(folder, exist_ok=True)
    cls_name = "".join(p.capitalize() for p in slug.split("_")) + "Plugin"
    # replace (pas .format) : le template contient des accolades litterales
    # dans les exemples de docstring ({"type": ...}) qui casseraient .format.
    content = _NEW_PLUGIN_TEMPLATE.replace("{cls}", cls_name).replace("{name}", slug)
    with open(os.path.join(folder, PLUGIN_MODULE_FILE), "w", encoding="utf-8") as f:
        f.write(content)
    return folder


def _hook_snippet(method):
    """Stub de methode a inserer pour un hook FrameViewerPlugin (get_overlays,
    render_patch...) -- signature + docstring + corps minimal valide."""
    sig = inspect.signature(method)
    doc = inspect.getdoc(method) or ""
    lines = [f"    def {method.__name__}{sig}:"]
    if doc:
        first = doc.splitlines()[0]
        lines.append(f'        """{first}"""')
    lines.append("        pass")
    return "\n".join(lines) + "\n"


def _api_call_snippet(method):
    """Extrait d'appel a inserer pour une methode PluginAPI (ex.
    api.read_csv_dict(...)), parametres places en commentaire indicatif."""
    sig = inspect.signature(method)
    params = [p for p in sig.parameters if p != "self"]
    return f"api.{method.__name__}({', '.join(params)})"


class PluginEditorDialog(QtWidgets.QDialog):

    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw
        self.setWindowTitle("Éditeur de plugin")
        self.resize(880, 560)

        root = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()

        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel("<b>Plugins</b> (dossier plugins/)"))
        self._plugin_list = QtWidgets.QListWidget()
        self._plugin_list.setMaximumWidth(200)
        self._plugin_list.currentTextChanged.connect(self._load_selected)
        left.addWidget(self._plugin_list)
        new_btn = QtWidgets.QPushButton("Nouveau plugin...")
        new_btn.clicked.connect(self._new_plugin)
        left.addWidget(new_btn)
        top.addLayout(left)

        mid = QtWidgets.QVBoxLayout()
        self._file_lbl = QtWidgets.QLabel("(aucun plugin sélectionné)")
        self._file_lbl.setStyleSheet("font-family:monospace; font-size:11px; color:#888;")
        mid.addWidget(self._file_lbl)
        self._editor = QtWidgets.QPlainTextEdit()
        self._editor.setStyleSheet("font-family:Consolas,monospace; font-size:12px;")
        self._editor.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self._editor.setTabStopDistance(4 * self._editor.fontMetrics().horizontalAdvance(" "))
        mid.addWidget(self._editor, 1)
        btn_row = QtWidgets.QHBoxLayout()
        save_btn = QtWidgets.QPushButton("Enregistrer")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        reload_btn = QtWidgets.QPushButton("Enregistrer + recharger les plugins")
        reload_btn.clicked.connect(self._save_and_reload)
        btn_row.addWidget(reload_btn)
        btn_row.addStretch(1)
        self._status = QtWidgets.QLabel("")
        btn_row.addWidget(self._status)
        mid.addLayout(btn_row)
        top.addLayout(mid, 1)

        right = QtWidgets.QVBoxLayout()
        ref_lbl = QtWidgets.QLabel(
            "<b>Objets et actions disponibles</b><br>(double-clic pour insérer)")
        ref_lbl.setWordWrap(True)
        right.addWidget(ref_lbl)
        self._ref_tree = QtWidgets.QTreeWidget()
        self._ref_tree.setHeaderHidden(True)
        self._ref_tree.setMaximumWidth(320)
        self._ref_tree.itemDoubleClicked.connect(self._insert_ref)
        self._ref_tree.setToolTip(
            "« Actions/compétences » (colonne du haut) : méthodes que TON "
            "plugin peut définir (get_overlays, render_patch, ...).\n"
            "« Objets » (colonne du bas) : méthodes de l'app que TON code "
            "peut appeler via api.xxx(...) pour lire des données ou agir "
            "sur l'interface.")
        right.addWidget(self._ref_tree)
        top.addLayout(right)

        root.addLayout(top, 1)
        self._populate_reference()
        self._refresh_plugin_list()

    # ---------- reference API (browse + insert) ----------
    def _populate_reference(self):
        hooks_item = QtWidgets.QTreeWidgetItem(["Actions / compétences (à définir)"])
        for name, method in inspect.getmembers(FrameViewerPlugin, predicate=inspect.isfunction):
            if name.startswith("_"):
                continue
            sig = inspect.signature(method)
            doc = (inspect.getdoc(method) or "").splitlines()
            it = QtWidgets.QTreeWidgetItem([f"{name}{sig}"])
            it.setToolTip(0, doc[0] if doc else "")
            it.setData(0, 1, ("hook", method))
            hooks_item.addChild(it)
        self._ref_tree.addTopLevelItem(hooks_item)

        api_item = QtWidgets.QTreeWidgetItem(["Objets (api.xxx -- à appeler)"])
        for name, method in inspect.getmembers(PluginAPI, predicate=inspect.isfunction):
            if name.startswith("_"):
                continue
            sig = inspect.signature(method)
            doc = (inspect.getdoc(method) or "").splitlines()
            it = QtWidgets.QTreeWidgetItem([f"{name}{sig}"])
            it.setToolTip(0, doc[0] if doc else "")
            it.setData(0, 1, ("api", method))
            api_item.addChild(it)
        self._ref_tree.addTopLevelItem(api_item)
        self._ref_tree.expandAll()

    def _insert_ref(self, item, _col=0):
        data = item.data(0, 1)
        if not data:
            return
        kind, method = data
        snippet = _hook_snippet(method) if kind == "hook" else _api_call_snippet(method)
        self._editor.insertPlainText(snippet)
        self._editor.setFocus()

    # ---------- liste des plugins existants ----------
    def _refresh_plugin_list(self):
        self._plugin_list.blockSignals(True)
        self._plugin_list.clear()
        # scanne les dossiers livre + perso (AppData) ; le 1er root gagne en cas
        # de meme nom de dossier. On memorise le dossier reel de chaque entree.
        self._entry_folders = {}
        for root in plugins_roots():
            if not os.path.isdir(root):
                continue
            for entry in sorted(os.listdir(root)):
                folder = os.path.join(root, entry)
                if (os.path.isdir(folder) and entry.startswith(PLUGIN_FOLDER_PREFIX)
                        and os.path.isfile(os.path.join(folder, PLUGIN_MODULE_FILE))
                        and entry not in self._entry_folders):
                    self._entry_folders[entry] = folder
                    self._plugin_list.addItem(entry)
        self._plugin_list.blockSignals(False)
        if self._plugin_list.count():
            self._plugin_list.setCurrentRow(0)

    def _current_folder(self):
        entry = self._plugin_list.currentItem()
        if entry is None:
            return None
        return getattr(self, "_entry_folders", {}).get(entry.text())

    def _current_path(self):
        folder = self._current_folder()
        return os.path.join(folder, PLUGIN_MODULE_FILE) if folder else None

    def _load_selected(self, _text=None):
        path = self._current_path()
        if path is None or not os.path.isfile(path):
            self._editor.setPlainText("")
            self._file_lbl.setText("(aucun plugin sélectionné)")
            return
        with open(path, "r", encoding="utf-8") as f:
            self._editor.setPlainText(f.read())
        # libelle court "plugins_xxx/plugin.py" (le dossier reel, livre ou
        # perso, est dans l'infobulle du chemin absolu).
        folder = self._current_folder() or os.path.dirname(path)
        self._file_lbl.setText(os.path.join(os.path.basename(folder), PLUGIN_MODULE_FILE))
        self._file_lbl.setToolTip(path)
        self._status.setText("")

    def _save(self):
        path = self._current_path()
        if path is None:
            self._status.setText("Aucun plugin sélectionné.")
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(self._editor.toPlainText())
        self._status.setText("Enregistré.")

    def _save_and_reload(self):
        self._save()
        self._mw._plugin_loader.reload_all()
        if self._mw._raw is not None:
            self._mw._display()
        self._status.setText("Enregistré + plugins rechargés.")

    def _new_plugin(self):
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Nouveau plugin", "Nom du plugin (identifiant simple, ex. mon_plugin) :")
        if not ok or not name.strip():
            return
        slug = re.sub(r"[^a-zA-Z0-9_]", "_", name.strip().lower())
        if not slug or not slug[0].isalpha():
            QtWidgets.QMessageBox.warning(
                self, "Nouveau plugin", "Nom invalide (lettres/chiffres/underscore, "
                "doit commencer par une lettre).")
            return
        folder = os.path.join(plugins_root(), f"{PLUGIN_FOLDER_PREFIX}{slug}")
        if os.path.exists(folder):
            QtWidgets.QMessageBox.warning(
                self, "Nouveau plugin", f"{folder} existe déjà.")
            return
        create_code_plugin_folder(slug)
        self._refresh_plugin_list()
        self.select_entry(f"{PLUGIN_FOLDER_PREFIX}{slug}")
        self._status.setText(f"Créé -- {PLUGIN_FOLDER_PREFIX}{slug}/plugin.py")

    def select_entry(self, entry_name):
        """Selectionne le plugin `entry_name` (nom de dossier) dans la liste,
        ce qui charge son plugin.py dans l'editeur."""
        for i in range(self._plugin_list.count()):
            if self._plugin_list.item(i).text() == entry_name:
                self._plugin_list.setCurrentRow(i)
                return True
        return False
