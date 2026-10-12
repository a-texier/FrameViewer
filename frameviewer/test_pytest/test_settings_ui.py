"""Info bar, user settings persistence, shortcuts, probe and A/B wipe."""

from __future__ import annotations

import re

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtTest, QtWidgets
from PySide6.QtCore import Qt

from frameviewer.core.pipeline import BLEND_MODES
from frameviewer.core.sources import ImageSequenceSource, frame_info
from frameviewer.ui.multiview import BaseViewFrame, FusionView
from frameviewer.ui.shortcuts import conflicts, default_shortcuts, event_matches
from frameviewer.ui.video_widget import probe_text


def _sequence(tmp_path, count=4, value_step=30):
    paths = []
    for index in range(count):
        path = tmp_path / f"img_{index:02d}.png"
        cv2.imwrite(str(path), np.full((12, 16), index * value_step, np.uint8))
        paths.append(str(path))
    return ImageSequenceSource(paths, label="seq", directory=str(tmp_path))


def _window():
    from frameviewer.ui.main_window import MainWindow

    return MainWindow()


def test_frame_info_lists_file_size_type_and_position(tmp_path):
    source = _sequence(tmp_path)
    text = frame_info(source, 2, source.get(2))
    assert "img_02.png" in text
    assert "16x12" in text and "uint8 1ch" in text
    assert "frame 2/3" in text
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text)  # date du fichier
    reduced = frame_info(source, 2, source.get(2)[::2, ::2], 0.5)
    assert "(1:2)" in reduced
    assert frame_info(None, 0) == ""


def test_info_bar_follows_frames_and_setting(qapp, tmp_path):
    window = _window()
    try:
        window.show()
        window.set_source(_sequence(tmp_path))
        window.show_index(1)
        qapp.processEvents()
        bar = window._primary_frame.info_bar
        assert "img_01.png" in bar.full_text() and bar.isVisible()
        window.info_bar_chk.setChecked(False)
        qapp.processEvents()
        assert not bar.isVisible()
    finally:
        window.info_bar_chk.setChecked(True)
        window.close()


def test_parameters_and_window_layout_survive_restart(qapp, monkeypatch):
    from frameviewer.ui import main_window as mw_module

    # theme enregistre mais non applique : restyler tous les widgets laisses
    # par les tests precedents coute des minutes (voir test_theming.py)
    themes = []
    monkeypatch.setattr(mw_module, "apply_theme", themes.append)
    window = _window()
    window.prefetch_spin.setValue(6)
    window.info_bar_chk.setChecked(False)
    window.flt_invert_chk.setChecked(True)
    window.theme_combo.setCurrentIndex(window.theme_combo.findData("light"))
    window.close()
    qapp.processEvents()

    again = _window()
    try:
        assert again.prefetch_spin.value() == 6
        assert not again.info_bar_chk.isChecked()
        assert again.flt_invert_chk.isChecked()
        assert again.theme_combo.currentData() == "light"
        assert themes[-1] == "light"

        monkeypatch.setattr(
            QtWidgets.QMessageBox,
            "question",
            staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes),
        )
        again._reset_settings()
        assert again.prefetch_spin.value() == 0
        assert again.info_bar_chk.isChecked()
        assert again.theme_combo.currentData() == "dark"
        assert themes[-1] == "dark"
    finally:
        again.close()
        BaseViewFrame.info_bar_enabled = True


def _key_event(key, modifiers=Qt.NoModifier):
    return QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, modifiers)


def test_shortcut_matching_keeps_historic_behaviour():
    keys = default_shortcuts()
    assert event_matches(_key_event(Qt.Key_Right), keys["step_forward"])
    # a bare key ignores modifiers, as the hard-coded handler did
    assert event_matches(_key_event(Qt.Key_Right, Qt.ShiftModifier), keys["step_forward"])
    assert event_matches(_key_event(Qt.Key_Equal), keys["zoom_in"])
    assert event_matches(_key_event(Qt.Key_O, Qt.ControlModifier), keys["open_file"])
    assert not event_matches(_key_event(Qt.Key_O), keys["open_file"])
    assert conflicts(keys) == {}
    clash = dict(keys, step_back=["Right"])
    assert "Right" in conflicts(clash)


def test_remapped_navigation_key_moves_frames(qapp, tmp_path):
    window = _window()
    try:
        window.show()
        window.set_source(_sequence(tmp_path))
        window._shortcut_map["step_forward"] = ["D"]
        QtTest.QTest.keyClick(window, Qt.Key_D)
        assert window.cur == 1
        QtTest.QTest.keyClick(window, Qt.Key_Right)  # no longer bound
        assert window.cur == 1
        QtTest.QTest.keyClick(window, Qt.Key_Left)
        assert window.cur == 0
    finally:
        window.close()


def test_probe_reads_native_value_on_any_view():
    raw16 = np.full((4, 5), 51234, np.uint16)
    assert probe_text(raw16, 2, 1) == "x= 2   y= 1   v= 5.123e+04"
    assert probe_text(raw16, 9, 1) is None and probe_text(None, 0, 0) is None
    color = np.zeros((2, 2, 3), np.uint8)
    assert probe_text(color, 0, 0).endswith("v= 0")


def test_satellite_view_answers_hover_with_pixel_value(qapp, tmp_path):
    window = _window()
    try:
        window.show()
        window._set_split("h2")
        sat = window._split_canvas.satellites()[0]
        sat.load(_sequence(tmp_path, value_step=40), 0, 255, None, None, 0, (0, 255), {})
        sat._goto(2)
        sat._on_probe(3, 4)
        assert sat.video._probe_txt == "x= 3   y= 4   v= 80"
        assert "img_02.png" in sat.info_bar.full_text()
    finally:
        window.close()


def _mouse(kind, x, y, button):
    point = QtCore.QPointF(x, y)
    return QtGui.QMouseEvent(kind, point, point, button, Qt.LeftButton, Qt.NoModifier)


def test_wipe_mode_is_offered_and_handle_drag_moves_the_cut(qapp):
    assert {"wipe", "max", "mult"} <= {key for key, _label in BLEND_MODES}
    view = FusionView()
    view.resize(200, 150)
    view.show()
    qapp.processEvents()
    left = np.zeros((100, 100, 3), np.uint8)
    right = np.full((100, 100, 3), 255, np.uint8)
    view._mode_combo.setCurrentIndex(view._mode_combo.findData("wipe"))
    view.set_frames(left, right)
    view.set_alpha(0.5)
    handle = view._wipe_handle_x()
    assert handle is not None
    y = view.video.height() / 2
    press = _mouse(QtCore.QEvent.MouseButtonPress, handle, y, Qt.LeftButton)
    assert view.eventFilter(view.video, press)
    view.video._geom()
    target = view.video._w(25, 0).x()
    move = _mouse(QtCore.QEvent.MouseMove, target, y, Qt.NoButton)
    assert view.eventFilter(view.video, move)
    assert abs(view._alpha - 0.25) < 0.03
    out = view._last_out
    assert out[50, 10].max() == 0 and out[50, 60].min() == 255
    view.close()


def test_info_bar_keeps_file_name_and_facts_on_two_lines(qapp):
    from frameviewer.ui.multiview import InfoBar

    bar = InfoBar()
    facts = "1920x1080  |  uint16 1ch  |  frame 12/99"
    bar.set_info("C:/very/long/folder/name/that/does/not/fit/frame_00012.png  |  " + facts)
    width = bar.fontMetrics().horizontalAdvance(facts) + 10
    path_line, facts_line = bar._display_lines(width)
    assert facts_line == facts
    assert path_line.endswith("frame_00012.png") and "very/long" not in path_line


def test_panel_shortcuts_open_and_toggle_tabs(qapp):
    window = _window()
    try:
        window.show()
        qapp.processEvents()

        def tab():
            return window.right_tab.currentWidget()

        QtTest.QTest.keyClick(window, Qt.Key_P, Qt.ControlModifier)
        assert window.right_dock.isVisible() and tab() is window._plugins_page
        QtTest.QTest.keyClick(window, Qt.Key_P, Qt.ControlModifier | Qt.ShiftModifier)
        assert tab() is window.params_scroll
        QtTest.QTest.keyClick(window, Qt.Key_H, Qt.ControlModifier)
        assert tab() is window.side_split
        QtTest.QTest.keyClick(window, Qt.Key_H, Qt.ControlModifier)
        assert not window.right_dock.isVisible()
    finally:
        window.close()


def test_layout_menu_entries_have_drawings(qapp):
    window = _window()
    try:
        for mode, action in window._split_actions.items():
            assert not action.icon().isNull(), mode
            assert not action.icon().pixmap(16, 16).isNull()
    finally:
        window.close()
