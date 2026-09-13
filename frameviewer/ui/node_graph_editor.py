# -*- coding: utf-8 -*-
"""Editeur de graphe de noeuds (voir frameviewer/plugins/graph.py) : un
mini-langage visuel pour construire un plugin "formes" (hook get_overlays)
sans ecrire de Python.

Interaction :
  - Ajouter un bloc : bouton de la barre, clic droit sur le canvas, ou
    double-clic dans le vide (menu au curseur).
  - Relier : cliquer une sortie (droite, bleue) puis une entree (gauche,
    orange) -- pas de glisser-cable, plus fiable.
  - Deplacer/zoomer : glisser un bloc ; molette = zoom ; bouton du milieu
    (ou espace + glisser) = panoramique.
  - Suppr : retire le bloc/cable selectionne.

L'editeur vit dans une FENETRE NON MODALE (NodeGraphEditorWindow) : elle ne
bloque pas l'app, on peut naviguer dans la sequence et cliquer "Tester
(frame courante)" pour voir le JSON produit en direct. Enregistre un
`graph.json` + regenere un `plugin.py` wrapper (GraphPlugin) -- un plugin
"graphe" reste un plugin normal pour le loader (voir docs/plugins.md).
"""
import json
import os

from PySide6 import QtCore, QtGui, QtWidgets

from frameviewer.plugins.graph import REGISTRY, Graph, evaluate_graph_for_frame
from frameviewer.plugins.graph_runtime import GRAPH_FILE
from frameviewer.plugins.loader import PLUGIN_FOLDER_PREFIX, PLUGIN_MODULE_FILE

NODE_W = 200
ROW_H = 22
HEADER_H = 26
PORT_R = 5

_WRAPPER_TEMPLATE = '''# -*- coding: utf-8 -*-
# Genere par l'editeur de graphe (Parametres > Plugins > Editeur de graphe)
# -- NE PAS EDITER A LA MAIN, ce fichier est ECRASE a chaque sauvegarde
# depuis l'editeur. Toute la logique vit dans graph.json (memes voisins).
from frameviewer.plugins.graph_runtime import GraphPlugin as _Base


class {cls}(_Base):
    name = "{name}"
    description = "Plugin graphe (blocs) -- edite depuis Parametres > Plugins."


PLUGIN = {cls}
'''


_HELP_HTML = """
<h2>Éditeur de graphe -- à quoi ça sert</h2>
<p>Construire un <b>plugin qui dessine des formes sur chaque frame</b>
(boîtes, points, segments, ellipses, texte) <b>sans écrire de code</b>. Tu
poses des blocs, tu relies une sortie à une entrée, tu enregistres. Le
résultat s'affiche par-dessus la vidéo/séquence, en live et à l'export,
exactement comme les calques SIDECAR.</p>

<h3>Le principe (le flux)</h3>
<ol>
<li><b>Sources</b> (bleu) : une <i>Colonne CSV</i> lit une valeur dans la
ligne courante du CSV ; <i>Constante</i>, <i>Numéro de frame</i> donnent des
valeurs sans CSV.</li>
<li><b>Calcul / Apparence</b> : combine des valeurs (Opération, Milieu,
Comparer) ou fabrique une couleur (Couleur, Couleur fixe, Analyser couleur).</li>
<li><b>Formes</b> (rouge) : Rectangle, Ellipse, Point/Croix, Segment, Texte
-- reçoivent des coordonnées + une couleur et produisent une forme.</li>
<li><b>Sortie : Calques</b> : collecte toutes les formes reliées. <b>C'est
elle qui décide ce qui est dessiné.</b> Sans elle, rien n'apparaît.</li>
</ol>

<h3>Comment ça s'applique aux frames</h3>
<p>Un CSV = une ligne par détection, plusieurs lignes par frame. Le graphe
regroupe les lignes par la <b>Colonne frame</b>, puis, pour chaque frame,
évalue le graphe <b>une ligne à la fois</b> : chaque ligne produit ses
formes, dessinées sur cette frame-là. Les coordonnées sont en <b>pixels de
l'image</b>. Les couleurs sont en <b>RVB 0..255</b> (comme à l'écran, pas du
BGR).</p>

<h3>Conditions requises pour voir quelque chose</h3>
<ul>
<li>Un bloc <b>Sortie : Calques</b> présent, avec au moins une <b>Forme</b>
reliée à son entrée <i>forme</i>.</li>
<li>La <b>Colonne frame</b> (en haut) pointe sur la colonne du CSV qui
contient le numéro de frame -- sinon aucune ligne n'est associée à une
frame.</li>
<li>Les <i>Colonne CSV</i> pointent sur des colonnes qui existent vraiment
dans le fichier (la liste déroulante propose les colonnes d'un CSV trouvé à
côté du plugin).</li>
<li>À l'exécution : le plugin est <b>coché</b> dans Paramètres &gt; Plugins,
et un <b>CSV est glissé-déposé sur la vue</b> (ou renseigné via « Charger un
CSV »).</li>
</ul>

<h3>Tester et appliquer</h3>
<p><b>Tester (frame courante)</b> : évalue le graphe sur la frame affichée et
montre le JSON des formes produites -- sans rien recharger. Quand c'est bon :
<b>Enregistrer + recharger</b>, puis navigue sur une frame qui a des
détections.</p>

<h3>Multivue (plusieurs vues)</h3>
<p>En disposition multi-vues (h2, q4...), tu peux cibler une vue précise :
le bloc <b>Sortie : Calques</b> a un paramètre <b>vue</b>
(active/vue1/vue2...). Fais un sous-graphe « keypoints vue 1 » relié à une
Sortie réglée sur <b>vue1</b>, un autre « keypoints vue 2 » relié à une
Sortie <b>vue2</b>. Pour <b>relier deux vues</b>, utilise le bloc
<b>Segment inter-vues</b> (catégorie Inter-vues) : il prend un point (xa,ya)
de la vue A et un point (xb,yb) de la vue B et trace un trait d'une vue à
l'autre (visuel type appariement SIFT entre deux images). vue1 = 1re vue
(position 0), vue2 = 2e, etc. À l'export d'une frame unique, les éléments
inter-vues sont ignorés (ils n'ont de sens qu'à l'écran, sur plusieurs vues).</p>

<h3>Exemple prêt à l'emploi</h3>
<p>Clique <b>« Insérer l'exemple fonctionnel (keypoints) »</b> ci-dessous :
il ajoute un graphe complet (4 Colonnes CSV x1,y1,x2,y2 -&gt; Segment -&gt;
Sortie) qui trace un segment vert par paire de points. Renseigne la Colonne
frame, teste, enregistre.</p>
"""


class PortItem(QtWidgets.QGraphicsEllipseItem):
    def __init__(self, node_item, port_name, is_output):
        super().__init__(-PORT_R, -PORT_R, 2 * PORT_R, 2 * PORT_R)
        self.node_item = node_item
        self.port_name = port_name
        self.is_output = is_output
        self._base_brush = QtGui.QColor(90, 150, 220) if is_output else QtGui.QColor(220, 150, 90)
        self.setBrush(self._base_brush)
        self.setPen(QtGui.QPen(QtGui.QColor(20, 20, 25), 1))
        self.setZValue(10)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(f"{'Sortie' if is_output else 'Entrée'} : {port_name}\nClique pour relier.")

    def mousePressEvent(self, event):
        event.accept()
        scene = self.scene()
        if scene is not None and hasattr(scene, "port_clicked"):
            scene.port_clicked(self)


class NodeItem(QtWidgets.QGraphicsRectItem):
    def __init__(self, node_id, node_type, params):
        n_ports = max(len(node_type.inputs), len(node_type.outputs))
        h = HEADER_H + (n_ports + len(node_type.params)) * ROW_H + 10
        super().__init__(0, 0, NODE_W, h)
        self.node_id = node_id
        self.node_type = node_type
        self.setFlags(QtWidgets.QGraphicsItem.ItemIsMovable
                      | QtWidgets.QGraphicsItem.ItemIsSelectable
                      | QtWidgets.QGraphicsItem.ItemSendsGeometryChanges)
        self.setBrush(QtGui.QColor(45, 45, 54))
        self.setPen(QtGui.QPen(QtGui.QColor(95, 95, 110), 1))
        self.setZValue(1)
        self.on_moved = None   # callback(node_item), branche par l'editeur

        # bandeau de titre colore par categorie -- reperage visuel rapide.
        cat_col = _CATEGORY_COLORS.get(node_type.category, QtGui.QColor(70, 70, 82))
        band = QtWidgets.QGraphicsRectItem(1, 1, NODE_W - 2, HEADER_H - 2, self)
        band.setBrush(cat_col)
        band.setPen(QtGui.QPen(QtCore.Qt.NoPen))
        band.setZValue(0)

        title = QtWidgets.QGraphicsSimpleTextItem(node_type.label, self)
        title.setBrush(QtGui.QColor(245, 245, 245))
        f = title.font(); f.setBold(True); title.setFont(f)
        title.setPos(8, 5)

        self.input_ports = {}
        self.output_ports = {}
        self.param_widgets = {}

        y = HEADER_H
        for name, _t in node_type.inputs:
            p = PortItem(self, name, False)
            p.setParentItem(self)
            p.setPos(0, y + ROW_H / 2)
            self.input_ports[name] = p
            lbl = QtWidgets.QGraphicsSimpleTextItem(name, self)
            lbl.setPos(9, y + 3)
            lbl.setBrush(QtGui.QColor(205, 205, 205))
            y += ROW_H
        y2 = HEADER_H
        for name, _t in node_type.outputs:
            p = PortItem(self, name, True)
            p.setParentItem(self)
            p.setPos(NODE_W, y2 + ROW_H / 2)
            self.output_ports[name] = p
            lbl = QtWidgets.QGraphicsSimpleTextItem(name, self)
            w = lbl.boundingRect().width()
            lbl.setPos(NODE_W - 9 - w, y2 + 3)
            lbl.setBrush(QtGui.QColor(205, 205, 205))
            y2 += ROW_H
        y = max(y, y2)
        for pname, ptype, pdefault in node_type.params:
            lbl = QtWidgets.QGraphicsSimpleTextItem(pname, self)
            lbl.setPos(8, y + 4)
            lbl.setBrush(QtGui.QColor(165, 165, 165))
            cur = params.get(pname, pdefault)
            if ptype.startswith("choice:"):
                options = ptype.split(":", 1)[1].split(",")
                w = QtWidgets.QComboBox()
                w.addItems(options)
                if cur in options:
                    w.setCurrentText(cur)
            elif ptype == "column":
                w = QtWidgets.QComboBox()
                w.setEditable(True)
                if cur:
                    w.addItem(cur)
            else:
                w = QtWidgets.QLineEdit(str(cur))
            w.setMaximumWidth(NODE_W - 74)
            w.setStyleSheet("font-size:10px;")
            proxy = QtWidgets.QGraphicsProxyWidget(self)
            proxy.setWidget(w)
            proxy.setPos(66, y - 3)
            self.param_widgets[pname] = w
            y += ROW_H

    def current_params(self):
        out = {}
        for name, w in self.param_widgets.items():
            if isinstance(w, QtWidgets.QComboBox):
                out[name] = w.currentText()
            else:
                out[name] = w.text()
        return out

    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.ItemPositionHasChanged and self.on_moved:
            self.on_moved(self)
        return super().itemChange(change, value)

    def set_column_options(self, columns):
        w = self.param_widgets.get("colonne")
        if w is None or not isinstance(w, QtWidgets.QComboBox):
            return
        cur = w.currentText()
        w.blockSignals(True)
        w.clear()
        w.addItems(columns)
        if cur and cur not in columns:
            w.insertItem(0, cur)
        idx = w.findText(cur) if cur else -1
        w.setCurrentIndex(max(0, idx))
        w.blockSignals(False)


_CATEGORY_COLORS = {
    "Source": QtGui.QColor(60, 92, 130),
    "Calcul": QtGui.QColor(70, 110, 80),
    "Logique": QtGui.QColor(120, 90, 60),
    "Apparence": QtGui.QColor(110, 80, 120),
    "Forme": QtGui.QColor(120, 70, 80),
    "Inter-vues": QtGui.QColor(60, 100, 110),
    "Sortie": QtGui.QColor(100, 60, 60),
}


class EdgeItem(QtWidgets.QGraphicsPathItem):
    """Cable en courbe cubique (tangentes horizontales) entre une sortie et
    une entree -- rendu 'Blueprint' plus lisible qu'une ligne droite quand
    les blocs se chevauchent."""

    def __init__(self, src_port, dst_port):
        super().__init__()
        self.src_port = src_port
        self.dst_port = dst_port
        self._pen = QtGui.QPen(QtGui.QColor(150, 170, 200), 2)
        self._pen.setCosmetic(True)
        self.setPen(self._pen)
        self.setZValue(0)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)
        self.update_line()

    def update_line(self):
        a = self.src_port.scenePos()
        b = self.dst_port.scenePos()
        dx = max(40.0, abs(b.x() - a.x()) * 0.5)
        path = QtGui.QPainterPath(a)
        path.cubicTo(a.x() + dx, a.y(), b.x() - dx, b.y(), b.x(), b.y())
        self.setPath(path)

    def paint(self, painter, option, widget=None):
        if self.isSelected():
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 210, 80), 3))
        else:
            painter.setPen(self._pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawPath(self.path())


class GraphScene(QtWidgets.QGraphicsScene):
    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self._pending_port = None
        self.setBackgroundBrush(QtGui.QColor(28, 28, 33))

    def port_clicked(self, port):
        if self._pending_port is None:
            self._pending_port = port
            port.setBrush(QtGui.QColor(255, 220, 60))
            return
        prev = self._pending_port
        prev.setBrush(prev._base_brush)
        self._pending_port = None
        if prev is port:
            return
        if prev.is_output == port.is_output:
            return   # 2 sorties ou 2 entrees : rien a relier
        src, dst = (prev, port) if prev.is_output else (port, prev)
        self.editor.add_edge(src.node_item.node_id, src.port_name,
                             dst.node_item.node_id, dst.port_name)


# Synonymes de recherche par bloc (FR + EN + symboles) -- alimente la palette
# "type-to-search" : taper "box", "sift", "segment", "couleur"... fait
# remonter le bon bloc meme si le libelle exact n'est pas connu.
_NODE_KEYWORDS = {
    "csv_column": "csv colonne column donnee valeur champ",
    "constant": "constante valeur fixe nombre",
    "frame_index": "frame numero index image temps t courante",
    "compare": "comparer condition test superieur inferieur egal booleen if si > < >= <= ==",
    "midpoint": "milieu centre moyenne moitie",
    "math": "operation calcul addition soustraction multiplication division somme + - * /",
    "color_rgb": "couleur color rgb rouge vert bleu combine",
    "color_const": "couleur color fixe constante rgb",
    "parse_color_csv": "couleur color analyser parse texte rgb chaine",
    "rect_shape": "rectangle boite box bbox carre forme detection",
    "ellipse_shape": "ellipse cercle rond forme incertitude covariance",
    "point_shape": "point croix marqueur marker keypoint forme",
    "line_shape": "segment ligne line trait keypoint sift appariement match correspondance paire forme",
    "interview_segment": "segment inter vue vues multivue keypoint sift appariement match correspondance stereo vue1 vue2",
    "text_label": "texte text label libelle annotation etiquette",
    "output_overlays": "sortie output calque resultat collecte final vue multivue",
}


class NodePalette(QtWidgets.QWidget):
    """Palette "type-to-search" facon Blueprint : un champ de recherche + une
    liste filtrée en direct. Ouverte au clic droit / double-clic sur le fond
    du canvas. On tape ("box", "sift", "csv"...), la liste se réduit aux blocs
    correspondants (libellé, catégorie ou synonymes) ; Entrée (ou clic) ajoute
    le bloc sélectionné à l'endroit du curseur, Échap ferme. Flèches haut/bas
    pour naviguer sans quitter le champ."""

    def __init__(self, editor, scene_pos):
        super().__init__(editor.window(), QtCore.Qt.Popup)
        self._editor = editor
        self._scene_pos = scene_pos
        self.setMinimumWidth(300)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self.edit = QtWidgets.QLineEdit()
        self.edit.setPlaceholderText("Rechercher un bloc…  (tape, ↑↓, Entrée)")
        lay.addWidget(self.edit)
        self.list = QtWidgets.QListWidget()
        self.list.setMaximumHeight(340)
        lay.addWidget(self.list)

        by_cat = {}
        for nt in REGISTRY.values():
            by_cat.setdefault(nt.category, []).append(nt)
        for cat in sorted(by_cat):
            for nt in sorted(by_cat[cat], key=lambda n: n.label):
                it = QtWidgets.QListWidgetItem(f"{nt.label}    ·  {cat}")
                it.setData(QtCore.Qt.UserRole, nt.key)
                hay = f"{nt.label} {cat} {nt.key} {_NODE_KEYWORDS.get(nt.key, '')}".lower()
                it.setData(QtCore.Qt.UserRole + 1, hay)
                self.list.addItem(it)

        self._select_first_visible()
        self.edit.textChanged.connect(self._filter)
        self.edit.returnPressed.connect(self._commit)
        self.list.itemClicked.connect(lambda _it: self._commit())
        self.edit.installEventFilter(self)
        self.edit.setFocus()

    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() == QtCore.QEvent.KeyPress:
            k = ev.key()
            if k in (QtCore.Qt.Key_Down, QtCore.Qt.Key_Up):
                self._move(1 if k == QtCore.Qt.Key_Down else -1)
                return True
            if k == QtCore.Qt.Key_Escape:
                self.close()
                return True
        return super().eventFilter(obj, ev)

    def _visible_rows(self):
        return [i for i in range(self.list.count()) if not self.list.item(i).isHidden()]

    def _select_first_visible(self):
        vis = self._visible_rows()
        if vis:
            self.list.setCurrentRow(vis[0])

    def _move(self, step):
        vis = self._visible_rows()
        if not vis:
            return
        cur = self.list.currentRow()
        idx = vis.index(cur) if cur in vis else 0
        idx = max(0, min(len(vis) - 1, idx + step))
        self.list.setCurrentRow(vis[idx])

    def _filter(self, text):
        tokens = text.lower().split()
        for i in range(self.list.count()):
            it = self.list.item(i)
            hay = it.data(QtCore.Qt.UserRole + 1)
            it.setHidden(not all(t in hay for t in tokens))
        self._select_first_visible()

    def _commit(self):
        it = self.list.currentItem()
        if it is None or it.isHidden():
            vis = self._visible_rows()
            if not vis:
                return
            it = self.list.item(vis[0])
        key = it.data(QtCore.Qt.UserRole)
        self.close()
        self._editor.add_node(key, pos=[self._scene_pos.x(), self._scene_pos.y()])


class GraphView(QtWidgets.QGraphicsView):
    """Vue avec zoom molette (autour du curseur), panoramique au bouton du
    milieu ou espace+glisser, et palette de recherche de blocs au clic droit /
    double-clic dans le vide."""

    def __init__(self, scene, editor):
        super().__init__(scene)
        self._editor = editor
        self.setRenderHint(QtGui.QPainter.Antialiasing)
        self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self._panning = False
        self._pan_start = None

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        cur = self.transform().m11()
        # borne le zoom pour ne pas se perdre (0.2x .. 3x).
        if (cur < 0.2 and factor < 1) or (cur > 3.0 and factor > 1):
            return
        self.scale(factor, factor)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MiddleButton:
            self._panning = True
            self._pan_start = event.position().toPoint()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning and self._pan_start is not None:
            delta = event.position().toPoint() - self._pan_start
            self._pan_start = event.position().toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MiddleButton and self._panning:
            self._panning = False
            self.setCursor(QtCore.Qt.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.itemAt(event.position().toPoint()) is None:
            scene_pos = self.mapToScene(event.position().toPoint())
            self._editor.show_node_palette(event.globalPosition().toPoint(), scene_pos)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        if self.itemAt(event.pos()) is None:
            scene_pos = self.mapToScene(event.pos())
            self._editor.show_node_palette(event.globalPos(), scene_pos)
            event.accept()
            return
        super().contextMenuEvent(event)


class NodeGraphEditorWidget(QtWidgets.QWidget):
    """Coeur de l'editeur -- reutilisable (fenetre autonome, dock...). Ne
    connait de MainWindow que ce dont il a besoin : self._mw.cur / .source /
    ._raw / ._display() / ._plugin_loader."""

    def __init__(self, mw, plugin_folder, on_saved=None, parent=None):
        super().__init__(parent)
        self._mw = mw
        self.folder = plugin_folder
        self._on_saved = on_saved

        self.graph = self._load_or_new_graph()
        self.node_items = {}   # node_id -> NodeItem
        self.edge_items = []   # [EdgeItem]

        vbox = QtWidgets.QVBoxLayout(self)
        vbox.setContentsMargins(6, 6, 6, 6)
        top = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("Ajouter un bloc")
        add_btn.setMenu(self._build_add_menu(add_btn))
        top.addWidget(add_btn)

        csv_btn = QtWidgets.QPushButton("Charger un CSV...")
        csv_btn.clicked.connect(self._browse_csv)
        top.addWidget(csv_btn)

        top.addWidget(QtWidgets.QLabel("Colonne frame :"))
        self._frame_col_combo = QtWidgets.QComboBox()
        self._frame_col_combo.setEditable(True)
        self._frame_col_combo.setMinimumWidth(120)
        self._frame_col_combo.setToolTip(
            "Colonne du CSV qui identifie la frame (regroupe les lignes par "
            "frame avant d'evaluer le graphe). Laisse vide = 1re colonne.")
        top.addWidget(self._frame_col_combo)

        help_btn = QtWidgets.QPushButton("Aide")
        help_btn.setToolTip("Comment ça marche : le principe, un exemple "
                           "fonctionnel à insérer, les conditions requises.")
        help_btn.clicked.connect(self._show_help)
        top.addWidget(help_btn)

        top.addStretch(1)
        test_btn = QtWidgets.QPushButton("Tester (frame courante)")
        test_btn.clicked.connect(self._test_current_frame)
        top.addWidget(test_btn)
        save_btn = QtWidgets.QPushButton("Enregistrer")
        save_btn.clicked.connect(self._save)
        top.addWidget(save_btn)
        save_reload_btn = QtWidgets.QPushButton("Enregistrer + recharger")
        save_reload_btn.setToolTip(
            "Enregistre le graphe puis recharge les plugins et redessine la "
            "vue -- le resultat s'affiche tout de suite si le plugin est actif.")
        save_reload_btn.clicked.connect(self._save_and_reload)
        top.addWidget(save_reload_btn)
        vbox.addLayout(top)

        self.scene = GraphScene(self)
        self.scene.setSceneRect(0, 0, 4000, 3000)
        self.view = GraphView(self.scene, self)
        vbox.addWidget(self.view, 1)

        self._status = QtWidgets.QLabel(
            "Clic droit (ou double-clic) sur le fond = palette de blocs "
            "(tape pour chercher, Entrée pour ajouter). Clique une sortie "
            "(bleue) puis une entrée (orange) pour relier. Molette = zoom, "
            "bouton du milieu = déplacer la vue. Suppr = retirer.")
        self._status.setStyleSheet("color:#888; font-size:11px;")
        self._status.setWordWrap(True)
        vbox.addWidget(self._status)

        shortcut = QtGui.QShortcut(QtGui.QKeySequence.Delete, self.view)
        shortcut.activated.connect(self._delete_selected)

        self._did_initial_fit = False
        self._rebuild_from_graph()
        # colonnes proposees d'entree (csv_path du graphe, ou CSV voisin en
        # apercu) + colonne frame memorisee, meme sans csv_path.
        self._refresh_csv_columns()
        if self.graph.frame_column:
            self._frame_col_combo.setEditText(self.graph.frame_column)

    def showEvent(self, event):
        super().showEvent(event)
        # la vue n'a sa taille reelle qu'une fois affichee : on recadre au
        # premier show pour partir SUR les blocs (haut-gauche), pas centre sur
        # le vide au milieu d'une scene volontairement large (4000x3000).
        if not self._did_initial_fit:
            self._did_initial_fit = True
            QtCore.QTimer.singleShot(0, self._reset_view_to_blocks)

    def _reset_view_to_blocks(self):
        vp = self.view.viewport()
        rect = self.scene.itemsBoundingRect()
        if self.node_items and rect.isValid():
            # place le coin haut-gauche des blocs a ~40px du bord de la vue.
            cx = rect.left() + vp.width() / 2.0 - 40
            cy = rect.top() + vp.height() / 2.0 - 40
            self.view.centerOn(cx, cy)
        else:
            # graphe vide (nouveau plugin) : origine de la scene en haut-gauche.
            self.view.centerOn(vp.width() / 2.0, vp.height() / 2.0)

    # ---------- menu d'ajout ----------
    def _build_add_menu(self, parent):
        menu = QtWidgets.QMenu(parent)
        by_cat = {}
        for nt in REGISTRY.values():
            by_cat.setdefault(nt.category, []).append(nt)
        for cat in sorted(by_cat):
            sub = menu.addMenu(cat)
            for nt in sorted(by_cat[cat], key=lambda n: n.label):
                act = sub.addAction(nt.label)
                act.triggered.connect(lambda _=False, k=nt.key: self.add_node(k))
        return menu

    def show_node_palette(self, global_pos, scene_pos):
        """Ouvre la palette de recherche 'type-to-search' au curseur pour
        ajouter un bloc a `scene_pos`."""
        pal = NodePalette(self, scene_pos)
        pal.move(global_pos)
        pal.show()
        pal.edit.setFocus()

    # ---------- aide ----------
    def _show_help(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Éditeur de graphe -- mode d'emploi")
        dlg.resize(680, 560)
        lay = QtWidgets.QVBoxLayout(dlg)
        browser = QtWidgets.QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setHtml(_HELP_HTML)
        lay.addWidget(browser, 1)
        row = QtWidgets.QHBoxLayout()
        ex_btn = QtWidgets.QPushButton("Insérer l'exemple fonctionnel (keypoints)")
        ex_btn.setToolTip("Ajoute au canvas un petit graphe complet qui "
                         "dessine un segment par paire de points d'un CSV.")

        def _do_example():
            self.insert_example_keypoints()
            dlg.accept()
        ex_btn.clicked.connect(_do_example)
        row.addWidget(ex_btn)
        row.addStretch(1)
        close_btn = QtWidgets.QPushButton("Fermer")
        close_btn.clicked.connect(dlg.accept)
        row.addWidget(close_btn)
        lay.addLayout(row)
        dlg.exec()

    def insert_example_keypoints(self):
        """Construit dans le canvas un graphe d'exemple FONCTIONNEL : chaque
        ligne CSV (x1,y1,x2,y2) devient un segment reliant les deux points
        (visuel type appariement de keypoints). Decale les blocs pour ne pas
        recouvrir un graphe deja present."""
        dx = 60 * (len(self.node_items) > 0)
        base_x = 40 + dx
        x1 = self.add_node("csv_column", params={"colonne": "x1", "type": "nombre"}, pos=[base_x, 40])
        y1 = self.add_node("csv_column", params={"colonne": "y1", "type": "nombre"}, pos=[base_x, 190])
        x2 = self.add_node("csv_column", params={"colonne": "x2", "type": "nombre"}, pos=[base_x, 340])
        y2 = self.add_node("csv_column", params={"colonne": "y2", "type": "nombre"}, pos=[base_x, 490])
        col = self.add_node("color_const", params={"rgb": "0,255,0"}, pos=[base_x + 260, 520])
        seg = self.add_node("line_shape", pos=[base_x + 300, 150])
        out = self.add_node("output_overlays", pos=[base_x + 600, 190])
        self.add_edge(x1.node_id, "valeur", seg.node_id, "x1")
        self.add_edge(y1.node_id, "valeur", seg.node_id, "y1")
        self.add_edge(x2.node_id, "valeur", seg.node_id, "x2")
        self.add_edge(y2.node_id, "valeur", seg.node_id, "y2")
        self.add_edge(col.node_id, "couleur", seg.node_id, "couleur")
        self.add_edge(seg.node_id, "forme", out.node_id, "forme")
        if not self._frame_col_combo.currentText().strip():
            self._frame_col_combo.setEditText("frame")
        self._status.setText(
            "Exemple keypoints inséré. Vérifie la « Colonne frame », puis "
            "« Tester (frame courante) » ou « Enregistrer + recharger ».")

    # ---------- fichiers ----------
    def _load_or_new_graph(self):
        path = os.path.join(self.folder, GRAPH_FILE)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return Graph.from_dict(json.load(f))
        return Graph()

    def _save(self):
        for nid, item in self.node_items.items():
            self.graph.nodes[nid]["params"] = item.current_params()
            self.graph.nodes[nid]["pos"] = [item.pos().x(), item.pos().y()]
        self.graph.frame_column = self._frame_col_combo.currentText().strip() or None
        with open(os.path.join(self.folder, GRAPH_FILE), "w", encoding="utf-8") as f:
            json.dump(self.graph.to_dict(), f, indent=1)
        entry = os.path.basename(self.folder)
        slug = entry[len(PLUGIN_FOLDER_PREFIX):] if entry.startswith(PLUGIN_FOLDER_PREFIX) else entry
        cls = "".join(p.capitalize() for p in slug.split("_")) + "GraphPlugin"
        with open(os.path.join(self.folder, PLUGIN_MODULE_FILE), "w", encoding="utf-8") as f:
            f.write(_WRAPPER_TEMPLATE.format(cls=cls, name=slug))
        self._status.setText("Enregistré.")

    def _save_and_reload(self):
        self._save()
        self._mw._plugin_loader.reload_all()
        if getattr(self._mw, "_raw", None) is not None:
            self._mw._display()
        if callable(self._on_saved):
            self._on_saved()
        self._status.setText("Enregistré + plugins rechargés.")

    def _browse_csv(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Choisir un CSV pour ce graphe", "", "CSV (*.csv)")
        if not path:
            return
        self.graph.csv_path = path
        self._refresh_csv_columns()

    def _find_folder_csv(self):
        """Premier .csv trouve dans le dossier du plugin -- sert d'apercu pour
        peupler les listes deroulantes de colonnes SANS forcer un csv_path en
        dur dans graph.json (les plugins livres gardent csv_path=None et
        recoivent leur CSV par glisser-depose a l'execution)."""
        try:
            for entry in sorted(os.listdir(self.folder)):
                if entry.lower().endswith(".csv"):
                    return os.path.join(self.folder, entry)
        except OSError:
            pass
        return None

    def _effective_csv_path(self):
        """CSV a utiliser pour peupler les colonnes dans l'editeur : le
        csv_path du graphe s'il existe, sinon un CSV voisin du plugin."""
        if self.graph.csv_path and os.path.isfile(self.graph.csv_path):
            return self.graph.csv_path
        return self._find_folder_csv()

    def _refresh_csv_columns(self):
        path = self._effective_csv_path()
        if not path:
            return
        import csv as _csv
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            header = next(_csv.reader(f), [])
        self._frame_col_combo.clear()
        self._frame_col_combo.addItems(header)
        if self.graph.frame_column in header:
            self._frame_col_combo.setCurrentText(self.graph.frame_column)
        for item in self.node_items.values():
            if item.node_type.key == "csv_column":
                item.set_column_options(header)
        origin = "" if self.graph.csv_path else " (aperçu du dossier)"
        self._status.setText(f"Colonnes CSV : {os.path.basename(path)}{origin} "
                             f"-- {len(header)} colonne(s) proposée(s).")

    def _test_current_frame(self):
        self._save()
        idx = self._mw.cur if getattr(self._mw, "source", None) is not None else 0
        frame_rows = []
        csv_path = self._effective_csv_path()
        if csv_path:
            import csv as _csv
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(_csv.DictReader(f))
            frame_col = self.graph.frame_column or (next(iter(rows[0])) if rows else None)
            frame_rows = [r for r in rows if str(r.get(frame_col, "")).strip()
                         and int(float(r[frame_col])) == idx]
        try:
            shapes = evaluate_graph_for_frame(self.graph, frame_rows, idx)
        except Exception as ex:
            QtWidgets.QMessageBox.warning(self, "Test du graphe", f"Erreur : {ex}")
            return
        QtWidgets.QMessageBox.information(
            self, "Test du graphe",
            f"Frame {idx} -- {len(frame_rows)} ligne(s) CSV -> {len(shapes)} forme(s) :\n\n"
            + json.dumps(shapes, indent=1, ensure_ascii=False)[:3000])

    # ---------- construction du canvas ----------
    def add_node(self, type_key, node_id=None, params=None, pos=None):
        nt = REGISTRY[type_key]
        nid = node_id or f"{type_key}_{len(self.graph.nodes) + 1}"
        while nid in self.graph.nodes:
            nid = f"{nid}_"
        params = dict(params or {})
        pos = pos or [40 + 30 * len(self.node_items), 40]
        self.graph.nodes[nid] = {"type": type_key, "params": params, "pos": pos}
        item = NodeItem(nid, nt, params)
        item.setPos(pos[0], pos[1])
        item.on_moved = self._on_node_moved
        self.scene.addItem(item)
        self.node_items[nid] = item
        if nt.key == "csv_column" and self.graph.csv_path:
            self._refresh_csv_columns()
        return item

    def add_edge(self, src_id, src_port, dst_id, dst_port):
        src_item, dst_item = self.node_items.get(src_id), self.node_items.get(dst_id)
        if src_item is None or dst_item is None:
            return
        dst_nt = dst_item.node_type
        if dst_nt.variadic_input != dst_port:
            # port normal : une seule connexion entrante -- retire l'ancienne
            for e in list(self.edge_items):
                if e.dst_port.node_item.node_id == dst_id and e.dst_port.port_name == dst_port:
                    self._remove_edge(e)
        self.graph.edges.append((src_id, src_port, dst_id, dst_port))
        edge = EdgeItem(src_item.output_ports[src_port], dst_item.input_ports[dst_port])
        self.scene.addItem(edge)
        self.edge_items.append(edge)
        self._status.setText(f"Relié : {src_id}.{src_port} -> {dst_id}.{dst_port}")

    def _on_node_moved(self, node_item):
        for e in self.edge_items:
            if e.src_port.node_item is node_item or e.dst_port.node_item is node_item:
                e.update_line()

    def _remove_edge(self, edge):
        self.scene.removeItem(edge)
        self.edge_items.remove(edge)
        tup = (edge.src_port.node_item.node_id, edge.src_port.port_name,
              edge.dst_port.node_item.node_id, edge.dst_port.port_name)
        if tup in self.graph.edges:
            self.graph.edges.remove(tup)

    def _remove_node(self, node_item):
        for e in [e for e in self.edge_items
                 if e.src_port.node_item is node_item or e.dst_port.node_item is node_item]:
            self._remove_edge(e)
        self.scene.removeItem(node_item)
        self.node_items.pop(node_item.node_id, None)
        self.graph.nodes.pop(node_item.node_id, None)

    def _delete_selected(self):
        for it in list(self.scene.selectedItems()):
            if isinstance(it, NodeItem):
                self._remove_node(it)
            elif isinstance(it, EdgeItem):
                self._remove_edge(it)

    def _rebuild_from_graph(self):
        for nid, node in list(self.graph.nodes.items()):
            nt = REGISTRY.get(node["type"])
            if nt is None:
                continue
            item = NodeItem(nid, nt, node.get("params", {}))
            pos = node.get("pos") or [40, 40]
            item.setPos(pos[0], pos[1])
            item.on_moved = self._on_node_moved
            self.scene.addItem(item)
            self.node_items[nid] = item
        for src_id, src_port, dst_id, dst_port in list(self.graph.edges):
            src_item, dst_item = self.node_items.get(src_id), self.node_items.get(dst_id)
            if src_item is None or dst_item is None:
                continue
            sp = src_item.output_ports.get(src_port)
            dp = dst_item.input_ports.get(dst_port)
            if sp is None or dp is None:
                continue
            edge = EdgeItem(sp, dp)
            self.scene.addItem(edge)
            self.edge_items.append(edge)
        if self.graph.csv_path:
            self._refresh_csv_columns()


class NodeGraphEditorWindow(QtWidgets.QWidget):
    """Fenetre autonome NON MODALE hebergeant l'editeur -- ne bloque pas
    l'application (on peut naviguer dans la sequence pendant l'edition)."""

    def __init__(self, mw, plugin_folder, on_saved=None):
        super().__init__(None)
        self.setWindowFlag(QtCore.Qt.Window, True)
        self.setWindowTitle(f"Éditeur de graphe -- {os.path.basename(plugin_folder)}")
        self.resize(1040, 680)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.editor = NodeGraphEditorWidget(mw, plugin_folder, on_saved=on_saved, parent=self)
        lay.addWidget(self.editor)


def open_node_graph_editor(mw, plugin_folder, on_saved=None):
    """Ouvre (ou ramene au premier plan) une fenetre d'editeur NON MODALE
    pour ce dossier plugin. Une seule fenetre par dossier -- rouvrir depuis
    Parametres/Plugins ne cree pas de doublon."""
    windows = getattr(mw, "_graph_editor_windows", None)
    if windows is None:
        windows = mw._graph_editor_windows = {}
    key = os.path.normpath(plugin_folder)
    existing = windows.get(key)
    if existing is not None:
        try:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return existing
        except RuntimeError:
            windows.pop(key, None)   # fenetre Qt deja detruite
    win = NodeGraphEditorWindow(mw, plugin_folder, on_saved=on_saved)
    win.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
    win.destroyed.connect(lambda *_: windows.pop(key, None))
    windows[key] = win
    win.show()
    win.raise_()
    win.activateWindow()
    return win
