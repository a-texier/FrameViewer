# -*- coding: utf-8 -*-
"""Vue image centrale : zoom/pan, ROI, ligne de profil, regle, dessin des
overlays SIDECAR."""
import math

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

class VideoWidget(QtWidgets.QWidget):
    clicked = QtCore.Signal(int, int)                 # coordonnees image
    roiChanged = QtCore.Signal(int, int, int, int)    # x, y, w, h (image)
    lineChanged = QtCore.Signal(int, int, int, int)   # x1,y1,x2,y2 (profil ligne)
    rulerChanged = QtCore.Signal(object)              # list of (x,y) tuples (règle)
    hovered = QtCore.Signal(int, int)                 # survol : coordonnees image (-1,-1 = sortie)
    viewChanged = QtCore.Signal()                     # zoom / pan modifié (pour lier les vues)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(360, 240)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)   # recoit les fleches/raccourcis
        self._pixmap = None
        self._iw = 0
        self._ih = 0
        self._markers = []         # (x, y, index)
        self._overlays = []        # graphiques SIDECAR de la frame courante
        self._scale = 1.0
        self._ox = 0.0
        self._oy = 0.0
        self._hover = None         # (ix, iy)
        self._probe_txt = None     # texte "x= y= v=" affiché en haut-gauche (vert)
        # zoom / pan
        self._zoom = 1.0
        self._pan = QtCore.QPointF(0.0, 0.0)
        self._panning = False
        self._pan_moved = False
        self._pan_anchor = QtCore.QPointF(0.0, 0.0)
        self._pan_orig = QtCore.QPointF(0.0, 0.0)
        # selection rectangulaire (ROI) : clic-glisse = rectangle, clic simple = clic
        self._roi = None           # (x, y, w, h) en coords image
        self._roi_green = False    # ROI dessinee en vert (mode crop d'export)
        self._roi_drag = None      # coin de depart (ix, iy)
        self._left_down = False
        self._drawing_roi = False
        self._press_pos = QtCore.QPointF(0.0, 0.0)
        # outil actif : "roi" | "line" | "ruler"
        self._tool_mode = "roi"
        self._line = None          # (x1,y1,x2,y2) en cours de trace
        self._line_start = None    # point de depart du trace
        self._ruler_pts = []       # [(x,y)] 0/1/2 points cliques

    def set_tool_mode(self, mode):
        """mode : "roi" | "line" | "ruler"."""
        self._tool_mode = mode
        self._line = None
        self._line_start = None
        self._ruler_pts = []
        cur = {
            "roi":   Qt.ArrowCursor,
            "line":  Qt.CrossCursor,
            "ruler": Qt.CrossCursor,
        }.get(mode, Qt.ArrowCursor)
        self.setCursor(cur)
        self.update()

    def set_frame(self, bgr):
        if bgr is None:
            self._pixmap = None
            self.update()
            return
        bgr = np.ascontiguousarray(bgr)
        if bgr.ndim == 2:
            h, w = bgr.shape
            qimg = QtGui.QImage(bgr.data, w, h, bgr.strides[0], QtGui.QImage.Format_Grayscale8)
        else:
            h, w = bgr.shape[:2]
            qimg = QtGui.QImage(bgr.data, w, h, bgr.strides[0], QtGui.QImage.Format_BGR888)
        self._iw, self._ih = w, h
        self._pixmap = QtGui.QPixmap.fromImage(qimg)
        self.update()

    def set_markers(self, markers):
        self._markers = markers
        self.update()

    def set_overlays(self, overlays):
        self._overlays = overlays or []
        self.update()

    def _fit_scale(self):
        if self._iw == 0 or self._ih == 0:
            return 1.0
        return min(self.width() / self._iw, self.height() / self._ih)

    def _geom(self):
        if not self._pixmap or self._iw == 0 or self._ih == 0:
            return
        W, H = self.width(), self.height()
        s = self._fit_scale() * self._zoom
        self._scale = s
        self._ox = (W - self._iw * s) / 2.0 + self._pan.x()
        self._oy = (H - self._ih * s) / 2.0 + self._pan.y()

    def _img_at(self, pos):
        """Coordonnees image (float, non bornees) sous un point ecran."""
        self._geom()
        if self._scale <= 0:
            return 0.0, 0.0
        return ((pos.x() - self._ox) / self._scale,
                (pos.y() - self._oy) / self._scale)

    def _to_image(self, pos):
        self._geom()
        if self._scale <= 0:
            return None
        ix = (pos.x() - self._ox) / self._scale
        iy = (pos.y() - self._oy) / self._scale
        if 0 <= ix < self._iw and 0 <= iy < self._ih:
            return int(ix), int(iy)
        return None

    def _w(self, x, y):
        return QtCore.QPointF(self._ox + x * self._scale, self._oy + y * self._scale)

    def _clampx(self, x):
        return int(max(0, min(self._iw, round(x))))

    def _clampy(self, y):
        return int(max(0, min(self._ih, round(y))))

    def _clamp_pan(self):
        if not self._pixmap:
            return
        W, H = self.width(), self.height()
        s = self._fit_scale() * self._zoom
        over_x = max(0.0, (self._iw * s - W) / 2.0)
        over_y = max(0.0, (self._ih * s - H) / 2.0)
        self._pan = QtCore.QPointF(max(-over_x, min(over_x, self._pan.x())),
                                   max(-over_y, min(over_y, self._pan.y())))

    # ----- zoom / pan API -----
    def get_view(self):
        return (self._zoom, self._pan.x(), self._pan.y())

    def set_view(self, zoom, panx, pany):
        self._zoom = max(1.0, min(60.0, float(zoom)))
        self._pan = QtCore.QPointF(float(panx), float(pany))
        self._clamp_pan()
        self.update()

    def _zoom_at(self, pos, factor):
        if not self._pixmap:
            return
        bx, by = self._img_at(pos)                 # point image sous le curseur (avant)
        new_zoom = max(1.0, min(60.0, self._zoom * factor))
        if abs(new_zoom - self._zoom) < 1e-6:
            return
        self._zoom = new_zoom
        W, H = self.width(), self.height()
        s = self._fit_scale() * self._zoom
        base_x = (W - self._iw * s) / 2.0
        base_y = (H - self._ih * s) / 2.0
        if self._zoom <= 1.0001:
            self._pan = QtCore.QPointF(0.0, 0.0)
        else:
            self._pan = QtCore.QPointF(pos.x() - base_x - bx * s,
                                       pos.y() - base_y - by * s)
        self._clamp_pan()
        self.update()
        self.viewChanged.emit()

    def fit(self):
        self._zoom = 1.0
        self._pan = QtCore.QPointF(0.0, 0.0)
        self.update()

    def zoom_in(self):
        self._zoom_at(QtCore.QPointF(self.width() / 2.0, self.height() / 2.0), 1.25)

    def zoom_out(self):
        self._zoom_at(QtCore.QPointF(self.width() / 2.0, self.height() / 2.0), 1.0 / 1.25)

    def reset_view(self):
        self._zoom = 1.0
        self._pan = QtCore.QPointF(0.0, 0.0)
        self._panning = False
        self._roi = None
        self._roi_drag = None
        self.update()

    def wheelEvent(self, e):
        if not self._pixmap:
            return
        factor = 1.2 if e.angleDelta().y() > 0 else (1.0 / 1.2)
        self._zoom_at(e.position(), factor)

    def set_probe_text(self, txt):
        self._probe_txt = txt
        self.update()

    # ----- selection rectangulaire (ROI) -----
    def clear_roi(self):
        self._roi = None
        self._roi_drag = None
        self.update()

    def set_roi_export_mode(self, on):
        """Dessine la ROI en vert (mode selection de sous-zone a exporter)."""
        self._roi_green = bool(on)
        self.update()

    def set_roi_rect(self, x, y, w, h):
        """Impose le rectangle ROI affiché (ex. suivi d'une boîte .ver),
        sans passer par un geste souris."""
        self._roi = (x, y, w, h)
        self._roi_drag = None
        self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(20, 20, 22))
        if not self._pixmap:
            p.setPen(QtGui.QColor(150, 150, 150))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "Glisse ici une video, une image, un dossier, un .specialized\n"
                       "ou un .sidecar de calques (overlays)\n\n"
                       "(ou depose un fichier sur l'icone de l'exe)")
            return
        self._geom()
        target = QtCore.QRectF(self._ox, self._oy, self._iw * self._scale, self._ih * self._scale)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        p.drawPixmap(target, self._pixmap, QtCore.QRectF(self._pixmap.rect()))

        self._draw_overlays(p)

        # marqueurs de clics de la frame courante
        for (mx, my, mi) in self._markers:
            wp = self._w(mx, my)
            wx, wy = wp.x(), wp.y()
            p.setPen(QtGui.QPen(QtGui.QColor(0, 230, 90), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawLine(QtCore.QLineF(wx - 7, wy, wx + 7, wy))
            p.drawLine(QtCore.QLineF(wx, wy - 7, wx, wy + 7))
            p.drawEllipse(QtCore.QPointF(wx, wy), 7, 7)
            p.drawText(QtCore.QPointF(wx + 9, wy - 3), str(mi))

        # croix de visee + coordonnees (+ valeur pixel) sous le curseur
        if self._hover is not None:
            ix, iy = self._hover
            wp = self._w(ix, iy)
            wx, wy = wp.x(), wp.y()
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 70), 1))
            p.drawLine(QtCore.QLineF(target.left(), wy, target.right(), wy))
            p.drawLine(QtCore.QLineF(wx, target.top(), wx, target.bottom()))
            # texte "x= y= v=" en haut-gauche de la vue (vert), valeur si connue
            txt = self._probe_txt or f"x= {ix}   y= {iy}"
            f = p.font(); f.setBold(True); p.setFont(f)
            p.setPen(QtGui.QColor(0, 0, 0))
            p.drawText(11, 21, txt)
            p.setPen(QtGui.QColor(0, 230, 90))
            p.drawText(10, 20, txt)

        # rectangle de selection (ROI)
        if self._roi is not None and self._roi[2] > 0 and self._roi[3] > 0:
            rx, ry, rw, rh = self._roi
            rect = QtCore.QRectF(self._w(rx, ry), self._w(rx + rw, ry + rh))
            _rc = (QtGui.QColor(40, 220, 90) if self._roi_green
                   else QtGui.QColor(255, 220, 40))
            pen = QtGui.QPen(_rc, 1.5, Qt.DashLine)
            pen.setCosmetic(True)
            p.setPen(pen)
            _fill = QtGui.QColor(_rc); _fill.setAlpha(30)
            p.setBrush(_fill)
            p.drawRect(rect)

        # ── profil de ligne (outil line) ──
        if self._tool_mode == "line" and self._line:
            x1, y1, x2, y2 = self._line
            p.setPen(QtGui.QPen(QtGui.QColor(255, 220, 0), 2))
            p.drawLine(self._w(x1, y1), self._w(x2, y2))
            p.setBrush(QtGui.QColor(255, 220, 0))
            for px, py in ((x1, y1), (x2, y2)):
                p.drawEllipse(self._w(px, py), 4, 4)
            p.setBrush(Qt.NoBrush)
        # ── règle multipoint (outil ruler) ──
        if self._tool_mode == "ruler" and self._ruler_pts:
            pts_w = [self._w(x, y) for (x, y) in self._ruler_pts]
            p.setPen(QtGui.QPen(QtGui.QColor(255, 120, 0), 2))
            p.setBrush(QtGui.QColor(255, 120, 0))
            for wp in pts_w:
                p.drawEllipse(wp, 4, 4)
            p.setBrush(Qt.NoBrush)
            if len(pts_w) == 1:
                p.setPen(QtGui.QColor(255, 120, 0))
                p.drawText(pts_w[0] + QtCore.QPointF(6, -4), "P1")
            for i in range(1, len(pts_w)):
                p.setPen(QtGui.QPen(QtGui.QColor(255, 120, 0), 2))
                p.drawLine(pts_w[i - 1], pts_w[i])
                x1, y1 = self._ruler_pts[i - 1]; x2, y2 = self._ruler_pts[i]
                dist = math.hypot(x2 - x1, y2 - y1)
                mid = QtCore.QPointF((pts_w[i-1].x() + pts_w[i].x()) / 2,
                                     (pts_w[i-1].y() + pts_w[i].y()) / 2)
                p.setPen(QtGui.QColor(0, 0, 0))
                p.drawText(mid + QtCore.QPointF(7, 1), f"{dist:.1f}px")
                p.setPen(QtGui.QColor(255, 120, 0))
                p.drawText(mid + QtCore.QPointF(6, 0), f"{dist:.1f}px")

    def _draw_overlays(self, p):
        for g in self._overlays:
            col = QtGui.QColor(*g.get("color", (40, 220, 60)))
            thick = max(1, int(g.get("thickness", 1)))
            pen = QtGui.QPen(col, thick)
            pen.setCosmetic(True)
            p.setPen(pen)
            pts = [self._w(x, y) for (x, y) in g.get("points", [])]
            t = g.get("type", "")
            if t == "points":
                p.setBrush(col)
                for wp in pts:
                    p.drawEllipse(wp, 2.6, 2.6)
                p.setBrush(Qt.NoBrush)
            elif t in ("croix", "croixx", "x"):
                d = 5.0
                for wp in pts:
                    p.drawLine(QtCore.QLineF(wp.x() - d, wp.y() - d, wp.x() + d, wp.y() + d))
                    p.drawLine(QtCore.QLineF(wp.x() - d, wp.y() + d, wp.x() + d, wp.y() - d))
            elif t in ("ligne_brisee_fermee", "polygone", "polygone_ferme"):
                p.setBrush(Qt.NoBrush)
                if len(pts) >= 2:
                    p.drawPolygon(QtGui.QPolygonF(pts))
            elif t in ("ligne_brisee", "polyligne", "ligne", "segment"):
                if len(pts) >= 2:
                    p.drawPolyline(QtGui.QPolygonF(pts))
            elif t in ("ellipse", "cercle"):
                if pts:
                    a = g.get("a", 0.0) * self._scale
                    b = g.get("b", 0.0) * self._scale
                    if b <= 0:
                        b = a
                    p.setBrush(Qt.NoBrush)
                    p.save()
                    p.translate(pts[0])
                    p.rotate(g.get("rotation", 0.0))
                    p.drawEllipse(QtCore.QPointF(0, 0), max(0.5, a), max(0.5, b))
                    p.restore()
            else:
                p.setBrush(col)
                for wp in pts:
                    p.drawEllipse(wp, 2.0, 2.0)
                p.setBrush(Qt.NoBrush)

            tx = g.get("text")
            if tx and tx.get("label"):
                if tx.get("auto") and pts:
                    anchor = QtCore.QPointF(pts[0].x() + 6, pts[0].y() - 6)
                elif pts:
                    anchor = QtCore.QPointF(pts[0].x() + tx.get("x", 0) * self._scale,
                                            pts[0].y() + tx.get("y", 0) * self._scale)
                else:
                    anchor = self._w(tx.get("x", 0), tx.get("y", 0))
                font = p.font()
                font.setPixelSize(max(8, int(tx.get("size", 10))))
                p.setFont(font)
                p.setPen(QtGui.QPen(QtGui.QColor(*tx.get("color", g.get("color", (40, 220, 60))))))
                p.drawText(anchor, tx["label"])

    def mousePressEvent(self, e):
        if not self._pixmap:
            return
        self.setFocus()
        if e.button() == Qt.MiddleButton:          # clic molette -> panoramique
            self._panning = True
            self._pan_moved = False
            self._pan_anchor = e.position()
            self._pan_orig = QtCore.QPointF(self._pan)
            self.setCursor(Qt.ClosedHandCursor)
            return
        # ── clic droit en mode règle : réinitialiser la polyligne ──
        if e.button() == Qt.RightButton and self._tool_mode == "ruler":
            self._ruler_pts.clear()
            self.rulerChanged.emit([])
            self.update()
            return
        if e.button() == Qt.LeftButton:
            self._left_down = True
            ix, iy = self._img_at(e.position())
            # ── mode profil de ligne ──
            if self._tool_mode == "line":
                self._line_start = (self._clampx(ix), self._clampy(iy))
                self._line = None
                self.update()
                return
            # ── mode règle : polyligne multipoint (clic droit pour stop/reset) ──
            if self._tool_mode == "ruler":
                pt = (self._clampx(ix), self._clampy(iy))
                self._ruler_pts.append(pt)
                self.rulerChanged.emit(list(self._ruler_pts))
                self.update()
                return
            # ── mode ROI (défaut) ──
            self._drawing_roi = False
            self._press_pos = e.position()
            self._roi_drag = (self._clampx(ix), self._clampy(iy))

    def mouseMoveEvent(self, e):
        if self._panning:
            d = e.position() - self._pan_anchor
            if abs(d.x()) + abs(d.y()) > 3:
                self._pan_moved = True
            self._pan = QtCore.QPointF(self._pan_orig.x() + d.x(),
                                       self._pan_orig.y() + d.y())
            self._clamp_pan()
            self.update()
            self.viewChanged.emit()
            return
        # ── mode profil de ligne (glisse) ──
        if self._tool_mode == "line" and self._left_down and self._line_start is not None:
            ix, iy = self._img_at(e.position())
            x1, y1 = self._line_start
            self._line = (x1, y1, self._clampx(ix), self._clampy(iy))
            self._hover = self._to_image(e.position())
            self.update()
            return
        # clic gauche maintenu + deplacement -> on dessine un rectangle (ROI)
        if self._left_down and self._roi_drag is not None:
            d = e.position() - self._press_pos
            if not self._drawing_roi and (abs(d.x()) + abs(d.y())) > 4:
                self._drawing_roi = True
            if self._drawing_roi:
                x0, y0 = self._roi_drag
                ix, iy = self._img_at(e.position())
                x1, y1 = self._clampx(ix), self._clampy(iy)
                self._roi = (min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
                self._hover = self._to_image(e.position())
                self.update()
                return
        self._hover = self._to_image(e.position()) if self._pixmap else None
        if self._hover is not None:
            self.hovered.emit(int(self._hover[0]), int(self._hover[1]))
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton and self._panning:
            self._panning = False
            self.setCursor(Qt.CrossCursor if self._tool_mode in ("line", "ruler") else Qt.ArrowCursor)
            if not self._pan_moved:                # clic molette sans glissement -> ajuster
                self.fit()
            return
        if e.button() == Qt.LeftButton and self._left_down:
            self._left_down = False
            # ── mode profil de ligne ──
            if self._tool_mode == "line" and self._line_start is not None:
                if self._line:
                    x1, y1, x2, y2 = self._line
                    if abs(x2 - x1) + abs(y2 - y1) > 2:
                        self.lineChanged.emit(x1, y1, x2, y2)
                self._line_start = None
                return
            if self._drawing_roi:                  # rectangle dessine -> ROI (toggle auto)
                self._drawing_roi = False
                self._roi_drag = None
                if self._roi and self._roi[2] >= 2 and self._roi[3] >= 2:
                    x, y, w, h = self._roi
                    self.roiChanged.emit(int(x), int(y), int(w), int(h))
                else:
                    self._roi = None
                    self.roiChanged.emit(0, 0, 0, 0)
                self.update()
                return
            # clic ponctuel -> enregistrement de clic (sans toucher a la ROI existante)
            self._roi_drag = None
            pt = self._to_image(e.position())
            if pt is not None:
                self.clicked.emit(pt[0], pt[1])

    def leaveEvent(self, _):
        self._hover = None
        self.hovered.emit(-1, -1)
        self.update()



