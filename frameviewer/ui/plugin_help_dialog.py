# -*- coding: utf-8 -*-
"""Aide plugin (bouton ? de l'onglet Plugins) : recap detaille des hooks (ce que
TON plugin peut definir) et des objets api.xxx (ce que ton code peut appeler),
groupes par theme colore, le strict REQUIS en vert, avec pour chaque element : a
quoi ca sert + un exemple ultra simple. Complementaire de l'editeur (qui, lui,
insere le code)."""
from PySide6 import QtWidgets

# (titre_section, couleur, [(signature, a_quoi_ca_sert, exemple), ...])
# La 1ere section est le REQUIS (vert).
_SECTIONS = [
    ("REQUIS (le minimum d'un plugin)", "#3ad07a", True, [
        ("class MaClasse(FrameViewerPlugin)",
         "Un plugin = une sous-classe de FrameViewerPlugin.",
         "class MaClasse(FrameViewerPlugin):"),
        ("name = \"mon_id\"",
         "Identifiant lisible, affiche dans l'onglet Plugins.",
         "name = \"bbox_viewer\""),
        ("PLUGIN = MaClasse",
         "Expose la classe au loader (en bas du fichier). Sans ca, rien.",
         "PLUGIN = MaClasse"),
    ]),
    ("Cycle de vie", "#5aafff", False, [
        ("on_load(self, api)",
         "Appele au chargement/rechargement : initialise ton etat ici.",
         "def on_load(self, api): self.rows = []"),
        ("on_unload(self, api)",
         "Avant un rechargement : libere ce que tu as ouvert.",
         "def on_unload(self, api): ..."),
    ]),
    ("Dessin (ce que le plugin affiche)", "#c58aff", False, [
        ("overlay_elements(self, api)",
         "Cases On/Off du plugin : liste de (cle, libelle).",
         "return [(\"bbox\", \"Boites\")]"),
        ("render_overlay(self, api, frame_idx, size)",
         "Overlay BGRA plein cadre de la frame courante, ou None.",
         "c = np.zeros((h, w, 4), np.uint8); return c"),
        ("render_panel(self, api, frame_idx, size)",
         "Image affichee DANS la carte du plugin (apercu), ou None.",
         "return mon_image_bgr"),
        ("get_overlays(self, api, frame_idx)",
         "Formes au schema SIDECAR (points/lignes...), dessinees live + export.",
         "return [{\"type\":\"points\",\"points\":[(x,y)]}]"),
        ("render_patch(self, api, frame_idx, size)",
         "Vignette BGR dans un coin (self.corner / self.margin).",
         "return vignette_bgr"),
    ]),
    ("Interactivite (souris / clavier)", "#ffb454", False, [
        ("on_view_hover(self, api, frame_idx, x, y)",
         "Survol en coords IMAGE : memorise puis api.request_repaint().",
         "self._h = idx; api.request_repaint()"),
        ("on_view_click(self, api, frame_idx, x, y)",
         "Clic en coords IMAGE : pose une selection persistante.",
         "self._sel = idx; api.request_repaint()"),
        ("on_view_key(self, api, frame_idx, key, text)",
         "Touche libre (l'appli garde fleches/espace/zoom). Renvoie True si traitee.",
         "if text == 'g': ...; return True"),
    ]),
    ("Glisser-deposer", "#8ad0c0", False, [
        ("accepts_drop(self, api, path)",
         "True si CE plugin veut traiter le fichier glisse sur la vue.",
         "return path.endswith('.csv')"),
        ("on_drop(self, api, path)",
         "Appele si accepts_drop a renvoye True.",
         "self.rows = api.read_csv_dict(path)"),
    ]),
    ("api : frame / sequence", "#7fb5ff", False, [
        ("api.current_frame_index()", "Numero de la frame affichee (0-based).", "i = api.current_frame_index()"),
        ("api.frame_count()", "Nombre de frames de la sequence.", "n = api.frame_count()"),
        ("api.image_size()", "(largeur, hauteur) de l'image.", "w, h = api.image_size()"),
        ("api.raw_frame(idx=None)", "Frame brute (ndarray natif), avant LUT.", "f = api.raw_frame()"),
        ("api.displayed_frame(idx=None)", "Frame BGR telle qu'affichee (LUT/filtres).", "f = api.displayed_frame()"),
        ("api.source_name()", "Nom de la source ouverte.", "s = api.source_name()"),
    ]),
    ("api : annotations existantes", "#7fb5ff", False, [
        ("api.ver_boxes(frame_idx=None)", "Boites .ver/YOLO visibles de la frame.", "for b in api.ver_boxes(): ..."),
        ("api.sidecar_graphs(frame_idx=None)", "Graphiques SIDECAR de la frame.", "g = api.sidecar_graphs()"),
    ]),
    ("api : fichiers / entrees", "#7fb5ff", False, [
        ("self.<nom_entree>", "Chemin du fichier depose sous l'entree <nom> (ou None).", "path = self.csv_data"),
        ("api.plugin_dir()", "Dossier du plugin (pour lire un fichier livre a cote).", "d = api.plugin_dir()"),
        ("api.read_csv_dict(path, **kw)", "Lit un CSV -> liste de dicts (stdlib).", "rows = api.read_csv_dict(p, delimiter=';')"),
        ("api.map_csv_columns(path, roles, ...)", "Demande a l'utilisateur d'associer les colonnes.", "m = api.map_csv_columns(p, ['x','y'])"),
    ]),
    ("api : interface / logs", "#7fb5ff", False, [
        ("api.log(msg, level=\"info|ok|warn|error\")", "Ecrit dans la Console (colore par plugin + niveau, avec heure et ligne).", "api.log('probleme', level='warn')"),
        ("api.status(msg)", "Message court dans la barre de statut.", "api.status('pret')"),
        ("api.request_repaint()", "Force un redessin de la vue (apres un changement d'etat).", "api.request_repaint()"),
        ("api.element_enabled(cle)", "Etat d'une case On/Off d'overlay_elements.", "if api.element_enabled('bbox'): ..."),
        ("api.add_dock(widget, titre)", "Ajoute un panneau lateral (dock).", "api.add_dock(w, 'Reglages')"),
        ("api.add_menu_action(label, cb)", "Ajoute un bouton dans la section Actions.", "api.add_menu_action('Go', self.go)"),
    ]),
    ("api : libs externes (pandas/polars)", "#ff8aa0", False, [
        ("api.external_import(mod, site_packages)", "Importe une lib d'un env externe (meme process).", "pd = api.external_import('pandas', sp)"),
        ("api.run_external(python_exe, code)", "Execute du code dans l'env externe (sous-process isole).", "out = api.run_external(py, code)"),
        ("api.env_report()", "Diagnostic : d'ou numpy/pandas/cv2 sont charges.", "api.log(api.env_report())"),
    ]),
]


def _build_html():
    parts = [
        "<style>"
        "h2{margin:14px 0 4px 0;padding:3px 8px;border-radius:4px;color:#111;font-size:13px;}"
        "code{font-family:Consolas,monospace;color:#cdd6e0;}"
        ".sig{font-family:Consolas,monospace;font-weight:bold;font-size:12px;}"
        ".role{color:#9ab;font-size:12px;}"
        ".ex{font-family:Consolas,monospace;color:#8fd18f;font-size:12px;}"
        "table{border-collapse:collapse;margin:0 0 6px 6px;}"
        "td{padding:2px 6px;vertical-align:top;}"
        "</style>"
    ]
    for title, color, required, items in _SECTIONS:
        parts.append(f"<h2 style='background:{color}'>{title}</h2>")
        sig_col = "#3ad07a" if required else "#e6ebf2"
        parts.append("<table>")
        for sig, role, ex in items:
            sig_h = sig.replace("<", "&lt;").replace(">", "&gt;")
            ex_h = ex.replace("<", "&lt;").replace(">", "&gt;")
            role_h = role.replace("<", "&lt;").replace(">", "&gt;")
            parts.append(
                "<tr>"
                f"<td><span class='sig' style='color:{sig_col}'>{sig_h}</span><br>"
                f"<span class='role'>{role_h}</span><br>"
                f"<span class='ex'>ex : {ex_h}</span></td>"
                "</tr>")
        parts.append("</table>")
    return "".join(parts)


class PluginHelpDialog(QtWidgets.QDialog):

    def __init__(self, mw):
        super().__init__(mw)
        self.setWindowTitle("Aide plugin : hooks et api")
        self.resize(660, 620)
        v = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "Vert = <b>REQUIS</b>. En dessous, les hooks a definir et les objets "
            "api.xxx a appeler, par theme. Chaque entree : a quoi ca sert + un "
            "exemple ultra simple. (Pour inserer du code, utilise l'editeur.)")
        intro.setWordWrap(True)
        v.addWidget(intro)
        browser = QtWidgets.QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setStyleSheet("QTextBrowser{background:#141414;border:1px solid #333;}")
        browser.setHtml(_build_html())
        v.addWidget(browser, 1)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        b = QtWidgets.QPushButton("Fermer")
        b.clicked.connect(self.accept)
        row.addWidget(b)
        v.addLayout(row)
