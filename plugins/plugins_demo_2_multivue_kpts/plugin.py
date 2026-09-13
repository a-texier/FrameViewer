# -*- coding: utf-8 -*-
"""DEMO inter-connexion inter-vues -- 2 vues (gauche/droite ou haut/bas).

Relie des points appartes ENTRE DEUX VUES : un rond sur chaque vue + un trait
qui traverse les deux viewports, meme code couleur par piste. Marche a
l'identique en :
  - cote a cote (h2) et mode Temporel (i-N | i) : vue A = gauche, vue B = droite ;
  - empile (v2) : vue A = haut, vue B = bas.

FORMAT du hook get_multiview_overlays (entree `views`) : reference complete dans
FrameViewerPlugin.get_multiview_overlays (frameviewer/plugins/api.py).

FORMAT DE SORTIE (rappel concret) : le hook renvoie une LISTE de dicts ; chaque
dict est une forme (JSON simple : valeurs int / float / tuple). [] = rien.
  # forme ciblant UNE vue -> clef "view" = index de slot (0, 1, ...)
  {"type":"rectangle","view":0,"points":[(x1,y1),(x2,y2)],"color":(r,g,b),"thickness":2}
  {"type":"cercle","view":0,"points":[(x,y)],"a":rayon_px,"color":(r,g,b)}
  {"type":"points","view":0,"points":[(x,y),...],"color":(r,g,b)}
  {"type":"polygone"|"ligne_brisee"|"croix","view":0,"points":[...],"color":(r,g,b)}
  # LIEN entre deux vues -> va/vb = index de slot, pa/pb = points
  {"type":"segment_inter_vues","va":0,"pa":(x,y),"vb":1,"pb":(x,y),"color":(r,g,b),"thickness":2}
Coordonnees en PIXELS IMAGE ; color = (r,g,b) 0..255 ; slot absent -> ignore.
Ici on ne decrit que la LOGIQUE de CE plugin.

Logique : on prend les DEUX premieres vues (views[0] = A, views[1] = B) et on
emet, par paire, un rond sur chaque vue + un lien inter-vues (helper _pair_specs,
simple confort -- tu peux renvoyer n'importe quelles formes a la place).

A quoi sert `mode` ici : UNIQUEMENT a choisir quels NOMS DE COLONNES portent la
frame de chaque vue (gauche/droite vs haut/bas). Le plugin reste positionnel
(A = 1re vue, B = 2e) ; il ne depend pas du mode autrement. Un plugin peut tres
bien ignorer le mode (cf. plugins_demo_multivue_2csv).

Adapter a votre CSV SANS le reformater : editez le dictionnaire COLS ci-dessous
(roles -> vos noms de colonnes, par mode). Colonne `id` optionnelle (couleur).
Le CSV reel pre-calcule doit etre depose explicitement dans l'entree
`pairs_csv`. Sans CSV valide, le plugin ne dessine rien.
"""
import os
import csv

import cv2
import numpy as np

from frameviewer.plugins.api import FrameViewerPlugin

# palette RGB (l'app lit color=(r,g,b)).
_PALETTE = [
    (255, 90, 90), (90, 200, 255), (120, 230, 120), (255, 200, 70),
    (210, 130, 255), (255, 150, 200), (150, 255, 220), (255, 255, 120),
]

# >>> EDITEZ ICI pour coller a votre CSV (roles -> noms de colonnes) <<<
#   fa/fb = colonnes de frame des vues A / B ; xa..yb = coordonnees image.
COLS = {
    "h2":       {"fa": "frame_left", "fb": "frame_right",
                 "xa": "xL", "ya": "yL", "xb": "xR", "yb": "yR"},
    "temporal": {"fa": "frame_left", "fb": "frame_right",
                 "xa": "xL", "ya": "yL", "xb": "xR", "yb": "yR"},
    "v2":       {"fa": "frame_top",  "fb": "frame_bottom",
                 "xa": "xT", "ya": "yT", "xb": "xB", "yb": "yB"},
}
ID_COL = "id"        # optionnel (code couleur) ; mettre None pour l'ignorer
CONFIDENCE_COL = "confidence"


def _to_int(v):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def _to_float(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


class Demo2MultivueKptsPlugin(FrameViewerPlugin):
    name = "demo_2_multivue_kpts"
    description = ("Demo : relie des keypoints appartes entre 2 vues "
                   "(gauche/droite, temporel i-N|i, ou haut/bas).")

    def on_load(self, api):
        """Init de l'etat. IN: api (PluginAPI, non utilise ici). OUT: None.
        `self.pairs_csv` (chemin du CSV depose) est injecte par le loader avant
        chaque appel de hook (None si rien de depose)."""
        self._cache_key = None    # (chemin, mtime) du CSV deja parse
        self._rows = []           # lignes brutes du CSV (list[dict])
        self._data_status = "CSV non charge"
        self._min_confidence = 0.70
        self._radius = 11
        self._thickness = 2
        self._scale_by_confidence = True
        self._score_colormap = True
        self._score_scope = "frame"
        self._frame_match_count = 0
        self._active_score_bounds = (0.0, 0.5, 1.0)
        self._panel_status_label = None
        self._panel_match_label = None
        self._panel_colorbar = None

    def _track_color(self, pid, fallback_idx=0):
        """Couleur RGB stable pour une piste. IN: pid (str|None, id de piste),
        fallback_idx (int, si pas d'id). OUT: (r,g,b)."""
        if pid is None or pid == "":
            return _PALETTE[fallback_idx % len(_PALETTE)]
        return _PALETTE[hash(str(pid)) % len(_PALETTE)]

    @staticmethod
    def _score_color(normalized):
        """Coolwarm_r lisible : faible=rouge, median=clair, fort=bleu."""
        value = max(0.0, min(1.0, float(normalized)))
        red, middle, blue = (220, 55, 55), (238, 232, 220), (55, 120, 225)
        if value <= 0.5:
            alpha = value * 2.0
            return tuple(int(round(red[i] + alpha * (middle[i] - red[i]))) for i in range(3))
        alpha = (value - 0.5) * 2.0
        return tuple(int(round(middle[i] + alpha * (blue[i] - middle[i]))) for i in range(3))

    @staticmethod
    def _bounds(values):
        valid = [float(value) for value in values if value is not None]
        if not valid:
            return 0.0, 0.5, 1.0
        lo, hi = min(valid), max(valid)
        median = float(np.median(np.asarray(valid, dtype=np.float64)))
        if hi <= lo:
            pad = max(0.001, abs(lo) * 0.01)
            return lo - pad, median, hi + pad
        return lo, median, hi

    @staticmethod
    def _normalize_score(confidence, bounds):
        lo, median, hi = bounds
        value = float(confidence)
        if value <= median:
            normalized = 0.5 if median <= lo else 0.5 * (value - lo) / (median - lo)
        else:
            normalized = 0.5 if hi <= median else 0.5 + 0.5 * (value - median) / (hi - median)
        return max(0.0, min(1.0, normalized))

    def _pair_specs(self, ia, a, ib, b, col, normalized=0.5):
        """Formes d'UNE paire appariee (helper de confort, non impose).
        IN: ia/ib (int, index de slot des vues A/B), a/b ((x,y) en pixels image),
        col ((r,g,b)). OUT: list[dict] = rond sur A + rond sur B + lien A-B."""
        radius = float(self._radius)
        if self._scale_by_confidence:
            # A score normalise de 0.5 conserve exactement la taille choisie.
            radius *= 0.55 + 0.90 * max(0.0, min(1.0, normalized))
        return [
            {"type": "cercle", "view": ia, "points": [a],
             "a": radius, "color": col, "thickness": self._thickness},
            {"type": "cercle", "view": ib, "points": [b],
             "a": radius, "color": col, "thickness": self._thickness},
            {"type": "segment_inter_vues", "va": ia, "pa": a,
             "vb": ib, "pb": b, "color": col, "thickness": self._thickness},
        ]

    def _ensure_rows(self):
        """Charge/rafraichit le CSV depose (memoise sur chemin+mtime).
        IN: - (lit self.pairs_csv). OUT: None (remplit self._rows: list[dict])."""
        supplied = getattr(self, "pairs_csv", None)
        path = supplied if supplied and os.path.isfile(supplied) else None
        if not path or not os.path.isfile(path):
            self._rows, self._cache_key = [], None
            self._data_status = "CSV absent"
            return
        key = (path, os.path.getmtime(path))
        if key == self._cache_key:
            return
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fields = set(reader.fieldnames or [])
            required = {"frame_left", "frame_right", "frame_top", "frame_bottom",
                        "xL", "yL", "xR", "yR", "xT", "yT", "xB", "yB",
                        "confidence"}
            if not required.issubset(fields):
                self._rows = []
                self._data_status = "CSV invalide"
            else:
                self._rows = list(reader)
                self._data_status = f"CSV reel : {len(self._rows)} points"
        self._cache_key = key
        self._update_panel_status()

    def _matching_pairs(self, mode, A, B):
        """Retourne les paires valides de la paire de frames courante."""
        m = COLS.get(mode) or COLS["h2"]
        fa, fb = A.get("frame_idx"), B.get("frame_idx")
        if fa is None or fb is None:
            return []
        pairs = []
        for index, row in enumerate(self._rows):
            if _to_int(row.get(m["fa"])) != fa or _to_int(row.get(m["fb"])) != fb:
                continue
            coords = tuple(_to_float(row.get(m[key])) for key in ("xa", "ya", "xb", "yb"))
            if any(value is None for value in coords):
                continue
            confidence = _to_float(row.get(CONFIDENCE_COL))
            confidence = 1.0 if confidence is None else confidence
            if confidence < self._min_confidence:
                continue
            pairs.append((index, row, coords, confidence))
        return pairs

    def _csv_specs(self, mode, A, B):
        """Formes depuis le CSV reel, pour les frames courantes des 2 vues.
        IN: mode (str, choisit les colonnes via COLS), A/B (dict de vue du
        contexte : index + frame_idx). OUT: list[dict]. On garde les lignes ou
        la colonne frame de A == frame courante de A ET idem pour B, puis on
        emet une paire (2 ronds + lien) par ligne, couleur par `id`."""
        pairs = self._matching_pairs(mode, A, B)
        if self._score_scope == "csv":
            bounds = self._bounds(_to_float(row.get(CONFIDENCE_COL)) for row in self._rows)
        else:
            bounds = self._bounds(pair[3] for pair in pairs)
        self._frame_match_count = len(pairs)
        self._active_score_bounds = bounds
        specs = []
        for i, row, (xa, ya, xb, yb), confidence in pairs:
            pid = row.get(ID_COL) if ID_COL else None
            normalized = self._normalize_score(confidence, bounds)
            color = (self._score_color(normalized) if self._score_colormap
                     else self._track_color(pid, i))
            specs += self._pair_specs(A["index"], (xa, ya),
                                      B["index"], (xb, yb),
                                      color, normalized)
        specs.append({
            "type": "texte_ecran", "view": A["index"],
            "label": f"{len(pairs)} keypoints apparies",
            "color": (245, 245, 245), "background": (20, 20, 25, 205),
        })
        self._update_panel_status()
        return specs

    def get_multiview_overlays(self, api, views):
        """Hook multivue (voir le format canonique dans api.py).
        IN: api (PluginAPI), views (dict contexte multivue : mode, count, views
        [index/role/view_key/source_name/frame_idx/w/h]). OUT: list[dict] de
        formes inter-vues. On agit sur les 2 premieres vues (A=gauche/haut,
        B=droite/bas) ; les donnees proviennent toujours d'un CSV reel."""
        vlist = (views or {}).get("views", [])
        if len(vlist) < 2:
            return []
        A, B = vlist[0], vlist[1]
        mode = (views or {}).get("mode", "h2")
        self._ensure_rows()
        if not self._rows:
            self._frame_match_count = 0
            self._update_panel_status()
            return []
        return self._csv_specs(mode, A, B)

    def render_panel(self, api, frame_idx, size):
        width = max(220, int(size[0]) if size and size[0] else 280)
        panel = np.full((104, width, 3), 25, np.uint8)
        self._ensure_rows()
        status = self._data_status
        cv2.putText(panel, status, (10, 25), cv2.FONT_HERSHEY_SIMPLEX,
                    0.52, (120, 230, 120) if self._rows else (80, 80, 230), 1, cv2.LINE_AA)
        cv2.putText(panel, f"confiance >= {self._min_confidence:.2f}", (10, 54),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, (230, 230, 230), 1, cv2.LINE_AA)
        cv2.putText(panel, f"frame : {self._frame_match_count} appariements", (10, 82),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, (230, 230, 230), 1, cv2.LINE_AA)
        return panel

    def _update_panel_status(self):
        path = getattr(self, "pairs_csv", None)
        try:
            if self._panel_status_label is not None:
                name = os.path.basename(path) if path else "aucun CSV"
                self._panel_status_label.setText(
                    f"CSV reel : {name}\n{len(self._rows)} lignes au total")
                self._panel_status_label.setToolTip(path or "")
            if self._panel_match_label is not None:
                self._panel_match_label.setText(
                    f"Frame courante : {self._frame_match_count} correspondances visibles")
            if self._panel_colorbar is not None:
                lo, median, hi = self._active_score_bounds
                scope = "frame" if self._score_scope == "frame" else "CSV complet"
                self._panel_colorbar.setText(
                    f"{lo:.3f}   mediane {median:.3f}   {hi:.3f}\n{scope}")
                self._panel_colorbar.setStyleSheet(
                    "QLabel{color:#17171c;font-weight:bold;padding:4px;"
                    "border:1px solid #555;border-radius:3px;"
                    "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                    "stop:0 rgb(220,55,55),stop:0.5 rgb(238,232,220),"
                    "stop:1 rgb(55,120,225));}")
        except RuntimeError:
            self._panel_status_label = None
            self._panel_match_label = None
            self._panel_colorbar = None

    def build_panel(self, api):
        from PySide6 import QtCore, QtWidgets
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QFormLayout(widget)
        threshold = QtWidgets.QDoubleSpinBox()
        threshold.setObjectName("multiview_confidence")
        threshold.setRange(0.0, 1.0); threshold.setSingleStep(0.05)
        threshold.setValue(self._min_confidence)
        radius = QtWidgets.QSpinBox(); radius.setObjectName("multiview_radius")
        radius.setRange(2, 20); radius.setValue(self._radius)
        thickness = QtWidgets.QSpinBox(); thickness.setObjectName("multiview_thickness")
        thickness.setRange(1, 8); thickness.setValue(self._thickness)
        scale = QtWidgets.QCheckBox("Taille proportionnelle a la confiance")
        scale.setObjectName("multiview_scale_confidence")
        scale.setChecked(self._scale_by_confidence)
        colormap = QtWidgets.QCheckBox("Coolwarm_r : faible rouge, fort bleu")
        colormap.setObjectName("multiview_score_colormap")
        colormap.setChecked(self._score_colormap)
        scope = QtWidgets.QComboBox()
        scope.setObjectName("multiview_score_scope")
        scope.addItem("Adaptatif a la frame", "frame")
        scope.addItem("Absolu sur tout le CSV", "csv")
        scope.setCurrentIndex(max(0, scope.findData(self._score_scope)))
        threshold.valueChanged.connect(
            lambda value: (setattr(self, "_min_confidence", float(value)), api.request_repaint()))
        radius.valueChanged.connect(
            lambda value: (setattr(self, "_radius", int(value)), api.request_repaint()))
        thickness.valueChanged.connect(
            lambda value: (setattr(self, "_thickness", int(value)), api.request_repaint()))
        scale.toggled.connect(
            lambda value: (setattr(self, "_scale_by_confidence", bool(value)), api.request_repaint()))
        colormap.toggled.connect(
            lambda value: (setattr(self, "_score_colormap", bool(value)), api.request_repaint()))
        scope.currentIndexChanged.connect(
            lambda _index: (setattr(self, "_score_scope", scope.currentData()),
                            api.request_repaint()))
        self._panel_status_label = QtWidgets.QLabel()
        self._panel_status_label.setWordWrap(True)
        self._panel_status_label.setObjectName("multiview_csv_status")
        self._panel_match_label = QtWidgets.QLabel()
        self._panel_match_label.setObjectName("multiview_match_count")
        self._panel_colorbar = QtWidgets.QLabel()
        self._panel_colorbar.setObjectName("multiview_score_colorbar")
        self._panel_colorbar.setAlignment(QtCore.Qt.AlignCenter)
        self._panel_colorbar.setMinimumHeight(40)
        layout.addRow("Confiance minimale", threshold)
        layout.addRow("Taille des points", radius)
        layout.addRow("Epaisseur des liens", thickness)
        layout.addRow(scale)
        layout.addRow(colormap)
        layout.addRow("Normalisation taille/couleur", scope)
        layout.addRow(self._panel_status_label)
        layout.addRow(self._panel_match_label)
        layout.addRow(self._panel_colorbar)
        self._ensure_rows()
        self._update_panel_status()
        api.add_dock(widget, "Correspondances multivues", area="right")
        return widget


PLUGIN = Demo2MultivueKptsPlugin
