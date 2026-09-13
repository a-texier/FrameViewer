# -*- coding: utf-8 -*-
"""Grille multi-vues : mini-barre de lecture, cadre de vue commun
(BaseViewFrame/PrimaryFrame/SatelliteView), fusion (blend), SplitCanvas,
et le slider de navigation avec zone IN/OUT."""
import math
import os

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from frameviewer.core.annotations import bake_sidecar_overlays, draw_annotation_boxes
from frameviewer.core.feature_registry import supports_path
from frameviewer.core.io_utils import fmt_time
from frameviewer.core.pipeline import BLEND_MODES, blend_frames, render_frame
from frameviewer.core.plugin_render import alpha_over

try:
    from frameviewer.core.annotation_loader import is_annotation_path
except ImportError:
    def is_annotation_path(path):
        return os.path.splitext(path)[1].lower() in (".ver", ".txt")
from frameviewer.ui.video_widget import VideoWidget

class MiniBar(QtWidgets.QWidget):
    """Barre de lecture compacte présente sur CHAQUE vue en multivue."""
    first       = QtCore.Signal()
    prev        = QtCore.Signal()
    nxt         = QtCore.Signal()
    last        = QtCore.Signal()
    playToggled = QtCore.Signal(bool)
    loopToggled = QtCore.Signal(bool)
    seek        = QtCore.Signal(int)

    _BS = ("QPushButton{padding:0px;border:1px solid #444;border-radius:2px;"
           "background:#1c1c22;color:#ccc;font-size:10px;}"
           "QPushButton:hover{border-color:#5aafff;}"
           "QPushButton:checked{background:#2d4f70;border-color:#5aafff;color:#fff;}")

    def __init__(self, parent=None):
        super().__init__(parent)
        # FrameSlider = QSlider avec surbrillance rouge de la zone IN→OUT
        self.slider = FrameSlider(Qt.Horizontal)
        self.slider.setFocusPolicy(Qt.NoFocus)
        self.slider.setFixedHeight(14)
        self.slider.valueChanged.connect(self.seek.emit)

        def _b(txt, tip, checkable=False):
            b = QtWidgets.QPushButton(txt)
            b.setFixedSize(20, 17)
            b.setFocusPolicy(Qt.NoFocus)
            b.setCheckable(checkable)
            b.setToolTip(tip)
            b.setStyleSheet(self._BS)
            return b

        self.b_first = _b("|◀", "Première frame")
        self.b_prev  = _b("◀", "Frame précédente")
        self.b_play  = _b("▶", "Lecture / pause", True)
        self.b_next  = _b("▶|", "Frame suivante")
        self.b_last  = _b("▶▶", "Dernière frame")
        self.b_loop  = _b("↻", "Lecture en boucle", True)
        self.b_first.clicked.connect(self.first.emit)
        self.b_prev.clicked.connect(self.prev.emit)
        self.b_next.clicked.connect(self.nxt.emit)
        self.b_last.clicked.connect(self.last.emit)
        self.b_play.toggled.connect(self.playToggled.emit)
        self.b_loop.toggled.connect(self.loopToggled.emit)

        self.lbl = QtWidgets.QLabel("─")
        self.lbl.setStyleSheet("color:#8a8a95; font-size:9px;")

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(2, 0, 2, 1)
        row.setSpacing(2)
        for b in (self.b_first, self.b_prev, self.b_play,
                  self.b_next, self.b_last, self.b_loop):
            row.addWidget(b)
        row.addWidget(self.lbl, 1)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.slider)
        lay.addLayout(row)

    def set_extract_zone(self, in_f, out_f):
        self.slider.set_extract_zone(in_f, out_f)

    def set_split_segments(self, segments):
        self.slider.set_split_segments(segments)

    def set_range(self, n):
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, n - 1))
        self.slider.blockSignals(False)

    def set_pos(self, cur):
        self.slider.blockSignals(True)
        self.slider.setValue(cur)
        self.slider.blockSignals(False)

    def set_info(self, cur, total, fps):
        self.lbl.setText(f"{cur}/{total}  {fmt_time(cur / max(fps, 0.001))}  {fps:.0f}fps")

    def set_playing(self, on):
        self.b_play.blockSignals(True)
        self.b_play.setChecked(on)
        self.b_play.setText("⏸" if on else "▶")
        self.b_play.blockSignals(False)


# ─────────────────────── cadre commun d'une vue ───────────────────
class BaseViewFrame(QtWidgets.QFrame):
    """Bandeau [i] nom + mini-barre + sélection + drops + échange par clic droit.
    Toutes les vues (principale ou secondaire) en héritent : elles sont
    strictement identiques du point de vue de l'utilisateur."""
    activated      = QtCore.Signal(int)        # position cliquée
    doubled        = QtCore.Signal(int)        # double-clic -> plein écran
    mediaDropped   = QtCore.Signal(int, list)  # média déposé
    overlayDropped = QtCore.Signal(int, str)   # .sidecar / .ver déposé
    folderDropped  = QtCore.Signal(int, str)   # dossier seul déposé (média +/- annotations)
    swapRequested  = QtCore.Signal(int, int)   # (depuis_pos, vers_pos)
    convertClicked = QtCore.Signal(int)        # bouton "Convertir" de la vue
    navInteracted  = QtCore.Signal(int)        # play/precedent/suivant de CETTE vue touche a la main
    frameChanged   = QtCore.Signal(int, int)   # position et frame affichee

    _SS_IDLE   = "QFrame#vf{border:2px solid #333;background:#0e0e10;}"
    _SS_ACTIVE = "QFrame#vf{border:2px solid #5aafff;background:#0e0e10;}"
    _SS_ACTIVE_GREEN = "QFrame#vf{border:2px solid #3ad06a;background:#0e0e10;}"
    _SS_FROZEN = "QFrame#vf{border:2px solid #4a4a52;background:#141416;}"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("vf")
        self.setStyleSheet(self._SS_IDLE)
        self.setAcceptDrops(True)
        self.setMinimumSize(130, 100)
        self.pos_idx = 0
        self._name = ""
        self._drag_from = None
        self._vid = None
        self._is_active = False
        self._active_green = False     # highlight vert (vue moteur en temporel)
        self._frozen = False           # vue figee (vue i-N en temporel)
        self._frozen_label = None

        self.banner = QtWidgets.QLabel(" [1] — vide —")
        self.banner.setStyleSheet(
            "background:#141418; color:#5aafff; font-size:10px; padding:1px 4px;")
        self.banner.setToolTip(
            "Clic : sélectionner cette vue (outils TI + histogramme)\n"
            "Double-clic : plein écran / retour\n"
            "Clic droit glissé : échanger avec une autre vue")

        self.convert_btn = QtWidgets.QToolButton()
        self.convert_btn.setText("⇄ Convertir")
        self.convert_btn.setToolTip(
            "Convertir cette vue vers un autre format\n"
            "(avec calques + contraste gravés si coché)")
        self.convert_btn.setFixedHeight(22)
        self.convert_btn.setFocusPolicy(Qt.NoFocus)
        self.convert_btn.setStyleSheet(
            "QToolButton{background:#1c1c22;color:#ddd;border:1px solid #444;"
            "border-radius:3px;font-size:11px;padding:2px 8px;}"
            "QToolButton:hover{border-color:#5aafff;color:#fff;}")
        self.convert_btn.clicked.connect(lambda: self.convertClicked.emit(self.pos_idx))

        self.banner_row = QtWidgets.QWidget()
        self.banner_row.setStyleSheet("background:#141418;")
        _brow = QtWidgets.QHBoxLayout(self.banner_row)
        _brow.setContentsMargins(0, 0, 0, 0)
        _brow.setSpacing(0)
        _brow.addWidget(self.banner, 1)
        _brow.addWidget(self.convert_btn)

        self.mini = MiniBar()

    # -- bandeau / position --
    def set_name(self, name):
        self._name = name or ""
        self._refresh_banner()

    def set_position(self, p):
        self.pos_idx = p
        self._refresh_banner()

    def _refresh_banner(self):
        if self._frozen:
            return   # le bandeau figé (Past Frame) prime
        self.banner.setText(f" [{self.pos_idx + 1}] {self._name or '— vide —'}")

    def _apply_style(self):
        if self._frozen:
            self.setStyleSheet(self._SS_FROZEN)
        elif self._is_active:
            self.setStyleSheet(self._SS_ACTIVE_GREEN if self._active_green
                               else self._SS_ACTIVE)
        else:
            self.setStyleSheet(self._SS_IDLE)

    def set_active(self, on):
        self._is_active = bool(on)
        self._apply_style()

    def set_active_green(self, on):
        """Choisit la couleur de sélection : vert (vue moteur en temporel) au
        lieu du bleu par défaut."""
        self._active_green = bool(on)
        self._apply_style()

    def set_frozen(self, on, label=None):
        """Vue figée (mode temporel, vue i-N) : zone grisée, pas de bouton
        Convertir ni de mini-barre, bandeau vert clair 'Past Frame', et clics
        souris ignorés (aucune activation / outil / échange)."""
        self._frozen = bool(on)
        self.convert_btn.setVisible(not self._frozen)
        if self._frozen:
            self.mini.setVisible(False)
            self._frozen_label = label or "Past Frame"
            self.banner.setText(f" {self._frozen_label}")
            self.banner.setStyleSheet(
                "background:#bfe8c8; color:#000; font-weight:bold; "
                "font-size:10px; padding:1px 4px;")
        else:
            self._frozen_label = None
            self.banner.setStyleSheet(
                "background:#141418; color:#5aafff; font-size:10px; padding:1px 4px;")
            self._refresh_banner()
        self._apply_style()

    def set_chrome_visible(self, on):
        """Le bandeau [i] nom est TOUJOURS affiché (même en vue unique).
        La mini-barre de lecture n'apparaît qu'en multivue."""
        self.banner.setVisible(True)
        self.mini.setVisible(on)

    # -- interactions souris sur la zone vidéo --
    def _watch_video(self, vid):
        self._vid = vid
        vid.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if obj is self._vid:
            t = ev.type()
            if self._frozen and t in (QtCore.QEvent.MouseButtonPress,
                                      QtCore.QEvent.MouseButtonDblClick,
                                      QtCore.QEvent.MouseButtonRelease,
                                      QtCore.QEvent.MouseMove):
                # vue figée : on avale les interactions (pas d'activation, pas
                # d'outil, pas d'échange) ; on garde juste le focus clavier.
                if t == QtCore.QEvent.MouseButtonPress:
                    self.window().setFocus(Qt.MouseFocusReason)
                return True
            if t == QtCore.QEvent.MouseButtonDblClick:
                self.doubled.emit(self.pos_idx)
                return True
            if t == QtCore.QEvent.MouseButtonPress:
                # Ce filtre AVALE le clic (return True) avant que Qt ne fasse
                # son focus-follows-click habituel sur self._vid -> sans ce
                # setFocus() explicite, le focus clavier reste bloque sur le
                # dernier widget interactif touche (ex: onglets du panneau
                # droit, qui capturent Gauche/Droite pour changer d'onglet)
                # et les fleches du clavier n'atteignent plus jamais
                # MainWindow.keyPressEvent.
                self.window().setFocus(Qt.MouseFocusReason)
                if ev.button() == Qt.RightButton:
                    self._drag_from = self._evt_pos(ev)
                    return True
                was_active = self._is_active
                self.activated.emit(self.pos_idx)
                if not was_active:
                    # Ce clic sert uniquement à activer la vue : l'activation
                    # peut reparenter/échanger les widgets (moteur <-> vue) en
                    # plein milieu du geste souris. On ne laisse donc PAS ce
                    # même clic démarrer un outil (ROI/ligne/règle) sur un
                    # widget qui vient de changer de rôle -> évite le
                    # rectangle ROI fantôme propagé sur la mauvaise vue.
                    return True
            elif t == QtCore.QEvent.MouseMove and self._drag_from is not None:
                if (self._evt_pos(ev) - self._drag_from).manhattanLength() > 12:
                    self._drag_from = None
                    self._start_swap_drag()
                    return True
            elif t == QtCore.QEvent.MouseButtonRelease:
                if ev.button() == Qt.RightButton:
                    self._drag_from = None
                    return True
        return super().eventFilter(obj, ev)

    @staticmethod
    def _evt_pos(ev):
        try:
            return ev.position().toPoint()
        except AttributeError:
            return ev.pos()

    def _start_swap_drag(self):
        mime = QtCore.QMimeData()
        mime.setData("application/x-fv-viewpos", str(self.pos_idx).encode())
        drag = QtGui.QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)

    # -- drag & drop --
    def dragEnterEvent(self, e):
        md = e.mimeData()
        if (md.hasFormat("application/x-fv-viewpos")
                or md.hasUrls() or md.hasText()):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        self.dragEnterEvent(e)

    def dropEvent(self, e):
        if self._frozen:
            return   # la vue i-N est un miroir figé du flux moteur : pas de drop
        md = e.mimeData()
        if md.hasFormat("application/x-fv-viewpos"):
            try:
                src = int(bytes(md.data("application/x-fv-viewpos")).decode())
            except Exception:
                return
            if src != self.pos_idx:
                self.swapRequested.emit(src, self.pos_idx)
            e.acceptProposedAction()
            return
        paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
        if not paths and md.hasText():
            paths = [t for t in [md.text()] if t]
        if not paths:
            return
        if len(paths) == 1 and os.path.isdir(paths[0]):
            # un seul dossier depose : la decision (images ? annotations ?
            # rien d'exploitable directement ?) est centralisee cote
            # MainWindow._open_dropped_folder -- source unique, ne PAS la
            # dupliquer ici (c'est exactement ce qui a deja cause un bug).
            self.folderDropped.emit(self.pos_idx, paths[0])
            e.acceptProposedAction()
            return
        def _is_overlay(p):
            # .sidecar = calque VISUIMG ; is_annotation_path = .ver / .txt isole /
            # dossier YOLO (un .txt par frame) -- CE dernier cas manquait ici
            # (present seulement dans MainWindow.dropEvent), ce qui faisait
            # traiter un dossier d'annotations YOLO comme un dossier media.
            return supports_path(p, "overlay_format") or is_annotation_path(p)
        # plusieurs .ver/.sidecar/dossiers YOLO d'un coup = plusieurs calques
        if all(_is_overlay(p) for p in paths):
            for p in paths:
                self.overlayDropped.emit(self.pos_idx, p)
        else:
            self.mediaDropped.emit(self.pos_idx, paths)
        e.acceptProposedAction()


# ──────────────────────────── vue secondaire ──────────────────────
class SatelliteView(BaseViewFrame):
    """Vue autonome (non pilotée par le moteur) : source, lecture et rendu propres."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = None
        self._cur = 0
        self._fps = 25.0
        self._loop = True
        self._rp = (0, 255, None, None, 0, (0, 255), {})
        self._last_bgr = None
        self._fetch_scale = 1.0
        self._overlays = {}
        self._annots = {}
        self._annot_path = ""
        self._layers = []
        self._hist_mode = "Custom"
        self._rec_on = False
        self.on_render = None
        # fourni par MainWindow : overlay_provider(source_key, frame_idx, size)
        # -> BGRA|None. Permet a CETTE vue d'afficher son propre overlay plugin
        # (par vue, comme les .ver), meme non selectionnee / en lecture.
        self.overlay_provider = None

        self.video = VideoWidget()
        self._watch_video(self.video)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.banner_row)
        lay.addWidget(self.video, 1)
        lay.addWidget(self.mini)

        # navInteracted : touche a la main play/precedent/suivant de CETTE
        # vue -> les fleches clavier gauche/droite doivent ensuite lui etre
        # dediees (voir MainWindow._on_view_nav_interacted / _kbd_target),
        # sans swap moteur (contrairement a un clic sur l'image elle-meme).
        self.mini.first.connect(lambda: (self._goto(0),
                                         self.navInteracted.emit(self.pos_idx)))
        self.mini.prev.connect(lambda: (self._goto(self._cur - 1),
                                        self.navInteracted.emit(self.pos_idx)))
        self.mini.nxt.connect(lambda: (self._goto(self._cur + 1),
                                       self.navInteracted.emit(self.pos_idx)))
        self.mini.last.connect(
            lambda: (self._goto((self._source.count - 1) if self._source else 0),
                     self.navInteracted.emit(self.pos_idx)))
        self.mini.seek.connect(self._goto)
        # bouger le slider d'une vue non active doit l'activer (comme un clic
        # dessus) : activer PENDANT le glisser reparenterait le widget en
        # plein milieu du geste souris (perte du grab -> le slider « freeze »
        # et il faut recliquer). On laisse donc le glisser se terminer
        # normalement sur ce widget, et on n'active la vue qu'au relâchement.
        self.mini.slider.sliderReleased.connect(
            lambda: self.activated.emit(self.pos_idx))
        self.mini.playToggled.connect(lambda on: (self._toggle_play(on),
                                                   self.navInteracted.emit(self.pos_idx)))
        self.mini.loopToggled.connect(self._set_loop)
        self.mini.b_loop.setChecked(True)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)

    # -- source / rendu --
    def load(self, source, lo, hi, lut_data, cube_lut, cube_size, full_range, filters):
        self._source = source
        self._fps = getattr(source, 'fps', 25.0) or 25.0
        self._rp = (lo, hi, lut_data, cube_lut, cube_size, full_range, filters)
        self.set_name(source.name if source else "")
        self.mini.set_range(source.count if source else 0)
        self._goto(0)

    def update_render(self, lut_data, cube_lut, cube_size, full_range, filters):
        """Met à jour uniquement les paramètres GLOBAUX (LUT, filtres).
        La fenêtre lo/hi reste propre à cette vue (réglée via son histogramme
        quand elle est sélectionnée)."""
        lo, hi = self._rp[0], self._rp[1]
        self._rp = (lo, hi, lut_data, cube_lut, cube_size, full_range, filters)
        if self._source:
            self._render()

    def set_window(self, lo, hi):
        rp = list(self._rp)
        rp[0], rp[1] = lo, hi
        self._rp = tuple(rp)
        if self._source:
            self._render()

    def set_fetch_scale(self, s):
        self._fetch_scale = float(s) if s else 1.0
        if self._source:
            self._render()

    def _goto(self, idx):
        if not self._source:
            return
        n = self._source.count
        self._cur = max(0, min(idx, n - 1)) if n > 0 else 0
        self._render()
        self.frameChanged.emit(self.pos_idx, self._cur)

    def _render(self):
        if not self._source:
            self.video.set_frame(None)
            return
        raw = self._source.get(self._cur)
        if raw is None:
            return
        if self._fetch_scale < 1.0:
            h, w = raw.shape[:2]
            raw = cv2.resize(raw, (max(1, int(w * self._fetch_scale)),
                                   max(1, int(h * self._fetch_scale))),
                             interpolation=cv2.INTER_AREA)
        lo, hi, lut_data, cube_lut, cube_size, full_range, filters = self._rp
        bgr = render_frame(raw, lo, hi, lut_data, cube_lut, cube_size,
                           full_range, filters)
        # annotations .ver : dessinées sur la vue (même inactive), calques visibles
        vis_tracks = {L["key"] for L in self._layers
                      if L["kind"] == "ver" and L["visible"]}
        dets = self._annots.get(self._cur, [])
        if dets and vis_tracks:
            keep = [d for d in dets if (d[5] if len(d) > 5 else 0) in vis_tracks]
            if keep:
                oh, ow = raw.shape[:2]
                bgr = draw_annotation_boxes(bgr, keep, ow, oh, rotation=0,
                                            scale=self._fetch_scale)
        # overlay plugin "code" PAR VUE (comme les .ver) : cette vue affiche son
        # propre overlay depuis SES fichiers, meme non selectionnee / en lecture.
        if self.overlay_provider is not None and self._source is not None:
            ov = self.overlay_provider(self._source, self._cur, (bgr.shape[1], bgr.shape[0]))
            if ov is not None:
                bgr = alpha_over(bgr, ov)
        self._last_bgr = bgr
        sidecar_vis = any(L["kind"] == "sidecar" and L["visible"] for L in self._layers)
        self.video.set_overlays(self._overlays.get(self._cur, []) if sidecar_vis else [])
        self.video.set_frame(bgr)
        total = max(0, self._source.count - 1)
        self.mini.set_pos(self._cur)
        self.mini.set_info(self._cur, total, self._fps)
        if self.on_render:
            self.on_render()

    def render_as_viewed(self, idx):
        """Rend la frame `idx` de CETTE vue satellite exactement comme
        affichée : LUT/contraste + boîtes .ver + calque SIDECAR (cochés). Pour
        export (conversion par vue, composition multi-vues)."""
        if self._source is None:
            return None
        raw = self._source.get(idx)
        if raw is None:
            return None
        lo, hi, lut_data, cube_lut, cube_size, full_range, filters = self._rp
        bgr = render_frame(raw, lo, hi, lut_data, cube_lut, cube_size, full_range, filters)
        if bgr is None:
            return None
        if bgr.ndim == 2:
            bgr = cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGR)
        vis_tracks = {L["key"] for L in self._layers
                      if L["kind"] == "ver" and L["visible"]}
        dets = self._annots.get(idx, [])
        if dets and vis_tracks:
            keep = [d for d in dets if (d[5] if len(d) > 5 else 0) in vis_tracks]
            if keep:
                oh, ow = raw.shape[:2]
                bgr = draw_annotation_boxes(bgr, keep, ow, oh, rotation=0,
                                            scale=self._fetch_scale)
        sidecar_vis = any(L["kind"] == "sidecar" and L["visible"] for L in self._layers)
        if sidecar_vis:
            graphs = self._overlays.get(idx, [])
            if graphs:
                bgr = bake_sidecar_overlays(bgr, graphs)
        return bgr

    def _set_loop(self, on):
        self._loop = on

    def _toggle_play(self, on):
        self.mini.set_playing(on)
        if on and self._source:
            self._timer.start(int(1000.0 / max(self._fps, 0.1)))
        else:
            self._timer.stop()

    def _tick(self):
        if not self._source:
            return
        n = self._source.count
        nxt = self._cur + 1
        if nxt >= n:
            if not self._loop:
                self.mini.b_play.setChecked(False)
                return
            nxt = 0
        self._goto(nxt)

    # -- état (pour l'échange / l'activation) --
    def has_source(self):
        return self._source is not None

    def export_state(self):
        return dict(source=self._source, cur=self._cur, lo=self._rp[0],
                    hi=self._rp[1], overlays=self._overlays, annots=self._annots,
                    annot_path=self._annot_path, layers=list(self._layers),
                    track_next=0, mode=self._hist_mode,
                    zoom=self.video.get_view(), rec_on=self._rec_on,
                    name=self._source.name if self._source else "")

    def import_state(self, st):
        self.mini.b_play.setChecked(False)
        self._toggle_play(False)
        self._source = st.get("source")
        self._cur = st.get("cur", 0)
        self._overlays = st.get("overlays") or {}
        self._annots = st.get("annots") or {}
        self._annot_path = st.get("annot_path", "")
        self._layers = st.get("layers") or []
        self._hist_mode = st.get("mode", "Custom")
        self._rec_on = st.get("rec_on", False)
        rp = list(self._rp)
        rp[0], rp[1] = st.get("lo", 0), st.get("hi", 255)
        self._rp = tuple(rp)
        if self._source is not None:
            self._fps = getattr(self._source, 'fps', 25.0) or 25.0
            self.set_name(self._source.name)
            self.mini.set_range(self._source.count)
            self._goto(min(self._cur, max(0, self._source.count - 1)))
            self.video.set_view(*st.get("zoom", (1.0, 0.0, 0.0)))
        else:
            self.clear()

    def clear(self):
        self.mini.b_play.setChecked(False)
        self._toggle_play(False)
        self._source = None
        self._last_bgr = None
        self._cur = 0
        self._overlays = {}
        self._annots = {}
        self._layers = []
        self._hist_mode = "Custom"
        self._rec_on = False
        self.video.set_overlays([])
        self.video.set_frame(None)
        self.video.reset_view()
        self.set_name("")
        self.mini.set_range(0)
        self.mini.lbl.setText("─")
        if self.on_render:
            self.on_render()


# ──────────────────────────── vue fusion (blend) ──────────────────
class FusionView(QtWidgets.QFrame):
    """Bloc de superposition : blend alpha de deux rendus (gauche/droite)."""
    alphaChanged   = QtCore.Signal(float)
    extractClicked = QtCore.Signal()
    selected       = QtCore.Signal()

    _SS_IDLE   = "QFrame#fusv{border:2px solid #6b5a2a;background:#0e0e10;}"
    _SS_ACTIVE = "QFrame#fusv{border:2px solid #ff9a3d;background:#0e0e10;}"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("fusv")
        self.setStyleSheet(self._SS_IDLE)
        self.setMinimumSize(120, 80)
        self._a = None
        self._b = None
        self._alpha = 0.5
        self._mode = "alpha"
        self._last_out = None      # rendu fusionné courant (pour la ROI)

        self.video = VideoWidget()
        self.video.installEventFilter(self)
        self._title = QtWidgets.QLabel(" ⧉ Fusion  (vue 1 ⊕ vue 2)")
        self._title.setStyleSheet(
            "background:#141418; color:#ff9a3d; font-size:10px; padding:1px 3px;")
        self._mode_combo = QtWidgets.QComboBox()
        self._mode_combo.setFocusPolicy(Qt.NoFocus)
        for _k, _lbl in BLEND_MODES:
            self._mode_combo.addItem(_lbl, _k)
        self._mode_combo.setToolTip("Mode de fusion")
        self._mode_combo.setStyleSheet(
            "QComboBox{background:#241d10;color:#ff9a3d;border:1px solid #6b5a2a;"
            "border-radius:2px;font-size:10px;padding:0 4px;}")
        self._mode_combo.currentIndexChanged.connect(self._on_mode)
        self._sld = QtWidgets.QSlider(Qt.Horizontal)
        self._sld.setRange(0, 100); self._sld.setValue(50)
        self._sld.setFocusPolicy(Qt.NoFocus)
        self._sld.valueChanged.connect(self._on_slider)
        self._sld.setStyleSheet(
            "QSlider::groove:horizontal{height:5px;background:#3a2f1a;border-radius:2px;}"
            "QSlider::sub-page:horizontal{background:#ff9a3d;border-radius:2px;}"
            "QSlider::handle:horizontal{background:#ff9a3d;width:12px;margin:-4px 0;"
            "border-radius:6px;}")
        self._albl = QtWidgets.QLabel("α 0.50")
        self._albl.setStyleSheet("color:#ff9a3d; font-size:10px; font-weight:bold;")
        self._albl.setFixedWidth(54)

        self._extract_btn = QtWidgets.QPushButton("⤓ Extraire")
        self._extract_btn.setFixedHeight(18)
        self._extract_btn.setFocusPolicy(Qt.NoFocus)
        self._extract_btn.setToolTip(
            "Extraire le rendu fusionné (MP4 / PNG / SPECIALIZED) sur la plage IN→OUT\n"
            "(ou toute la séquence si IN/OUT non définis)")
        self._extract_btn.setStyleSheet(
            "QPushButton{background:#3a2f1a;color:#ff9a3d;border:1px solid #6b5a2a;"
            "border-radius:2px;font-size:10px;padding:0 6px;}"
            "QPushButton:hover{border-color:#ff9a3d;}")
        self._extract_btn.clicked.connect(self.extractClicked.emit)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(4, 0, 4, 0); row.setSpacing(4)
        row.addWidget(self._mode_combo)
        row.addWidget(self._sld, 1)
        row.addWidget(self._albl)
        row.addWidget(self._extract_btn)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        lay.addWidget(self._title)
        lay.addWidget(self.video, 1)
        lay.addLayout(row)

    def eventFilter(self, obj, ev):
        if (obj is self.video and ev.type() == QtCore.QEvent.MouseButtonPress
                and ev.button() == Qt.LeftButton):
            self.selected.emit()
        return super().eventFilter(obj, ev)

    def set_active(self, on):
        self.setStyleSheet(self._SS_ACTIVE if on else self._SS_IDLE)

    def _on_slider(self, v):
        self._alpha = v / 100.0
        self._albl.setText(f"α {self._alpha:.2f}")
        self.alphaChanged.emit(self._alpha)
        self._render()

    def _on_mode(self, _=None):
        self._mode = self._mode_combo.currentData()
        # α n'a de sens que pour le mode alpha
        on = self._mode == "alpha"
        self._sld.setEnabled(on)
        self._albl.setEnabled(on)
        self._render()

    def mode(self):
        return self._mode

    def set_alpha(self, alpha):
        self._alpha = max(0.0, min(1.0, float(alpha)))
        self._sld.blockSignals(True)
        self._sld.setValue(int(round(self._alpha * 100)))
        self._sld.blockSignals(False)
        self._albl.setText(f"α {self._alpha:.2f}")
        self._render()

    def set_frames(self, a, b):
        self._a = a
        self._b = b
        self._render()

    @staticmethod
    def _to_bgr(img):
        if img is None:
            return None
        if img.ndim == 2:
            return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        return img

    def _render(self):
        a = self._to_bgr(self._a)
        b = self._to_bgr(self._b)
        if a is None and b is None:
            self._last_out = None
            self.video.set_frame(None)
            return
        if a is None:
            self._last_out = b
            self.video.set_frame(b); return
        if b is None:
            self._last_out = a
            self.video.set_frame(a); return
        out = blend_frames(a, b, self._alpha, self._mode)
        self._last_out = out
        self.video.set_frame(out)
class PrimaryFrame(BaseViewFrame):
    """Vue rendue par le moteur de MainWindow. Visuellement identique aux autres :
    même bandeau, même mini-barre. Sa mini-barre pilote le moteur."""

    def __init__(self, video: QtWidgets.QWidget, parent=None):
        super().__init__(parent)
        self.video = video
        self._watch_video(video)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.banner_row)
        lay.addWidget(self.video, 1)
        lay.addWidget(self.mini)


class MultiViewOverlay(QtWidgets.QWidget):
    """Couche transparente au-dessus du SplitCanvas : dessine les formes
    plugin ciblant une vue precise (view=index) et les SEGMENTS INTER-VUES
    (type "segment_inter_vues") -- ce que le moteur d'une vue unique ne peut
    pas faire, puisqu'une ligne relie deux viewports differents. Les
    coordonnees des formes sont en pixels IMAGE ; on les mappe vers le
    referentiel du canvas via SplitCanvas.map_image_point()."""

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self._specs = []
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def set_specs(self, specs):
        self._specs = list(specs or [])
        self.update()

    def paintEvent(self, _e):
        if not self._specs:
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        for g in self._specs:
            col = QtGui.QColor(*g.get("color", (255, 190, 0)))
            thick = max(1, int(g.get("thickness", 2)))
            pen = QtGui.QPen(col, thick)
            pen.setCosmetic(True)
            p.setPen(pen)
            t = g.get("type", "")
            if t in ("texte_ecran", "screen_text"):
                view = int(g.get("view", 0))
                rect = self._canvas.view_rect(view)
                label = str(g.get("label", ""))
                if rect is None or not label:
                    continue
                p.save()
                p.setClipRect(rect)
                font = p.font()
                font.setBold(True)
                font.setPixelSize(max(10, int(g.get("font_size", 13))))
                p.setFont(font)
                metrics = QtGui.QFontMetrics(font)
                text_rect = metrics.boundingRect(label).adjusted(-8, -5, 8, 5)
                # view_rect() renvoie un QRectF pour conserver la precision du
                # mapping image. QFontMetrics.boundingRect() renvoie un QRect,
                # dont moveTopLeft exige strictement un QPoint sous PySide6 6.8.
                text_rect.moveTopLeft(QtCore.QPoint(
                    int(round(rect.left())) + 12,
                    int(round(rect.top())) + 12,
                ))
                background = g.get("background", (20, 20, 25, 205))
                p.setPen(Qt.NoPen)
                p.setBrush(QtGui.QColor(*background))
                p.drawRoundedRect(QtCore.QRectF(text_rect), 4, 4)
                p.setPen(pen)
                p.drawText(text_rect, Qt.AlignCenter, label)
                p.restore()
                continue
            if t == "segment_inter_vues":
                va, vb = g.get("va", 0), g.get("vb", 1)
                a = self._canvas.map_image_point(va, *g.get("pa", (0, 0)))
                b = self._canvas.map_image_point(vb, *g.get("pb", (0, 0)))
                if a is None or b is None:
                    continue
                # ne relier QUE si chaque extremite est visible dans SA vue :
                # sinon (zoom/pan hors cadre) le point projete tomberait dans une
                # autre vue et le trait partirait n'importe ou.
                ra, rb = self._canvas.view_rect(va), self._canvas.view_rect(vb)
                if (ra is not None and not ra.contains(a)) or \
                   (rb is not None and not rb.contains(b)):
                    continue
                p.drawLine(a, b)
                p.setBrush(col)
                for pt in (a, b):
                    p.drawEllipse(pt, 3.0, 3.0)
                p.setBrush(Qt.NoBrush)
                continue
            view = int(g.get("view", 0))
            pts = []
            for (x, y) in g.get("points", []):
                q = self._canvas.map_image_point(view, x, y)
                if q is not None:
                    pts.append(q)
            if not pts:
                continue
            # confine le dessin de CETTE forme a SA vue : au zoom, une forme ne
            # doit jamais deborder sur les vues voisines.
            rect = self._canvas.view_rect(view)
            p.save()
            if rect is not None:
                p.setClipRect(rect)
            if t == "points":
                p.setBrush(col)
                for q in pts:
                    p.drawEllipse(q, 2.6, 2.6)
                p.setBrush(Qt.NoBrush)
            elif t in ("croix", "croixx", "x"):
                d = 5.0
                for q in pts:
                    p.drawLine(QtCore.QLineF(q.x() - d, q.y() - d, q.x() + d, q.y() + d))
                    p.drawLine(QtCore.QLineF(q.x() - d, q.y() + d, q.x() + d, q.y() - d))
            elif t in ("ligne_brisee_fermee", "polygone", "polygone_ferme"):
                if len(pts) >= 2:
                    p.drawPolygon(QtGui.QPolygonF(pts))
            elif t in ("ligne_brisee", "polyligne", "ligne", "segment"):
                if len(pts) >= 2:
                    p.drawPolyline(QtGui.QPolygonF(pts))
            elif t in ("ellipse", "cercle"):
                sc = self._canvas.view_scale(view) or 1.0
                a = g.get("a", 0.0) * sc
                b = g.get("b", 0.0) * sc
                if b <= 0:
                    b = a
                p.drawEllipse(pts[0], max(0.5, a), max(0.5, b))
            elif t in ("rectangle", "rect", "bbox"):
                # 2 points = coins opposes ; 4 points = polygone ferme.
                if len(pts) >= 4:
                    p.drawPolygon(QtGui.QPolygonF(pts[:4]))
                elif len(pts) >= 2:
                    p.drawRect(QtCore.QRectF(pts[0], pts[1]).normalized())
            else:
                p.setBrush(col)
                for q in pts:
                    p.drawEllipse(q, 2.0, 2.0)
                p.setBrush(Qt.NoBrush)
            p.restore()


# ──────────────────────────── canvas multi-vues ───────────────────
class SplitCanvas(QtWidgets.QWidget):
    """Grille de vues toutes équivalentes. La vue ACTIVE est celle rendue par le
    moteur (PrimaryFrame) ; elle se déplace à la position sélectionnée."""
    activateRequested  = QtCore.Signal(int)        # position à activer
    swapRequested      = QtCore.Signal(int, int)
    mediaDropped       = QtCore.Signal(int, list)
    overlayDropped     = QtCore.Signal(int, str)
    folderDropped      = QtCore.Signal(int, str)
    doubled            = QtCore.Signal(int)
    fusionAlphaChanged = QtCore.Signal(float)
    fusionRoiChanged   = QtCore.Signal(int, int, int, int)
    fusionExtractRequested = QtCore.Signal()
    fusionSelected     = QtCore.Signal()
    convertClicked     = QtCore.Signal(int)        # bouton "Convertir" d'une vue
    navInteracted      = QtCore.Signal(int)        # play/precedent/suivant d'une vue touche a la main
    satelliteRendered  = QtCore.Signal()           # une vue satellite vient de se re-rendre
    viewFrameChanged   = QtCore.Signal(int, int)   # navigation directe d'une vue satellite

    MODES = {
        "1":        (1, 1, 1),
        "h2":       (1, 2, 2),
        "v2":       (2, 1, 2),
        "h3":       (1, 3, 3),
        "q4":       (2, 2, 4),
        "fusion":   (2, 2, 2),
        "temporal": (1, 2, 2),   # meme flux : gauche = i-N (satellite), droite = i (moteur)
    }

    def __init__(self, primary_frame: PrimaryFrame, parent=None):
        super().__init__(parent)
        self._primary = primary_frame
        self._sats: list = []
        self._fusion = None
        self._mode = "1"
        self._active_pos = 0
        self._fusion_active = False
        # mode temporel : roles figes (gauche = i-N suiveur, droite = i moteur),
        # ni activation ni swap. _temporal_sat = la vue satellite gauche dediee.
        self._temporal = False
        self._temporal_sat = None
        self._primary_bgr = None
        self._primary_getter = None
        self._grid = QtWidgets.QGridLayout(self)
        self._grid.setSpacing(2)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._order = [primary_frame]
        self._wire(primary_frame)
        # couche multivue (segments inter-vues + formes ciblant une vue) --
        # flotte au-dessus de la grille, transparente aux clics.
        self._mv_overlay = MultiViewOverlay(self)
        self._mv_overlay.setGeometry(self.rect())
        self._overlay_wired = set()   # ids des videos deja reliees au repaint overlay
        self._relayout()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "_mv_overlay", None) is not None:
            self._mv_overlay.setGeometry(self.rect())
            self._mv_overlay.raise_()

    # -- multivue (couche overlay) --
    def set_multiview_overlays(self, specs):
        """Formes plugin ciblant une vue precise + segments inter-vues, a
        dessiner par la couche MultiViewOverlay (au-dessus de toutes les
        vues). `specs` = liste de dicts (voir MultiViewOverlay)."""
        if getattr(self, "_mv_overlay", None) is not None:
            self._mv_overlay.setGeometry(self.rect())
            self._mv_overlay.raise_()
            self._mv_overlay.set_specs(specs)

    def _view_video(self, pos):
        w = self.widget_at(pos)
        if w is None or not w.isVisible():
            return None
        return getattr(w, "video", None)

    def view_scale(self, pos):
        """Facteur image->ecran de la vue `pos` (pour dimensionner une
        ellipse), ou None si la vue n'est pas disponible."""
        v = self._view_video(pos)
        if v is None:
            return None
        try:
            v._geom()
            return float(getattr(v, "_scale", 1.0))
        except Exception:
            return None

    def map_image_point(self, pos, x, y):
        """Convertit un point en pixels IMAGE de la vue `pos` (0-based) en un
        QPoint dans le referentiel de ce canvas, ou None si la vue n'est pas
        affichee / pas prete. `pos` >= nombre de vues visibles -> None."""
        if pos >= self.n_slots():
            return None
        v = self._view_video(pos)
        if v is None:
            return None
        try:
            v._geom()
            local = v._w(float(x), float(y))     # coords locales a la VideoWidget
            return v.mapTo(self, local.toPoint())
        except Exception:
            return None

    def view_rect(self, pos):
        """QRectF de la zone video de la vue `pos` dans le referentiel du canvas,
        ou None. Sert a confiner (clip) le dessin d'une forme a SA vue et a
        verifier qu'une extremite de lien est bien visible dans son cadre."""
        v = self._view_video(pos)
        if v is None:
            return None
        try:
            tl = v.mapTo(self, QtCore.QPoint(0, 0))
            return QtCore.QRectF(float(tl.x()), float(tl.y()),
                                 float(v.width()), float(v.height()))
        except Exception:
            return None

    # -- construction --
    def _wire(self, w):
        w.activated.connect(self._on_activated)
        w.doubled.connect(self.doubled.emit)
        w.mediaDropped.connect(self.mediaDropped.emit)
        w.overlayDropped.connect(self.overlayDropped.emit)
        w.folderDropped.connect(self.folderDropped.emit)
        w.swapRequested.connect(self.swapRequested.emit)
        w.convertClicked.connect(self.convertClicked.emit)
        w.navInteracted.connect(self.navInteracted.emit)
        w.frameChanged.connect(self.viewFrameChanged.emit)

    def _sat_rendered(self):
        """Appele apres le rendu d'une vue satellite : rafraichit la fusion ET
        previent (signal) pour recalculer les liens inter-vues -- sinon bouger
        une vue NON moteur ne mettait pas a jour les overlays multivue."""
        self._refresh_fusion()
        self.satelliteRendered.emit()

    def _ensure_sats(self, n):
        while len(self._sats) < n:
            sv = SatelliteView(self)
            sv.on_render = self._sat_rendered
            # overlay plugin par vue : le fournisseur (pose par MainWindow) est
            # transmis a chaque satellite cree a la demande.
            sv.overlay_provider = getattr(self, "satellite_overlay_provider", None)
            self._wire(sv)
            self._sats.append(sv)

    def _ensure_fusion(self):
        if self._fusion is None:
            self._fusion = FusionView(self)
            self._fusion.alphaChanged.connect(self.fusionAlphaChanged.emit)
            # la ROI est aussi utilisable sur le rendu fusionné
            self._fusion.video.roiChanged.connect(self.fusionRoiChanged.emit)
            self._fusion.extractClicked.connect(self.fusionExtractRequested.emit)
            self._fusion.selected.connect(self._on_fusion_selected)

    def set_primary_getter(self, fn):
        self._primary_getter = fn

    # -- disposition --
    def set_mode(self, mode):
        if mode not in self.MODES:
            return
        n = self.MODES[mode][2]
        self._ensure_sats(n - 1)
        if mode == "fusion":
            self._ensure_fusion()
        was_temporal = self._temporal
        self._temporal = (mode == "temporal")
        self._mode = mode
        if self._temporal:
            # roles figes : slot 0 = satellite gauche (i-N), slot 1 = moteur (i).
            self._temporal_sat = self._sats[0]
            self._order = [self._temporal_sat, self._primary]
            self._active_pos = 1
        else:
            self._temporal_sat = None
            # la vue principale revient en position 0 au changement de disposition
            self._order = [self._primary] + self._sats[:n - 1]
            self._active_pos = 0
        self._fusion_active = False
        self._relayout()
        # apparence des roles temporels (apres relayout : les widgets sont poses)
        if self._temporal:
            self._temporal_sat.set_frozen(True, "Past Frame i-N")
            self._primary.set_active_green(True)   # vue droite (i) = moteur = vert
        else:
            self._primary.set_active_green(False)
            if was_temporal and self._sats:
                self._sats[0].set_frozen(False)
        self._update_borders()

    def _clear_grid(self):
        while self._grid.count():
            it = self._grid.takeAt(0)
            if it.widget():
                it.widget().hide()
                it.widget().setParent(None)
        for i in range(self._grid.rowCount()):
            self._grid.setRowStretch(i, 0)
        for i in range(self._grid.columnCount()):
            self._grid.setColumnStretch(i, 0)

    def _relayout(self):
        rows, cols, n = self.MODES[self._mode]
        self._clear_grid()
        multi = n > 1
        if self._mode == "fusion":
            self._grid.addWidget(self._order[0], 0, 0)
            self._grid.addWidget(self._order[1], 0, 1)
            self._grid.addWidget(self._fusion, 1, 0, 1, 2)
            self._fusion.show()
            self._grid.setColumnStretch(0, 1); self._grid.setColumnStretch(1, 1)
            self._grid.setRowStretch(0, 1); self._grid.setRowStretch(1, 1)
        else:
            if self._fusion is not None:
                self._fusion.hide()
            for i, w in enumerate(self._order):
                r, c = divmod(i, cols)
                self._grid.addWidget(w, r, c)
            for c in range(cols):
                self._grid.setColumnStretch(c, 1)
            for r in range(rows):
                self._grid.setRowStretch(r, 1)
        for i, w in enumerate(self._order):
            w.show()
            w.set_position(i)
            w.set_chrome_visible(multi)
        for sv in self._sats:
            if sv not in self._order:
                sv.hide()
        self._update_borders()
        # la couche multivue doit rester AU-DESSUS de la grille reconstruite.
        if getattr(self, "_mv_overlay", None) is not None:
            self._mv_overlay.setGeometry(self.rect())
            self._mv_overlay.raise_()
        self._wire_overlay_repaint()
        self._refresh_fusion()

    def _wire_overlay_repaint(self):
        """Repeint la couche inter-vues quand une vue zoome/panne : sinon les
        formes restent projetees a l'ancienne echelle jusqu'au prochain rendu.
        Connexion unique par video (garde par id)."""
        if getattr(self, "_mv_overlay", None) is None:
            return
        for w in self._order:
            v = getattr(w, "video", None)
            if v is not None and id(v) not in self._overlay_wired:
                v.viewChanged.connect(self._mv_overlay.update)
                self._overlay_wired.add(id(v))

    # -- activation / échange --
    def _on_activated(self, pos):
        # sélectionner une vue normale désélectionne la fusion
        if self._fusion is not None:
            self._fusion.set_active(False)
        self._fusion_active = False
        if pos == self._active_pos:
            self._update_borders()
        # emis meme si pos == active_pos (re-clic sur la vue deja active) :
        # _on_activate_pos remet alors la cible clavier "engine", utile si
        # elle avait ete deviee vers une autre vue via sa mini-barre.
        self.activateRequested.emit(pos)

    def _on_fusion_selected(self):
        """Clic sur la vue fusion : bordure orange, désélection des vues bleues."""
        self._fusion_active = True
        for w in self._order:
            w.set_active(False)
        if self._fusion is not None:
            self._fusion.set_active(True)
        self.fusionSelected.emit()

    def move_primary_to(self, pos):
        """Place la vue principale (moteur) à la position pos, en échangeant
        le widget qui s'y trouve avec l'ancienne position."""
        if self._temporal:
            return   # roles figes en mode temporel
        if pos == self._active_pos or pos >= len(self._order):
            return
        a, b = self._active_pos, pos
        self._order[a], self._order[b] = self._order[b], self._order[a]
        self._active_pos = b
        self._relayout()

    def swap_positions(self, p1, p2):
        if self._temporal:
            return   # roles figes en mode temporel
        if p1 == p2 or p1 >= len(self._order) or p2 >= len(self._order):
            return
        self._order[p1], self._order[p2] = self._order[p2], self._order[p1]
        if self._active_pos == p1:
            self._active_pos = p2
        elif self._active_pos == p2:
            self._active_pos = p1
        self._relayout()

    def _update_borders(self):
        for i, w in enumerate(self._order):
            w.set_active((i == self._active_pos) and not self._fusion_active)
        if self._fusion is not None:
            self._fusion.set_active(self._fusion_active and self._mode == "fusion")

    # -- accès --
    def widget_at(self, pos):
        return self._order[pos] if 0 <= pos < len(self._order) else None

    def active_pos(self):
        return self._active_pos

    def primary(self):
        return self._primary

    def n_slots(self):
        return self.MODES[self._mode][2]

    def mode(self):
        return self._mode

    def satellites(self):
        return [w for w in self._order if w is not self._primary]

    def is_temporal(self):
        return self._temporal

    def temporal_sat(self):
        return self._temporal_sat if self._temporal else None

    def ordered_views(self):
        """-> [(index, widget, is_primary), ...] pour les vues affichees, dans
        l'ordre de lecture (0 = haut-gauche). Sert au contexte multivue expose
        aux plugins (MainWindow._views_context)."""
        out = []
        for i, w in enumerate(self._order):
            if not w.isVisible():
                continue
            out.append((i, w, w is self._primary))
        return out

    # -- rendus --
    def set_fusion_alpha(self, alpha):
        if self._fusion is not None:
            self._fusion.set_alpha(alpha)

    def signal_primary_render(self, bgr):
        self._primary_bgr = bgr
        self._refresh_fusion()

    def _frame_of(self, w):
        if w is self._primary:
            if self._primary_bgr is None and self._primary_getter is not None:
                return self._primary_getter()
            return self._primary_bgr
        return getattr(w, "_last_bgr", None)

    def _refresh_fusion(self):
        if self._mode != "fusion" or self._fusion is None or len(self._order) < 2:
            return
        self._fusion.set_frames(self._frame_of(self._order[0]),
                                self._frame_of(self._order[1]))

    def update_sat_render(self, lut_data, cube_lut, cube_size,
                          full_range, filters):
        """Propage les réglages GLOBAUX (LUT / filtres) aux autres vues.
        Leur fenêtre lo/hi n'est PAS touchée."""
        for sv in self.satellites():
            if sv.has_source():
                sv.update_render(lut_data, cube_lut, cube_size,
                                 full_range, filters)
        self._refresh_fusion()

    def set_fetch_scale(self, s):
        for sv in self._sats:
            sv.set_fetch_scale(s)
class FrameSlider(QtWidgets.QSlider):
    """QSlider avec surbrillance rouge de la zone d'extraction IN/OUT."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._in = -1
        self._out = -1
        self._segments = []   # [(in, out)] : morceaux du splitting (vert)

    def set_extract_zone(self, in_f, out_f):
        self._in = in_f
        self._out = out_f
        self.update()

    def set_split_segments(self, segments):
        """Liste de morceaux (in, out) surlignes en vert transparent + numero."""
        self._segments = list(segments or [])
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        total = self.maximum() - self.minimum()
        if total <= 0:
            return
        mg = 6
        gw = self.width() - 2 * mg
        h = self.height()
        # morceaux du splitting : bandes vertes numerotees (sous la zone rouge)
        if self._segments:
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            f = p.font(); f.setPixelSize(9); f.setBold(True); p.setFont(f)
            for i, seg in enumerate(self._segments, 1):
                s, en = seg[0], seg[1]
                if s < 0 or en < s:
                    continue
                gx1 = mg + int((s - self.minimum()) / total * gw)
                gx2 = mg + int((en - self.minimum()) / total * gw)
                p.fillRect(gx1, 1, max(2, gx2 - gx1), h - 2,
                           QtGui.QColor(40, 200, 90, 110))
                p.setPen(QtGui.QColor(190, 255, 210))
                p.drawText(QtCore.QRectF(gx1, 0, max(10, gx2 - gx1), h),
                           Qt.AlignCenter, str(i))
            p.end()
        if self._in >= 0 and self._out > self._in:
            x1 = mg + int((self._in - self.minimum()) / total * gw)
            x2 = mg + int((self._out - self.minimum()) / total * gw)
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing, False)
            p.fillRect(x1, 1, max(2, x2 - x1), h - 2, QtGui.QColor(220, 50, 50, 130))
            p.end()
        # étoiles rouges : marquent visuellement les frames IN et OUT posées,
        # même si une seule des deux est encore définie (avant la zone rouge).
        pts = [f for f in (self._in, self._out)
               if f is not None and f >= self.minimum() and f <= self.maximum()]
        if pts:
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            p.setPen(QtGui.QPen(QtGui.QColor(140, 0, 0), 1))
            p.setBrush(QtGui.QColor(255, 45, 45))
            for f in pts:
                x = mg + (f - self.minimum()) / total * gw
                p.drawPolygon(self._star_poly(x, h / 2.0, 5.0, 2.2))
            p.end()

    @staticmethod
    def _star_poly(cx, cy, r_out, r_in):
        pts = []
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5
            r = r_out if i % 2 == 0 else r_in
            pts.append(QtCore.QPointF(cx + r * math.cos(ang), cy + r * math.sin(ang)))
        return QtGui.QPolygonF(pts)
