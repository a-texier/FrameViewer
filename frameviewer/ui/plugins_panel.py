# -*- coding: utf-8 -*-
"""Onglet Plugins (modele v2) : segments Plugin Code / Plugin Graphe (memes
onglets que les boutons standards), cartes de plugin depliables, entrees
nommees (variables Python) avec drag-and-drop, jeux d'entrees (contrats) qui
s'empilent -- un nouveau jeu vide apparait automatiquement des que le
precedent est rempli, chaque jeu rempli a un oeil (afficher/masquer) et peut
etre supprime, chaque fichier peut etre retire individuellement.

L'etat vit sur MainWindow (`_plugin_state`, persiste en QSettings) ; ce module
est la vue + les intentions, il appelle des methodes de MainWindow pour muter
l'etat, rafraichir l'affichage et ouvrir les editeurs."""
import os

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from frameviewer.ui.i18n import set_ui_text, ui_text


# ---------------------------------------------------------------------------
# Gabarit d'un nouveau plugin code : autonome et commente. Les entrees
# declarees deviennent des attributs self.<nom> (chemin depose, ou None),
# positionnes par le viewer avant chaque rendu -- le plugin ouvre le fichier
# lui-meme avec le module standard `csv`. Rien n'est cache dans la classe mere.
# ---------------------------------------------------------------------------
_CODE_TEMPLATE = '''# -*- coding: utf-8 -*-
"""{name} -- plugin overlay code (modele v2, autonome).

Ce plugin dessine un overlay transparent (BGRA) par-dessus l'image affichee.
Il recoit le numero de frame courant et lit SES fichiers d'entree, dont le
CHEMIN est fourni automatiquement par le viewer (glisser-deposer dans l'onglet
Plugins). Chaque entree declaree devient un attribut du meme nom :
{inputs_doc}

Le viewer positionne ces attributs AVANT chaque appel de render_overlay (un
attribut vaut None tant qu'aucun fichier n'est depose). On ne recoit que le
chemin : c'est le plugin qui ouvre et interprete le fichier (ici avec le
module standard `csv`, mais pandas/openpyxl/... conviennent aussi).

=== HOOKS de FrameViewerPlugin (surcharge uniquement ce dont tu as besoin) ===
  on_load(self, api) / on_unload(self, api)          : cycle de vie
  overlay_elements(self, api) -> [(cle, libelle)]    : cases On/Off
  render_overlay(self, api, frame_idx, size) -> BGRA (H,W,4) | None
  render_panel(self, api, frame_idx, size)   -> ndarray | None  (image dans la carte)
  on_view_hover(self, api, frame_idx, x, y)          : souris, coords IMAGE
  on_view_click(self, api, frame_idx, x, y)          : clic, coords IMAGE
  on_view_key(self, api, frame_idx, key, text) -> bool  : touche (si libre ; True=traitee)
  get_overlays(self, api, frame_idx) -> [dict au schema SIDECAR]    (ancien modele)
  render_patch(self, api, frame_idx, size) -> BGR | None        (vignette coin ; self.corner/self.margin)
  build_panel(self, api) -> QWidget | None                      (dock, via api.add_dock)
  menu_actions(self, api) -> [(libelle, callback)]
  accepts_drop(self, api, path) -> bool  /  on_drop(self, api, path)

=== api (PluginAPI) : outils fournis a chaque hook ===========================
  Frontend : api.element_enabled(cle, default=True), api.request_repaint(),
             api.status(msg), api.log(msg)
  Frame    : api.frame_count(), api.current_frame_index(), api.image_size(),
             api.raw_frame(idx=None), api.displayed_frame(idx=None), api.source_name()
  Annot    : api.ver_boxes(frame_idx=None), api.sidecar_graphs(frame_idx=None)
  Fichier  : api.plugin_dir(), api.read_csv_dict(path, **kw),
             api.map_csv_columns(csv_path, roles, optional_roles=, remember_key=, title=)
  UI       : api.add_dock(widget, title, area), api.add_menu_action(label, cb)
  Entrees  : self.<nom> (recommande) ; api.input(nom)/api.inputs()/api.attached_csvs() (back-compat)
  Libs ext : api.external_import(module, site_packages) / api.run_external(py_exe, code)
             -> tirer pandas & co d'un env conda/venv (l'exe reste minimal)

Interactif : render_overlay DESSINE depuis un etat ; on_view_hover/click METTENT
A JOUR l'etat puis api.request_repaint() force le redessin (sans lui, rien ne
bouge tant que la frame ne change pas). Reference complete : plugins/PLUGIN_API.md.
"""
import os
import csv

import cv2
import numpy as np

from frameviewer.plugins.api import FrameViewerPlugin


# === Libs externes (pandas & co, PAS dans l'exe minimal) : procedure ========
#   Besoin d'une lib externe ?
#     +-- essaie : api.external_import("lib", chemin_site_packages)
#     |     +-- ca marche ? -> FINI (cas normal, ex. pandas chez toi)
#     |     +-- crash "numpy ... failed to import" (la lib veut un autre numpy
#     |         que celui du bundle) ? -> passe a la methode B ci-dessous
#   Choisis TON env (rien n'est impose par l'exe). En mode source, pandas est
#   deja importable -> chemin vide OK.
#   Methode A (meme process ; partage le numpy de l'exe -> versions compatibles) :
#     pd = api.external_import("pandas", r"C:/.../mon_env/Lib/site-packages")
#     rows = pd.read_csv(self.mon_csv, sep=";").to_dict("records")
#   Methode B (sous-process isole ; numpy propre, n'importe quelle version) :
#     out = api.run_external(r"C:/.../mon_env/python.exe",
#         "import json,pandas as pd;"
#         "print(json.dumps(pd.read_csv('f.csv',sep=';').to_dict('records')))")
#     import json; rows = json.loads(out)
# ============================================================================


class {cls}(FrameViewerPlugin):
    name = "{slug}"
    description = "{name}"

    def on_load(self, api):
        # Entrees : renseignees par le viewer avant chaque rendu avec le chemin
        # du fichier depose (ou None tant qu'aucun fichier n'est fourni).
{inputs_init}
        self._cache = None   # (signature_fichier -> lignes) pour ne pas relire a chaque frame
        self._hover = None   # etat interactif (voir le stub on_view_hover plus bas)
        # --- lib externe (optionnel) : l'exe reste minimal ; tu tires une lib
        # d'un env Python EXTERIEUR que TU choisis (rien n'est impose par l'exe).
        # A) meme processus (append sys.path : le numpy de l'exe reste
        #    prioritaire -> pas de 2e numpy, ok si la lib accepte ce numpy) :
        # try:
        #     self._pd = api.external_import("pandas", r"C:/.../mon_env/Lib/site-packages")
        # except Exception as e:
        #     self._pd = None; api.log(f"lib externe indispo: {e}")
        # B) isolation TOTALE (la lib tourne avec SON numpy, aucun contact avec
        #    l'exe -> n'importe quelle version) -> sous-process, echange stdout :
        #     txt = api.run_external(r"C:/.../mon_env/python.exe",
        #                            "import json,pandas as pd; ...; print(json.dumps(res))")

    def overlay_elements(self, api):
        # (cle, libelle) : une case On/Off par element dessine, affichee sous
        # la carte du plugin. render_overlay lit leur etat via api.element_enabled.
        return [("main", "Affichage principal")]

    def render_overlay(self, api, frame_idx, size):
        # size = (largeur, hauteur) de l'image affichee ; frame_idx est 0-based.
        rows = self._load_rows()
        if not rows:
            return None
        w, h = int(size[0]), int(size[1])
        canvas = np.zeros((h, w, 4), np.uint8)   # BGRA transparent, plein cadre
        drew = False
        if api.element_enabled("main"):
            # TODO : filtrer `rows` sur frame_idx puis dessiner en cv2, ex. :
            #   cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 255, 0, 255), 1)
            #   drew = True
            pass
        return canvas if drew else None

    def _load_rows(self):
        """Ouvre le fichier d'entree avec le module csv standard et met le
        resultat en cache tant que le chemin et la date de modification ne
        changent pas. A adapter au format reel (delimiteur, colonnes)."""
        path = {first_input}
        if not path or not os.path.isfile(path):
            return []
        sig = (path, os.path.getmtime(path))
        if self._cache is not None and self._cache[0] == sig:
            return self._cache[1]
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            # CSV separe par des ';' -> csv.DictReader(f, delimiter=";")
            rows = list(csv.DictReader(f))
        self._cache = (sig, rows)
        return rows

    # === Hooks optionnels : decommente pour surcharger =======================
    # def on_view_hover(self, api, frame_idx, x, y):
    #     # x, y en coords IMAGE. Memorise un etat puis redessine SI il change.
    #     new = None   # ex. index de l'element sous le curseur
    #     if new != self._hover:
    #         self._hover = new
    #         api.request_repaint()   # -> render_overlay est rappele et relit self._hover
    #
    # def on_view_click(self, api, frame_idx, x, y):
    #     # clic sur l'image (coords IMAGE) : poser une selection persistante.
    #     pass
    #
    # def on_view_key(self, api, frame_idx, key, text):
    #     # binding sur touche (si l'appli ne l'utilise pas). text = caractere.
    #     # Renvoyer True quand la touche est traitee.
    #     return False
    #
    # def render_panel(self, api, frame_idx, size):
    #     # Image (BGR/BGRA) affichee DANS la carte du plugin. None = aucune zone.
    #     return None
    #
    # def on_unload(self, api):
    #     # Appele avant un rechargement : liberer d'eventuelles ressources.
    #     pass


PLUGIN = {cls}
'''


def _input_names(inputs):
    """Normalise une liste d'entrees (chaines ou dicts) en noms de variables."""
    out = []
    for it in inputs or []:
        name = (it if isinstance(it, str) else str(it.get("name", ""))).strip()
        if name and name not in out:
            out.append(name)
    return out


def write_code_plugin_template(folder, slug, name, inputs):
    """Genere folder/plugin.py a partir des entrees declarees (assistant)."""
    cls = "".join(p.capitalize() for p in slug.split("_")) + "Plugin"
    names = _input_names(inputs)
    doc = "\n".join(f"  - self.{n}" for n in names) or "  (aucune entree fichier declaree)"
    init = "\n".join(f'        self.{n} = None' for n in names) or "        pass"
    first = f"self.{names[0]}" if names else "None"
    content = (_CODE_TEMPLATE
               .replace("{cls}", cls).replace("{slug}", slug).replace("{name}", name)
               .replace("{inputs_doc}", doc).replace("{inputs_init}", init)
               .replace("{first_input}", first))
    with open(os.path.join(folder, "plugin.py"), "w", encoding="utf-8") as f:
        f.write(content)


def _is_identifier(text):
    return text.isidentifier()


class CodePluginWizard(QtWidgets.QDialog):
    """Assistant de creation d'un plugin code : nom lisible, entree frame, et
    N entrees fichiers designees par un NOM DE VARIABLE (identifiant Python).
    Chaque entree deviendra self.<nom> dans le code genere."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nouveau plugin code")
        self.resize(460, 420)
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self._name = QtWidgets.QLineEdit()
        self._name.setPlaceholderText("ex. BBox Viewer")
        form.addRow("Nom du plugin :", self._name)
        self._frame = QtWidgets.QLineEdit("Current Frame")
        form.addRow("Entrée frame courante :", self._frame)
        lay.addLayout(form)
        lay.addWidget(QtWidgets.QLabel(
            "Entrées fichiers -- une variable par fichier à glisser-déposer.\n"
            "Le nom doit être un identifiant Python (ex. csv_plots) : il "
            "devient self.<nom> dans le code."))
        self._list = QtWidgets.QListWidget()
        lay.addWidget(self._list, 1)
        row = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("+ Ajouter une entrée")
        add.clicked.connect(self._add)
        row.addWidget(add)
        rm = QtWidgets.QPushButton("Retirer")
        rm.clicked.connect(self._remove)
        row.addWidget(rm)
        row.addStretch(1)
        lay.addLayout(row)
        self._list.addItem("csv_data")
        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _add(self):
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Nouvelle entrée", "Nom de variable (identifiant Python) :")
        name = name.strip()
        if not ok or not name:
            return
        if not _is_identifier(name):
            QtWidgets.QMessageBox.warning(
                self, "Nouvelle entrée",
                f"« {name} » n'est pas un identifiant Python valide.")
            return
        self._list.addItem(name)

    def _remove(self):
        r = self._list.currentRow()
        if r >= 0:
            self._list.takeItem(r)

    def result_values(self):
        names = []
        for i in range(self._list.count()):
            n = self._list.item(i).text().strip()
            if n and _is_identifier(n) and n not in names:
                names.append(n)
        return self._name.text(), self._frame.text(), [{"name": n} for n in names]


def _eye_icon(open_eye):
    """Icone d'oeil dessinee (ouvert/barre) -- pas d'emoji."""
    pm = QtGui.QPixmap(18, 18)
    pm.fill(Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    col = QtGui.QColor("#cdd6e0") if open_eye else QtGui.QColor("#7c8794")
    pen = QtGui.QPen(col)
    pen.setWidthF(1.4)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    path = QtGui.QPainterPath()
    path.moveTo(2, 9)
    path.quadTo(9, 2.5, 16, 9)
    path.quadTo(9, 15.5, 2, 9)
    p.drawPath(path)
    if open_eye:
        p.setBrush(col)
        p.drawEllipse(QtCore.QPointF(9, 9), 2.3, 2.3)
    else:
        p.drawLine(3, 15, 15, 3)   # barre = masque
    p.end()
    return QtGui.QIcon(pm)


def _icon_from(draw_fn):
    """Fabrique une QIcon 18x18 dessinee (pas d'emoji) via `draw_fn(painter)`."""
    pm = QtGui.QPixmap(18, 18)
    pm.fill(Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    pen = QtGui.QPen(QtGui.QColor("#cdd6e0"))
    pen.setWidthF(1.4)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    draw_fn(p)
    p.end()
    return QtGui.QIcon(pm)


def _draft_icon():
    def d(p):
        # feuille avec coin plie
        poly = QtGui.QPolygonF([QtCore.QPointF(*pt) for pt in
                                [(4, 2), (11, 2), (14, 5), (14, 16), (4, 16)]])
        p.drawPolygon(poly)
        p.drawLine(11, 2, 11, 5)      # pli
        p.drawLine(11, 5, 14, 5)
        p.drawLine(6, 9, 12, 9)       # lignes de texte
        p.drawLine(6, 11, 12, 11)
        p.drawLine(6, 13, 10, 13)
    return _icon_from(d)


def _folder_icon():
    def d(p):
        poly = QtGui.QPolygonF([QtCore.QPointF(*pt) for pt in
                                [(2, 14), (2, 6), (7, 6), (9, 8), (16, 8), (16, 14)]])
        p.drawPolygon(poly)
    return _icon_from(d)


def _trash_icon():
    def d(p):
        p.drawLine(3, 5, 15, 5)       # couvercle
        p.drawLine(7, 5, 7, 3)        # anse
        p.drawLine(7, 3, 11, 3)
        p.drawLine(11, 3, 11, 5)
        p.drawLine(5, 5, 6, 15)       # corps (trapeze)
        p.drawLine(13, 5, 12, 15)
        p.drawLine(6, 15, 12, 15)
        p.drawLine(9, 7, 9, 13)       # stries
    return _icon_from(d)


def _bgr_to_qpix(img, max_w=320, max_h=120):
    """ndarray BGR/BGRA -> QPixmap borne (pour l'apercu render_panel)."""
    if img is None or getattr(img, "size", 0) == 0:
        return None
    if img.ndim == 3 and img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    elif img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    h, w = img.shape[:2]
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    qimg = QtGui.QImage(rgb.data, w, h, 3 * w, QtGui.QImage.Format_RGB888).copy()
    pix = QtGui.QPixmap.fromImage(qimg)
    if w > max_w or h > max_h:
        pix = pix.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return pix


class InputDropZone(QtWidgets.QFrame):
    """Zone d'une entree : nom + zone drag-and-drop. Vide = grisee ; fichier
    associe = bordure verte + chemin. Parcourir/effacer. Le fichier appartient
    au JEU d'entrees (contrat) courant du plugin."""
    fileChanged = QtCore.Signal(str, str)   # (nom_entree, chemin | "")
    deleteRequested = QtCore.Signal(str)    # (nom_entree) : supprimer l'entree declaree

    def __init__(self, name, path, parent=None):
        super().__init__(parent)
        self._name = name
        self.setObjectName(f"plugin_input_{name}")
        self.setAcceptDrops(True)
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 3)
        lay.setSpacing(6)
        tag = QtWidgets.QLabel(name)
        tag.setStyleSheet("font-family:monospace;")
        lay.addWidget(tag)
        self._path_lbl = QtWidgets.QLineEdit()
        self._path_lbl.setReadOnly(True)
        # sinon le QLineEdit intercepte le drop (ou le laisse remonter a la
        # fenetre -> tentative d'ouverture OpenCV du CSV) : on force le drop a
        # etre traite par CETTE zone.
        self._path_lbl.setAcceptDrops(False)
        lay.addWidget(self._path_lbl, 1)
        self._browse = QtWidgets.QToolButton()
        self._browse.setText("…")
        self._browse.setToolTip("Choisir un fichier")
        self._browse.clicked.connect(self._pick)
        lay.addWidget(self._browse)
        self._clear = QtWidgets.QToolButton()
        self._clear.setText("x")
        self._clear.setToolTip("Vider ce fichier (sous-entrée)")
        self._clear.clicked.connect(lambda: self._set(""))
        lay.addWidget(self._clear)
        self._del = QtWidgets.QToolButton()
        self._del.setIcon(_trash_icon())
        self._del.setAutoRaise(True)
        self._del.setToolTip("Supprimer l'entrée du plugin (manifest + code)")
        self._del.clicked.connect(lambda: self.deleteRequested.emit(self._name))
        lay.addWidget(self._del)
        self._apply(path or "")

    def _apply(self, path):
        self._path = path or ""
        if path:
            self._path_lbl.setText(path)
            self.setStyleSheet("InputDropZone{border:1px solid #4a8; "
                               "background:rgba(60,160,110,0.12);}")
        else:
            self._path_lbl.setText("")
            self._path_lbl.setPlaceholderText("Glisser-déposer un fichier ici")
            # bordure bleutee au repos : signale une zone de depot accessible.
            self.setStyleSheet("InputDropZone{border:1px dashed #4a6a9a; "
                               "background:rgba(90,140,200,0.06);}")

    def _hint(self, on):
        # surbrillance bleue pendant un glisser : "zone accessible au drop".
        if on:
            self.setStyleSheet("InputDropZone{border:2px solid #5aafff; "
                               "background:rgba(90,175,255,0.20);}")
        else:
            self._apply(getattr(self, "_path", "") or "")

    def _set(self, path):
        self._apply(path)
        self.fileChanged.emit(self._name, path)

    def _pick(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, ui_text(self, f"Fichier pour « {self._name} »"))
        if path:
            self._set(os.path.normpath(path))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            self._hint(True)
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self._hint(False)

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p:
                self._set(os.path.normpath(p))
                break
        e.acceptProposedAction()


class PluginCard(QtWidgets.QFrame):
    """Carte d'un plugin : en-tete (deplier, nom, statut, actif, editeur,
    supprimer) + corps (entrees declarees + jeux d'entrees + apercu)."""

    def __init__(self, panel, lp):
        super().__init__(panel)
        self._panel = panel
        self._mw = panel._mw
        self.lp = lp
        self.pid = lp.plugin_id
        self.setObjectName("PluginCard")
        self._build()

    # ---- construction ----
    def _build(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        head = QtWidgets.QHBoxLayout()
        self._arrow = QtWidgets.QToolButton()
        self._arrow.setObjectName(f"plugin_expand_{self.pid}")
        self._arrow.setArrowType(Qt.RightArrow)
        self._arrow.setAutoRaise(True)
        self._arrow.clicked.connect(self._toggle)
        head.addWidget(self._arrow)
        name = _ElidedPluginName(self.lp.name)
        f = name.font(); f.setBold(True); name.setFont(f)
        head.addWidget(name)
        status = "Actif" if self.lp.enabled else ("Erreur" if self.lp.instance is None else "Inactif")
        col = "#3a3" if self.lp.enabled else ("#c55" if self.lp.instance is None else "#777")
        self._badge = QtWidgets.QLabel(status)
        self._badge.setStyleSheet(f"background:{col}; color:white; border-radius:3px; "
                                  "padding:1px 8px; font-size:10px;")
        head.addWidget(self._badge)
        head.addStretch(1)
        self._chk = QtWidgets.QCheckBox("Actif")
        self._chk.setObjectName(f"plugin_active_{self.pid}")
        self._chk.setChecked(self.lp.enabled)
        self._chk.setEnabled(self.lp.instance is not None)
        self._chk.toggled.connect(self._on_enable)
        head.addWidget(self._chk)
        b_edit = QtWidgets.QToolButton(); b_edit.setIcon(_draft_icon())
        b_edit.setAutoRaise(True); b_edit.setToolTip("Éditer le plugin")
        b_edit.clicked.connect(lambda: self._panel.open_editor(self.lp))
        head.addWidget(b_edit)
        b_folder = QtWidgets.QToolButton(); b_folder.setIcon(_folder_icon())
        b_folder.setAutoRaise(True); b_folder.setToolTip("Ouvrir le dossier")
        b_folder.clicked.connect(lambda: self._panel.open_folder(self.lp))
        head.addWidget(b_folder)
        b_del = QtWidgets.QToolButton(); b_del.setIcon(_trash_icon())
        b_del.setAutoRaise(True); b_del.setToolTip("Supprimer le plugin")
        b_del.clicked.connect(lambda: self._panel.delete_plugin(self.lp))
        head.addWidget(b_del)
        outer.addLayout(head)

        self._body = QtWidgets.QWidget()
        self._body.setObjectName(f"plugin_body_{self.pid}")
        self._body.setVisible(False)
        bl = QtWidgets.QVBoxLayout(self._body)
        bl.setContentsMargins(18, 2, 4, 4)
        bl.setSpacing(6)
        if self.lp.instance is None and self.lp.error:
            err = QtWidgets.QLabel("Erreur de chargement :\n" + self.lp.error.strip().splitlines()[-1])
            err.setStyleSheet("color:#e08030; font-family:monospace; font-size:11px;")
            err.setWordWrap(True)
            bl.addWidget(err)
        self._groups_box = QtWidgets.QWidget(); bl.addWidget(self._groups_box)
        self._actions_box = QtWidgets.QWidget(); bl.addWidget(self._actions_box)
        self._preview = QtWidgets.QLabel()
        self._preview.setMinimumHeight(10)
        bl.addWidget(self._preview)
        outer.addWidget(self._body)
        self._refresh_border(False)

    def _refresh_border(self, selected):
        c = "#4a90d9" if selected else "#3a3a44"
        w = 2 if selected else 1
        self.setStyleSheet(f"#PluginCard{{border:{w}px solid {c}; border-radius:6px; "
                           "background:rgba(255,255,255,5);}}")

    # ---- interactions ----
    def mousePressEvent(self, e):
        self._panel.select_card(self)
        super().mousePressEvent(e)

    def set_selected(self, on):
        self._refresh_border(on)

    def on_view_changed(self):
        # vue courante changee : si la carte est depliee, reconstruire son corps
        # pour montrer les fichiers/cases de la nouvelle vue (sans la replier).
        if self._body.isVisible():
            self._rebuild_body()

    def _toggle(self):
        show = not self._body.isVisible()
        self._body.setVisible(show)
        self._arrow.setArrowType(Qt.DownArrow if show else Qt.RightArrow)
        if show:
            self._rebuild_body()

    def _on_enable(self, on):
        self._mw._plugin_loader.set_enabled(self.pid, on)
        self._mw._save_enabled_plugin_ids()
        set_ui_text(self._badge, "Actif" if on else "Inactif")
        self._badge.setStyleSheet(f"background:{'#3a3' if on else '#777'}; color:white; "
                                  "border-radius:3px; padding:1px 8px; font-size:10px;")
        self._mw._repaint_views()
        self._panel.update_summary()

    # ---- corps : entrees declarees + jeux d'entrees ----
    def _rebuild_body(self):
        self._mw._normalize_plugin_groups(self.pid)
        self._build_groups()
        self._build_actions()
        self._update_preview()

    def refresh_preview_if_visible(self):
        """Redessine l'apercu render_panel si la carte est depliee -- appele a
        chaque frame par MainWindow._display (sinon le panel restait fige)."""
        if self._body.isVisible():
            self._update_preview()

    def _build_actions(self):
        # boutons du hook menu_actions + ouverture du dock build_panel, si le
        # plugin les fournit. Regroupes sous "Actions" dans le corps de la carte.
        from frameviewer.plugins.api import FrameViewerPlugin
        _clear_layout(self._actions_box)
        inst = self.lp.instance
        if inst is None:
            return
        cls = type(inst)
        actions = []
        if cls.menu_actions is not FrameViewerPlugin.menu_actions:
            actions = self._mw._plugin_loader.collect_menu_actions(self.lp)
        has_panel = cls.build_panel is not FrameViewerPlugin.build_panel
        if not actions and not has_panel:
            return
        lay = QtWidgets.QVBoxLayout(self._actions_box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        lbl = QtWidgets.QLabel("Actions")
        lbl.setStyleSheet("color:#9ab; font-size:11px;")
        lay.addWidget(lbl)
        for label, cb in actions:
            b = QtWidgets.QPushButton(str(label))
            b.clicked.connect(lambda _=False, fn=cb: self._run_action(fn))
            lay.addWidget(b)
        if has_panel:
            b = QtWidgets.QPushButton("Ouvrir le panneau (dock)")
            b.setObjectName(f"plugin_open_dock_{self.pid}")
            b.clicked.connect(self._open_dock_panel)
            lay.addWidget(b)

    def _run_action(self, fn):
        # callback plugin protege : une exception ne casse jamais l'UI.
        try:
            fn()
        except Exception as e:
            self._mw._plugin_loader.log(f"action plugin : {e}", level="error",
                                        pid=self.pid)

    def _open_dock_panel(self):
        # un seul dock par plugin : s'il existe deja (meme masque), on le
        # re-affiche au lieu d'en empiler un nouveau a chaque clic.
        docks = getattr(self._mw, "_plugin_docks", None)
        if docks is None:
            docks = self._mw._plugin_docks = {}
        existing = docks.get(self.pid)
        if existing is not None:
            try:
                existing.show()
                existing.raise_()
                return
            except RuntimeError:
                docks.pop(self.pid, None)   # dock detruit -> on recree
        try:
            before = getattr(self._mw, "_plugin_last_dock", None)
            self._mw._plugin_loader.build_panel(self.lp)
            dock = getattr(self._mw, "_plugin_last_dock", None)
            if dock is not None and dock is not before:
                docks[self.pid] = dock
        except Exception as e:
            self._mw._plugin_loader.log(f"build_panel : {e}", level="error",
                                        pid=self.pid)

    def _build_groups(self):
        _clear_layout(self._groups_box)
        st = self._mw._plugin_state_for(self.pid)      # entrees declarees (global)
        vs = self._mw._plugin_view_state(self.pid)     # jeux d'entrees de la vue courante
        lay = QtWidgets.QVBoxLayout(self._groups_box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        fr = QtWidgets.QLabel("Frame : " + self.lp.manifest.get("frame_input", "Current Frame")
                              + "  (fournie automatiquement)")
        fr.setStyleSheet("color:#9ab; font-size:11px;")
        lay.addWidget(fr)

        req = [i for i in st["inputs"] if not i.get("hidden")]
        # en-tete : entrees declarees (variables self.<nom>) + ajout structurel
        hdr = QtWidgets.QHBoxLayout()
        if req:
            names = ", ".join("self." + i["name"] for i in req)
            hl = QtWidgets.QLabel("Entrées : " + names)
        else:
            hl = QtWidgets.QLabel("Aucune entrée fichier.")
        hl.setStyleSheet("color:#9ab; font-size:11px;")
        hl.setWordWrap(True)
        hdr.addWidget(hl, 1)
        add = QtWidgets.QToolButton(); add.setText("+ Entrée")
        add.setToolTip("Ajouter une entrée (change le format du plugin : nouvelle self.<nom>)")
        add.clicked.connect(self._add_input)
        hdr.addWidget(add)
        lay.addLayout(hdr)

        if not req:
            return

        for gi, group in enumerate(vs["contracts"]):
            has_file = any(group["files"].get(i["name"]) for i in req)
            box = QtWidgets.QFrame()
            box.setStyleSheet("QFrame{border:1px solid #3a3a44; border-radius:5px;}")
            gl = QtWidgets.QVBoxLayout(box)
            gl.setContentsMargins(6, 4, 6, 6)
            gl.setSpacing(3)
            title = QtWidgets.QHBoxLayout()
            tl = QtWidgets.QLabel(f"Jeu d'entrées {gi + 1}")
            tl.setStyleSheet("border:none; color:#bcd;")
            title.addWidget(tl)
            title.addStretch(1)
            if has_file:
                visible = group.get("visible", True)
                eye = QtWidgets.QToolButton()
                eye.setIcon(_eye_icon(visible))
                eye.setAutoRaise(True)
                eye.setToolTip("Afficher / masquer ce jeu d'entrées")
                eye.clicked.connect(lambda _=False, k=gi: self._toggle_group_visible(k))
                title.addWidget(eye)
                rm = QtWidgets.QToolButton(); rm.setText("Suppr.")
                rm.setAutoRaise(True)
                rm.setToolTip("Supprimer ce jeu d'entrées complet")
                rm.clicked.connect(lambda _=False, k=gi: self._remove_group(k))
                title.addWidget(rm)
            gl.addLayout(title)
            for inp in req:
                zone = InputDropZone(inp["name"], group["files"].get(inp["name"], ""))
                zone.fileChanged.connect(
                    lambda name, path, k=gi: self._on_input_file(k, name, path))
                zone.deleteRequested.connect(self._delete_input)
                gl.addWidget(zone)
            lay.addWidget(box)

    # ---- mutations d'etat ----
    def _commit(self, rebuild=True):
        self._mw._save_plugin_state()
        self._mw._repaint_views()
        if rebuild:
            self._rebuild_body()
        self._panel.update_summary()

    def _toggle_group_visible(self, k):
        vs = self._mw._plugin_view_state(self.pid)
        if 0 <= k < len(vs["contracts"]):
            g = vs["contracts"][k]
            g["visible"] = not g.get("visible", True)
            self._commit()

    def _remove_group(self, k):
        vs = self._mw._plugin_view_state(self.pid)
        if 0 <= k < len(vs["contracts"]):
            vs["contracts"].pop(k)
            self._commit()

    def _on_input_file(self, k, name, path):
        vs = self._mw._plugin_view_state(self.pid)
        if not (0 <= k < len(vs["contracts"])):
            return
        files = vs["contracts"][k]["files"]
        if path:
            files[name] = path
        else:
            files.pop(name, None)
        # Un changement provenant de InputDropZone.dropEvent est emis de facon
        # synchrone. Reconstruire ici detruirait la zone alors que Qt execute
        # encore son dropEvent, ce qui peut provoquer un crash natif dans le
        # binaire PyInstaller. On laisse l'evenement se terminer avant le rebuild.
        self._commit(rebuild=False)
        QtCore.QTimer.singleShot(0, self._rebuild_body)

    def _add_input(self):
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Nouvelle entrée",
            "Nom de variable (identifiant Python, ex. csv_labels) :")
        name = name.strip()
        if not ok or not name:
            return
        if not name.isidentifier():
            QtWidgets.QMessageBox.warning(
                self, "Nouvelle entrée",
                f"« {name} » n'est pas un identifiant Python valide.")
            return
        if self._mw._add_plugin_input(self.pid, name):
            self._commit()

    def _delete_input(self, name):
        r = QtWidgets.QMessageBox.question(
            self, "Supprimer l'entrée",
            f"Supprimer l'entrée « {name} » du plugin ?\n\n"
            f"Elle sera retirée du manifest.json et la ligne self.{name} "
            f"du plugin.py, ainsi que les fichiers déposés sous cette entrée.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if r != QtWidgets.QMessageBox.Yes:
            return
        if self._mw._remove_plugin_input(self.pid, name):
            self._commit()

    def _update_preview(self):
        from frameviewer.plugins.api import FrameViewerPlugin
        # Un plugin qui ne fournit pas render_panel n'a pas d'apercu de zone :
        # on masque simplement la zone (l'overlay se voit sur l'image, pas ici).
        inst = self.lp.instance
        if inst is None or type(inst).render_panel is FrameViewerPlugin.render_panel:
            self._preview.setVisible(False)
            return
        self._preview.setVisible(True)
        try:
            idx = self._mw.cur if self._mw.source is not None else 0
            img = self._mw._plugin_loader.render_plugin_panel(self.pid, idx, (300, 110))
        except Exception:
            img = None
        pix = _bgr_to_qpix(img)
        if pix is not None:
            self._preview.setPixmap(pix)
            self._preview.setText("")
        else:
            self._preview.setPixmap(QtGui.QPixmap())
            set_ui_text(self._preview, "(aucun aperçu pour cette frame)")
            self._preview.setStyleSheet("color:#888; font-size:11px;")


def _clear_layout(widget):
    old = widget.layout()
    if old is not None:
        while old.count():
            it = old.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
            elif it.layout():
                _clear_sub(it.layout())
        QtWidgets.QWidget().setLayout(old)   # detache l'ancien layout


def _clear_sub(lay):
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w:
            w.deleteLater()
        elif it.layout():
            _clear_sub(it.layout())


_SEG_SS = (
    "QPushButton{padding:6px 14px; border:1px solid #555; background:#1e1e1e;"
    "color:#aaa; border-radius:4px; font-weight:bold;}"
    "QPushButton:hover:!checked{background:#2e2e2e; color:#ddd;}"
    "QPushButton:checked{background:#2d4f70; color:#fff; border-color:#5aafff;}")


class _ElidedPluginName(QtWidgets.QLabel):
    """Nom borne : la carte reste stable et le nom complet reste accessible."""

    WIDTH = 150

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self._full_text = str(text)
        self.setFixedWidth(self.WIDTH)
        self.setToolTip(self._full_text)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self._update_text()

    def _update_text(self):
        available = max(0, self.width() - 2)
        self.setText(self.fontMetrics().elidedText(
            self._full_text, Qt.ElideRight, available))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QtCore.QEvent.FontChange, QtCore.QEvent.StyleChange):
            self._update_text()


_HELP_BUTTON_SS = (
    "QToolButton{min-width:28px;min-height:28px;border:1px solid #5aafff;"
    "border-radius:4px;background:#1769aa;color:white;font-weight:bold;}"
    "QToolButton:hover{background:#2185d0;border-color:#8ac7ff;}"
    "QToolButton:pressed{background:#125789;}")


class PluginsPanel(QtWidgets.QWidget):
    """Contenu complet de l'onglet Plugins."""

    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw
        self._kind = "code"          # segment actif
        self._selected_pid = None
        self._cards = []
        self._build()

    def _build(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(6)
        info = QtWidgets.QLabel(
            "Glissez-déposez vos fichiers sur les entrées du plugin.")
        info.setWordWrap(True)
        lay.addWidget(info)

        # Segments Plugin Code / Plugin Graphe : memes onglets, format bouton standard.
        seg = QtWidgets.QHBoxLayout()
        seg.setSpacing(4)
        self._b_code = QtWidgets.QPushButton("Plugin Code")
        self._b_graph = QtWidgets.QPushButton("Plugin Graphe")
        for b in (self._b_code, self._b_graph):
            b.setCheckable(True)
            b.setStyleSheet(_SEG_SS)
        self._b_code.setChecked(True)
        self._b_code.clicked.connect(lambda: self._set_kind("code"))
        self._b_graph.clicked.connect(lambda: self._set_kind("graph"))
        seg.addWidget(self._b_code, 1)
        seg.addWidget(self._b_graph, 1)
        lay.addLayout(seg)

        row = QtWidgets.QHBoxLayout()
        self._b_new = QtWidgets.QPushButton("+ Plugin Code")
        self._b_new.clicked.connect(self._new_plugin)
        row.addWidget(self._b_new)
        b_import = QtWidgets.QPushButton("Importer")
        b_import.clicked.connect(lambda: self._mw._import_plugin())
        row.addWidget(b_import)
        b_reload = QtWidgets.QPushButton("Actualiser")
        b_reload.setToolTip("Recharge tous les plugins depuis le disque "
                            "(recompile le code, sans cache).")
        b_reload.clicked.connect(lambda: self._mw._reload_plugins_from_settings())
        row.addWidget(b_reload)
        b_console = QtWidgets.QPushButton("Console")
        b_console.setObjectName("plugin_console_button")
        b_console.setToolTip("Journal (logs, prints, erreurs) + test sur la frame "
                             "courante + diagnostic env + export debug VS Code.")
        b_console.clicked.connect(lambda: self._mw._open_plugin_console())
        row.addWidget(b_console)
        b_help = QtWidgets.QToolButton()
        b_help.setText("?")
        b_help.setStyleSheet(_HELP_BUTTON_SS)
        b_help.setToolTip("Aide : tous les hooks et objets api, par theme "
                          "(le requis en vert), avec exemples.")
        b_help.clicked.connect(lambda: self._mw._open_plugin_help())
        row.addWidget(b_help)
        lay.addLayout(row)

        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._holder = QtWidgets.QWidget()
        self._holder_lay = QtWidgets.QVBoxLayout(self._holder)
        self._holder_lay.setSpacing(6)
        self._holder_lay.addStretch(1)
        self._scroll.setWidget(self._holder)
        lay.addWidget(self._scroll, 1)

        self._summary = QtWidgets.QLabel("")
        self._summary.setStyleSheet("color:#9ab; font-size:11px;")
        self._summary.setWordWrap(True)
        lay.addWidget(self._summary)
        self._elem_box = QtWidgets.QGroupBox("Éléments affichés (plugin sélectionné)")
        self._elem_lay = QtWidgets.QVBoxLayout(self._elem_box)
        lay.addWidget(self._elem_box)

    # ---- segments ----
    def _set_kind(self, kind):
        self._kind = kind
        self._b_code.setChecked(kind == "code")
        self._b_graph.setChecked(kind == "graph")
        set_ui_text(
            self._b_new,
            "+ Plugin Code" if kind == "code" else "+ Plugin Graphe")
        self.refresh()

    def _new_plugin(self):
        if self._kind == "graph":
            self._mw._new_graph_plugin_from_settings()
        else:
            self._mw._new_code_plugin_wizard()

    # ---- selection / cartes ----
    def select_card(self, card):
        self._selected_pid = card.pid
        for c in self._cards:
            c.set_selected(c.pid == card.pid)
        self.update_summary()

    def on_view_changed(self):
        """Vue courante changee : rebatit le corps des cartes DEPLIEES (fichiers
        et cases de la nouvelle vue) sans recreer les cartes -> le depliement
        reste persistant d'une vue a l'autre."""
        for c in self._cards:
            c.on_view_changed()
        self.update_summary()

    def refresh_previews(self):
        """Redessine les apercus render_panel des cartes depliees (appele a
        chaque frame par MainWindow._display)."""
        for c in self._cards:
            c.refresh_preview_if_visible()

    def selected_lp(self):
        for lp in self._mw._plugin_loader.plugins:
            if lp.plugin_id == self._selected_pid:
                return lp
        return None

    def open_editor(self, lp):
        if lp.kind == "graph":
            self._mw._open_graph_editor_for(lp.folder)
        else:
            self._mw._open_code_editor_for(os.path.basename(lp.folder))

    def open_folder(self, lp):
        self._mw._open_folder(lp.folder)

    def delete_plugin(self, lp):
        self._mw._delete_plugin(lp.plugin_id, lp.folder)

    # ---- rafraichissement ----
    def refresh(self):
        for c in self._cards:
            c.setParent(None)
            c.deleteLater()
        self._cards = []
        loader = self._mw._plugin_loader
        shown = [lp for lp in loader.plugins if lp.kind == self._kind]
        for lp in shown:
            card = PluginCard(self, lp)
            self._holder_lay.insertWidget(self._holder_lay.count() - 1, card)
            self._cards.append(card)
        if self._selected_pid not in [lp.plugin_id for lp in shown] and shown:
            self._selected_pid = shown[0].plugin_id
        for c in self._cards:
            c.set_selected(c.pid == self._selected_pid)
        self.update_summary()

    def update_summary(self):
        lp = self.selected_lp()
        loader = self._mw._plugin_loader
        n = len([l for l in loader.plugins if l.kind == self._kind])
        act = len([l for l in loader.enabled_plugins() if l.kind == self._kind])
        if lp is None:
            set_ui_text(
                self._summary, f"{n} plugin(s) {self._kind}, {act} actif(s).")
        else:
            st = self._mw._plugin_state_for(lp.plugin_id)
            n_in = sum(1 for i in st["inputs"] if not i.get("hidden"))
            sets = self._mw._visible_contracts(lp.plugin_id)
            last = lp.last_run or "—"
            set_ui_text(
                self._summary,
                f"Plugin : {lp.name}   |   Entrées : {n_in}   |   "
                f"Jeux actifs : {len(sets)}   |   Statut : "
                f"{'● Actif' if lp.enabled else '○ Inactif'}   |   "
                f"Dernière exécution : {last}")
        self._rebuild_elements(lp)

    def _rebuild_elements(self, lp):
        _clear_sub(self._elem_lay)
        from frameviewer.plugins.api import FrameViewerPlugin, PluginAPI
        elems = []
        if lp is not None and lp.instance is not None and \
                type(lp.instance).overlay_elements is not FrameViewerPlugin.overlay_elements:
            try:
                elems = lp.instance.overlay_elements(
                    PluginAPI(self._mw, lp.folder, lp.plugin_id)) or []
            except Exception:
                elems = []
        if not elems:
            lbl = QtWidgets.QLabel("Le plugin sélectionné ne déclare pas d'éléments.")
            lbl.setStyleSheet("color:#888;")
            self._elem_lay.addWidget(lbl)
            return
        states = self._mw._plugin_view_state(lp.plugin_id)["elements"]
        keymap = getattr(lp, "element_keys", None) or {}
        for key, label in elems:
            kb = keymap.get(key)
            text = f"{label}   [{kb}]" if kb else label
            cb = QtWidgets.QCheckBox(text)
            cb.setToolTip("Touche clavier : " + kb if kb else "Aucune touche mappée")
            cb.setChecked(bool(states.get(key, True)))
            cb.toggled.connect(lambda on, k=key, p=lp.plugin_id: self._mw._on_element_toggled(p, k, on))
            self._elem_lay.addWidget(cb)
