# -*- coding: utf-8 -*-
"""Bridge the visual block graph and the standard plugin contract.

A graph package uses the same loader and hooks as a code plugin, while its
logic lives in ``graph.json``. The editor generates a small ``plugin.py``
wrapper around ``GraphPlugin``.
"""
import json
import os

from frameviewer.plugins.api import FrameViewerPlugin
from frameviewer.plugins.graph import Graph, evaluate_graph_for_frame

GRAPH_FILE = "graph.json"


class GraphPlugin(FrameViewerPlugin):
    """Base class for editor-generated graph plugins."""

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
            frame_col = next(iter(rows[0]))   # Fall back to the first CSV column.
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
            # A graph error on one frame must not disable the complete plugin.
            # Keeping it enabled allows live editing and immediate reloads.
            api.log(f"graphe : erreur d'evaluation -- {ex}")
            return []
