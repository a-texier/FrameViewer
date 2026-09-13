# -*- coding: utf-8 -*-
"""Icones vectorielles dessinees a la volee (QPainter), sans dependre d'une
police d'emoji. Sur Linux sans police emoji, les glyphes 📷/🔊/📋... s'affichent
en rectangles (tofu) : ces icones les remplacent partout.

Usage :
    from frameviewer.ui import icons
    btn.setIcon(icons.icon("camera"))
    btn.setText("")               # retirer l'emoji du libelle
    tabs.addTab(w, icons.icon("layers"), "Hist / Calque")
"""
from PySide6 import QtCore, QtGui
from PySide6.QtCore import Qt

_DEFAULT_COLOR = "#cdd6e0"


def _pt(seq):
    return [QtCore.QPointF(x, y) for (x, y) in seq]


# --- fonctions de dessin : recoivent un QPainter sur un canvas 18x18 ---

def _camera(p):
    p.drawRoundedRect(QtCore.QRectF(2, 5.5, 14, 9.5), 1.5, 1.5)
    p.drawPolyline(QtGui.QPolygonF(_pt([(6, 5.5), (7.2, 3.5), (10.8, 3.5), (12, 5.5)])))
    p.drawEllipse(QtCore.QPointF(9, 10.2), 2.7, 2.7)


def _speaker(p):
    p.drawPolygon(QtGui.QPolygonF(_pt([(3, 7), (6, 7), (9.5, 4), (9.5, 14), (6, 11), (3, 11)])))
    p.drawArc(QtCore.QRectF(9, 5.5, 6, 7), int(-55 * 16), int(110 * 16))
    p.drawArc(QtCore.QRectF(10.5, 7.2, 5, 3.6), int(-55 * 16), int(110 * 16))


def _speaker_muted(p):
    p.drawPolygon(QtGui.QPolygonF(_pt([(3, 7), (6, 7), (9.5, 4), (9.5, 14), (6, 11), (3, 11)])))
    p.drawLine(11.5, 6.5, 15.5, 11.5)
    p.drawLine(15.5, 6.5, 11.5, 11.5)


def _layers(p):
    p.drawPolygon(QtGui.QPolygonF(_pt([(9, 2.5), (16, 6), (9, 9.5), (2, 6)])))
    p.drawPolyline(QtGui.QPolygonF(_pt([(2, 9.5), (9, 13), (16, 9.5)])))
    p.drawPolyline(QtGui.QPolygonF(_pt([(2, 12.5), (9, 16), (16, 12.5)])))


def _puzzle(p):
    path = QtGui.QPainterPath()
    path.moveTo(3, 4)
    path.lineTo(7, 4)
    path.quadTo(7, 1.5, 9, 1.5)
    path.quadTo(11, 1.5, 11, 4)
    path.lineTo(15, 4)
    path.lineTo(15, 8)
    path.quadTo(12.5, 8, 12.5, 10)
    path.quadTo(12.5, 12, 15, 12)
    path.lineTo(15, 15)
    path.lineTo(3, 15)
    path.closeSubpath()
    p.drawPath(path)


def _gear(p):
    import math
    p.save()
    p.translate(9, 9)
    for k in range(8):
        p.save()
        p.rotate(k * 45)
        p.drawRect(QtCore.QRectF(-1.3, -8, 2.6, 3.2))
        p.restore()
    p.drawEllipse(QtCore.QPointF(0, 0), 4.6, 4.6)
    p.drawEllipse(QtCore.QPointF(0, 0), 1.9, 1.9)
    p.restore()
    _ = math


def _loop(p):
    p.drawArc(QtCore.QRectF(3, 3, 12, 12), int(60 * 16), int(250 * 16))
    p.drawPolyline(QtGui.QPolygonF(_pt([(12.5, 1.5), (14.5, 4.2), (11.6, 5)])))


def _record(p):
    p.setBrush(QtGui.QColor("#e05555"))
    p.drawEllipse(QtCore.QPointF(9, 9), 5, 5)


def _stop(p):
    p.setBrush(p.pen().color())
    p.drawRect(QtCore.QRectF(4.5, 4.5, 9, 9))


def _play(p):
    p.setBrush(p.pen().color())
    p.drawPolygon(QtGui.QPolygonF(_pt([(5, 3.5), (14, 9), (5, 14.5)])))


def _mouse(p):
    p.drawRoundedRect(QtCore.QRectF(5, 2.5, 8, 13), 4, 4)
    p.drawLine(9, 3.5, 9, 7.5)


def _toolbox(p):
    p.drawRoundedRect(QtCore.QRectF(2.5, 6.5, 13, 8.5), 1, 1)
    p.drawPolyline(QtGui.QPolygonF(_pt([(6.5, 6.5), (6.5, 4), (11.5, 4), (11.5, 6.5)])))
    p.drawLine(2.5, 10, 15.5, 10)
    p.drawRect(QtCore.QRectF(7.5, 8.5, 3, 3))


def _window(p):
    p.drawRoundedRect(QtCore.QRectF(2.5, 3.5, 13, 11), 1, 1)
    p.drawLine(2.5, 7, 15.5, 7)


def _keyboard(p):
    p.drawRoundedRect(QtCore.QRectF(2, 5, 14, 8), 1.2, 1.2)
    for x in (4.5, 7, 9.5, 12):
        p.drawPoint(QtCore.QPointF(x, 7.8))
    for x in (5.5, 8, 10.5):
        p.drawPoint(QtCore.QPointF(x, 10))
    p.drawLine(6.5, 11.2, 11.5, 11.2)


def _menu(p):
    for y in (5, 9, 13):
        p.drawLine(3, y, 15, y)


def _fullscreen(p):
    p.drawPolyline(QtGui.QPolygonF(_pt([(3, 6), (3, 3), (6, 3)])))
    p.drawPolyline(QtGui.QPolygonF(_pt([(12, 3), (15, 3), (15, 6)])))
    p.drawPolyline(QtGui.QPolygonF(_pt([(15, 12), (15, 15), (12, 15)])))
    p.drawPolyline(QtGui.QPolygonF(_pt([(6, 15), (3, 15), (3, 12)])))


def _swap(p):
    p.drawPolyline(QtGui.QPolygonF(_pt([(4, 6.5), (14, 6.5)])))
    p.drawPolyline(QtGui.QPolygonF(_pt([(11.5, 4), (14, 6.5), (11.5, 9)])))
    p.drawPolyline(QtGui.QPolygonF(_pt([(14, 11.5), (4, 11.5)])))
    p.drawPolyline(QtGui.QPolygonF(_pt([(6.5, 9), (4, 11.5), (6.5, 14)])))


def _link(p):
    p.drawArc(QtCore.QRectF(2.5, 6.5, 7, 5), int(90 * 16), int(180 * 16))
    p.drawArc(QtCore.QRectF(8.5, 6.5, 7, 5), int(-90 * 16), int(180 * 16))
    p.drawLine(6.5, 9, 11.5, 9)


def _film(p):
    p.drawRoundedRect(QtCore.QRectF(2.5, 3.5, 13, 11), 1, 1)
    p.drawLine(6, 3.5, 6, 14.5)
    p.drawLine(12, 3.5, 12, 14.5)
    for y in (5.5, 8.5, 11.5):
        p.drawLine(3.2, y, 5.3, y)
        p.drawLine(12.7, y, 14.8, y)


def _trash(p):
    p.drawLine(3, 5, 15, 5)
    p.drawPolyline(QtGui.QPolygonF(_pt([(7, 5), (7, 3), (11, 3), (11, 5)])))
    p.drawPolyline(QtGui.QPolygonF(_pt([(5, 5), (6, 15), (12, 15), (13, 5)])))
    p.drawLine(9, 7, 9, 13)


def _doc(p):
    p.drawPolygon(QtGui.QPolygonF(_pt([(4, 2), (11, 2), (14, 5), (14, 16), (4, 16)])))
    p.drawPolyline(QtGui.QPolygonF(_pt([(11, 2), (11, 5), (14, 5)])))
    for y in (9, 11, 13):
        p.drawLine(6, y, 12 if y < 13 else 10, y)


def _crop(p):
    # cadre de rognage / ROI (equerres)
    p.drawLine(5, 2.5, 5, 15.5)
    p.drawLine(2.5, 13, 15.5, 13)
    p.drawPolyline(QtGui.QPolygonF(_pt([(5, 5), (13, 5), (13, 15.5)])))
    p.drawPolyline(QtGui.QPolygonF(_pt([(2.5, 5), (5, 5)])))


def _roi(p):
    # rectangle de selection en pointilles + poignees
    pen = p.pen()
    pen2 = QtGui.QPen(pen)
    pen2.setStyle(Qt.DashLine)
    p.setPen(pen2)
    p.drawRect(QtCore.QRectF(3.5, 4.5, 11, 9))
    p.setPen(pen)
    p.setBrush(pen.color())
    for c in ((3.5, 4.5), (14.5, 4.5), (3.5, 13.5), (14.5, 13.5)):
        p.drawEllipse(QtCore.QPointF(*c), 1.3, 1.3)


def _scissors(p):
    p.drawEllipse(QtCore.QPointF(4.5, 12.5), 2.2, 2.2)
    p.drawEllipse(QtCore.QPointF(4.5, 5.5), 2.2, 2.2)
    p.drawLine(6.3, 11.3, 15, 4)
    p.drawLine(6.3, 6.7, 15, 14)


def _bracket_in(p):
    p.drawPolyline(QtGui.QPolygonF(_pt([(11, 3), (6, 3), (6, 15), (11, 15)])))
    p.setBrush(p.pen().color())
    p.drawPolygon(QtGui.QPolygonF(_pt([(9, 6.5), (13, 9), (9, 11.5)])))


def _bracket_out(p):
    p.drawPolyline(QtGui.QPolygonF(_pt([(7, 3), (12, 3), (12, 15), (7, 15)])))
    p.setBrush(p.pen().color())
    p.drawPolygon(QtGui.QPolygonF(_pt([(5, 6.5), (9, 9), (5, 11.5)])))


def _reticle(p):
    p.drawEllipse(QtCore.QPointF(9, 9), 5.5, 5.5)
    p.drawLine(9, 1.5, 9, 6)
    p.drawLine(9, 12, 9, 16.5)
    p.drawLine(1.5, 9, 6, 9)
    p.drawLine(12, 9, 16.5, 9)


def _plus(p):
    p.drawLine(9, 3.5, 9, 14.5)
    p.drawLine(3.5, 9, 14.5, 9)


_DRAW = {
    "camera": _camera,
    "speaker": _speaker,
    "speaker_muted": _speaker_muted,
    "layers": _layers,
    "puzzle": _puzzle,
    "gear": _gear,
    "loop": _loop,
    "record": _record,
    "stop": _stop,
    "play": _play,
    "mouse": _mouse,
    "toolbox": _toolbox,
    "window": _window,
    "keyboard": _keyboard,
    "menu": _menu,
    "fullscreen": _fullscreen,
    "swap": _swap,
    "link": _link,
    "film": _film,
    "trash": _trash,
    "doc": _doc,
    "crop": _crop,
    "roi": _roi,
    "scissors": _scissors,
    "bracket_in": _bracket_in,
    "bracket_out": _bracket_out,
    "reticle": _reticle,
    "plus": _plus,
}


def pixmap(name, size=18, color=_DEFAULT_COLOR, width=1.4):
    pm = QtGui.QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    if size != 18:
        p.scale(size / 18.0, size / 18.0)
    pen = QtGui.QPen(QtGui.QColor(color))
    pen.setWidthF(width)
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    fn = _DRAW.get(name)
    if fn is not None:
        fn(p)
    p.end()
    return pm


def icon(name, size=18, color=_DEFAULT_COLOR, width=1.4):
    return QtGui.QIcon(pixmap(name, size, color, width))
