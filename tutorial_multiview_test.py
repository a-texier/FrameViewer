#!/usr/bin/env python3
"""Focused offscreen regression tests for tutorial data and linked views."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets
from PySide6.QtCore import Qt
import shiboken6

from frameviewer.core.annotation_loader import load_annotations
from frameviewer.core.sources import ImageSequenceSource
from frameviewer.tutorial_data import is_tutorial_source
from frameviewer.ui import main_window as main_window_module
from frameviewer.ui.link_views_dialog import LinkViewsDialog
from frameviewer.ui.main_window import MainWindow


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data_test"


def _load_plugin(folder: str):
    path = ROOT / "plugins" / folder / "plugin.py"
    spec = importlib.util.spec_from_file_location("test_" + folder, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module.PLUGIN()


class _PluginApi:
    def __init__(self, folder: Path):
        self._folder = folder

    def plugin_dir(self):
        return str(self._folder)

    def request_repaint(self):
        pass

    def add_dock(self, widget, title, area="right"):
        self.widget = widget

    def log(self, *args, **kwargs):
        pass


def test_annotations() -> None:
    folder = load_annotations(DATA / "annotations_yolo", 1920, 1080)
    assert len(folder) == 10
    assert folder["frame_05000"][0][6] == ("vehicle",)
    merged = load_annotations(DATA / "merged_yolo.txt", 1920, 1080)
    assert sorted(merged) == list(range(10))
    assert merged[0][0][6] == ("vehicle",)
    tracked = load_annotations(DATA / "example.ver", 1920, 1080)
    assert sorted(tracked) == [0, 1, 2]
    assert tracked[0][0][5:] == (1, ("vehicle", "moving"))


def test_link_dialogs(app) -> None:
    layouts = {"h2": 2, "v2": 2, "h3": 3, "q4": 4, "fusion": 2}
    for mode, count in layouts.items():
        sources = [{"name": f"source {i}", "count": 30} for i in range(count)]
        dialog = LinkViewsDialog(None, mode, sources, list(range(count)), True)
        assert len(dialog._spins) == count
        assert dialog.values() == (True, list(range(count)))
        assert all(spin.maximum() == 29 for spin in dialog._spins)
        before = dialog._spins[0].value()
        dialog._spins[0].stepUp()
        assert dialog._spins[0].value() == before + 1
        dialog.show()
        app.processEvents()
        spin = dialog._spins[0]
        option = QtWidgets.QStyleOptionSpinBox()
        spin.initStyleOption(option)
        up_rect = spin.style().subControlRect(
            QtWidgets.QStyle.CC_SpinBox, option,
            QtWidgets.QStyle.SC_SpinBoxUp, spin)
        clicked_before = spin.value()
        QtTest.QTest.mouseClick(spin, Qt.LeftButton, pos=up_rect.center())
        assert spin.value() == clicked_before + 1
        apply_button = dialog.findChild(QtWidgets.QPushButton, "link_apply")
        assert apply_button is not None
        apply_button.click()
        assert dialog.result() == QtWidgets.QDialog.Accepted
        dialog.close()

    captured = []

    class _CancelledLinkDialog:
        def __init__(self, _parent, _mode, _sources, starts, active):
            captured.append((list(starts), bool(active)))

        def exec(self):
            return False

    window = MainWindow()
    window._set_split("v2")
    window._link_start_frames_by_mode["v2"] = [2, 0]
    window._link_start_frames = [2, 0]
    window._link_views = True
    window.link_btn.setChecked(False)  # etat inverse par le clic Qt avant le slot
    original_dialog = main_window_module.LinkViewsDialog
    main_window_module.LinkViewsDialog = _CancelledLinkDialog
    try:
        window._open_link_dialog(False)
    finally:
        main_window_module.LinkViewsDialog = original_dialog
    assert captured == [([2, 0], True)]
    assert window.link_btn.isChecked()
    window.close()


def _load_satellite(window, widget, paths, name):
    source = ImageSequenceSource(paths, label=name, directory=str(Path(paths[0]).parent))
    widget.load(source, window.hist.lo, window.hist.hi, window.lut_combo.currentData(),
                window._cube_lut, window._cube_size, window.hist.full_range,
                window._filter_flags())


def _drop_path(widget, path: Path) -> None:
    mime = QtCore.QMimeData()
    mime.setUrls([QtCore.QUrl.fromLocalFile(str(path))])
    enter = QtGui.QDragEnterEvent(
        QtCore.QPoint(20, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier
    )
    QtWidgets.QApplication.sendEvent(widget, enter)
    drop = QtGui.QDropEvent(
        QtCore.QPointF(20, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier
    )
    QtWidgets.QApplication.sendEvent(widget, drop)


def _wait_until(app, predicate, timeout_ms=4000) -> None:
    timer = QtCore.QElapsedTimer()
    timer.start()
    while not predicate() and timer.elapsed() < timeout_ms:
        app.processEvents()
        QtCore.QThread.msleep(10)
    assert predicate()


def test_linked_navigation_and_plugin_api(app) -> None:
    rgb = sorted(str(path) for path in (DATA / "traffic_rgb").glob("*.png"))
    thermal = sorted(str(path) for path in (DATA / "traffic_ir").glob("*.png"))
    window = MainWindow()
    window.show()
    app.processEvents()

    # 20 logical frames let the public [10, 0] example be exercised exactly.
    window.set_source(ImageSequenceSource(rgb * 2, label="RGB20", directory=str(DATA / "traffic_rgb")))
    by_stem = load_annotations(DATA / "annotations_yolo", 1920, 1080)
    mapped = window._pair_yolo_by_name(by_stem)
    assert sorted(mapped) == list(range(10))
    fallback = window._pair_yolo_by_name({"unmatched_2": [(0,)], "unmatched_10": [(1,)]})
    assert sorted(fallback) == [0, 1]
    window._set_split("v2")
    bottom = window._split_canvas.widget_at(1)
    _load_satellite(window, bottom, thermal * 2, "IR20")
    window._link_start_frames_by_mode["v2"] = [10, 0]
    window._link_start_frames = [10, 0]
    window._set_link_enabled(True, reset_base=True)
    assert (window.cur, bottom._cur) == (10, 0)
    window.show_index(13)
    assert (window.cur, bottom._cur, window._sync_base_frame) == (13, 3, 3)
    bottom._goto(5)
    assert (window.cur, bottom._cur, window._sync_base_frame) == (15, 5, 5)

    window.view.set_view(2.0, 20.0, 12.0)
    window._sync_view_transform(window.view)
    assert bottom.video.get_view() == window.view.get_view()
    context = window._views_context()
    assert context["sync_base_frame"] == 5
    assert [view["start_frame"] for view in context["views"]] == [10, 0]

    # Exact four-view offset contract from the UI specification.
    window._set_link_enabled(False)
    window._set_split("q4")
    for index, widget, primary in window._split_canvas.ordered_views():
        if not primary:
            _load_satellite(window, widget, thermal * 3, f"IR30-{index}")
    window.set_source(ImageSequenceSource(rgb * 3, label="RGB30", directory=str(DATA / "traffic_rgb")))
    window._link_start_frames_by_mode["q4"] = [17, 8, 0, 0]
    window._link_start_frames = [17, 8, 0, 0]
    window._set_link_enabled(True, reset_base=True)
    frames = []
    for _index, widget, primary in window._split_canvas.ordered_views():
        frames.append(window.cur if primary else widget._cur)
    assert frames == [17, 8, 0, 0]
    for origin, widget, primary in window._split_canvas.ordered_views():
        base = 2 + origin
        target = window._link_start_frames[origin] + base
        if primary:
            window.show_index(target)
        else:
            widget._goto(target)
        current = [
            window.cur if is_primary else view._cur
            for _idx, view, is_primary in window._split_canvas.ordered_views()
        ]
        assert current == [base + start for start in [17, 8, 0, 0]]
    before = window._sync_base_frame
    window._bar_step(1)
    assert window._sync_base_frame == before + 1
    before = window._sync_base_frame
    window._on_tick()
    assert window._sync_base_frame == before + 1
    window._slider_pressed()
    window.slider.setValue(24)
    window._slider_released()
    assert window._sync_base_frame == 7
    window.close()


def test_plugins() -> None:
    multiview_folder = ROOT / "plugins" / "plugins_demo_2_multivue_kpts"
    api = _PluginApi(multiview_folder)
    plugin = _load_plugin("plugins_demo_2_multivue_kpts")
    plugin.on_load(api)
    plugin.pairs_csv = None
    views = {
        "mode": "v2",
        "views": [{"index": 0, "frame_idx": 0}, {"index": 1, "frame_idx": 0}],
    }
    assert plugin.get_multiview_overlays(api, views) == []
    assert plugin._data_status == "CSV absent"
    plugin.pairs_csv = str(DATA / "multiview_keypoints.csv")
    for frame in range(10):
        views["views"][0]["frame_idx"] = frame
        views["views"][1]["frame_idx"] = frame
        specs = plugin.get_multiview_overlays(api, views)
        text_specs = [spec for spec in specs if spec["type"] == "texte_ecran"]
        shape_specs = [spec for spec in specs if spec["type"] != "texte_ecran"]
        assert len(text_specs) == 1 and len(shape_specs) % 3 == 0, frame
        assert text_specs[0]["label"].endswith("keypoints apparies")
        assert plugin._frame_match_count == len(shape_specs) // 3
    assert plugin._data_status.startswith("CSV reel")
    plugin._min_confidence = 1.0
    empty_specs = plugin.get_multiview_overlays(api, views)
    assert len(empty_specs) == 1 and empty_specs[0]["label"].startswith("0 ")
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as bad:
        bad.write("wrong,columns\n1,2\n")
        bad_path = bad.name
    try:
        plugin.pairs_csv = bad_path
        plugin._cache_key = None
        assert plugin.get_multiview_overlays(api, views) == []
        assert plugin._data_status == "CSV invalide"
    finally:
        os.unlink(bad_path)
    plugin.pairs_csv = str(DATA / "multiview_keypoints.csv")
    plugin._cache_key = None
    plugin._min_confidence = 0.70
    plugin.get_multiview_overlays(api, views)
    panel = plugin.build_panel(api)
    assert panel.findChild(QtWidgets.QDoubleSpinBox, "multiview_confidence") is not None
    radius = panel.findChild(QtWidgets.QSpinBox, "multiview_radius")
    assert radius is not None and radius.value() == 11
    assert panel.findChild(QtWidgets.QSpinBox, "multiview_thickness") is not None
    colormap = panel.findChild(QtWidgets.QCheckBox, "multiview_score_colormap")
    assert colormap is not None and colormap.isChecked()
    scope = panel.findChild(QtWidgets.QComboBox, "multiview_score_scope")
    assert scope is not None and scope.count() == 2
    assert panel.findChild(QtWidgets.QLabel, "multiview_csv_status").text().endswith(
        "600 lignes au total")
    assert panel.findChild(QtWidgets.QLabel, "multiview_match_count") is not None
    assert plugin._score_color(0.0)[0] > plugin._score_color(0.0)[2]
    assert plugin._score_color(1.0)[2] > plugin._score_color(1.0)[0]
    bounds = plugin._bounds([0.6, 0.7, 0.9])
    assert bounds == (0.6, 0.7, 0.9)
    assert plugin._normalize_score(0.7, bounds) == 0.5
    median_specs = plugin._pair_specs(0, (1, 1), 1, (2, 2), (255, 0, 0), 0.5)
    assert median_specs[0]["a"] == 11

    showcase_folder = ROOT / "plugins" / "plugins_demo_showcase"
    showcase_api = _PluginApi(showcase_folder)
    showcase = _load_plugin("plugins_demo_showcase")
    showcase.on_load(showcase_api)
    assert showcase._csv_path() is None
    showcase.boxes = str(DATA / "showcase_boxes.csv")
    assert len(showcase._frame_rows(0)) > 0
    showcase_panel = showcase.build_panel(showcase_api)
    assert len(showcase_panel.findChildren(QtWidgets.QSlider)) == 4


def test_tutorial_state(app) -> None:
    window = MainWindow()
    window.resize(1366, 768)
    window.show()
    app.processEvents()
    tutorial = window._tutorial
    settings = tutorial._settings()
    settings.remove(tutorial.SETTINGS_KEY)
    settings.sync()
    tutorial.start()
    app.processEvents()
    assert tutorial.index == 0 and len(tutorial.steps) >= 40
    assert tutorial._demo_plugins_inactive()
    tutorial._poll()
    assert window.rect().contains(tutorial.bubble.geometry())
    assert all(mask.testAttribute(Qt.WA_TransparentForMouseEvents) for mask in tutorial._masks)
    QtTest.QTest.keyClick(window, Qt.Key_Right)
    assert tutorial.index == 1
    QtTest.QTest.keyClick(window, Qt.Key_Left)
    assert tutorial.index == 0
    assert tutorial.explorer.list.count() == 2
    ir_item = tutorial.explorer.list.item(1)
    assert not ir_item.data(tutorial.explorer.list.DRAG_ENABLED_ROLE)
    assert not bool(ir_item.flags() & Qt.ItemIsDragEnabled)
    assert len(list((tutorial.workspace / "traffic_rgb").glob("*.png"))) == 10
    step_index = lambda title: next(
        index for index, step in enumerate(tutorial.steps) if step.title == title
    )
    tutorial.index = step_index("Deposer Trafic RGB")
    tutorial._enter_step()
    assert tutorial.explorer.isVisible()
    # Exercise the same URL MIME and BaseViewFrame drop path as a real drag.
    item = tutorial.explorer.list.item(0)
    mime = tutorial.explorer.list.mime_for_item(item)
    enter = QtGui.QDragEnterEvent(
        QtCore.QPoint(20, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier
    )
    QtWidgets.QApplication.sendEvent(window._primary_frame, enter)
    drop = QtGui.QDropEvent(
        QtCore.QPointF(20, 20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier
    )
    QtWidgets.QApplication.sendEvent(window._primary_frame, drop)
    app.processEvents()
    assert window.source is not None and window.source.count == 10
    tutorial._poll()
    assert not tutorial.explorer.isVisible()

    tutorial.index = step_index("Zoom, panoramique et ajustement")
    tutorial._enter_step()
    app.processEvents()
    checklist = tutorial.bubble.waiting.text()
    assert checklist.count("[ ]") == 3

    tutorial.index = step_index("Histogramme et LUT")
    tutorial._enter_step()
    app.processEvents()
    assert window.right_tab.currentIndex() == 0
    assert tutorial.steps[tutorial.index].target() is window.hist_render_panel
    assert window.lut_hist_combo.isVisibleTo(window)
    assert not tutorial._is_ready()
    window.hist.canvas.lo += 1.0
    assert tutorial._is_ready()

    tutorial.index = step_index("Deposer Trafic IR")
    tutorial._enter_step()
    app.processEvents()
    assert ir_item.data(tutorial.explorer.list.DRAG_ENABLED_ROLE)
    assert bool(ir_item.flags() & Qt.ItemIsDragEnabled)
    tutorial.explorer.reveal_annotations()
    assert tutorial.explorer.list.count() == 3
    tutorial.index = step_index("Deposer Trafic RGB")
    window.source = None
    tutorial.next()
    assert tutorial.index == 3
    tutorial.stop()
    assert not tutorial.completed()
    tutorial.start()
    tutorial.index = len(tutorial.steps) - 1
    tutorial.next()
    assert tutorial.completed()
    assert window.tutorial_btn.graphicsEffect() is None
    window.close()
    restarted = MainWindow()
    assert restarted._tutorial.completed()
    assert restarted.tutorial_btn.graphicsEffect() is None
    restarted.close()


def test_tutorial_action_path(app) -> None:
    window = MainWindow()
    window.resize(1366, 768)
    window.show()
    app.processEvents()
    tutorial = window._tutorial
    tutorial.start()
    by_title = {
        step.title: index for index, step in enumerate(tutorial.steps)
    }

    def enter(title):
        tutorial.index = by_title[title]
        tutorial._enter_step()
        app.processEvents()

    enter("Deposer Trafic RGB")
    _drop_path(window._primary_frame, tutorial.workspace / "traffic_rgb")
    _wait_until(app, lambda: is_tutorial_source(window.source, "traffic_rgb"))
    tutorial._poll()
    assert not tutorial.explorer.isVisible()

    enter("Activer et enregistrer un point")
    window.rec_btn.setChecked(True)
    window.on_click(20, 20)
    assert tutorial._is_ready() and Path(window.clicks_path).is_file()
    enter("Arreter l'enregistrement")
    window.rec_btn.setChecked(False)
    assert tutorial._is_ready()

    window.show_index(0)
    enter("Marquer le debut IN")
    window.extract_in_btn.click()
    assert tutorial._is_ready()
    enter("Naviguer puis marquer OUT")
    window.show_index(3)
    window.extract_out_btn.click()
    assert tutorial._is_ready()
    enter("Extraire ou ajouter un morceau")
    window.seg_add_btn.click()
    assert tutorial._is_ready()

    enter("Ouvrir Hist / Calque")
    window.right_tab.setCurrentIndex(0)
    assert tutorial._is_ready()
    enter("Deposer les annotations YOLO")
    _drop_path(window._primary_frame, tutorial.workspace / "annotations_yolo")
    _wait_until(app, tutorial._annotations_loaded)
    assert tutorial._is_ready()
    enter("Masquer uniquement le calque YOLO")
    yolo_item = window.layer_list.topLevelItem(0)
    yolo_item.setCheckState(0, Qt.Unchecked)
    assert tutorial._is_ready()
    enter("Ouvrir Plugins")
    window.right_tab.setCurrentIndex(1)
    assert tutorial._is_ready()

    enter("Activer le showcase")
    card = tutorial._plugin_card("demo_showcase")
    card._chk.setChecked(True)
    assert tutorial._is_ready()
    enter("Deplier la carte")
    card._arrow.click()
    app.processEvents()
    assert tutorial._is_ready()
    enter("Deposer le CSV des boites")
    assert tutorial.explorer.isVisible()
    zone = tutorial._plugin_input_zone("demo_showcase", "boxes")
    _drop_path(zone, tutorial.workspace / "showcase_boxes.csv")
    tutorial._poll()
    assert tutorial._is_ready()
    assert not tutorial.explorer.isVisible()
    enter("Ouvrir le panneau du plugin")
    tutorial._plugin_control("demo_showcase", "plugin_open_dock").click()
    app.processEvents()
    assert tutorial._is_ready()
    enter("Desactiver le showcase")
    tutorial._plugin_control("demo_showcase", "plugin_active").setChecked(False)
    assert tutorial._is_ready()

    window._set_split("v2")
    enter("Deposer Trafic IR")
    lower = window._split_canvas.widget_at(1)
    _drop_path(lower, tutorial.workspace / "traffic_ir")
    _wait_until(app, lambda: tutorial._satellite_has("traffic_ir"))
    assert tutorial._is_ready() and not tutorial._enable_plugin("demo_showcase")

    window._link_start_frames_by_mode["v2"] = [0, 0]
    window._link_start_frames = [0, 0]
    window._set_link_enabled(True, reset_base=True)
    enter("Tester un decalage")
    window._link_start_frames_by_mode["v2"] = [2, 0]
    window._link_start_frames = [2, 0]
    window._set_link_enabled(True, reset_base=True)
    assert tutorial._is_ready()
    enter("Verifier la navigation synchronisee")
    tutorial._linked_canvas_clicked = True
    window._apply_link_base(1)
    assert tutorial._is_ready()
    enter("Revenir a [0, 0]")
    window._link_start_frames_by_mode["v2"] = [0, 0]
    window._link_start_frames = [0, 0]
    window._set_link_enabled(True, reset_base=True)
    assert tutorial._is_ready()

    enter("Activer les correspondances")
    tutorial._plugin_control("demo_2_multivue_kpts", "plugin_active").setChecked(True)
    assert tutorial._is_ready()
    enter("Deplier la carte multivue")
    tutorial._plugin_control("demo_2_multivue_kpts", "plugin_expand").click()
    app.processEvents()
    assert tutorial._is_ready()
    enter("Deposer le CSV des correspondances")
    assert tutorial.explorer.isVisible()
    zone = tutorial._plugin_input_zone("demo_2_multivue_kpts", "pairs_csv")
    _drop_path(zone, tutorial.workspace / "multiview_keypoints.csv")
    # Le rebuild de la carte doit avoir lieu apres le retour du dropEvent.
    # Une reconstruction synchrone ici causait un use-after-free natif dans l'EXE.
    assert shiboken6.isValid(zone)
    app.processEvents()
    rebuilt_zone = tutorial._plugin_input_zone("demo_2_multivue_kpts", "pairs_csv")
    assert rebuilt_zone is not None and shiboken6.isValid(rebuilt_zone)
    tutorial._poll()
    assert tutorial._is_ready()
    assert not tutorial.explorer.isVisible()
    view_keys = [view["view_key"] for view in window._views_context()["views"]]
    assert len(view_keys) == 2
    for key in view_keys:
        contracts = window._plugin_view_state("demo_2_multivue_kpts", key)["contracts"]
        assert any(group["files"].get("pairs_csv") for group in contracts)
    enter("Ouvrir les reglages multivues")
    tutorial._plugin_control("demo_2_multivue_kpts", "plugin_open_dock").click()
    app.processEvents()
    assert tutorial._is_ready()
    dock = tutorial._plugin_dock("demo_2_multivue_kpts")
    status = dock.findChild(QtWidgets.QLabel, "multiview_csv_status")
    assert status is not None and "600 lignes" in status.text()
    enter("Naviguer avec les correspondances")
    tutorial._linked_canvas_clicked = True
    window._apply_link_base(1)
    assert tutorial._is_ready()
    tutorial.stop()
    window.close()
    restarted = MainWindow()
    assert restarted._tutorial._demo_plugins_inactive()
    for pid in ("demo_showcase", "demo_2_multivue_kpts"):
        state = restarted._plugin_state_for(pid)
        assert all(
            not contract.get("files")
            for view_state in state.get("by_view", {}).values()
            for contract in view_state.get("contracts", [])
        )
    settings = restarted._plugin_settings()
    enabled = settings.value("enabled_plugins", [])
    if isinstance(enabled, str):
        enabled = [enabled] if enabled else []
    assert not ({"demo_showcase", "demo_2_multivue_kpts"} & set(enabled or []))
    persisted = json.loads(settings.value("plugin_state", "{}") or "{}")
    assert "demo_showcase" not in persisted
    assert "demo_2_multivue_kpts" not in persisted
    restarted.close()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="frameviewer_tutorial_test_") as config:
        os.environ["APPDATA"] = config
        os.environ["XDG_CONFIG_HOME"] = config
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        test_annotations()
        test_link_dialogs(app)
        test_linked_navigation_and_plugin_api(app)
        test_plugins()
        test_tutorial_state(app)
        test_tutorial_action_path(app)
        app.processEvents()
    print("TUTORIAL/MULTIVIEW TESTS OK")


if __name__ == "__main__":
    main()
