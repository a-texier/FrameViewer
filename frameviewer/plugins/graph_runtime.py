# -*- coding: utf-8 -*-
"""Pont entre le graphe de blocs (frameviewer/plugins/graph.py) et le
contrat de plugin (FrameViewerPlugin) -- un plugin "graphe" est un plugin
COMME UN AUTRE (meme dossier plugins_<nom>/, meme loader, memes hooks) :
seule sa logique vit dans un fichier graph.json au lieu d'etre ecrite a la
main. Le plugin.py genere par l'editeur de graphe
(ui/node_graph_editor.py) est un simple wrapper qui sous-classe
GraphPlugin -- voir docs/plugins.md."""
import json
import os

from frameviewer.plugins.api import FrameViewerPlugin
from frameviewer.plugins.graph import Graph, evaluate_graph_for_frame

GRAPH_FILE = "graph.json"


class GraphPlugin(FrameViewerPlugin):
    """Classe de base des plugins "graphe". NE PAS sous-classer a la main
    pour un autre usage : le plugin.py genere sera ECRASE a la prochaine
    sauvegarde depuis l'editeur de graphe."""

    def on_load(self, api):
        self._graph = None
        self._rows_by_frame = {}
        path = os.path.join(api.plugin_dir(), GRAPH_FILE)
        if not os.path.isfile(path):
            api.log(f"{path} introuvable -- graphe vide.")
            return
        with open(path, "r", encoding="utf-8") as f:
            self._graph = Graph.from_dict(json.load(f))
        if self._graph.csv_path and os.path.isfile(self._graph.csv_path):
            self._load_csv(api, self._graph.csv_path)

    def _load_csv(self, api, path):
        rows = api.read_csv_dict(path)
        frame_col = self._graph.frame_column if self._graph else None
        if not frame_col and rows:
            frame_col = next(iter(rows[0]))   # repli : 1re colonne du CSV
        by_frame = {}
        for row in rows:
            try:
                fr = int(float(row.get(frame_col, "")))
            except (TypeError, ValueError):
                continue
            by_frame.setdefault(fr, []).append(row)
        self._rows_by_frame = by_frame
        if self._graph is not None:
            self._graph.csv_path = path

    def accepts_drop(self, api, path):
        return self._graph is not None and path.lower().endswith(".csv")

    def on_drop(self, api, path):
        self._load_csv(api, path)
        n = sum(len(v) for v in self._rows_by_frame.values())
        api.log(f"graphe : {n} ligne(s) chargées depuis {path}.")
        api.status(f"{n} ligne(s) chargées.")
        api.request_repaint()

    def get_overlays(self, api, frame_idx):
        if self._graph is None:
            return []
        rows = self._rows_by_frame.get(frame_idx, [])
        try:
            return evaluate_graph_for_frame(self._graph, rows, frame_idx)
        except Exception as ex:
            # une erreur de graphe (cycle, bloc invalide...) ne doit pas
            # desactiver tout le plugin pour une seule frame en erreur --
            # contrairement aux autres hooks, celui-ci est appele a CHAQUE
            # frame pendant que l'utilisateur edite le graphe en direct ;
            # le proteger ici garde le plugin actif (donc rechargeable)
            # pendant qu'il corrige, au lieu de le desactiver au 1er essai.
            api.log(f"graphe : erreur d'evaluation -- {ex}")
            return []
