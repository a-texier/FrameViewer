from __future__ import annotations

import numpy as np
from PySide6 import QtCore, QtGui, QtTest, QtWidgets
from PySide6.QtCore import Qt

import tutorial_multiview_test as scenario_tests
from frameviewer.ui.color_bar import ColorBar
from frameviewer.ui.histogram import HistCanvas, HistogramLUT
from frameviewer.ui.multiview import FrameSlider
from frameviewer.ui.multiview import MultiViewOverlay
from frameviewer.ui.plugins_panel import InputDropZone


def _drop_path(widget, path):
    mime = QtCore.QMimeData()
    mime.setUrls([QtCore.QUrl.fromLocalFile(str(path))])
    enter = QtGui.QDragEnterEvent(
        QtCore.QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier
    )
    QtWidgets.QApplication.sendEvent(widget, enter)
    drop = QtGui.QDropEvent(
        QtCore.QPointF(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier
    )
    QtWidgets.QApplication.sendEvent(widget, drop)
    return enter, drop


def test_histogram_canvas_maps_values_and_drags_handles(qapp):
    canvas = HistCanvas()
    canvas.resize(500, 160)
    canvas.set_domain(0, 1000)
    canvas.set_window(200, 800)
    canvas.set_counts(np.arange(256))
    canvas.show()
    qapp.processEvents()
    assert abs(canvas._valx(canvas._xval(400)) - 400) < 1e-6
    spy = QtTest.QSignalSpy(canvas.rangeChanged)
    QtTest.QTest.mousePress(canvas, Qt.LeftButton, pos=QtCore.QPoint(int(canvas._xval(200)), 60))
    QtTest.QTest.mouseMove(canvas, QtCore.QPoint(int(canvas._xval(300)), 60))
    QtTest.QTest.mouseRelease(canvas, Qt.LeftButton, pos=QtCore.QPoint(int(canvas._xval(300)), 60))
    assert spy.count() >= 1
    assert 295 <= canvas.lo <= 305 and canvas.lo < canvas.hi
    canvas.close()


def test_histogram_panel_modes_ranges_and_step_buttons(qapp):
    panel = HistogramLUT()
    panel.set_data_range(10, 1010)
    panel.set_histogram(np.arange(100, dtype=np.uint16).reshape(10, 10))
    panel.set_window(100, 900)
    assert panel.lo == 100 and panel.hi == 900
    before = panel.lo
    panel.lo_plus.click()
    assert panel.lo > before
    panel.fullrange_btn.click()
    assert panel.lo == 10 and panel.hi == 1010
    panel.custom_btn.click()
    assert panel.per_frame_mode() == "Custom"
    panel.log_chk.setChecked(True)
    assert panel.canvas.log


def test_color_bar_renders_gray_and_colormap(qapp):
    bar = ColorBar()
    bar.resize(320, 30)
    bar.set_range(12, 4095)
    bar.show()
    qapp.processEvents()
    gray = bar.grab().toImage()
    assert not gray.isNull() and gray.width() == 320
    bar.set_lut(2)
    qapp.processEvents()
    colored = bar.grab().toImage()
    assert colored.pixelColor(250, 5) != gray.pixelColor(250, 5)
    bar.close()


def test_frame_slider_keeps_extract_and_segment_state(qapp):
    slider = FrameSlider(Qt.Horizontal)
    slider.setRange(0, 100)
    slider.set_extract_zone(10, 30)
    slider.set_split_segments([(10, 20), (40, 60)])
    slider.resize(500, 30)
    slider.show()
    qapp.processEvents()
    assert (slider._in, slider._out) == (10, 30)
    assert slider._segments == [(10, 20), (40, 60)]
    assert not slider.grab().isNull()


def test_input_drop_zone_accepts_real_url_event_without_closing_app(qapp, tmp_path):
    path = tmp_path / "pairs.csv"
    path.write_text("frame,score\n0,1\n", encoding="utf-8")
    zone = InputDropZone("pairs", "")
    received = []
    zone.fileChanged.connect(lambda name, value: received.append((name, value)))
    zone.show()
    qapp.processEvents()
    enter, drop = _drop_path(zone, path)
    assert enter.isAccepted() and drop.isAccepted()
    assert received == [("pairs", str(path))]
    assert zone._path == str(path)
    zone.close()


def test_multiview_screen_text_accepts_fractional_view_rect(qapp):
    class Canvas(QtWidgets.QWidget):
        def view_rect(self, _index):
            return QtCore.QRectF(0.5, 1.5, 300.0, 180.0)

        def map_image_point(self, _index, x, y):
            return QtCore.QPointF(x, y)

    canvas = Canvas()
    canvas.resize(320, 200)
    overlay = MultiViewOverlay(canvas)
    overlay.resize(canvas.size())
    overlay.set_specs([{
        "type": "texte_ecran", "view": 0, "label": "60 keypoints apparies",
        "color": (255, 255, 255), "background": (20, 20, 25, 205),
    }])
    canvas.show()
    overlay.show()
    qapp.processEvents()
    # Direct call keeps Python exceptions observable by pytest. The former
    # QRect/QPointF mismatch raised here on the first paint after CSV drop.
    overlay.paintEvent(None)
    assert not overlay.grab().isNull()
    canvas.close()


def test_link_dialog_all_layouts_native_buttons_and_active_reopen(qapp):
    scenario_tests.test_link_dialogs(qapp)
