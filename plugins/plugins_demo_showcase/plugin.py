# -*- coding: utf-8 -*-
"""Demo showcase -- un seul plugin qui exerce TOUS les hooks du contrat, avec
un exemple minimal et commente pour chacun. Aucune lib externe (uniquement cv2 +
numpy du bundle) et aucune dependance a dcp_utils : ce fichier est autonome, on
peut le copier ailleurs tel quel.

Ou va quoi (rappel) :
  - render_overlay  -> SUR l'image (pleine, suit le zoom, part a l'export).
  - render_patch    -> dans un COIN de l'image (mini vignette, attribut `corner`).
  - render_panel    -> image dans la CARTE du plugin (onglet Plugins), suit la frame.
  - overlay_elements-> cases On/Off dans la CARTE du plugin (lues par element_enabled).
  - get_overlays    -> marqueurs dessines par le moteur SIDECAR (live + export).
  - menu_actions    -> boutons "Actions" dans le corps de la carte du plugin.
  - build_panel     -> QWidget (slider/boutons) ouvert en DOCK via le bouton
                       "Ouvrir le panneau (dock)" de la carte.
  - accepts_drop/on_drop -> recevoir un CSV glisse-depose.
  - on_view_hover/click/key -> interaction souris/clavier sur la vue.

Coordonnees : le CSV d'exemple stocke des FRACTIONS (0..1) de l'image, converties
en pixels affiches dans render_overlay. Ainsi tout reste aligne quel que soit le
zoom, et le survol/clic (donnes en coordonnees IMAGE) sont ramenes en fractions
via api.image_size(), donc coherents avec le dessin.
"""
import csv
import os

import cv2
import numpy as np

from frameviewer.plugins.api import FrameViewerPlugin   # ###REQUIS### (classe mere)

# couleurs BGRA (overlay) et BGR (patch/panel)
_GREEN = (60, 220, 60)
_YELLOW = (0, 255, 255)
_CYAN = (255, 220, 0)
_MAGENTA = (255, 0, 255)
_GREY = (90, 90, 90)
_WHITE = (255, 255, 255)


class DemoShowcasePlugin(FrameViewerPlugin):   # ###REQUIS### sous-classe
    name = "demo_showcase"                      # ###REQUIS### identifiant stable
    description = "Exemple qui montre tous les hooks (overlay, patch, panel, dock, drop, souris)."
    corner = "tr"    # coin d'incrustation du render_patch : tl|tr|bl|br
    margin = 8

    # ###REQUIS (en pratique)### init de l'etat.
    def on_load(self, api):
        # Entree declaree dans manifest.json : le viewer injecte uniquement le
        # chemin explicitement depose par l'utilisateur. La demo ne charge
        # jamais silencieusement un fichier par defaut.
        self.boxes = None
        self._rows_cache = None     # {frame:int -> [dict, ...]}
        self._cache_sig = None      # (chemin, mtime) du CSV deja lu
        # etat interactif
        self._hover = None          # id de la boite survolee
        self._selected = None       # id de la boite selectionnee au clic
        self._glow = False          # touche 'g'
        self._show_test = False     # touche 't'
        self._alpha = 100           # opacite overlay 0..100 (touches +/-)
        self._fill_alpha = 18       # remplissage des boites 0..100
        self._box_thickness = 2
        self._grid_thickness = 2
        self._boxes_px = []         # [(id, x1,y1,x2,y2)] en pixels affiches (hit-test)
        api.log("demo_showcase charge : depose le CSV dans l'entree boxes", level="ok")

    def on_unload(self, api):
        # rien a liberer ici ; on montre juste que le hook existe.
        self._rows_cache = None

    # ---------------------------------------------------------------- cases On/Off
    # ###REQUIS pour les toggles### -> une case par element dans la carte plugin.
    def overlay_elements(self, api):
        return [
            ("bbox", "Boites"),
            ("label", "Labels"),
            ("grid", "Grille"),
            ("sidecar_mark", "Marqueur SIDECAR (get_overlays)"),
        ]

    # ---------------------------------------------------------------- overlay image
    # ###REQUIS pour dessiner SUR l'image### canvas BGRA pleine image.
    def render_overlay(self, api, frame_idx, size):
        if self._csv_path() is None:
            return None
        w, h = int(size[0]), int(size[1])
        if w <= 0 or h <= 0:
            return None
        canvas = np.zeros((h, w, 4), np.uint8)
        drew = False
        self._boxes_px = []

        if api.element_enabled("grid"):
            self._draw_grid(canvas, w, h)
            drew = True

        if api.element_enabled("bbox"):
            for r in self._frame_rows(frame_idx):
                x1, y1 = int(r["xf"] * w), int(r["yf"] * h)
                x2, y2 = int((r["xf"] + r["wf"]) * w), int((r["yf"] + r["hf"]) * h)
                self._boxes_px.append((r["id"], x1, y1, x2, y2))
                sel = (r["id"] == self._selected)
                hov = (r["id"] == self._hover)
                color = _YELLOW if sel else (_CYAN if hov else _GREEN)
                thick = self._box_thickness + (1 if (sel or hov) else 0)
                if self._fill_alpha > 0:
                    cv2.rectangle(canvas, (x1, y1), (x2, y2),
                                  (*color, round(255 * self._fill_alpha / 100)), -1)
                if self._glow and (sel or hov):
                    cv2.rectangle(canvas, (x1 - 3, y1 - 3), (x2 + 3, y2 + 3),
                                  (*color, 60), thick + 6)
                cv2.rectangle(canvas, (x1, y1), (x2, y2), (*color, 255), thick)
                if api.element_enabled("label"):
                    cv2.putText(canvas, r["label"], (x1, max(12, y1 - 5)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (*_WHITE, 255), 1, cv2.LINE_AA)
                drew = True

        if self._show_test:
            cv2.putText(canvas, "TEST (touche t)", (10, h - 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (*_MAGENTA, 255), 2, cv2.LINE_AA)
            drew = True

        if not drew:
            return None
        # opacite globale pilotee par les touches +/- (voir on_view_key).
        if self._alpha < 100:
            canvas[:, :, 3] = (canvas[:, :, 3].astype(np.uint16) * self._alpha // 100).astype(np.uint8)
        return canvas

    # ---------------------------------------------------------------- vignette coin
    # mini image BGR incrustee dans le coin `corner` de la vue.
    def render_patch(self, api, frame_idx, size):
        if self._csv_path() is None:
            return None
        pw, ph = 170, 64
        patch = np.full((ph, pw, 3), 30, np.uint8)
        cv2.rectangle(patch, (0, 0), (pw - 1, ph - 1), _GREEN, 1)
        n = len(self._frame_rows(frame_idx))
        cv2.putText(patch, f"frame {frame_idx}", (8, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, _WHITE, 1, cv2.LINE_AA)
        cv2.putText(patch, f"{n} boite(s)", (8, 48),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, _CYAN, 1, cv2.LINE_AA)
        return patch

    # ---------------------------------------------------------------- carte plugin
    # image BGR affichee DANS la carte du plugin (onglet Plugins), pas sur l'image.
    def render_panel(self, api, frame_idx, size):
        w = max(220, int(size[0]) if size and size[0] else 300)
        h = 180
        frame = api.displayed_frame(frame_idx)
        panel = np.full((h, w, 3), 24, np.uint8)
        n = len(self._frame_rows(frame_idx))
        if frame is not None and frame.size:
            fh, fw = frame.shape[:2]
            scale = min(w / fw, (h - 28) / fh)
            tw, th = max(1, int(fw * scale)), max(1, int(fh * scale))
            thumb = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)
            ox, oy = (w - tw) // 2, 28 + (h - 28 - th) // 2
            panel[oy:oy + th, ox:ox + tw] = thumb
            for row in self._frame_rows(frame_idx):
                x1 = ox + int(row["xf"] * tw); y1 = oy + int(row["yf"] * th)
                x2 = ox + int((row["xf"] + row["wf"]) * tw)
                y2 = oy + int((row["yf"] + row["hf"]) * th)
                cv2.rectangle(panel, (x1, y1), (x2, y2), _GREEN, 1)
        cv2.rectangle(panel, (0, 0), (w - 1, h - 1), _GREEN, 1)
        cv2.putText(panel, f"frame {frame_idx} - {n} vehicle(s)", (8, 19),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, _WHITE, 1, cv2.LINE_AA)
        return panel

    # ---------------------------------------------------------------- dock Qt
    # QWidget interactif ouvert en dock via le bouton "Ouvrir le panneau" de la
    # carte (build_panel est appele par l'UI, qui fait api.add_dock).
    def build_panel(self, api):
        from PySide6 import QtCore, QtWidgets
        box = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(box)
        def add_slider(label, lo, hi, value, setter, suffix=""):
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel(label), 1)
            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            slider.setRange(lo, hi); slider.setValue(value)
            slider.setObjectName("showcase_" + label.lower().replace(" ", "_"))
            value_label = QtWidgets.QLabel(f"{value}{suffix}")
            value_label.setFixedWidth(42)
            def changed(v):
                setter(int(v)); value_label.setText(f"{v}{suffix}")
                api.request_repaint()
            slider.valueChanged.connect(changed)
            row.addWidget(slider, 2); row.addWidget(value_label)
            lay.addLayout(row)
            return slider

        add_slider("Opacite", 0, 100, self._alpha,
                   lambda v: setattr(self, "_alpha", v), " %")
        add_slider("Remplissage", 0, 100, self._fill_alpha,
                   lambda v: setattr(self, "_fill_alpha", v), " %")
        add_slider("Trait boites", 1, 8, self._box_thickness,
                   lambda v: setattr(self, "_box_thickness", v))
        add_slider("Trait grille", 1, 8, self._grid_thickness,
                   lambda v: setattr(self, "_grid_thickness", v))
        btn = QtWidgets.QPushButton("Deselectionner")

        def on_reset():
            self._selected = None
            api.request_repaint()
        btn.clicked.connect(on_reset)
        lay.addWidget(btn)
        lay.addStretch(1)
        api.add_dock(box, "Showcase", area="right")
        return box

    # ---------------------------------------------------------------- actions
    def menu_actions(self, api):
        def reload_data():
            self._rows_cache = None
            self._cache_sig = None
            api.log("data rechargee", level="ok")
            api.request_repaint()
        return [("Recharger le CSV", reload_data)]

    # ---------------------------------------------------------------- overlays SIDECAR
    # marqueurs dessines par le moteur SIDECAR existant (live ET export).
    def get_overlays(self, api, frame_idx):
        if self._csv_path() is None or not api.element_enabled("sidecar_mark"):
            return []
        wimg, himg = api.image_size()
        if wimg <= 0 or himg <= 0:
            return []
        cx, cy = wimg // 2, himg // 2
        return [{
            "type": "croix",
            "points": [(cx, cy)],
            "color": (255, 0, 255),
            "thickness": 2,
            "text": {"label": "centre", "x": 8, "y": 8, "size": 14},
        }]

    # ---------------------------------------------------------------- drag & drop
    def accepts_drop(self, api, path):
        return str(path).lower().endswith(".boxes.csv")

    def on_drop(self, api, path):
        self.boxes = path
        self._rows_cache = None
        self._cache_sig = None
        api.log(f"CSV rattache : {os.path.basename(path)}", level="ok")
        api.request_repaint()

    # ---------------------------------------------------------------- interaction
    def on_view_hover(self, api, frame_idx, x, y):
        new = self._box_at(api, frame_idx, x, y)
        if new != self._hover:
            self._hover = new
            api.request_repaint()

    def on_view_click(self, api, frame_idx, x, y):
        self._selected = self._box_at(api, frame_idx, x, y)
        api.status(f"selection: {self._selected}")
        api.request_repaint()

    def on_view_key(self, api, frame_idx, key, text):
        t = (text or "").lower()
        if t == "g":
            self._glow = not self._glow
            api.request_repaint()
            return True
        if t == "t":
            self._show_test = not self._show_test
            api.request_repaint()
            return True
        if t in ("+", "="):
            self._alpha = min(100, self._alpha + 10)
            api.request_repaint()
            return True
        if t in ("-", "_"):
            self._alpha = max(0, self._alpha - 10)
            api.request_repaint()
            return True
        return False    # touche non geree -> suit son chemin normal

    # ---------------------------------------------------------------- utilitaires
    def _csv_path(self):
        p = self.boxes
        if p and os.path.isfile(p):
            return p
        return None

    def _load(self):
        path = self._csv_path()
        if not path:
            self._rows_cache = {}
            return
        sig = (path, os.path.getmtime(path))
        if sig == self._cache_sig and self._rows_cache is not None:
            return
        by_frame = {}
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                try:
                    fr = int(float(row["frame"]))
                    rec = {
                        "id": int(float(row["id"])),
                        "xf": float(row["xf"]), "yf": float(row["yf"]),
                        "wf": float(row["wf"]), "hf": float(row["hf"]),
                        "label": row.get("label", ""),
                    }
                except (KeyError, ValueError):
                    continue
                by_frame.setdefault(fr, []).append(rec)
        self._rows_cache = by_frame
        self._cache_sig = sig

    def _frame_rows(self, frame_idx):
        self._load()   # no-op si deja en cache (compare chemin + mtime)
        return (self._rows_cache or {}).get(int(frame_idx), [])

    def _draw_grid(self, canvas, w, h, step=80):
        for gx in range(step, w, step):
            cv2.line(canvas, (gx, 0), (gx, h), (*_GREY, 120), self._grid_thickness)
        for gy in range(step, h, step):
            cv2.line(canvas, (0, gy), (w, gy), (*_GREY, 120), self._grid_thickness)

    def _box_at(self, api, frame_idx, x, y):
        # (x, y) sont en coordonnees IMAGE ; les boites sont stockees en fractions
        # -> on ramene le clic en fraction via image_size, meme espace = coherent
        # quel que soit le zoom.
        wimg, himg = api.image_size()
        if wimg <= 0 or himg <= 0:
            return None
        fx, fy = x / wimg, y / himg
        for r in self._frame_rows(frame_idx):
            if r["xf"] <= fx <= r["xf"] + r["wf"] and r["yf"] <= fy <= r["yf"] + r["hf"]:
                return r["id"]
        return None


PLUGIN = DemoShowcasePlugin   # ###REQUIS### expose la classe au loader
