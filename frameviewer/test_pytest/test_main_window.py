from __future__ import annotations

from pathlib import Path

import tutorial_multiview_test as scenario_tests
from frameviewer.core.sources import ImageSequenceSource
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
