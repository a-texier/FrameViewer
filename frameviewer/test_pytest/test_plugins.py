from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

import tutorial_multiview_test as scenario_tests
from frameviewer.plugins.api import PluginAPI
from frameviewer.plugins.graph import Graph, evaluate_graph_for_frame, vue_to_index
from frameviewer.plugins.loader import LoadedPlugin, PluginLoader
from frameviewer.plugins.manifest import load, normalize, save, validate


def test_manifest_normalization_deduplicates_inputs_and_keeps_keys(tmp_path):
    manifest = normalize(
        {
            "name": "Example",
            "kind": "invalid",
            "inputs": ["boxes", {"name": "boxes"}, {"name": "scores"}, {"name": ""}],
            "element_keys": {"main": "v", "empty": ""},
        },
        tmp_path,
    )
    assert manifest["kind"] == "code"
    assert manifest["inputs"] == [{"name": "boxes"}, {"name": "scores"}]
    assert manifest["element_keys"] == {"main": "v", "empty": "m"}
    assert validate(manifest) == (True, "")


def test_manifest_save_load_and_invalid_file_fallback(tmp_path):
    save(tmp_path, {"name": "Saved", "inputs": ["data"]})
    assert load(tmp_path)["inputs"] == [{"name": "data"}]
    (tmp_path / "manifest.json").write_text("not json", encoding="utf-8")
    fallback = load(tmp_path)
    assert fallback["name"] == tmp_path.name and fallback["kind"] == "code"


def test_manifest_validation_reports_empty_and_duplicate_inputs():
    assert validate({"name": "", "inputs": []})[0] is False
    assert validate({"name": "x", "inputs": [{"name": ""}]})[0] is False
    assert validate({"name": "x", "inputs": [{"name": "a"}, {"name": "a"}]})[0] is False


def _rectangle_graph() -> Graph:
    nodes = {
        "x1": {"type": "constant", "params": {"valeur": "10"}},
        "y1": {"type": "constant", "params": {"valeur": "20"}},
        "x2": {"type": "constant", "params": {"valeur": "30"}},
        "y2": {"type": "constant", "params": {"valeur": "40"}},
        "shape": {"type": "rect_shape", "params": {}},
        "out": {"type": "output_overlays", "params": {"vue": "vue2"}},
    }
    edges = [
        ("x1", "valeur", "shape", "x1"), ("y1", "valeur", "shape", "y1"),
        ("x2", "valeur", "shape", "x2"), ("y2", "valeur", "shape", "y2"),
        ("shape", "forme", "out", "forme"),
    ]
    return Graph(nodes, edges)


def test_graph_evaluation_routes_shapes_to_requested_view():
    graph = _rectangle_graph()
    shapes = graph.evaluate({})
    assert len(shapes) == 1
    assert shapes[0]["view"] == 1
    assert shapes[0]["points"] == [(10.0, 20.0), (30.0, 20.0), (30.0, 40.0), (10.0, 40.0)]
    restored = Graph.from_dict(graph.to_dict())
    assert restored.edges == graph.edges


def test_graph_cycle_is_rejected():
    graph = Graph(
        {"a": {"type": "constant", "params": {}}, "b": {"type": "constant", "params": {}}},
        [("a", "valeur", "b", "x"), ("b", "valeur", "a", "x")],
    )
    with pytest.raises(ValueError, match="cycle"):
        graph.evaluate({})


def test_frame_only_graph_runs_without_csv_rows():
    graph = Graph(
        {
            "frame": {"type": "frame_index", "params": {}},
            "out": {"type": "output_overlays", "params": {}},
        },
        [],
    )
    assert evaluate_graph_for_frame(graph, [], frame_idx=7) == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [("active", None), ("vue1", 0), ("vue4", 3), ("bad", None)],
)
def test_view_name_mapping(value, expected):
    assert vue_to_index(value) == expected


class _Source:
    count = 2
    name = "source"

    def get(self, index):
        return np.full((4, 5, 3), index, np.uint8)


class _Window:
    def __init__(self):
        self.source = _Source()
        self.cur = 1
        self._raw = self.source.get(1)
        self._plugin_state = {"demo": {"elements": {"main": False}}}

    def _apply_rotation(self, raw):
        return raw

    def process(self, raw):
        return raw + 1

    def _views_context(self):
        return {"mode": "v2", "count": 2, "sync_base_frame": 1, "views": []}


def test_plugin_api_returns_defensive_frames_inputs_and_context(tmp_path):
    window = _Window()
    api = PluginAPI(window, str(tmp_path), "demo", {"boxes": "input.csv"})
    assert api.frame_count() == 2 and api.current_frame_index() == 1
    assert api.image_size() == (5, 4) and api.source_name() == "source"
    assert api.input("boxes") == "input.csv"
    assert api.inputs() == {"boxes": "input.csv"}
    assert api.attached_csvs() == ["input.csv"]
    assert api.element_enabled("main") is False
    displayed = api.displayed_frame()
    displayed[:] = 99
    assert int(window.source.get(1)[0, 0, 0]) == 1
    assert api.views()["sync_base_frame"] == 1


def test_plugin_api_csv_reader(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("frame,score\n0,0.8\n1,0.9\n", encoding="utf-8")
    api = PluginAPI(_Window(), str(tmp_path))
    assert api.read_csv_dict(path) == [
        {"frame": "0", "score": "0.8"}, {"frame": "1", "score": "0.9"}
    ]


def test_loader_disables_only_failing_plugin(tmp_path):
    class Failing:
        def get_overlays(self, _api, _frame):
            raise RuntimeError("expected failure")

    window = _Window()
    loader = PluginLoader(window)
    loaded = LoadedPlugin("failing", str(tmp_path))
    loaded.instance = Failing()
    loaded.enabled = True
    loader.plugins = [loaded]
    result = loader._safe_call(loaded, "get_overlays", None, 0, default=[])
    assert result == [] and not loaded.enabled
    assert "expected failure" in loaded.error
    assert any(record["level"] == "error" for record in loader.log_records())


def test_real_demo_plugins_cover_all_frames_and_score_controls():
    scenario_tests.test_plugins()
