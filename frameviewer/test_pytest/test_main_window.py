from __future__ import annotations

from pathlib import Path

import tutorial_multiview_test as scenario_tests
from PySide6 import QtWidgets
from frameviewer.core.sources import ImageSequenceSource
from frameviewer.ui.i18n import LANGUAGE_SETTING
from frameviewer.ui.main_window import MainWindow


def _source(data_dir, folder, count=10):
    paths = sorted(str(path) for path in (data_dir / folder).glob("*.png"))
    return ImageSequenceSource(paths[:count], label=folder, directory=str(data_dir / folder))


def test_window_starts_with_stable_toolbar_and_inactive_demo_plugins(qapp):
    window = MainWindow()
    window.show()
    qapp.processEvents()
    assert window.windowTitle().startswith("FrameViewer")
    assert window.tutorial_btn is not None
    assert not window.link_btn.isEnabled()
    enabled = set(window._plugin_loader.enabled_ids())
    assert "demo_showcase" not in enabled
    assert "demo_2_multivue_kpts" not in enabled
    window.close()


def test_language_toggle_is_ordered_persistent_and_reversible(qapp):
    window = MainWindow()
    toolbar = window.findChild(QtWidgets.QToolBar, "main_toolbar")
    widgets = [toolbar.widgetForAction(action) for action in toolbar.actions()]
    selector = window.findChild(QtWidgets.QWidget, "language_selector")
    assert widgets.index(selector) < widgets.index(window.tutorial_btn)
    assert window._ui_language == "fr"
    assert window.tools_btn.text() == "Outils TI"

    window._set_ui_language("en")
    qapp.processEvents()
    assert window.tools_btn.text() == "Image tools"
    assert window.right_tab.tabText(0) == "Histogram / Layers"
    assert toolbar.windowTitle() == "Tools"
    assert window.tools_dock.windowTitle() == "Image tools"
    assert window._plugin_settings().value(LANGUAGE_SETTING) == "en"

    plugin_owned = QtWidgets.QWidget(window)
    plugin_owned.setProperty("frameviewer_plugin_owned", True)
    plugin_label = QtWidgets.QLabel("Fermer", plugin_owned)
    window._i18n.refresh()
    assert plugin_label.text() == "Fermer"

    window._set_ui_language("fr")
    qapp.processEvents()
    assert window.tools_btn.text() == "Outils TI"
    assert window.right_tab.tabText(0) == "Hist / Calque"
    assert toolbar.windowTitle() == "Outils"
    assert window.tools_dock.windowTitle() == "Outils TI"
    window.close()


def test_saved_english_language_is_restored(qapp):
    first = MainWindow()
    first._set_ui_language("en")
    first.close()
    qapp.processEvents()

    restarted = MainWindow()
    qapp.processEvents()
    assert restarted._ui_language == "en"
    assert restarted._language_buttons["en"].isChecked()
    assert restarted.tools_btn.text() == "Image tools"
    restarted.close()


def test_language_switch_translates_new_dialogs_and_dynamic_status_without_timer(qapp):
    window = MainWindow()
    window._set_ui_language("en")
    assert not hasattr(window._i18n, "_timer")

    window.statusBar().showMessage("Aucune source chargée.", 1000)
    assert window.statusBar().currentMessage() == "No source loaded."

    dialog = QtWidgets.QDialog(window)
    dialog.setWindowTitle("Paramètres")
    layout = QtWidgets.QVBoxLayout(dialog)
    label = QtWidgets.QLabel("Fermer")
    layout.addWidget(label)
    dialog.show()
    qapp.processEvents()
    assert dialog.windowTitle() == "Settings"
    assert label.text() == "Close"

    window._set_ui_language("fr")
    qapp.processEvents()
    assert dialog.windowTitle() == "Paramètres"
    assert label.text() == "Fermer"
    dialog.close()
    window.close()


def test_layout_slot_counts_and_single_view_link_guard(qapp, data_dir):
    window = MainWindow()
    window.set_source(_source(data_dir, "traffic_rgb"))
    expected = {"1": 1, "h2": 2, "v2": 2, "h3": 3, "q4": 4, "fusion": 2}
    for mode, count in expected.items():
        window._set_split(mode)
        assert window._split_canvas.n_slots() == count
    window._set_split("1")
    assert not window.link_btn.isEnabled()
    window.close()


def test_view_context_exposes_offsets_and_common_base(qapp, data_dir):
    window = MainWindow()
    window.show()
    qapp.processEvents()
    window.set_source(_source(data_dir, "traffic_rgb"))
    window._set_split("v2")
    lower = window._split_canvas.widget_at(1)
    scenario_tests._load_satellite(
        window, lower,
        sorted(str(path) for path in (data_dir / "traffic_ir").glob("*.png")),
        "traffic_ir",
    )
    qapp.processEvents()
    window._link_start_frames_by_mode["v2"] = [2, 0]
    window._link_start_frames = [2, 0]
    window._set_link_enabled(True, reset_base=True)
    context = window._views_context()
    assert context["sync_base_frame"] == 0
    assert [view["start_frame"] for view in context["views"]] == [2, 0]
    assert (window.cur, lower._cur) == (2, 0)
    window._apply_link_base(7)
    assert (window.cur, lower._cur) == (9, 7)
    assert window._link_base_bounds() == (0, 7)
    window.close()


def test_linked_navigation_zoom_plugin_api_and_grid_offsets(qapp):
    scenario_tests.test_linked_navigation_and_plugin_api(qapp)
