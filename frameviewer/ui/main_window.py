# -*- coding: utf-8 -*-
"""Fenetre principale : barre d'outils, docks, navigation, dispatch
d'ouverture. Le gros morceau (~3600 lignes) -- volontairement pas eclate en
sous-classes (voir docs/architecture.md), seulement deplace tel quel."""
import os
import time as _time

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    _HAS_AUDIO = True
except ImportError:
    _HAS_AUDIO = False

try:
    from frameviewer.core.annotation_loader import (is_annotation_path,
                                                     load_annotations as _load_annotations,
                                                     scan_dropped_folder)
    _HAS_ANNOT = True
except ImportError:
    _HAS_ANNOT = False
    _load_annotations = None
    is_annotation_path = None
    scan_dropped_folder = None

from frameviewer.core.annotations import (_ann_color, _rotate_box_coords,
                                          bake_sidecar_overlays, draw_annotation_boxes)
from frameviewer.core.constants import COLORMAPS, FRENCH_COLORS, IMAGE_EXTS, VIDEO_EXTS
from frameviewer.core.feature_registry import (extensions_for_kind,
                                                operation_for_kind,
                                                supports_path)
from frameviewer.core.io_utils import fmt_time, list_files, list_images, natural_sort
from frameviewer.core.pipeline import (auto_window_sigma, blend_frames, load_cube,
                                       render_frame, to_intensity)
from frameviewer.core.plugin_render import alpha_over, composite_patches
from frameviewer.core.sources import (FusionSource, ImageSequenceSource, SpecializedSource,
                                      VideoSource, YuvSource, describe_source)
from frameviewer.ui import icons
from frameviewer.ui.color_bar import ColorBar
from frameviewer.ui.export_dialogs import (ClipExtractDialog, ConvertDialog,
                                           RoiConvertDialog, SplitExtractDialog)
from frameviewer.ui.history_list import HistoryList, _HistRow
from frameviewer.ui.histogram import HistogramLUT
from frameviewer.ui.layer_export_dialog import LayerExportDialog
from frameviewer.ui.link_views_dialog import LinkViewsDialog
from frameviewer.ui.yolo_convert_dialog import YoloConvertDialog
from frameviewer.ui.multiview import (BaseViewFrame, FrameSlider, PrimaryFrame,
                                      SatelliteView, SplitCanvas)
from frameviewer.ui.theme import _APP_LOGO_B64
from frameviewer.ui.tools_panel import ToolsPanel
from frameviewer.ui.video_widget import VideoWidget
from frameviewer.plugins.loader import PluginLoader
from frameviewer.workers.annot_load_worker import AnnotFolderLoadWorker


_EPHEMERAL_DEMO_PLUGIN_IDS = {"demo_showcase", "demo_2_multivue_kpts"}
from frameviewer.workers.convert_worker import ConvertWorker

SpecializedWriter = operation_for_kind("sequence_format", "writer")
parse_overlay_sidecar = operation_for_kind("overlay_format", "read")
find_sequence_for_sidecar = operation_for_kind("overlay_format", "find_media")
SEQUENCE_FORMAT_EXTS = extensions_for_kind("sequence_format")
OVERLAY_FORMAT_EXTS = extensions_for_kind("overlay_format")
from frameviewer.workers.prefetch_thread import _PrefetchThread

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FrameViewer - OpenCV")
        import base64 as _b64
        _pm = QtGui.QPixmap()
        _pm.loadFromData(_b64.b64decode(_APP_LOGO_B64))
        self.setWindowIcon(QtGui.QIcon(_pm))
        self.resize(1320, 820)
        # Les docks et la barre basse contiennent beaucoup de controles. Leur
        # minimumSizeHint cumule depassait 2000 px et rendait la fenetre
        # impossible a reduire sur un ecran 1366 px. Le layout reste libre de
        # distribuer l'espace, avec un plancher explicite utilisable.
        self.setMinimumSize(900, 600)
        self.setAcceptDrops(True)
        # StrongFocus : necessaire pour que self.setFocus() (repris par les
        # vues/mini-barres au clic, voir BaseViewFrame.eventFilter et
        # _kbd_target ci-dessous) fonctionne reellement -- une QMainWindow a
        # NoFocus par defaut, et setFocus() est un no-op sur un widget NoFocus.
        self.setFocusPolicy(Qt.StrongFocus)
        # Cible des fleches clavier gauche/droite (et Espace) : "engine"
        # (vue active pilotee par le moteur, comportement historique),
        # "all" (barre du bas -> pilote TOUTES les vues), ou une instance
        # SatelliteView precise (mini-barre de CETTE vue touchee a la main).
        # Mis a jour par _on_activate_pos, _bar_play/_bar_step et
        # _on_view_nav_interacted (voir docstrings respectives).
        self._kbd_target = "engine"

        self.source = None
        self.cur = 0
        self._raw = None
        self.clicks = []           # (frame, x, y)
        self.clicks_path = None
        self._cube_lut = None
        self._cube_size = 0
        self._slider_user = False
        self._was_playing = False
        self.overlays = {}         # {num: [graphique...]}
        self._pending = None       # overlays en attente de sequence
        self._conv_workers = {}    # conv_id -> {'worker', 'item', 'label'}
        self._conv_id_seq = 0
        self._history = []         # [(display_label, src_kind, src_arg, bit_label, is_color), ...]
        self._frame_buf = ""       # saisie clavier en cours d'un numero de frame
        # --- resolution de fetch (optim reseau) ---
        self._fetch_scale = 1.0
        # --- rotation / crop dynamique / F11 ---
        self._rotation = 0
        self._dyn_crop = False
        self._annotations = {}  # frame_idx -> [(cls,x1,y1,x2,y2,track_id)]
        self._annot_path = ""
        self._annot_track_next = 0   # prochain track_id (calques .ver multiples)
        # calques (overlays SIDECAR + tracks .ver) de la vue active, pour le gestionnaire
        self._layers = []            # [{kind:'sidecar'|'ver', name, key, visible}]
        self._view_only = False
        self._dock_vis_save = {}
        # --- extraction clip ---
        self._extract_start = -1
        self._extract_end = -1
        self._segments = []          # morceaux [(in, out)] pour le splitting
        self._export_use_roi = False # rogner l'export sur la ROI (crop vert)
        # --- capture (options memorisees, choisies dans le popup appareil photo) ---
        self._capture_overlays = True
        # --- affichage ---
        self._crosshair = False
        self._user_adjusting = False  # True quand refresh_frame est appele par l'user (drag/spin)
        self._temp_sub_on = False     # soustraction temporelle activee
        self._temp_sub_k  = 1        # decalage k frames
        self._display_bgr = None     # derniere frame rendue (pour Ctrl+C)
        self._capture_fmt = "png"
        # --- prefetch ---
        self._prefetch_n = 0
        self._prefetch_cache = {}
        self._prefetch_thread = None
        self._annot_load_thread = None   # chargement de dossier YOLO en tache de fond
        # --- mesure fps reel ---
        self._last_tick_t = 0.0
        self._fps_est = 0.0
        self._fps_tick_n = 0
        # --- audio ---
        if _HAS_AUDIO:
            self._audio_player = QMediaPlayer(self)
            self._audio_out = QAudioOutput(self)
            self._audio_player.setAudioOutput(self._audio_out)
            self._audio_out.setMuted(True)
        else:
            self._audio_player = None
            self._audio_out = None

        self.view = VideoWidget()
        self.view.setObjectName("main_canvas")
        self.view.clicked.connect(self.on_click)

        # --- barre de navigation ---
        self.slider = FrameSlider(Qt.Horizontal)
        self.slider.setObjectName("main_frame_slider")
        self.slider.setEnabled(False)
        self.slider.setFocusPolicy(Qt.NoFocus)
        self.slider.sliderPressed.connect(self._slider_pressed)
        self.slider.sliderReleased.connect(self._slider_released)
        self.slider.valueChanged.connect(self._slider_changed)
        # champ de saisie directe du numero de frame (pave numerique + Entree)
        self.frame_spin = QtWidgets.QSpinBox()
        self.frame_spin.setRange(0, 0)
        self.frame_spin.setFocusPolicy(Qt.ClickFocus)
        self.frame_spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.frame_spin.setKeyboardTracking(False)
        self.frame_spin.setAlignment(Qt.AlignRight)
        self.frame_spin.setToolTip("Tape un numero de frame puis Entree")
        self.frame_spin.editingFinished.connect(self._frame_spin_done)
        self.frame_label = QtWidgets.QLabel("-")
        self.frame_label.setMinimumWidth(190)
        self.frame_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # --- boutons lecture ---
        self.play_btn = self._btn("Lecture", self._bar_play)
        self.loop_btn = self._btn("", None, checkable=True)
        self.loop_btn.setIcon(icons.icon("loop"))
        self.loop_btn.setToolTip("Lecture en boucle")
        self.first_btn = self._btn("|<", lambda: self._bar_goto(0))
        self.prev_btn = self._btn("<", lambda: self._bar_step(-1))
        self.next_btn = self._btn(">", lambda: self._bar_step(1))
        self.last_btn = self._btn(">|", self._bar_last)

        # --- LUT ---
        self.lut_combo = QtWidgets.QComboBox()
        self.lut_combo.setObjectName("main_lut")
        self.lut_combo.setFocusPolicy(Qt.NoFocus)
        for name, val in COLORMAPS.items():
            self.lut_combo.addItem(name, val)
        self.lut_combo.currentIndexChanged.connect(self.refresh_frame)

        # --- fps ---
        self.fps_spin = QtWidgets.QDoubleSpinBox()
        self.fps_spin.setFocusPolicy(Qt.StrongFocus)
        self.fps_spin.setRange(0.1, 480.0)
        self.fps_spin.setValue(25.0)
        self.fps_spin.setDecimals(2)
        self.fps_spin.setSuffix(" fps")
        self.fps_spin.valueChanged.connect(self._fps_changed)
        self.fps_label = QtWidgets.QLabel("")
        self.fps_label.setFixedWidth(72)
        self.fps_label.setToolTip("FPS reel mesure en cours de lecture")
        self.son_btn = self._btn("", self._toggle_audio, checkable=True)
        self.son_btn.setIcon(icons.icon("speaker"))
        self.son_btn.setToolTip("Activer / desactiver le son (MP4 uniquement)")
        self.son_btn.setEnabled(False)
        self.vid_rec_btn = self._btn(" Vidéo", self._toggle_vid_rec, checkable=True)
        self.vid_rec_btn.setIcon(icons.icon("record"))
        self.vid_rec_btn.hide()  # remplace par extraction clip
        self.capture_btn = self._btn("", self._capture_frame)
        self.capture_btn.setIcon(icons.icon("camera"))
        self.capture_btn.setToolTip("Capturer la frame courante (Ctrl+E)")
        # --- extraction clip ---
        self.extract_in_btn = self._btn(" IN", self._set_extract_in)
        self.extract_in_btn.setObjectName("extract_in_button")
        self.extract_in_btn.setIcon(icons.icon("bracket_in"))
        self.extract_in_btn.setToolTip(
            "Marquer le début du clip à la frame courante")
        self.extract_out_btn = self._btn(" OUT", self._set_extract_out)
        self.extract_out_btn.setObjectName("extract_out_button")
        self.extract_out_btn.setIcon(icons.icon("bracket_out"))
        self.extract_out_btn.setToolTip(
            "Marquer la fin du clip à la frame courante")
        self.extract_lbl = QtWidgets.QLabel("─")
        self.extract_lbl.setFixedWidth(78)
        self.extract_lbl.setAlignment(Qt.AlignCenter)
        self.extract_lbl.setStyleSheet("color:#888; font-size:11px;")
        self.extract_lbl.setToolTip("Zone sélectionnée pour l'extraction")
        self.extract_clip_btn = self._btn("Extraire...", self._do_extract_clip)
        self.extract_clip_btn.setToolTip(
            "Extraire le clip sélectionné (IN → OUT)")
        # --- ROI d'export (crop vert) ---
        self.roi_export_btn = self._btn(" ROI", self._toggle_roi_export, checkable=True)
        self.roi_export_btn.setIcon(icons.icon("roi", color="#3fd07a"))
        self.roi_export_btn.setToolTip(
            "Rogner l'export sur une sous-zone.\n"
            "Activer puis tracer un rectangle vert dans la vue :\n"
            "seule cette zone est exportée (clip IN→OUT et morceaux).")
        # --- splitting : morceaux multiples ---
        self.seg_add_btn = self._btn(" Ajouter morceau", self._add_segment)
        self.seg_add_btn.setObjectName("add_segment_button")
        self.seg_add_btn.setIcon(icons.icon("plus"))
        self.seg_add_btn.setToolTip(
            "Ajoute la plage IN→OUT courante à la liste des morceaux,\n"
            "puis réinitialise IN/OUT pour définir le morceau suivant.")
        self.split_extract_btn = self._btn(" Extraire morceaux...", self._open_split_extract)
        self.split_extract_btn.setIcon(icons.icon("scissors"))
        self.split_extract_btn.setToolTip(
            "Extrait tous les morceaux en une fois "
            "(nom commun {nom}_{IN}_{OUT}).")

        # --- enregistrement clics ---
        self.rec_btn = self._btn("REC clics", None, checkable=True)
        self.rec_btn.setObjectName("record_clicks")
        self.rec_btn.toggled.connect(self._rec_toggled)
        self.undo_btn = self._btn("Annuler clic", self.undo_click)
        self.clear_btn = self._btn("Effacer clics", self.clear_clicks)

        # --- conversion (menu deroulant) / ouverture ---
        self.convert_btn = QtWidgets.QToolButton()
        self.convert_btn.setObjectName("convert_button")
        self.convert_btn.setText("Convertir")
        self.convert_btn.setToolTip("Convertir la sequence courante vers un autre format")
        self.convert_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.convert_menu = QtWidgets.QMenu(self.convert_btn)
        self.convert_btn.setMenu(self.convert_menu)
        self.convert_btn.setVisible(False)
        # en multivue, le menu est retiré et ce bouton devient une action
        # directe "Convertir toutes les vues" (voir _set_split) ; avec un
        # QMenu attaché en InstantPopup, clicked() ne se déclenche pas donc
        # cette connexion reste inoffensive tant qu'un menu est présent.
        self.convert_btn.clicked.connect(self._convert_all_views)
        self.open_file_btn = self._btn("Ouvrir fichier...", self.open_file_dialog)
        self.open_dir_btn = self._btn("Ouvrir dossier...", self.open_dir_dialog)

        # --- histogramme + barre d'outils + docks (caches par defaut) ---
        self.hist = HistogramLUT()
        self.hist.windowChanged.connect(self.refresh_frame)
        self.hist.perFrameChanged.connect(self.refresh_frame)
        self._roi = None
        self._roi_track_id = None  # suivi ROI <-> boîte .ver (None = pas de suivi)
        # split canvas créé avant les docks (le dock Outils TI s'y connecte)
        self._prev_split_mode = None
        self._primary_frame = PrimaryFrame(self.view)
        self._split_canvas = SplitCanvas(self._primary_frame)
        self._split_canvas.activateRequested.connect(self._on_activate_pos)
        self._split_canvas.swapRequested.connect(self._on_swap_positions)
        self._split_canvas.mediaDropped.connect(self._on_view_media_dropped)
        self._split_canvas.overlayDropped.connect(self._on_view_overlay_dropped)
        self._split_canvas.folderDropped.connect(self._on_view_folder_dropped)
        self._split_canvas.doubled.connect(self._on_view_doubled)
        self._split_canvas.fusionRoiChanged.connect(self._on_fusion_roi)
        self._split_canvas.fusionExtractRequested.connect(self._extract_fusion)
        self._split_canvas.fusionSelected.connect(self._on_fusion_selected)
        self._split_canvas.convertClicked.connect(self._on_view_convert_clicked)
        self._split_canvas.navInteracted.connect(self._on_view_nav_interacted)
        # une vue satellite qui bouge doit rafraichir les liens inter-vues
        # (le rendu du moteur n'est PAS declenche dans ce cas).
        self._split_canvas.satelliteRendered.connect(self._refresh_multiview_overlays)
        self._split_canvas.viewFrameChanged.connect(self._on_linked_view_frame_changed)
        self._split_canvas.set_primary_getter(lambda: self._display_bgr)
        # chaque vue satellite compose son propre overlay plugin (par vue) : le
        # fournisseur est pose sur le canvas (transmis aux satellites crees a la
        # demande) + sur ceux deja crees le cas echeant.
        self._split_canvas.satellite_overlay_provider = self._satellite_overlay
        for _sv in getattr(self._split_canvas, "_sats", []):
            _sv.overlay_provider = self._satellite_overlay
        # mini-barre de la vue principale -> moteur ; touche a la main ->
        # les fleches clavier suivent de nouveau le moteur (cible "engine").
        _mb = self._primary_frame.mini
        _mb.first.connect(lambda: (self.seek(0), self._set_kbd_target("engine")))
        _mb.prev.connect(lambda: (self.step(-1), self._set_kbd_target("engine")))
        _mb.nxt.connect(lambda: (self.step(1), self._set_kbd_target("engine")))
        _mb.last.connect(lambda: (self._go_last(), self._set_kbd_target("engine")))
        _mb.seek.connect(self.seek)
        _mb.playToggled.connect(lambda on: (self._mini_play(on), self._set_kbd_target("engine")))
        _mb.loopToggled.connect(lambda on: self.loop_btn.setChecked(on))
        _mb.b_loop.setChecked(True)
        # sonde de valeur au survol de la vue active (rendue dans self.view)
        self.view.hovered.connect(self._on_probe)
        self._probe_src = None
        # lien des vues (zoom/pan/frame)
        self._link_views = False
        self._link_start_frames_by_mode = {}
        self._link_start_frames = [0]
        self._sync_base_frame = 0
        self._syncing = False
        self._wired_videos = set()
        self._build_toolbar()
        self._build_docks()
        self.view.roiChanged.connect(self.on_roi)

        # ligne de scrub (slider + n° de frame) : masquée en multivue
        self._seek_widget = QtWidgets.QWidget()
        seek = QtWidgets.QHBoxLayout(self._seek_widget)
        seek.setContentsMargins(0, 0, 0, 0)
        seek.addWidget(self.slider, 1)
        seek.addWidget(QtWidgets.QLabel("frame"))
        seek.addWidget(self.frame_spin)
        seek.addWidget(self.frame_label)

        # -- barre du bas, compacte et centrée --
        self._all_lbl = QtWidgets.QLabel("FPS (toutes les vues) :")
        self._all_lbl.setStyleSheet("color:#ffffff; font-size:10px; font-weight:bold;")
        self._all_lbl.hide()
        ctrl = QtWidgets.QHBoxLayout()
        ctrl.addStretch(1)
        for w in (self.play_btn, self.loop_btn,
                  self.first_btn, self.prev_btn, self.next_btn, self.last_btn):
            ctrl.addWidget(w)
        ctrl.addSpacing(6)
        ctrl.addWidget(self._all_lbl)
        ctrl.addWidget(self.fps_spin)
        ctrl.addWidget(self.fps_label)
        ctrl.addStretch(1)
        ctrl.addWidget(self.convert_btn)
        ctrl.addWidget(self.open_file_btn)
        ctrl.addWidget(self.open_dir_btn)

        self._ctrl_widget = QtWidgets.QWidget()
        self._ctrl_widget.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        bottom = QtWidgets.QVBoxLayout(self._ctrl_widget)
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(4)
        bottom.addWidget(self._seek_widget)
        bottom.addLayout(ctrl)

        central = QtWidgets.QWidget()
        central.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        lay = QtWidgets.QVBoxLayout(central)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(self._split_canvas, 1)
        lay.addWidget(self._ctrl_widget)
        self._central_lay = lay
        self.setCentralWidget(central)

        self.status = self.statusBar()
        self.status.showMessage("Pret. Glisse un fichier/dossier/specialized/sidecar, ou utilise Ouvrir...")

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._on_tick)

        self._shortcuts()
        # Raccourcis fullscreen -- actifs meme quand un sous-widget a le focus
        QtGui.QShortcut(Qt.Key_F11, self).activated.connect(self._toggle_view_only)
        QtGui.QShortcut(Qt.Key_Escape, self).activated.connect(self._exit_fullscreen)
        # Bouton overlay sortie fullscreen
        self._fs_exit_btn = QtWidgets.QPushButton("\u26f6  Quitter", self)
        self._fs_exit_btn.setStyleSheet(
            "QPushButton{background:rgba(20,20,20,200);color:#fff;"
            "border:1px solid #666;border-radius:4px;"
            "padding:6px 14px;font-size:13px;font-weight:bold;}"
            "QPushButton:hover{background:rgba(80,80,80,220);}")
        self._fs_exit_btn.setFocusPolicy(Qt.NoFocus)
        self._fs_exit_btn.clicked.connect(self._exit_fullscreen)
        self._fs_exit_btn.hide()
        self._enable_playback(False)

        # --- plugins (voir docs/plugins.md) : charges en tout dernier,
        # fenetre entierement construite (docks/statusbar dispo pour on_load).
        # Etat/overlay PAR VUE : chaque vue (primaire ET satellites) affiche son
        # propre overlay depuis ses fichiers (cf. _satellite_overlay), voir
        # docs/plugins.md. Le survol/clic interactif reste sur la vue active.
        self._plugin_menu_actions = []   # [(label, callback)], rempli par PluginAPI.add_menu_action
        # modele plugin v2 : etat par plugin_id (entrees, contrats de fichiers,
        # cases par element), restaure depuis QSettings (voir docs/plugins.md).
        self._plugin_state = self._load_plugin_state()
        # cle de vue par OBJET source (pas par nom) : deux vues chargeant le
        # meme fichier sont deux sources distinctes -> cles distinctes, donc
        # data plugin dissociee. id(src) -> (src, key).
        self._view_keys = {}
        self._plugin_loader = PluginLoader(self)
        # restaure l'etat actif memorise (les plugins sont decoches par
        # defaut) AVANT la decouverte, pour que _load_one applique le bon etat.
        self._plugin_loader.set_enabled_ids(self._load_enabled_plugin_ids())
        self._plugin_loader.discover_and_load()
        # remplit la zone Plugins de l'onglet Parametres, maintenant que les
        # plugins sont decouverts (l'onglet est construit avant le loader).
        self._refresh_plugins_settings()
        # restaure l'etat ouvert/ferme des panneaux (docks) memorise par user.
        self._restore_dock_layout()
        # filtre d'evenements APPLICATIF : les touches mappees aux elements de
        # plugin (manifest element_keys, defaut 'm') doivent basculer les cases
        # meme quand le focus n'est pas sur la vue (panneau plugins, arbre, etc.
        # avalaient la touche) -- cf. _app_key_toggle.
        QtWidgets.QApplication.instance().installEventFilter(self)
        from frameviewer.ui.tutorial import FrameViewerTutorial
        self._tutorial = FrameViewerTutorial(self)
        QtCore.QTimer.singleShot(
            0, lambda: self.resizeDocks(
                [self.tools_dock, self.right_dock], [260, 360], Qt.Horizontal))

    # ----- barre d'outils (toggles) -----
    def _start_tutorial(self):
        self._tutorial.start()

    def _build_toolbar(self):
        tb = QtWidgets.QToolBar("Outils")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setFloatable(False)
        self.addToolBar(Qt.TopToolBarArea, tb)

        self.tutorial_btn = self._btn("Tutoriel", self._start_tutorial)
        self.tutorial_btn.setObjectName("tutorial_button")
        self.tutorial_btn.setToolTip("Visite interactive complete de FrameViewer")
        tb.addWidget(self.tutorial_btn)
        tb.addSeparator()

        # ── panneau droit groupé (Calque / Hist / Conversion / Param) ──
        # Un seul bouton, tout à gauche de la barre, ouvre/ferme right_dock
        # (3 onglets : Hist/Calque, Conversions, Paramètres) ; une fois
        # ouvert, on change d'onglet directement dans le QTabWidget.
        _PANEL_SS = (
            "QPushButton{padding:3px 10px;border:1px solid transparent;border-radius:3px;}"
            "QPushButton:checked{background:#2d4f70;border-color:#5aafff;"
            "color:#fff;font-weight:bold;}"
            "QPushButton:hover:!checked{border-color:#666;}")
        self.right_panel_btn = self._btn(
            "☰  Calque / Hist / Conversion / Param",
            self._toggle_right_dock, checkable=True)
        self.right_panel_btn.setToolTip(
            "Ouvre/ferme le panneau : calques SIDECAR/.ver + histogramme,\n"
            "journal des conversions, paramètres (3 onglets)")
        self.right_panel_btn.setStyleSheet(_PANEL_SS)
        self.right_panel_btn.setObjectName("right_panel_button")
        tb.addWidget(self.right_panel_btn)

        self.tools_btn = self._btn("Outils TI", self._toggle_tools_dock, checkable=True)
        self.tools_btn.setObjectName("tools_button")
        self.hist_btn = self._btn("Historique", self._toggle_hist_dock, checkable=True)
        self.hist_btn.setObjectName("history_button")
        for b in (self.tools_btn, self.hist_btn):
            tb.addWidget(b)

        tb.addSeparator()
        self.rot_btn = self._btn("↻ Rot.", self._rotate_90)
        self.rot_btn.setToolTip("Rotation 90° horaire  (Ctrl+R)")
        tb.addWidget(self.rot_btn)
        # ── split view (menu déroulant) ──
        tb.addSeparator()
        # (label menu, mode, libellé bouton compact, tooltip)
        _SPLIT_ITEMS = [
            ("◻   Vue unique",            "1",      "Vue unique"),
            ("▮ ▮   Côte à côte (2)",     "h2",     "Deux vues côte à côte"),
            ("▬ / ▬   Empilé (2)",        "v2",     "Deux vues empilées"),
            ("▮ ▮ ▮   3 colonnes",        "h3",     "Trois vues en colonnes"),
            ("⊞   Grille 2×2 (4)",        "q4",     "Quatre vues en grille"),
            ("⧉   Fusion (2 → 1, blend)", "fusion", "Superposition alpha de 2 sources"),
            ("◧ ◨   Temporel (i-N | i)",  "temporal", "Même flux : frame i-N à gauche, i à droite"),
        ]
        self.split_btn = QtWidgets.QToolButton()
        self.split_btn.setObjectName("view_layout_button")
        self.split_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.split_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.split_btn.setToolTip("Disposition des vues (split / fusion)")
        self.split_btn.setStyleSheet(
            "QToolButton{padding:3px 10px;border:1px solid #555;border-radius:3px;}"
            "QToolButton:hover{border-color:#5aafff;}"
            "QToolButton::menu-indicator{width:0px;}")
        self.split_menu = QtWidgets.QMenu(self.split_btn)
        self.split_btn.setMenu(self.split_menu)
        self._split_actions: dict = {}
        grp = QtGui.QActionGroup(self.split_menu)
        grp.setExclusive(True)
        for lbl, mode, tip in _SPLIT_ITEMS:
            act = QtGui.QAction(lbl, self.split_menu, checkable=True)
            act.setToolTip(tip)
            act.triggered.connect(lambda _=False, m=mode: self._set_split(m))
            grp.addAction(act)
            self.split_menu.addAction(act)
            self._split_actions[mode] = act
            if mode == "fusion":
                sep = self.split_menu.insertSeparator(act)
                head = QtGui.QAction("Vues liées (2 sources)", self.split_menu)
                head.setEnabled(False)
                self.split_menu.insertAction(act, head)
        self._split_labels = {m: lbl for lbl, m, _ in _SPLIT_ITEMS}
        self._split_actions["1"].setChecked(True)
        self.split_btn.setText("▦  Vues ▾")
        tb.addWidget(self.split_btn)
        # contrôle N du mode Temporel (visible seulement dans ce mode) : la vue
        # gauche montre la frame i-N, la droite la frame i (moteur).
        self.temporal_n_lbl = QtWidgets.QLabel("  N ")
        self.temporal_n_spin = QtWidgets.QSpinBox()
        self.temporal_n_spin.setRange(1, 1000000)
        self.temporal_n_spin.setValue(1)
        self.temporal_n_spin.setFocusPolicy(Qt.ClickFocus)
        self.temporal_n_spin.setToolTip(
            "Écart temporel N : la vue gauche affiche la frame i-N")
        self.temporal_n_spin.valueChanged.connect(self._on_temporal_n_changed)
        # widgets ajoutes a la barre -> QAction : on masque via l'ACTION pour
        # qu'ils ne laissent aucune trace hors mode temporel.
        self._temporal_n_lbl_act = tb.addWidget(self.temporal_n_lbl)
        self._temporal_n_spin_act = tb.addWidget(self.temporal_n_spin)
        self._temporal_n_lbl_act.setVisible(False)
        self._temporal_n_spin_act.setVisible(False)
        tb.addWidget(QtWidgets.QLabel("  Résolution "))
        self.fetch_res_combo = QtWidgets.QComboBox()
        self.fetch_res_combo.setFocusPolicy(Qt.NoFocus)
        self.fetch_res_combo.addItem("Pleine (1:1)", 1.0)
        self.fetch_res_combo.addItem("½  (1:2)", 0.5)
        self.fetch_res_combo.addItem("¼  (1:4)", 0.25)
        self.fetch_res_combo.addItem("⅛  (1:8)", 0.125)
        self.fetch_res_combo.setToolTip(
            "Résolution de lecture (toutes les vues)\n"
            "Pleine = qualité max ; ½/¼/⅛ = plus rapide (réseau/SSH lent)")
        self.fetch_res_combo.currentIndexChanged.connect(self._on_fetch_res_changed)
        tb.addWidget(self.fetch_res_combo)
        # lier les vues (zoom / pan / frame synchronisés)
        self.link_btn = self._btn("🔗 Lier", self._open_link_dialog, checkable=True)
        self.link_btn.setObjectName("link_views_button")
        self.link_btn.setToolTip(
            "Lier les vues : zoom, déplacement et frame synchronisés entre\n"
            "toutes les vues (comparaison côte à côte)")
        self.link_btn.setStyleSheet(
            "QPushButton{padding:3px 8px;border:1px solid transparent;border-radius:3px;}"
            "QPushButton:checked{background:#2d4f70;border-color:#5aafff;color:#fff;}"
            "QPushButton:hover:!checked{border-color:#666;}")
        self.link_btn.setEnabled(False)
        tb.addWidget(self.link_btn)
        # spacer pousse Aide + FS a l'extreme droite
        _spc = QtWidgets.QWidget()
        _spc.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        tb.addWidget(_spc)
        self.config_btn = self._btn("Config", self._open_config_dir)
        self.config_btn.setObjectName("config_button")
        self.config_btn.setToolTip(
            "Ouvrir le dossier de configuration utilisateur\n"
            "(settings.ini + plugins perso, dans AppData\\FrameViewer)")
        tb.addWidget(self.config_btn)
        self.aide_btn = self._btn("Aide", self._show_aide)
        self.aide_btn.setObjectName("help_button")
        tb.addWidget(self.aide_btn)
        self.fs_btn = self._btn("⛶", self._toggle_view_only)
        self.fs_btn.setToolTip("Vue plein cadre / normale  (F11 / Échap)")
        tb.addWidget(self.fs_btn)

    # ----- construction des docks (caches par defaut) -----
    def _build_docks(self):
        # ===== panneau droit unifie (QTabWidget) =====
        self.right_dock = QtWidgets.QDockWidget("Panneaux", self)
        self.right_dock.setObjectName("right_panels_dock")
        self.right_dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)

        _TAB_SS = (
            "QTabBar::tab{min-width:90px;padding:6px 14px;"
            "border:1px solid #555;border-bottom:none;"
            "border-radius:3px 3px 0 0;background:#1e1e1e;color:#aaa;}"
            "QTabBar::tab:selected{background:#2d4f70;color:#fff;"
            "border-color:#5aafff;font-weight:bold;}"
            "QTabBar::tab:hover:!selected{background:#2e2e2e;color:#ddd;}"
            "QTabWidget::pane{border:1px solid #555;"
            "border-top:2px solid #5aafff;}")
        self.right_tab = QtWidgets.QTabWidget()
        self.right_tab.setObjectName("right_panels_tabs")
        self.right_tab.setStyleSheet(_TAB_SS)
        self.right_tab.setUsesScrollButtons(False)
        self.right_tab.tabBar().setExpanding(True)
        self.right_tab.tabBar().setElideMode(Qt.ElideRight)

        # --- onglet 0 : Calque SIDECAR / Histogramme ---
        xw = QtWidgets.QWidget()
        xv = QtWidgets.QVBoxLayout(xw)
        xv.setContentsMargins(6, 6, 6, 6)
        head = QtWidgets.QHBoxLayout()
        head.addWidget(QtWidgets.QLabel("Calque SIDECAR / annotations — vue sélectionnée"))
        head.addStretch(1)
        xv.addLayout(head)
        row = QtWidgets.QHBoxLayout()
        self.overlay_chk = QtWidgets.QCheckBox("Afficher les overlays")
        self.overlay_chk.setObjectName("show_overlays")
        self.overlay_chk.setChecked(True)
        self.overlay_chk.toggled.connect(self._overlay_refresh)
        row.addWidget(self.overlay_chk)
        row.addStretch(1)
        row.addWidget(QtWidgets.QLabel("Decalage:"))
        self.sidecar_offset = QtWidgets.QSpinBox()
        self.sidecar_offset.setRange(-1000000, 1000000)
        self.sidecar_offset.setValue(0)
        self.sidecar_offset.valueChanged.connect(self._overlay_refresh)
        row.addWidget(self.sidecar_offset)
        xv.addLayout(row)
        self.sidecar_label = QtWidgets.QLabel("Aucun SIDECAR charge")
        self.sidecar_label.setWordWrap(True)
        xv.addWidget(self.sidecar_label)

        # ── gestionnaire de calques (overlays présents, cases + suppression) ──
        # Un .ver multi-tracks (plusieurs objets dans le même fichier) est
        # groupé sous UNE entrée dépliable : case du groupe = coche/décoche
        # TOUS ses tracks d'un coup ; on déplie pour affiner track par track.
        lrow = QtWidgets.QHBoxLayout()
        lrow.setSpacing(4)
        lrow.addWidget(QtWidgets.QLabel("Calques :"))
        lrow.addStretch(1)
        _b_all = self._btn("Tout", lambda: self._layers_set_all(True))
        _b_all.setToolTip("Cocher (afficher) tous les calques")
        _b_none = self._btn("Aucun", lambda: self._layers_set_all(False))
        _b_none.setToolTip("Décocher (masquer) tous les calques")
        _b_del = self._btn("Suppr. all  🗑", self._delete_all_layers)
        _b_del.setToolTip(
            "Supprime TOUS les calques listés, immédiatement (pas besoin de\n"
            "les sélectionner). Pour retirer un seul calque/.ver : sélectionne-le\n"
            "dans la liste puis touche Suppr (multi-sélection possible).")
        for _b in (_b_all, _b_none, _b_del):
            _b.setStyleSheet("QPushButton{padding:1px 6px;font-size:11px;}")
            lrow.addWidget(_b)
        xv.addLayout(lrow)
        self.layer_list = QtWidgets.QTreeWidget()
        self.layer_list.setHeaderHidden(True)
        self.layer_list.setIndentation(14)
        self.layer_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.layer_list.setMaximumHeight(140)
        self.layer_list.setToolTip(
            "Calques de la vue sélectionnée.\n"
            "• Calque SIDECAR : une seule ligne.\n"
            "• .ver : groupé par fichier ; la case du groupe coche/décoche tous\n"
            "  ses tracks d'un coup — déplie (▸) pour choisir track par track.\n"
            "Sélection + touche Suppr = retirer ce(s) calque(s) (un .ver entier\n"
            "si le groupe est sélectionné, ou juste le track choisi).")
        self.layer_list.itemChanged.connect(self._on_layer_item_changed)
        self.layer_list.installEventFilter(self)
        xv.addWidget(self.layer_list)

        self.export_layers_btn = self._btn(
            "Export custom des calques...", self._open_layer_export_dialog)
        self.export_layers_btn.setToolTip(
            "Choisir une plage de frames puis réorganiser l'export des tracks\n"
            ".ver : éclater un .ver multi-tracks en plusieurs fichiers mono-track,\n"
            "ou fusionner plusieurs .ver en un seul fichier multi-tracks.")
        xv.addWidget(self.export_layers_btn)

        self.yolo_convert_btn = self._btn(
            "Convertir .ver <-> YOLO...", self._open_yolo_convert_dialog)
        self.yolo_convert_btn.setToolTip(
            "Exporter les calques .ver de cette vue en dataset YOLO (labels +\n"
            "images, split train/val/test), ou importer un dossier YOLO\n"
            "(un .txt par frame) en .ver.")
        xv.addWidget(self.yolo_convert_btn)

        # (Bouton "Plugins..." retire : la gestion des plugins vit desormais
        # dans la zone dediee de l'onglet Parametres -- evite le doublon.)

        _logs_lbl = QtWidgets.QLabel("Logs (frame courante) :")
        _logs_lbl.setStyleSheet("font-size:11px; color:#999;")
        xv.addWidget(_logs_lbl)
        self.sidecar_logs = QtWidgets.QListWidget()
        self.sidecar_logs.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.sidecar_logs.setFocusPolicy(Qt.NoFocus)
        self.sidecar_logs.setMaximumHeight(90)   # réduit : ne prend plus tout l'espace
        self.sidecar_logs.setToolTip(
            "Numero de la frame + libelle(s) des overlays affiches sur l'image")
        xv.addWidget(self.sidecar_logs)
        xv.addStretch(1)
        hw = QtWidgets.QWidget()
        hw.setObjectName("histogram_render_panel")
        self.hist_render_panel = hw
        hv = QtWidgets.QVBoxLayout(hw)
        hv.setContentsMargins(6, 6, 6, 6)
        hv.addWidget(QtWidgets.QLabel("Histogramme (vue sélectionnée)"))
        hv.addWidget(self.hist)
        # ── Rendu global (LUT + filtres) : s'applique à TOUTES les vues ──
        grp_rnd = QtWidgets.QGroupBox("Rendu — toutes les vues")
        rgl = QtWidgets.QVBoxLayout(grp_rnd)
        rgl.setContentsMargins(6, 4, 6, 6); rgl.setSpacing(3)
        lrow = QtWidgets.QHBoxLayout()
        lrow.addWidget(QtWidgets.QLabel("LUT :"))
        self.lut_hist_combo = QtWidgets.QComboBox()
        self.lut_hist_combo.setObjectName("global_lut")
        self.lut_hist_combo.setFocusPolicy(Qt.NoFocus)
        for _n, _v in COLORMAPS.items():
            self.lut_hist_combo.addItem(_n, _v)
        lrow.addWidget(self.lut_hist_combo, 1)
        rgl.addLayout(lrow)
        frow = QtWidgets.QHBoxLayout()
        self.flt_edges_chk  = QtWidgets.QCheckBox("Bords")
        self.flt_sharp_chk  = QtWidgets.QCheckBox("Netteté")
        self.flt_invert_chk = QtWidgets.QCheckBox("Inverser")
        self.flt_clahe_chk  = QtWidgets.QCheckBox("CLAHE")
        for _c in (self.flt_edges_chk, self.flt_sharp_chk,
                   self.flt_invert_chk, self.flt_clahe_chk):
            _c.setFocusPolicy(Qt.NoFocus)
            frow.addWidget(_c)
        rgl.addLayout(frow)
        hv.addWidget(grp_rnd)
        # échelle de couleur (LUT). La valeur du pixel au survol s'affiche
        # directement sur la vue (en vert, haut-gauche), pas ici.
        self.colorbar = ColorBar()
        hv.addWidget(self.colorbar)
        hv.addStretch(1)
        self.side_split = QtWidgets.QSplitter(Qt.Vertical)
        self.side_split.setChildrenCollapsible(False)
        self.side_split.addWidget(xw)
        self.side_split.addWidget(hw)
        self.side_split.setSizes([1000, 1000])
        self.right_tab.addTab(self.side_split, icons.icon("layers"), "Hist / Calque")

        # bande de sélection de vue : [1] [2] ... — ces panneaux (Hist/SIDECAR,
        # Conversions, Paramètres) reflètent la vue sélectionnée.
        _rc = QtWidgets.QWidget()
        _rcl = QtWidgets.QVBoxLayout(_rc)
        _rcl.setContentsMargins(0, 0, 0, 0); _rcl.setSpacing(2)
        self.view_strip = QtWidgets.QWidget()
        self._view_strip_lay = QtWidgets.QHBoxLayout(self.view_strip)
        self._view_strip_lay.setContentsMargins(4, 3, 4, 0)
        self._view_strip_lay.setSpacing(3)
        self._view_strip_lay.addWidget(QtWidgets.QLabel("Vue :"))
        self._view_btns = []
        self._view_strip_lay.addStretch(1)
        _rcl.addWidget(self.view_strip)
        _rcl.addWidget(self.right_tab, 1)
        self.right_dock.setWidget(_rc)
        self.right_tab.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        _rc.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        # Largeur mini garantissant que les 3 onglets (Hist/Calque, Conversions,
        # Paramètres) tiennent tous dans la barre d'onglets dès l'ouverture :
        # sans elle, un right_dock trop etroit au premier lancement fait
        # apparaitre les petites fleches de defilement d'onglets de Qt, qui
        # cachent Paramètres tant qu'on ne clique pas dessus.
        self.right_dock.setMinimumWidth(320)
        self.addDockWidget(Qt.RightDockWidgetArea, self.right_dock)
        # Visible par defaut (Calque SIDECAR + histo)
        self.right_tab.currentChanged.connect(
            lambda i: self._update_right_btns(i if self.right_dock.isVisible() else -1))
        self.right_dock.visibilityChanged.connect(
            lambda v: self._update_right_btns(
                self.right_tab.currentIndex() if v else -1))

        # ===== panneaux gauche : Outils TI (haut 2/3) + Historique (bas 1/3) =====
        self._build_tools_dock()   # doit etre cree avant hist pour le splitDockWidget
        self._build_hist_dock()
        self._build_convlog_dock()
        self._build_params_dock()

    def _build_hist_dock(self):
        self.hist_dock = QtWidgets.QDockWidget("Historique des sources", self)
        self.hist_dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        hw = QtWidgets.QWidget()
        hv = QtWidgets.QVBoxLayout(hw)
        hv.setContentsMargins(4, 4, 4, 4)
        hv.addWidget(QtWidgets.QLabel("Sources ouvertes cette session :"))
        self.hist_list = HistoryList()
        self.hist_list.path_resolver = self._history_path_at
        self.hist_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.hist_list.setToolTip(
            "Double-clic : rouvrir la source\n"
            "Glisser vers un bloc du split : l'y charger\n"
            "Sélection (Maj/Ctrl) + Suppr : retirer de l'historique")
        self.hist_list.itemDoubleClicked.connect(self._load_from_history_item)
        self.hist_list.installEventFilter(self)
        hv.addWidget(self.hist_list, 1)
        self.hist_dock.setWidget(hw)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.hist_dock)
        # Historique sous Outils TI, ratio 2:1 (outils 2/3, historique 1/3)
        self.splitDockWidget(self.tools_dock, self.hist_dock, Qt.Vertical)
        QtCore.QTimer.singleShot(0, self._set_left_split_ratio)
        # Visible par defaut
        self.hist_btn.setChecked(True)
        self.hist_dock.visibilityChanged.connect(self.hist_btn.setChecked)

    def _build_tools_dock(self):
        self.tools_dock = QtWidgets.QDockWidget("Outils TI", self)
        self.tools_dock.setObjectName("tools_dock")
        self.tools_dock.setAllowedAreas(
            Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.tools_panel = ToolsPanel()
        _tools_scroll = QtWidgets.QScrollArea()
        _tools_scroll.setWidget(self.tools_panel)
        _tools_scroll.setWidgetResizable(True)
        _tools_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        _tools_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tools_dock.setWidget(_tools_scroll)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.tools_dock)
        self.tools_dock.setMinimumWidth(210)
        # Visible par defaut
        self.tools_dock.show()
        self.tools_btn.setChecked(True)
        self.tools_dock.visibilityChanged.connect(self.tools_btn.setChecked)
        # connexions
        self.tools_panel.modeRequested.connect(self._on_tool_mode)
        self.tools_panel.tempSubChanged.connect(self._on_temp_sub)
        self.tools_panel.fft2dRequested.connect(self._run_fft2d)
        self.tools_panel.roiConvertRequested.connect(self._on_roi_convert)
        self.view.lineChanged.connect(self._on_line_changed)
        self.view.rulerChanged.connect(self._on_ruler_changed)

    def _toggle_tools_dock(self):
        self.tools_dock.setVisible(self.tools_btn.isChecked())
        if self.tools_btn.isChecked():
            self.tools_dock.raise_()
        else:
            # fermer le panneau retire les tracés jaunes (ROI / ligne / règle)
            self._clear_view_marks()

    def _clear_view_marks(self):
        """Efface les tracés jaunes (ROI, ligne, règle) de toutes les vues."""
        self._roi = None
        self._dyn_crop = False
        for w in (self._split_canvas._order if hasattr(self, "_split_canvas") else []):
            vid = getattr(w, "video", None)
            if vid is not None:
                vid.clear_roi()
        self.tools_panel.roi_panel.clear()
        self.tools_panel.update_profile(None)
        self.tools_panel.update_ruler_pts([])
        if self._raw is not None:
            self._display()

    def _set_left_split_ratio(self):
        """Répartit Outils TI (2/3) / Historique (1/3)."""
        h = self.height()
        if h > 0:
            self.resizeDocks([self.tools_dock, self.hist_dock],
                             [h * 67 // 100, h * 33 // 100], Qt.Vertical)

    def _on_tool_mode(self, mode):
        # changer d'outil efface les tracés jaunes de l'outil précédent
        self._clear_view_marks()
        self.view.set_tool_mode(mode)

    def _on_line_changed(self, x1, y1, x2, y2):
        """Appelé par VideoWidget quand un profil de ligne est tracé."""
        if self._raw is not None:
            inten = to_intensity(self._apply_rotation(self._raw))
            h, w = inten.shape[:2]
            n = max(abs(x2 - x1), abs(y2 - y1), 1)
            xs = np.linspace(x1, x2, n + 1).clip(0, w - 1).astype(int)
            ys = np.linspace(y1, y2, n + 1).clip(0, h - 1).astype(int)
            profile = inten[ys, xs].astype(np.float64)
            self.tools_panel.update_profile(profile)

    def _on_ruler_changed(self, pts):
        """Appelé par VideoWidget quand la polyligne de la règle change."""
        self.tools_panel.update_ruler_pts(pts)

    # ----- Ctrl+C : copie image + métadonnées -----
    def _copy_to_clipboard(self):
        if self._display_bgr is None or self.source is None:
            return
        bgr = self._display_bgr
        if bgr.ndim == 3:
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[:2]
            qimg = QtGui.QImage(
                rgb.data, w, h, rgb.strides[0],
                QtGui.QImage.Format_RGB888).copy()
        else:
            h, w = bgr.shape
            qimg = QtGui.QImage(
                bgr.data, w, h, bgr.strides[0],
                QtGui.QImage.Format_Grayscale8).copy()
        fps = getattr(self.source, 'fps', 25.0) or 25.0
        ts  = fmt_time(self.cur / max(fps, 0.001))
        meta = (f"Source : {self.source.name}\n"
                f"Frame  : {self.cur} / {self.source.count - 1}\n"
                f"Temps  : {ts}\n"
                f"Fenêtre: lo={self.hist.lo:.1f}  hi={self.hist.hi:.1f}\n"
                f"LUT    : {self.lut_combo.currentText()}")
        mime = QtCore.QMimeData()
        mime.setImageData(qimg)
        mime.setText(meta)
        QtWidgets.QApplication.clipboard().setMimeData(mime)
        self.statusBar().showMessage(
            "Image + métadonnées copiées dans le presse-papier (Ctrl+V pour coller)", 3000)

    # ----- FFT 2D spatiale sur le crop ROI (frame courante) -----
    def _run_fft2d(self):
        if self.source is None or self._roi is None or self._raw is None:
            self.statusBar().showMessage(
                "FFT 2D : charger une source et tracer une ROI d'abord", 3000)
            return
        x, y, w, h = self._roi
        inten = to_intensity(self._apply_rotation(self._raw))
        Hf, Wf = inten.shape[:2]
        x1 = max(0, int(x)); y1 = max(0, int(y))
        x2 = min(Wf, x1 + int(w)); y2 = min(Hf, y1 + int(h))
        if x2 - x1 < 4 or y2 - y1 < 4:
            self.statusBar().showMessage("FFT 2D : ROI trop petite", 3000)
            return
        crop = inten[y1:y2, x1:x2].astype(np.float64)
        # FFT 2D -> spectre de magnitude (log), recentré
        F = np.fft.fftshift(np.fft.fft2(crop))
        mag = np.log1p(np.abs(F))
        mn, mx = float(mag.min()), float(mag.max())
        spec = np.zeros_like(mag, dtype=np.uint8) if mx <= mn else \
            ((mag - mn) / (mx - mn) * 255.0).astype(np.uint8)
        spec = np.ascontiguousarray(spec)
        ch, cw = spec.shape
        qimg = QtGui.QImage(spec.data, cw, ch, spec.strides[0],
                            QtGui.QImage.Format_Grayscale8).copy()
        box = self.tools_panel.fft2d_label.size()
        tgt = box if (box.width() > 10 and box.height() > 10) else QtCore.QSize(200, 160)
        pix = QtGui.QPixmap.fromImage(qimg).scaled(
            tgt, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        info = f"Spectre FFT 2D — crop {cw}×{ch} px"
        self.tools_panel.show_fft2d(pix, info)
        self.statusBar().showMessage(info, 3000)

    # ----- split view -----
    def _set_split(self, mode: str):
        self._split_canvas.set_mode(mode)
        self._link_start_frames = self._starts_for_mode(mode)
        self.link_btn.setEnabled(mode != "1")
        if mode == "1" and self._link_views:
            self._set_link_enabled(False)
        act = self._split_actions.get(mode)
        if act is not None and not act.isChecked():
            act.setChecked(True)
        # libellé compact du bouton reflétant le mode courant
        short = {"1": "▦  Vues ▾", "h2": "▮▮  Vues ▾", "v2": "▬/▬  Vues ▾",
                 "h3": "▮▮▮  Vues ▾", "q4": "⊞  Vues ▾", "fusion": "⧉  Fusion ▾",
                 "temporal": "◧◨  Temporel ▾"}
        self.split_btn.setText(short.get(mode, "▦  Vues ▾"))
        _temporal = (mode == "temporal")
        self._temporal_n_lbl_act.setVisible(_temporal)
        self._temporal_n_spin_act.setVisible(_temporal)
        # Barre du bas toujours visible. En multivue : version épurée (fps
        # fixe + Convertir) qui pilote TOUTES les vues — pas de slider ni de
        # compteur de frame (n'auraient pas de sens pour un ensemble de
        # vues). Le groupe lecture/pas-à-pas « ⟳ toutes les vues » est retiré
        # en multivue : redondant avec la mini-barre de lecture propre à
        # chaque vue, et source de confusion (deux lectures indépendantes).
        if hasattr(self, "_ctrl_widget"):
            multi = mode != "1"
            self._ctrl_widget.setVisible(True)
            self._seek_widget.setVisible(not multi)
            # Lecture / pas-a-pas (play, -1, +1) restent visibles en multivue,
            # a gauche du FPS "toutes les vues" : ils pilotent alors TOUTES
            # les vues d'un coup (_bar_play / _bar_step), pas seulement le
            # moteur. loop/first/last restent reserves a la vue unique
            # (redondants avec la mini-barre de chaque vue en multivue).
            for w in (self.loop_btn, self.first_btn, self.last_btn,
                      self.fps_label, self.open_file_btn, self.open_dir_btn):
                w.setVisible(not multi)
            self._ctrl_widget.setStyleSheet(
                "QWidget{background:#1a1a1f;border:1px solid #33333c;"
                "border-radius:8px;color:#ffffff;}"
                "QPushButton{color:#ffffff;padding:4px 10px;}"
                "QLabel{color:#ffffff;}" if multi else "")
            self._all_lbl.setVisible(multi)
            _bottom_lay = self._ctrl_widget.layout()
            if _bottom_lay is not None:
                _bottom_lay.setContentsMargins(8, 10, 8, 8) if multi else \
                    _bottom_lay.setContentsMargins(0, 0, 0, 0)
            if hasattr(self, "_central_lay"):
                self._central_lay.setSpacing(10 if multi else 6)
            if multi:
                self.convert_btn.setMenu(None)
                self.convert_btn.setText("🎬 Convertir (toutes les vues)")
                self.convert_btn.setToolTip(
                    "Assemble un MP4 de la composition multi-vues actuelle\n"
                    "(disposition, calques et contraste inclus).")
                self.convert_btn.setVisible(True)
            else:
                self.convert_btn.setMenu(self.convert_menu)
                self._rebuild_convert_menu()
        self._update_view_strip()
        # (re)câbler les signaux de lien pour les vues nouvellement créées
        if self._link_views:
            self._wire_view_signals()
        if mode == "temporal":
            self._enter_temporal()
        else:
            self._leave_temporal()
            if self._link_views:
                self._apply_link_base(0)
        if mode == "fusion":
            self.statusBar().showMessage(
                "Fusion : bloc haut-gauche = source principale, "
                "déposez une 2ᵉ source en haut-droite. α réglable en bas (orange).", 6000)
        elif mode == "temporal":
            self.statusBar().showMessage(
                "Temporel : même flux, frame i-N à gauche et i à droite. "
                "Réglez N dans la barre d'outils.", 6000)

    # ----- mode temporel (i-N | i, même flux) -----
    def _reopen_source_like(self, src):
        """Reconstruit un objet source INDÉPENDANT sur le même flux que `src`
        (2e lecteur, curseur propre) -- jamais le même objet que le moteur, pour
        éviter les conflits de position décrits dans core/sources.py. None si le
        type de source ne se réouvre pas."""
        if src is None:
            return None
        try:
            if isinstance(src, YuvSource):   # avant le test paths (sinon relu en images)
                return YuvSource(list(src.paths), src.width, src.height, src.fmt,
                                 label=src.name, directory=src.directory)
            path = getattr(src, "path", None)
            if path:
                return self._build_source(path)
            paths = getattr(src, "paths", None)
            if paths:
                return ImageSequenceSource(
                    list(paths), label=getattr(src, "name", "sequence"),
                    directory=getattr(src, "_dir", None))
        except Exception as ex:
            self.statusBar().showMessage(f"Temporel : réouverture impossible ({ex})", 4000)
        return None

    def _ensure_temporal_source(self):
        """(Re)ouvre le 2e lecteur sur le flux du moteur et le charge dans la
        vue temporelle gauche."""
        sc = self._split_canvas
        sat = sc.temporal_sat()
        if sat is None or self.source is None:
            return
        self._close_temporal_source()
        clone = self._reopen_source_like(self.source)
        if clone is None:
            self.statusBar().showMessage(
                "Temporel : type de source non clonable.", 4000)
            return
        self._temporal_src = clone
        sat.load(clone, self.hist.lo, self.hist.hi, self.lut_combo.currentData(),
                 self._cube_lut, self._cube_size, self.hist.full_range,
                 self._filter_flags())
        sat.set_fetch_scale(self._fetch_scale)

    def _close_temporal_source(self):
        src = getattr(self, "_temporal_src", None)
        if src is not None:
            try:
                src.close()
            except Exception:
                pass
        self._temporal_src = None

    def _enter_temporal(self):
        if self.source is None:
            self.statusBar().showMessage(
                "Temporel : ouvrez d'abord une source.", 4000)
            return
        self._ensure_temporal_source()
        self._sync_temporal(self.cur)

    def _leave_temporal(self):
        if getattr(self, "_temporal_src", None) is None:
            return
        sats = getattr(self._split_canvas, "_sats", [])
        if sats:
            sats[0].clear()     # ne pas laisser traîner le clone dans la vue
        self._close_temporal_source()

    def _on_temporal_n_changed(self, _v):
        self._sync_temporal(self.cur)

    def _sync_temporal(self, cur):
        """Pose la vue temporelle gauche à max(0, cur - N). Sans effet hors mode
        temporel."""
        sc = getattr(self, "_split_canvas", None)
        if sc is None or not sc.is_temporal():
            return
        sat = sc.temporal_sat()
        if sat is None or not sat.has_source():
            return
        n = self.temporal_n_spin.value()
        target = max(0, int(cur) - int(n))
        if getattr(sat, "_cur", None) != target:
            sat._goto(target)

    # ----- inter-connexion inter-vues (capacité transverse) -----
    def _views_context(self):
        """Contexte multivue exposé aux plugins (api.views()) : décrit chaque
        slot affiché (index, rôle, identité source stable, frame courante,
        taille image)."""
        sc = self._split_canvas
        temporal = sc.is_temporal()
        tsat = sc.temporal_sat()
        views = []
        for index, w, is_primary in sc.ordered_views():
            if is_primary:
                src = self.source
                fidx = self.cur if src is not None else None
                role = "temporal_cur" if temporal else "primary"
                disp = self._display_bgr
                hpx, wpx = (disp.shape[0], disp.shape[1]) if disp is not None else (0, 0)
            else:
                src = getattr(w, "_source", None)
                fidx = getattr(w, "_cur", None) if src is not None else None
                role = "temporal_ref" if (temporal and w is tsat) else "satellite"
                last = getattr(w, "_last_bgr", None)
                hpx, wpx = (last.shape[0], last.shape[1]) if last is not None else (0, 0)
            views.append({
                "index": index,
                "role": role,
                "view_key": self._view_key_for(src) if src is not None else "__none__",
                "source_name": getattr(src, "name", "") if src is not None else "",
                "frame_idx": int(fidx) if fidx is not None else None,
                "start_frame": (self._link_start_frames[index]
                                if index < len(self._link_start_frames) else 0),
                "w": int(wpx), "h": int(hpx),
            })
        ctx = {"mode": sc.mode(), "count": len(views), "views": views,
               "sync_base_frame": int(self._sync_base_frame)}
        if temporal:
            ctx["n_offset"] = int(self.temporal_n_spin.value())
        return ctx

    # ----- gestion des vues (toutes équivalentes) -----
    def _build_source(self, p):
        """Construit une source à partir d'un chemin (dossier / vidéo / specialized / image)."""
        ext = os.path.splitext(p)[1].lower()
        if os.path.isdir(p):
            imgs = list_images(p)
            if not imgs:
                return None
            return ImageSequenceSource(imgs, label=os.path.basename(p), directory=p)
        if ext in ('.mp4', '.avi', '.mov', '.mkv', '.m4v', '.wmv',
                   '.flv', '.webm', '.mpg', '.mpeg', '.ts', '.m2ts', '.mts'):
            return VideoSource(p)
        if supports_path(p, "sequence_format"):
            return SpecializedSource(p)
        if ext in ('.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp', '.jp2'):
            return ImageSequenceSource(
                [p], label=os.path.basename(p), directory=os.path.dirname(p))
        return None

    def _on_view_media_dropped(self, pos: int, paths: list):
        """Média déposé sur une vue : la vue principale ouvre dans le moteur,
        une vue secondaire charge sa propre source. Un fichier de DONNEES (CSV,
        Excel...) non reconnu nativement est proposé à l'association avec une
        entrée de plugin, POUR LA VUE ciblée (qui devient active au préalable)."""
        w = self._split_canvas.widget_at(pos)
        if w is None or not paths:
            return
        # fichiers "données" (non media, non calque) -> association plugin par vue
        data = [p for p in paths if self._is_plugin_data(p)]
        media = [p for p in paths if p not in data]
        if data:
            if w is not self._primary_frame:
                self._on_activate_pos(pos)     # la vue ciblée devient la primaire
                w = self._primary_frame
            for p in data:
                if not self._offer_plugin_drop(p):
                    self._plugin_loader.dispatch_drop(p)   # repli : plugins legacy
            if not media:
                return
        if w is self._primary_frame:
            # un plugin legacy peut réclamer un fichier que l'app ne sait pas
            # ouvrir nativement AVANT de tenter de l'ouvrir comme média (voir
            # MainWindow.dropEvent) : la vue au premier plan reçoit la plupart
            # des glisser-déposer réels, pas MainWindow.dropEvent (fond).
            loader = getattr(self, "_plugin_loader", None)
            remaining = media
            if loader is not None:
                remaining = [p for p in media if not loader.dispatch_drop(p)]
            if remaining:
                self.open_paths(remaining)
            return
        try:
            src = self._build_source(media[0])
        except Exception as ex:
            self.statusBar().showMessage(f"Vue {pos + 1} : {ex}", 4000)
            return
        if src is None:
            return
        w.load(src, self.hist.lo, self.hist.hi,
               self.lut_combo.currentData(), self._cube_lut, self._cube_size,
               self.hist.full_range, self._filter_flags())
        w.set_fetch_scale(self._fetch_scale)
        self._add_to_history(src)

    def _load_overlay_path(self, path):
        """Applique un .sidecar (calque) ou un .ver/.txt (annotations) au moteur."""
        if supports_path(path, "overlay_format"):
            if parse_overlay_sidecar is None:
                return
            try:
                ov, info = parse_overlay_sidecar(path)
            except Exception as ex:
                QtWidgets.QMessageBox.warning(self, "SIDECAR illisible", str(ex))
                return
            if self.source is None:
                self._pending = (ov, info)
            else:
                self.apply_overlays(ov, info, name=os.path.basename(path))
        else:
            self._load_dropped_annotations(path)

    def _on_view_overlay_dropped(self, pos: int, path: str):
        """Calque/annotations déposés sur une vue : cette vue devient active,
        puis le calque lui est appliqué (fonctionne donc par vue)."""
        w = self._split_canvas.widget_at(pos)
        if w is None:
            return
        if w is not self._primary_frame:
            self._on_activate_pos(pos)
        self._load_overlay_path(path)

    # -- état moteur <-> vue --
    def _engine_export(self):
        return dict(source=self.source, cur=self.cur,
                    lo=self.hist.lo, hi=self.hist.hi,
                    mode=self.hist.per_frame_mode(),
                    zoom=self.view.get_view(),
                    rec_on=self.rec_btn.isChecked() if hasattr(self, "rec_btn") else False,
                    overlays=self.overlays, annots=self._annotations,
                    annot_path=self._annot_path, layers=list(self._layers),
                    track_next=self._annot_track_next,
                    name=self.source.name if self.source else "")

    def _engine_import(self, st):
        src = st.get("source")
        self.source = src
        self.overlays = st.get("overlays") or {}
        self._annotations = st.get("annots") or {}
        self._annot_path = st.get("annot_path", "")
        self._layers = st.get("layers") or []
        # prochain track_id = max(track) + 1 des annotations reprises
        mx = -1
        for dets in self._annotations.values():
            for d in dets:
                if len(d) > 5:
                    mx = max(mx, d[5])
        self._annot_track_next = st.get("track_next", mx + 1)
        self._rebuild_layer_list()
        # ROI : le rectangle jaune appartient à la vue quittée -> on l'efface
        self._roi = None
        self._roi_track_id = None
        self._dyn_crop = False
        self._raw = None
        self.view.clear_roi()
        self.tools_panel.roi_panel.clear()
        # zoom / pan : propres à CHAQUE vue (ne doit pas rester sur le widget
        # moteur quand celui-ci change de position/rôle) -> restaurés plus
        # bas juste après le chargement de la frame (dimensions connues).
        # paramètres liés à la vue : remis à neutre pour la nouvelle vue
        self._extract_start = -1
        self._extract_end = -1
        if hasattr(self, "slider"):
            self.slider.set_extract_zone(-1, -1)
        if hasattr(self, "_primary_frame"):
            self._primary_frame.mini.set_extract_zone(-1, -1)
        if hasattr(self, "extract_lbl"):
            self.extract_lbl.setText("─")
        self._segments = []
        if hasattr(self, "seg_list"):
            self._refresh_segments()
        if hasattr(self, "rec_btn"):
            _rec_on = bool(st.get("rec_on", False))
            self.rec_btn.blockSignals(True)
            self.rec_btn.setChecked(_rec_on)
            self.rec_btn.blockSignals(False)
            self.rec_btn.setText("REC clics  [ON]" if _rec_on else "REC clics")
        if hasattr(self, "crosshair_chk"):
            self.crosshair_chk.blockSignals(True)
            self.crosshair_chk.setChecked(False)
            self.crosshair_chk.blockSignals(False)
        self._crosshair = False
        self.clicks = []
        self._stop_prefetch()
        self._prefetch_cache.clear()
        self._configure_hist(src)
        if src is None:
            self._primary_frame.set_name("")
            self.view.set_overlays([])
            self.view.set_frame(None)
            self.view.reset_view()
            self._enable_playback(False)
            self._update_sidecar_logs([])
            return
        self.hist.set_window(st.get("lo", self.hist.lo),
                             st.get("hi", self.hist.hi), emit=False)
        # mode (Auto/Min-Max/Pleine plage/manuel) propre à la vue : restauré
        # AVANT show_index pour que le premier _display() de la nouvelle vue
        # respecte ce mode plutôt que celui, global, laissé par la vue quittée.
        self.hist.set_mode_silent(st.get("mode", "Custom"))
        n = src.count
        self.slider.blockSignals(True)
        self.slider.setMinimum(0); self.slider.setMaximum(max(0, n - 1))
        self.slider.blockSignals(False)
        self.frame_spin.blockSignals(True)
        self.frame_spin.setRange(0, max(0, n - 1))
        self.frame_spin.blockSignals(False)
        # fps « toutes les vues » : fixe en multivue, ne doit PAS changer
        # juste parce qu'on active une autre vue (source différente).
        if self._split_canvas.mode() == "1":
            self.fps_spin.blockSignals(True)
            self.fps_spin.setValue(float(src.fps))
            self.fps_spin.blockSignals(False)
        self._primary_frame.set_name(src.name)
        self._primary_frame.mini.set_range(n)
        self._rebuild_convert_menu()
        if self._audio_player is not None:
            if isinstance(src, VideoSource):
                self._audio_player.setSource(QUrl.fromLocalFile(os.path.abspath(src.path)))
            else:
                self._audio_player.setSource(QUrl())
            self.son_btn.setEnabled(isinstance(src, VideoSource))
        self._setup_clicks_file()
        self._load_existing_clicks()
        self._enable_playback(True)
        self.setWindowTitle(f"FrameViewer - {src.name}")
        self.sidecar_label.setText(
            f"{len(self.overlays)} frames annotees" if self.overlays else "Aucun SIDECAR charge")
        self.show_index(st.get("cur", 0))
        # zoom / pan restaurés après le chargement de la frame (dimensions
        # de l'image connues -> le clamp du pan est correct).
        self.view.set_view(*st.get("zoom", (1.0, 0.0, 0.0)))
        self._highlight_history()

    def _on_view_convert_clicked(self, pos: int):
        """Bouton ⇄ d'une vue : l'active si besoin, puis ouvre le dialogue de
        conversion (Extraire...) sur toute sa séquence, avec le même bouton
        « exporter la vue telle qu'affichée » que pour l'extraction IN→OUT."""
        sc = self._split_canvas
        w = sc.widget_at(pos)
        if w is None:
            return
        if w is not self._primary_frame:
            self._on_activate_pos(pos)
        if self.source is None:
            self.statusBar().showMessage(f"Vue [{pos + 1}] : aucune source à convertir.", 3000)
            return
        n = self.source.count
        dlg = ClipExtractDialog(self, 0, max(0, n - 1), has_in_out=False)
        dlg.setWindowTitle(f"Convertir — vue [{pos + 1}] : {self.source.name}")
        dlg.exec()

    def _on_roi_convert(self):
        """Bouton « Convertir » du panneau ROI : exporte la ROI (fixe ou
        suivant une boîte .ver) sur toute la séquence."""
        if self.source is None or self._roi is None:
            QtWidgets.QMessageBox.information(
                self, "Convertir la ROI",
                "Trace d'abord une ROI (glisser sur l'image), ou clique\n"
                "une boîte .ver pour la suivre, avant de convertir.")
            return
        dlg = RoiConvertDialog(self)
        dlg.exec()

    def _on_activate_pos(self, pos: int):
        """Sélection d'une vue : le moteur s'y rattache (outils TI + histogramme)."""
        sc = self._split_canvas
        if sc.is_temporal():
            return   # rôles figés : la droite est toujours le moteur, la gauche est figée
        w = sc.widget_at(pos)
        if w is None:
            return
        # cliquer sur une vue pour la selectionner ramene toujours les
        # fleches clavier sur le moteur (qui, apres le swap ci-dessous,
        # correspond justement a la vue cliquee).
        self._set_kbd_target("engine")
        # sélectionner une vue normale enlève la sélection orange de la fusion
        was_fusion = sc._fusion_active
        sc._fusion_active = False
        if w is self._primary_frame:
            if was_fusion:
                sc._update_borders()
                self._update_view_strip()
            return
        self.stop()
        base = self._sync_base_frame
        self._syncing = True
        try:
            old = self._engine_export()
            new = w.export_state()
            w.import_state(old)
            sc.move_primary_to(pos)
            self._engine_import(new)
        finally:
            self._syncing = False
        self.refresh_frame()
        self._update_view_strip()
        self._on_current_view_changed()
        if self._link_views:
            self._apply_link_base(base)

    def _on_swap_positions(self, p1: int, p2: int):
        """Échange le contenu de deux vues (clic droit glissé)."""
        sc = self._split_canvas
        w1, w2 = sc.widget_at(p1), sc.widget_at(p2)
        if w1 is None or w2 is None or w1 is w2:
            return
        self.stop()
        base = self._sync_base_frame
        self._syncing = True
        try:
            s1 = self._engine_export() if w1 is self._primary_frame else w1.export_state()
            s2 = self._engine_export() if w2 is self._primary_frame else w2.export_state()
            if w1 is self._primary_frame:
                self._engine_import(s2)
            else:
                w1.import_state(s2)
            if w2 is self._primary_frame:
                self._engine_import(s1)
            else:
                w2.import_state(s1)
        finally:
            self._syncing = False
        self.refresh_frame()
        self._on_current_view_changed()
        if self._link_views:
            self._apply_link_base(base)

    def _update_view_strip(self):
        """Reconstruit la bande [1] [2] … du dock Panneaux selon la disposition."""
        if not hasattr(self, "_view_strip_lay"):
            return
        n = self._split_canvas.n_slots()
        active = self._split_canvas.active_pos()
        # (re)créer le bon nombre de boutons
        while len(self._view_btns) < n:
            i = len(self._view_btns)
            b = QtWidgets.QPushButton(f"[{i + 1}]")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.setFixedHeight(20)
            b.setStyleSheet(
                "QPushButton{padding:1px 8px;border:1px solid #444;border-radius:3px;}"
                "QPushButton:checked{background:#2d4f70;border-color:#5aafff;color:#fff;}")
            b.clicked.connect(lambda _=False, p=i: self._on_activate_pos(p))
            self._view_btns.append(b)
            self._view_strip_lay.insertWidget(1 + i, b)
        for i, b in enumerate(self._view_btns):
            b.setVisible(i < n)
            b.blockSignals(True)
            b.setChecked(i == active and not self._split_canvas._fusion_active)
            b.blockSignals(False)

    def _on_fusion_selected(self):
        """Vue fusion sélectionnée (orange) : les mesures ROI + l'extraction la ciblent."""
        if not self.tools_panel.is_roi_active():
            self.tools_panel._set_tool("roi")
        self._split_canvas._fusion.video.set_tool_mode("roi")
        self._update_view_strip()
        self.statusBar().showMessage(
            "Vue fusion sélectionnée : trace une ROI dessus (mesures), "
            "règle α, ou « ⤓ Extraire » pour récupérer le blend.", 5000)

    def _on_fusion_roi(self, x, y, w, h):
        """ROI tracée sur le rendu fusionné : stats calculées sur le blend."""
        fus = self._split_canvas._fusion
        out = getattr(fus, "_last_out", None) if fus is not None else None
        if out is None or w <= 0 or h <= 0:
            self.tools_panel.roi_panel.clear()
            return
        H, W = out.shape[:2]
        x1 = max(0, min(int(x), W - 1)); y1 = max(0, min(int(y), H - 1))
        x2 = min(W, x1 + int(w)); y2 = min(H, y1 + int(h))
        if x2 <= x1 or y2 <= y1:
            self.tools_panel.roi_panel.clear()
            return
        bgr_crop = out[y1:y2, x1:x2]
        inten_crop = (cv2.cvtColor(bgr_crop, cv2.COLOR_BGR2GRAY)
                      if bgr_crop.ndim == 3 else bgr_crop)
        if self.tools_dock.isHidden():
            self.tools_dock.show(); self.tools_dock.raise_()
        if not self.tools_panel.is_roi_active():
            self.tools_panel._set_tool("roi")
        self.tools_panel.roi_panel.update_crop(bgr_crop, inten_crop)
        self.statusBar().showMessage(
            f"ROI sur le rendu fusionné — {x2 - x1}×{y2 - y1} px", 3000)

    # ----- lien des vues (zoom / pan / frame) -----
    def _all_view_videos(self):
        vids = [self._primary_frame.video]
        sc = getattr(self, "_split_canvas", None)
        if sc is not None:
            for sv in sc._sats:
                vids.append(sv.video)
            if sc._fusion is not None:
                vids.append(sc._fusion.video)
        return vids

    def _wire_view_signals(self):
        """Connecte viewChanged de chaque vue (une seule fois)."""
        for v in self._all_view_videos():
            if id(v) not in self._wired_videos:
                v.viewChanged.connect(lambda _v=v: self._sync_view_transform(_v))
                self._wired_videos.add(id(v))

    def _link_sources(self):
        sources = []
        for _index, widget, is_primary in self._split_canvas.ordered_views():
            source = self.source if is_primary else getattr(widget, "_source", None)
            sources.append({
                "name": getattr(source, "name", "") if source is not None else "",
                "count": getattr(source, "count", 0) if source is not None else 0,
            })
        return sources

    def _starts_for_mode(self, mode=None):
        mode = mode or self._split_canvas.mode()
        count = self._split_canvas.n_slots()
        values = list(self._link_start_frames_by_mode.get(mode, []))
        values = (values + [0] * count)[:count]
        self._link_start_frames_by_mode[mode] = values
        return values

    def _open_link_dialog(self, checked=False):
        mode = self._split_canvas.mode()
        if mode == "1":
            self.link_btn.blockSignals(True)
            self.link_btn.setChecked(False)
            self.link_btn.blockSignals(False)
            return
        previous = self._link_views
        # Qt inverse l'etat du bouton checkable avant d'appeler ce slot. Une
        # liaison deja active doit donc conserver son etat dans le dialogue.
        # Au premier clic, `checked` exprime bien l'intention initiale.
        requested = previous if previous else bool(checked)
        starts = self._starts_for_mode(mode)
        dialog = LinkViewsDialog(self, mode, self._link_sources(), starts, requested)
        if not dialog.exec():
            self.link_btn.blockSignals(True)
            self.link_btn.setChecked(previous)
            self.link_btn.blockSignals(False)
            return
        active, values = dialog.values()
        if mode != "temporal":
            self._link_start_frames_by_mode[mode] = list(values)
            self._link_start_frames = list(values)
        self._set_link_enabled(active, reset_base=active and mode != "temporal")

    def _set_link_enabled(self, active, reset_base=False):
        self._link_views = bool(active)
        self.link_btn.blockSignals(True)
        self.link_btn.setChecked(self._link_views)
        self.link_btn.blockSignals(False)
        if self._link_views:
            self._wire_view_signals()
            self._sync_view_transform(self.view)
            if reset_base:
                self._apply_link_base(0)
            starts = ", ".join(str(v) for v in self._link_start_frames)
            self.statusBar().showMessage(
                f"Vues liées : départs [{starts}], zoom / déplacement / frame synchronisés", 5000)
        else:
            self.statusBar().showMessage("Vues déliées", 2000)

    def _link_base_bounds(self):
        maximum = None
        for index, widget, is_primary in self._split_canvas.ordered_views():
            source = self.source if is_primary else getattr(widget, "_source", None)
            if source is None or not getattr(source, "count", 0):
                continue
            start = self._link_start_frames[index] if index < len(self._link_start_frames) else 0
            candidate = int(source.count) - 1 - int(start)
            maximum = candidate if maximum is None else min(maximum, candidate)
        return 0, max(0, maximum if maximum is not None else 0)

    def _apply_link_base(self, base):
        if not self._link_views or self._split_canvas.is_temporal():
            return
        lower, upper = self._link_base_bounds()
        base = max(lower, min(int(base), upper))
        self._sync_base_frame = base
        self._syncing = True
        try:
            for index, widget, is_primary in self._split_canvas.ordered_views():
                start = self._link_start_frames[index] if index < len(self._link_start_frames) else 0
                target = base + int(start)
                if is_primary:
                    if self.source is not None and self.cur != target:
                        self.show_index(target)
                elif widget.has_source() and getattr(widget, "_cur", None) != target:
                    widget._goto(target)
        finally:
            self._syncing = False
        self._refresh_multiview_overlays()

    def _sync_view_transform(self, src_video):
        if not self._link_views or self._syncing:
            return
        z, px, py = src_video.get_view()
        self._syncing = True
        try:
            for v in self._all_view_videos():
                if v is not src_video:
                    v.set_view(z, px, py)
        finally:
            self._syncing = False

    def _sync_view_frame(self, idx, origin_pos=None):
        if not self._link_views or self._syncing:
            return
        # en temporel, la vue i-N garde son écart N (gérée par _sync_temporal) :
        # on ne la force PAS sur idx.
        if self._split_canvas.is_temporal():
            return
        pos = self._split_canvas.active_pos() if origin_pos is None else int(origin_pos)
        start = self._link_start_frames[pos] if pos < len(self._link_start_frames) else 0
        self._apply_link_base(int(idx) - int(start))

    def _on_linked_view_frame_changed(self, pos, idx):
        if not self._link_views or self._syncing or self._split_canvas.is_temporal():
            return
        widget = self._split_canvas.widget_at(pos)
        if widget is self._primary_frame:
            return
        self._sync_view_frame(idx, origin_pos=pos)

    def _on_probe(self, x, y):
        """Sonde : valeur native du pixel survolé, écrite en vert sur la vue."""
        # forward aux plugins "overlay code" (hook on_view_hover) : coords image
        if self.source is not None and x >= 0 and y >= 0:
            self._plugin_loader.dispatch_hover(self.cur, x, y)
        src = self._probe_src
        if x < 0 or y < 0 or src is None:
            self.view.set_probe_text(None)
            return
        H, W = src.shape[:2]
        if 0 <= y < H and 0 <= x < W:
            v = src[y, x]
            if np.ndim(v) == 0:
                vtxt = f"{float(v):.4g}"
            else:
                vtxt = "/".join(f"{float(c):.4g}" for c in np.atleast_1d(v))
            self.view.set_probe_text(f"x= {x}   y= {y}   v= {vtxt}")
        else:
            self.view.set_probe_text(None)

    def _view_source_and_window(self, w):
        """Source + fenêtre (lo,hi) d'une vue (principale = moteur, sinon satellite)."""
        if w is self._primary_frame:
            return self.source, (self.hist.lo, self.hist.hi)
        return getattr(w, "_source", None), (getattr(w, "_rp", (0, 255))[0],
                                             getattr(w, "_rp", (0, 255))[1])

    def _extract_fusion(self):
        """Extrait le rendu fusionné (blend des 2 vues du haut) comme une source."""
        sc = self._split_canvas
        if sc.mode() != "fusion" or len(sc._order) < 2:
            return
        src_a, rp_a = self._view_source_and_window(sc._order[0])
        src_b, rp_b = self._view_source_and_window(sc._order[1])
        if src_a is None and src_b is None:
            self.statusBar().showMessage(
                "Fusion : charger une source dans les deux vues du haut", 4000)
            return
        alpha = sc._fusion._alpha if sc._fusion else 0.5
        mode = sc._fusion.mode() if sc._fusion else "alpha"
        fsrc = FusionSource(src_a, rp_a, src_b, rp_b,
                            self.lut_combo.currentData(), self._cube_lut,
                            self._cube_size, self.hist.full_range,
                            self._filter_flags(), alpha, mode)
        if fsrc.count <= 0:
            self.statusBar().showMessage("Fusion : séquences vides", 4000)
            return
        # plage IN→OUT si définie, sinon toute la séquence
        s = self._extract_start if self._extract_start >= 0 else 0
        e = self._extract_end if self._extract_end >= 0 else fsrc.count - 1
        s = max(0, min(s, fsrc.count - 1))
        e = max(s, min(e, fsrc.count - 1))
        # on bascule temporairement la source du moteur sur la fusion pour le dialog
        saved_src, saved_raw, saved_cur = self.source, self._raw, self.cur
        saved_rot = self._rotation
        self.source = fsrc
        self._rotation = 0
        try:
            dlg = ClipExtractDialog(self, s, e)
            dlg.exec()
        finally:
            self.source = saved_src
            self._raw = saved_raw
            self.cur = saved_cur
            self._rotation = saved_rot

    def _on_view_doubled(self, pos: int):
        """Double-clic : plein écran sur cette vue / retour à la disposition."""
        sc = self._split_canvas
        if sc.mode() != "1":
            if pos != sc.active_pos():
                self._on_activate_pos(pos)
            self._prev_split_mode = sc.mode()
            self._set_split("1")
        elif self._prev_split_mode and self._prev_split_mode != "1":
            prev = self._prev_split_mode
            self._prev_split_mode = None
            self._set_split(prev)

    def _mini_play(self, on):
        if on and not self.timer.isActive():
            self.play()
        elif not on and self.timer.isActive():
            self.stop()

    # ----- cible des fleches clavier (gauche/droite/espace) -----
    def _set_kbd_target(self, tgt):
        """tgt: "engine" (vue active/moteur), "all" (barre du bas -> toutes
        les vues), ou une instance SatelliteView precise. Reprend aussi le
        focus clavier sur la fenetre : necessaire car BaseViewFrame.eventFilter
        avale les clics sur les vues (voir son commentaire), ce qui empeche
        Qt de deplacer le focus tout seul."""
        self._kbd_target = tgt
        self.setFocus(Qt.OtherFocusReason)

    def _on_view_nav_interacted(self, pos: int):
        """Play/precedent/suivant touche a la main sur la mini-barre d'UNE
        vue (sans forcement l'activer/la selectionner) : les fleches clavier
        doivent desormais agir sur CETTE vue precisement."""
        w = self._split_canvas.widget_at(pos)
        if w is None:
            return
        self._set_kbd_target("engine" if w is self._primary_frame else w)
        if self._link_views and w is not self._primary_frame:
            self._sync_view_frame(getattr(w, "_cur", 0), origin_pos=pos)

    def _route_step(self, d):
        """Applique +-1 frame a la cible clavier courante (_kbd_target)."""
        tgt = self._kbd_target
        if tgt == "all":
            self._bar_step(d)
        elif tgt == "engine" or tgt is self._primary_frame:
            self.step(d)
        else:
            tgt._goto(tgt._cur + d)

    def _route_play_toggle(self):
        """Bascule lecture/pause pour la cible clavier courante (Espace)."""
        tgt = self._kbd_target
        if tgt == "all":
            self._bar_play()
        elif tgt == "engine" or tgt is self._primary_frame:
            self.toggle_play()
        else:
            tgt.mini.b_play.setChecked(not tgt.mini.b_play.isChecked())

    # ----- barre du bas : agit sur TOUTES les vues -----
    def _other_views(self):
        """Les vues autres que la vue active (pilotée par le moteur)."""
        if not hasattr(self, "_split_canvas"):
            return []
        return [w for w in self._split_canvas._order
                if w is not self._primary_frame and w.has_source()]

    def _bar_play(self):
        self._set_kbd_target("all")
        self.toggle_play()
        on = self.timer.isActive()
        if self._link_views:
            return
        for s in self._other_views():
            s.mini.b_play.setChecked(on)

    def _bar_step(self, d):
        self._set_kbd_target("all")
        self.step(d)
        if self._link_views:
            return
        for s in self._other_views():
            s._goto(s._cur + d)

    def _bar_goto(self, idx):
        self._set_kbd_target("all")
        self.seek(idx)
        if self._link_views:
            return
        for s in self._other_views():
            s._goto(idx)

    def _bar_last(self):
        self._set_kbd_target("all")
        if self._link_views and not self._split_canvas.is_temporal():
            _lower, upper = self._link_base_bounds()
            self._apply_link_base(upper)
            return
        self._go_last()
        for s in self._other_views():
            s._goto(max(0, s._source.count - 1))

    def _bcast_fps(self, fps):
        """fps de la barre du bas -> toutes les vues."""
        for s in self._other_views():
            s._fps = fps
            if s._timer.isActive():
                s._timer.start(int(1000.0 / max(fps, 0.1)))

    def _on_temp_sub(self, enabled, k):
        self._temp_sub_on = enabled
        self._temp_sub_k  = k
        self.refresh_frame()

    def _build_convlog_dock(self):
        # Onglet 1 : Plugins (remplace l'ancien onglet "Conversions" -- son
        # journal est deplace dans Parametres). Tout le plugin vit ici :
        # activation, CSV rattaches (drag-drop + cases), cases par element du
        # modele "overlay code", et les boutons d'edition/creation.
        self.right_tab.addTab(self._build_plugins_tab(), icons.icon("puzzle"), "Plugins")

    def _show_right_tab(self, idx):
        """Affiche l'onglet droit idx; masque si deja visible sur cet onglet."""
        if self.right_dock.isVisible() and self.right_tab.currentIndex() == idx:
            self.right_dock.hide()
            self._update_right_btns(-1)
        else:
            self.right_tab.setCurrentIndex(idx)
            self.right_dock.show()
            self.right_dock.raise_()
            self._update_right_btns(idx)

    def _toggle_right_dock(self):
        """Bouton unique de la barre d'outils : ouvre/ferme right_dock, sur
        l'onglet actuellement selectionne (ou le 1er a la toute premiere
        ouverture). Le changement d'onglet se fait ensuite dans le
        QTabWidget lui-meme."""
        if self.right_dock.isVisible():
            self.right_dock.hide()
            self._update_right_btns(-1)
        else:
            idx = max(0, self.right_tab.currentIndex())
            self.right_tab.setCurrentIndex(idx)
            self.right_dock.show()
            self.right_dock.raise_()
            self._update_right_btns(idx)

    def _update_right_btns(self, active):
        self.right_panel_btn.blockSignals(True)
        self.right_panel_btn.setChecked(active != -1)
        self.right_panel_btn.blockSignals(False)

    def _toggle_hist_dock(self):
        self.hist_dock.setVisible(self.hist_btn.isChecked())
        if self.hist_btn.isChecked():
            self.hist_dock.raise_()

    def _param_section_hdr(self, text):
        """Étiquette de sous-section (Paramètres) : distingue les réglages
        propres à la vue/source sélectionnée de ceux qui sont globaux."""
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet(
            "color:#5aafff; font-size:11px; font-weight:bold;"
            "padding-top:2px; border-bottom:1px solid #3a3a42;")
        return lbl

    def _build_params_dock(self):
        # Onglet 2 : Parametres (etendu) integre dans right_tab. Deux
        # sous-sections explicites : ce qui s'applique a la vue/source
        # selectionnee (reinitialise a chaque changement de vue, voir
        # _engine_import) vs ce qui est global a l'application (persiste
        # quelle que soit la vue active).
        pw = QtWidgets.QWidget()
        pv = QtWidgets.QVBoxLayout(pw)
        pv.setContentsMargins(8, 8, 8, 8)
        pv.setSpacing(8)

        pv.addWidget(self._param_section_hdr("Vue / source sélectionnée"))

        # --- Section : Outils de capture / rognage (cropping tools) ---
        grp_crop = QtWidgets.QGroupBox("Outils de capture / rognage")
        cropl = QtWidgets.QVBoxLayout(grp_crop)
        row_cap = QtWidgets.QHBoxLayout()
        row_cap.setSpacing(6)
        row_cap.addWidget(self.capture_btn)      # appareil photo
        row_cap.addWidget(self.son_btn)          # son, separe mais meme ligne
        row_cap.addStretch(1)
        cropl.addLayout(row_cap)
        _cap_hint = QtWidgets.QLabel(
            "Appareil photo : le format et les options s'ouvrent dans une "
            "fenêtre, puis choix du dossier d'enregistrement.")
        _cap_hint.setWordWrap(True)
        _cap_hint.setStyleSheet("color:#888; font-size:11px;")
        cropl.addWidget(_cap_hint)
        row_roi = QtWidgets.QHBoxLayout()
        row_roi.addWidget(self.roi_export_btn)
        row_roi.addStretch(1)
        cropl.addLayout(row_roi)
        pv.addWidget(grp_crop)

        # --- Section : Extraction IN / OUT (+ morceaux) ---
        grp_ext = QtWidgets.QGroupBox("Extraction IN / OUT")
        grp_ext.setObjectName("extraction_in_out_group")
        self.extraction_group = grp_ext
        extl = QtWidgets.QVBoxLayout(grp_ext)
        row_ext = QtWidgets.QHBoxLayout()
        row_ext.setSpacing(4)
        row_ext.addWidget(self.extract_in_btn)
        row_ext.addWidget(self.extract_out_btn)
        row_ext.addWidget(self.extract_lbl)
        row_ext.addWidget(self.extract_clip_btn)
        row_ext.addStretch(1)
        extl.addLayout(row_ext)
        _seg_hdr = QtWidgets.QLabel("Découpe par morceaux")
        _seg_hdr.setStyleSheet("color:#3fd07a; font-size:11px; font-weight:bold;")
        extl.addWidget(_seg_hdr)
        row_seg = QtWidgets.QHBoxLayout()
        row_seg.setSpacing(4)
        row_seg.addWidget(self.seg_add_btn)
        self.seg_count_lbl = QtWidgets.QLabel("aucun morceau")
        self.seg_count_lbl.setStyleSheet("color:#888; font-size:11px;")
        row_seg.addWidget(self.seg_count_lbl)
        row_seg.addStretch(1)
        extl.addLayout(row_seg)
        self.seg_list = QtWidgets.QListWidget()
        self.seg_list.setObjectName("segments_list")
        self.seg_list.setMaximumHeight(96)
        self.seg_list.setToolTip("Morceaux définis (surlignés en vert sur la timeline).")
        extl.addWidget(self.seg_list)
        row_seg2 = QtWidgets.QHBoxLayout()
        row_seg2.setSpacing(4)
        _seg_rm = QtWidgets.QPushButton("Retirer")
        _seg_rm.clicked.connect(self._remove_segment)
        _seg_cl = QtWidgets.QPushButton("Vider")
        _seg_cl.clicked.connect(self._clear_segments)
        row_seg2.addWidget(_seg_rm)
        row_seg2.addWidget(_seg_cl)
        row_seg2.addWidget(self.split_extract_btn)
        row_seg2.addStretch(1)
        extl.addLayout(row_seg2)
        pv.addWidget(grp_ext)
        self.split_extract_btn.setEnabled(False)

        # --- Section : Enregistrement de clics (rec clic tools) ---
        grp_clics = QtWidgets.QGroupBox("Enregistrement de clics")
        grp_clics.setObjectName("click_recording_group")
        self.clicks_group = grp_clics
        cl2 = QtWidgets.QHBoxLayout(grp_clics)
        cl2.setSpacing(4)
        cl2.addWidget(self.rec_btn)
        cl2.addWidget(self.undo_btn)
        cl2.addWidget(self.clear_btn)
        cl2.addStretch(1)
        pv.addWidget(grp_clics)

        # --- Traitement image : entièrement déplacé dans « Hist / SIDECAR » ---
        # (LUT + filtres, globaux). On garde ici uniquement les QAction internes
        # (état autoritatif des filtres, synchronisé avec les cases Hist/SIDECAR) ;
        # elles ne sont plus affichées dans Paramètres.
        self.filter_menu = QtWidgets.QMenu(self)
        self.act_edges  = QtGui.QAction("Bords", self.filter_menu, checkable=True)
        self.act_sharp  = QtGui.QAction("Nettete", self.filter_menu, checkable=True)
        self.act_invert = QtGui.QAction("Inverser", self.filter_menu, checkable=True)
        self.act_clahe  = QtGui.QAction("CLAHE", self.filter_menu, checkable=True)
        for act in (self.act_edges, self.act_sharp, self.act_invert, self.act_clahe):
            act.triggered.connect(self.refresh_frame)
            self.filter_menu.addAction(act)

        # --- Section : Reticule ---
        grp_cross = QtWidgets.QGroupBox("Réticule")
        cl3 = QtWidgets.QHBoxLayout(grp_cross)
        self.crosshair_chk = QtWidgets.QCheckBox("Afficher le réticule")
        self.crosshair_chk.setToolTip(
            "Trace une croix au centre de l'image.\n"
            "Utile pour l'alignement optique.")
        self.crosshair_chk.toggled.connect(self._on_crosshair_changed)
        cl3.addWidget(self.crosshair_chk)
        cl3.addStretch(1)
        pv.addWidget(grp_cross)

        pv.addWidget(self._param_section_hdr("Global — toutes les vues / application"))

        # (Le format de capture et ses options sont désormais dans le popup de
        #  l'appareil photo, cf. _capture_frame — on gagne de la place ici.)

        # --- Section : Prefetch ---
        grp_pref = QtWidgets.QGroupBox(
            "Préchargement  (séquences images / SPECIALIZED)")
        grp_pref.setToolTip(
            "Charge N frames en avance dans un thread de fond.\n"
            "Réduit les saccades en lecture réseau SSH.\n"
            "Désactivé pour les vidéos MP4 (VideoCapture non thread-safe).")
        pl = QtWidgets.QVBoxLayout(grp_pref)
        pl.setSpacing(6)
        row_pref = QtWidgets.QHBoxLayout()
        row_pref.addWidget(QtWidgets.QLabel("Frames en avance :"))
        self.prefetch_spin = QtWidgets.QSpinBox()
        self.prefetch_spin.setRange(0, 32)
        self.prefetch_spin.setValue(0)
        self.prefetch_spin.setFocusPolicy(Qt.StrongFocus)
        self.prefetch_spin.setToolTip(
            "0 = désactivé.\n"
            "4–8 : réseau rapide / LAN.\n"
            "12–32 : SSH lent (plus de RAM utilisée).")
        self.prefetch_spin.valueChanged.connect(self._on_prefetch_changed)
        row_pref.addWidget(self.prefetch_spin)
        row_pref.addStretch(1)
        pl.addLayout(row_pref)
        self._prefetch_lbl = QtWidgets.QLabel("")
        self._prefetch_lbl.setWordWrap(True)
        self._prefetch_lbl.setStyleSheet("color: #888; font-size: 11px;")
        pl.addWidget(self._prefetch_lbl)
        self._update_prefetch_info(0)
        pv.addWidget(grp_pref)

        # --- Section : Journal des conversions (deplace ici depuis l'ancien
        # onglet "Conversions", dont la place est reprise par l'onglet Plugins) ---
        pv.addWidget(self._param_section_hdr("Conversions"))
        grp_conv = QtWidgets.QGroupBox("Journal des conversions")
        gcv = QtWidgets.QVBoxLayout(grp_conv)
        self.convlog_list = QtWidgets.QListWidget()
        self.convlog_list.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.convlog_list.setFocusPolicy(Qt.NoFocus)
        self.convlog_list.setMaximumHeight(160)
        gcv.addWidget(self.convlog_list)
        _clr = QtWidgets.QPushButton("Effacer les terminées")
        _clr.clicked.connect(self._convlog_clear_done)
        gcv.addWidget(_clr)
        pv.addWidget(grp_conv)

        pv.addStretch(1)
        _scroll = QtWidgets.QScrollArea()
        _scroll.setObjectName("parameters_scroll")
        self.params_scroll = _scroll
        _scroll.setWidget(pw)
        _scroll.setWidgetResizable(True)
        _scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.right_tab.addTab(_scroll, icons.icon("gear"), "Paramètres")

        # ── sync bidirectionnelle filtres ↔ contrôles Hist/SIDECAR (globaux) ──
        for act, chk in (
            (self.act_edges,  self.flt_edges_chk),
            (self.act_sharp,  self.flt_sharp_chk),
            (self.act_invert, self.flt_invert_chk),
            (self.act_clahe,  self.flt_clahe_chk),
        ):
            act.toggled.connect(chk.setChecked)
            chk.toggled.connect(act.setChecked)
            # `triggered` ne part que sur clic menu : il faut re-rendre aussi
            # quand la case Hist/SIDECAR change l'action par code.
            chk.toggled.connect(self.refresh_frame)
        # ── sync LUT (bas de barre) ↔ LUT Hist/SIDECAR ──
        self.lut_combo.currentIndexChanged.connect(
            self.lut_hist_combo.setCurrentIndex)
        self.lut_hist_combo.currentIndexChanged.connect(
            self.lut_combo.setCurrentIndex)

    def _on_fetch_res_changed(self, _=None):
        scale = self.fetch_res_combo.currentData()
        self._fetch_scale = float(scale) if scale else 1.0
        self._update_fetch_res_info(self._fetch_scale)
        # appliquer la résolution à TOUTES les vues (satellites compris)
        if hasattr(self, "_split_canvas"):
            self._split_canvas.set_fetch_scale(self._fetch_scale)
        if self.source is not None and self._raw is not None:
            self.show_index(self.cur)

    def _update_fetch_res_info(self, scale):
        msgs = {
            1.0:   "Pleine résolution — qualité maximale",
            0.5:   "Moitié (4× moins) — réseau rapide",
            0.25:  "Quart (16× moins) — SSH lent, qualité réduite",
            0.125: "Huitième (64× moins) — SSH très lent",
        }
        msg = msgs.get(scale, str(scale))
        self.fetch_res_combo.setToolTip(f"Résolution de lecture\n{msg}")

    def _update_sidecar_logs(self, graphs):
        """Affiche, pour la frame COURANTE, le numero de frame + les libelles
        (les memes <texte> que ceux dessines sur l'image)."""
        self.sidecar_logs.clear()
        if not graphs:
            if self.overlays and self.overlay_chk.isChecked():
                self.sidecar_logs.addItem(f"Frame {self.cur} : (pas d'overlay)")
            return
        labels = [g["text"]["label"] for g in graphs
                  if g.get("text") and g["text"].get("label")]
        if labels:
            for lab in labels:
                self.sidecar_logs.addItem(f"Frame {self.cur}  -  {lab}")
        else:
            self.sidecar_logs.addItem(f"Frame {self.cur} : (aucun libelle)")

    def _filter_flags(self):
        return {
            "edges":   self.act_edges.isChecked(),
            "sharpen": self.act_sharp.isChecked(),
            "invert":  self.act_invert.isChecked(),
            "clahe":   self.act_clahe.isChecked(),
        }

    # ----- historique de sources -----
    def _add_to_history(self, src):
        kind, arg = self._source_spec_from(src)
        if kind is None:
            return
        # Retire l'entrée si elle existe déjà ailleurs dans la liste (évite les doublons)
        if any(k == kind and a == arg for _, k, a, *_r in self._history):
            return
        bit_label, is_color = self._probe_src_badge(src)
        self._history.insert(0, (src.name, kind, arg, bit_label, is_color))
        self._history = self._history[:30]
        self._update_history_list()

    def _probe_src_badge(self, src):
        """(bit_label, is_color) d'apres la 1ere frame de src, pour le badge
        [8b]/[16b] + pastille gris/couleur de l'historique.

        Une VideoSource (cv2.VideoCapture) renvoie TOUJOURS un BGR 3 canaux,
        même pour une vidéo au contenu réellement gris (canaux quasi-égaux) :
        se fier à `ndim`/`shape[2]` seul classait donc à tort ces vidéos en
        « couleur ». On compare les canaux entre eux (moyenne des écarts
        absolus) : proches -> gris, nettement différents -> couleur."""
        try:
            f0 = src.get(0) if src.count > 0 else None
        except Exception:
            f0 = None
        if f0 is None:
            return ("8b", False)
        dt = f0.dtype
        bits = np.iinfo(dt).bits if np.issubdtype(dt, np.integer) else 32
        bit_label = "16b" if bits > 8 else "8b"
        is_color = False
        if f0.ndim == 3 and f0.shape[2] >= 3:
            c0 = f0[:, :, 0].astype(np.int32)
            c1 = f0[:, :, 1].astype(np.int32)
            c2 = f0[:, :, 2].astype(np.int32)
            max_diff = max(np.abs(c0 - c1).mean(),
                           np.abs(c1 - c2).mean(),
                           np.abs(c0 - c2).mean())
            # seuil tolerant : le bruit de compression (H.264/mp4v) sur une
            # video reellement grise induit deja des ecarts inter-canaux de
            # ~2-3 (mesure sur cas reel) -> 8.0 les absorbe sans confondre
            # avec une scene couleur (ecarts typiquement >>10).
            is_color = max_diff > 8.0
        return (bit_label, is_color)

    def _source_spec_from(self, src):
        if isinstance(src, SpecializedSource):
            return ("specialized", src.path)
        if isinstance(src, VideoSource):
            return ("video", src.path)
        if isinstance(src, ImageSequenceSource):
            return ("images", list(src.paths))
        return (None, None)

    @staticmethod
    def _hist_row_text(name, kind, bit_label, star=""):
        icons = {"specialized": "[SPECIALIZED]", "video": "[VID]", "images": "[IMG]"}
        return f"{star}{icons.get(kind, '[?]')} [{bit_label}]  {name}"

    def _update_history_list(self):
        self.hist_list.clear()
        for name, kind, _arg, bit_label, is_color in self._history:
            item = QtWidgets.QListWidgetItem()
            row = _HistRow(self._hist_row_text(name, kind, bit_label), is_color)
            item.setSizeHint(row.sizeHint())
            self.hist_list.addItem(item)
            self.hist_list.setItemWidget(item, row)
        self._highlight_history()

    def _highlight_history(self):
        cur_kind, cur_arg = (self._source_spec_from(self.source)
                             if self.source else (None, None))
        for i, (name, kind, arg, bit_label, _is_color) in enumerate(self._history):
            item = self.hist_list.item(i)
            row = self.hist_list.itemWidget(item) if item is not None else None
            if row is None:
                continue
            is_active = (kind == cur_kind and arg == cur_arg)
            star = "★ " if is_active else "  "
            row.label.setText(self._hist_row_text(name, kind, bit_label, star))
            row.setStyleSheet(
                "background: rgba(220,60,60,60);" if is_active else "background: transparent;")

    def _history_path_at(self, row):
        """Chemin déposable pour l'entrée d'historique (pour le drag vers un bloc)."""
        if row < 0 or row >= len(self._history):
            return None
        _name, kind, arg, *_rest = self._history[row]
        if kind in ("specialized", "video"):
            return arg
        if kind == "images" and arg:
            folder = os.path.dirname(arg[0])
            return folder if folder else arg[0]
        return None

    def _load_from_history_item(self, item):
        idx = self.hist_list.row(item)
        if idx < 0 or idx >= len(self._history):
            return
        _name, kind, arg, *_rest = self._history[idx]
        if kind == "specialized":
            self.open_paths([arg])
        elif kind == "video":
            self.open_video(arg)
        elif kind == "images":
            folder = os.path.dirname(arg[0]) if arg else None
            if folder:
                self.open_folder(folder)

    def _delete_history_selected(self):
        """Suppr/Retour arriere (Maj+Suppr y compris) sur hist_list : retire
        les entrees selectionnees de self._history. N'affecte pas la source
        actuellement ouverte (juste le raccourci dans l'historique)."""
        rows = sorted({i.row() for i in self.hist_list.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for row in rows:
            if 0 <= row < len(self._history):
                del self._history[row]
        self._update_history_list()

    # ----- helpers UI -----
    def _btn(self, text, slot, checkable=False):
        b = QtWidgets.QPushButton(text)
        b.setFocusPolicy(Qt.NoFocus)
        b.setCheckable(checkable)
        if slot is not None:
            b.clicked.connect(slot)
        return b

    def _apply_fetch_scale(self, frame):
        """Reduit la resolution du frame selon self._fetch_scale (optim reseau)."""
        if frame is None or self._fetch_scale >= 1.0:
            return frame
        s = self._fetch_scale
        h, w = frame.shape[:2]
        nh = max(1, int(round(h * s)))
        nw = max(1, int(round(w * s)))
        return cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)

    # ──── extraction clip ────────────────────────────────────────
    def _set_extract_in(self):
        self._extract_start = self.cur
        self._update_extract_lbl()

    def _set_extract_out(self):
        self._extract_end = self.cur
        self._update_extract_lbl()

    def _update_extract_lbl(self):
        s, e = self._extract_start, self._extract_end
        if s < 0 and e < 0:
            txt = "\u2500"
        elif s >= 0 and e < 0:
            txt = f"{s}\u2192 ?"
        elif s < 0 and e >= 0:
            txt = f"? \u2192{e}"
        else:
            n = max(0, e - s + 1)
            txt = f"{s}\u2192{e} ({n}f)"
        self.extract_lbl.setText(txt)
        self.slider.set_extract_zone(s, e)
        # zone rouge aussi sur la mini-barre de la vue active (multivue)
        if hasattr(self, "_primary_frame"):
            self._primary_frame.mini.set_extract_zone(s, e)
        name = self.source.name if self.source else "?"
        self.extract_lbl.setToolTip(
            f"Zone IN→OUT de la vue sélectionnée : « {name} »\n"
            "L'extraction (bouton « Extraire… ») porte sur cette vue ;\n"
            "le chemin de sortie est demandé au moment d'extraire.")

    def _do_extract_clip(self):
        if self.source is None:
            return
        s, e = self._extract_start, self._extract_end
        if s < 0 or e < 0 or e <= s:
            QtWidgets.QMessageBox.information(
                self, "Extraction",
                "D\xe9finissez d'abord :\n"
                "  [  IN  \u2192 marque la frame de d\xe9but\n"
                "  OUT ]  \u2192 marque la frame de fin\n\n"
                "Astuce : activer la boucle (\u21ba) pour pr\xe9visualiser la zone.")
            return
        dlg = ClipExtractDialog(self, s, e, crop_rect=self._current_crop_rect())
        dlg.exec()

    # ──── ROI d'export (crop vert) ───────────────────────────────
    def _current_crop_rect(self):
        """ROI de crop a appliquer a l'export, ou None."""
        if self._export_use_roi and self._roi is not None:
            return tuple(int(v) for v in self._roi)
        return None

    def _toggle_roi_export(self, on):
        self._export_use_roi = bool(on)
        self.view.set_roi_export_mode(on)
        if on:
            # bascule sur l'outil ROI pour permettre de tracer la sous-zone
            if hasattr(self, "tools_panel") and not self.tools_panel.is_roi_active():
                self.tools_panel._set_tool("roi")
            self.view.set_tool_mode("roi")
            if self._roi is None:
                self.status.showMessage(
                    "Trace un rectangle vert dans la vue : seule cette zone sera exportée.",
                    5000)
        self._update_extract_lbl()

    # ──── splitting : morceaux multiples ─────────────────────────
    def _add_segment(self):
        s, e = self._extract_start, self._extract_end
        if s < 0 or e < 0 or e <= s:
            QtWidgets.QMessageBox.information(
                self, "Ajouter un morceau",
                "Définis d'abord IN puis OUT (OUT > IN) avant d'ajouter un morceau.")
            return
        self._segments.append((int(s), int(e)))
        # reinitialise IN/OUT pour enchainer le morceau suivant
        self._extract_start = -1
        self._extract_end = -1
        self._update_extract_lbl()
        self._refresh_segments()

    def _remove_segment(self):
        if not hasattr(self, "seg_list"):
            return
        row = self.seg_list.currentRow()
        if 0 <= row < len(self._segments):
            del self._segments[row]
            self._refresh_segments()

    def _clear_segments(self):
        self._segments = []
        self._refresh_segments()

    def _refresh_segments(self):
        if hasattr(self, "seg_list"):
            self.seg_list.clear()
            for i, (s, e) in enumerate(self._segments, 1):
                self.seg_list.addItem(f"#{i}   {s} → {e}   ({e - s + 1} f)")
        if hasattr(self, "seg_count_lbl"):
            n = len(self._segments)
            self.seg_count_lbl.setText(f"{n} morceau(x)" if n else "aucun morceau")
        if hasattr(self, "split_extract_btn"):
            self.split_extract_btn.setEnabled(bool(self._segments))
        # surbrillance verte sur la timeline (slider + mini-barre)
        if hasattr(self, "slider"):
            self.slider.set_split_segments(self._segments)
        if hasattr(self, "_primary_frame"):
            self._primary_frame.mini.set_split_segments(self._segments)

    def _open_split_extract(self):
        if self.source is None or not self._segments:
            QtWidgets.QMessageBox.information(
                self, "Extraire les morceaux",
                "Ajoute d'abord au moins un morceau (IN→OUT puis « Ajouter morceau »).")
            return
        dlg = SplitExtractDialog(self, self._segments, self._current_crop_rect())
        dlg.exec()

    # ──── reticule ───────────────────────────────────────────────
    def _on_crosshair_changed(self, on):
        self._crosshair = bool(on)
        if self._raw is not None:
            self._display()

    # ──── prefetch ───────────────────────────────────────────────
    def _on_prefetch_changed(self, n):
        self._prefetch_n = int(n)
        self._prefetch_cache.clear()
        self._stop_prefetch()
        self._update_prefetch_info(n)

    def _update_prefetch_info(self, n):
        if n == 0:
            msg = "D\xe9sactiv\xe9. Lecture directe depuis la source."
            col = "#888"
        elif n <= 4:
            msg = f"{n} frames pr\xe9charg\xe9es. R\xe9seau rapide / LAN."
            col = "#50c060"
        elif n <= 12:
            msg = f"{n} frames pr\xe9charg\xe9es. Recommand\xe9 SSH lent."
            col = "#e0a020"
        else:
            msg = f"{n} frames pr\xe9charg\xe9es. SSH tr\xe8s lent (RAM importante)."
            col = "#e07030"
        self._prefetch_lbl.setText(msg)
        self._prefetch_lbl.setStyleSheet(f"color: {col}; font-size: 11px;")

    def _prefetch_schedule(self, base_idx):
        n = self._prefetch_n
        if n <= 0 or self.source is None:
            return
        if hasattr(self.source, 'path') and not hasattr(self.source, 'paths'):
            return
        total = self.source.count
        indices = [i for i in range(base_idx + 1, min(base_idx + n + 1, total))
                   if i not in self._prefetch_cache]
        if not indices:
            return
        self._stop_prefetch()
        t = _PrefetchThread(self.source, indices, self._fetch_scale)
        t.done.connect(self._on_prefetch_done)
        self._prefetch_thread = t
        t.start()

    def _on_prefetch_done(self, cache):
        self._prefetch_cache.update(cache)
        max_cached = max(self._prefetch_n * 3, 8)
        while len(self._prefetch_cache) > max_cached:
            del self._prefetch_cache[min(self._prefetch_cache)]
        self._prefetch_thread = None

    def _stop_prefetch(self):
        if self._prefetch_thread is not None:
            self._prefetch_thread.stop()
            self._prefetch_thread = None

    def _apply_rotation(self, frame):
        if frame is None or self._rotation == 0:
            return frame
        codes = {90: cv2.ROTATE_90_CLOCKWISE,
                 180: cv2.ROTATE_180,
                 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
        code = codes.get(self._rotation)
        return cv2.rotate(frame, code) if code is not None else frame

    def _rotate_90(self):
        self._rotation = (self._rotation + 90) % 360
        lbl = f" {self._rotation}°" if self._rotation else ""
        self.rot_btn.setText(f"↻ Rot.{lbl}")
        self.refresh_frame()

    def _toggle_vid_rec(self, checked=False):
        if checked:
            if self.source is None or self._raw is None:
                self.vid_rec_btn.blockSignals(True)
                self.vid_rec_btn.setChecked(False)
                self.vid_rec_btn.blockSignals(False)
                return
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Enregistrer la lecture vidéo...",
                "capture.mp4", "Video MP4 (*.mp4)")
            if not path:
                self.vid_rec_btn.blockSignals(True)
                self.vid_rec_btn.setChecked(False)
                self.vid_rec_btn.blockSignals(False)
                return
            out = self.process(self._apply_rotation(self._raw))
            if out is None:
                self.vid_rec_btn.blockSignals(True)
                self.vid_rec_btn.setChecked(False)
                self.vid_rec_btn.blockSignals(False)
                return
            h, w = out.shape[:2]
            fps = self.fps_spin.value() or 25.0
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            self._vid_recorder = cv2.VideoWriter(path, fourcc, fps, (w, h))
            if not self._vid_recorder.isOpened():
                self._vid_recorder = None
                QtWidgets.QMessageBox.warning(
                    self, "Erreur", "Impossible d'ouvrir le fichier vidéo.")
                self.vid_rec_btn.blockSignals(True)
                self.vid_rec_btn.setChecked(False)
                self.vid_rec_btn.blockSignals(False)
                return
            self._vid_rec_path = path
            self.vid_rec_btn.setText("⏹ Stop")
            self.status.showMessage(f"Enregistrement → {path}", 0)
        else:
            self._stop_vid_rec()

    def _stop_vid_rec(self):
        if self._vid_recorder is not None:
            self._vid_recorder.release()
            self._vid_recorder = None
            if hasattr(self, "status"):
                self.status.showMessage(
                    f"Vidéo enregistrée → {self._vid_rec_path}", 5000)
        if hasattr(self, "vid_rec_btn"):
            self.vid_rec_btn.blockSignals(True)
            self.vid_rec_btn.setChecked(False)
            self.vid_rec_btn.blockSignals(False)
            self.vid_rec_btn.setText("⏺ Vidéo")

    def _capture_frame(self):
        if self._raw is None:
            return
        fmt, with_overlays = self._ask_capture_options()
        if fmt is None:
            return   # annule
        raw_rot = self._apply_rotation(self._raw)
        if fmt == "tiff16":
            data = raw_rot
            default = f"frame_{self.cur:05d}.tiff"
            filt = "TIFF (*.tiff *.tif)"
        else:
            data = (self._export_frame_as_viewed(self.cur) if with_overlays
                    else self.process(raw_rot))
            if data is None:
                return
            default = f"frame_{self.cur:05d}.png"
            filt = "PNG (*.png);;TIFF (*.tiff *.tif)"
        # crop ROI d'export (sous-zone verte) si actif
        crop = self._current_crop_rect()
        if crop is not None and data is not None:
            H, W = data.shape[:2]
            x, y, w, h = crop
            x = max(0, min(int(x), W - 1)); y = max(0, min(int(y), H - 1))
            x2 = min(W, x + int(w)); y2 = min(H, y + int(h))
            if x2 > x and y2 > y:
                data = data[y:y2, x:x2]
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Enregistrer la capture...", default, filt)
        if not path:
            return
        ok = cv2.imwrite(path, data)
        if not ok:
            QtWidgets.QMessageBox.warning(self, "Erreur", "Impossible d'ecrire l'image.")
        else:
            self.status.showMessage(f"Frame sauvegardée → {path}", 3000)

    def _ask_capture_options(self):
        """Popup de l'appareil photo : choix du format + options. Renvoie
        (fmt, with_overlays) ou (None, None) si annulé. La sélection du dossier
        se fait ensuite (fenêtre d'enregistrement), pour gagner de la place."""
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Capture — options")
        dlg.setMinimumWidth(360)
        v = QtWidgets.QVBoxLayout(dlg)
        v.addWidget(QtWidgets.QLabel("Format de capture :"))
        combo = QtWidgets.QComboBox()
        combo.addItem("PNG 8 bits  (affiché tel quel)", "png")
        combo.addItem("TIFF 16 bits  (données brutes, sans LUT)", "tiff16")
        idx = combo.findData(getattr(self, "_capture_fmt", "png"))
        if idx >= 0:
            combo.setCurrentIndex(idx)
        v.addWidget(combo)
        chk = QtWidgets.QCheckBox("Avec calques (boîtes .ver + overlays SIDECAR)")
        chk.setChecked(bool(getattr(self, "_capture_overlays", True)))
        v.addWidget(chk)
        hint = QtWidgets.QLabel("")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888; font-size:11px;")
        v.addWidget(hint)

        def _upd():
            if combo.currentData() == "tiff16":
                hint.setText("Données brutes 16 bits (LUT/filtres/calques ignorés).")
                chk.setEnabled(False)
            else:
                hint.setText("Image affichée (LUT + filtres). Les calques sont gravés si coché.")
                chk.setEnabled(True)
        combo.currentIndexChanged.connect(_upd)
        _upd()

        row = QtWidgets.QHBoxLayout()
        ok_btn = QtWidgets.QPushButton("Enregistrer...")
        ok_btn.setIcon(icons.icon("camera"))
        ok_btn.clicked.connect(dlg.accept)
        cancel_btn = QtWidgets.QPushButton("Annuler")
        cancel_btn.clicked.connect(dlg.reject)
        row.addWidget(ok_btn)
        row.addStretch(1)
        row.addWidget(cancel_btn)
        v.addLayout(row)

        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return None, None
        self._capture_fmt = combo.currentData() or "png"
        self._capture_overlays = chk.isChecked()
        return self._capture_fmt, self._capture_overlays

    def _toggle_view_only(self):
        self._view_only = not self._view_only
        on = self._view_only
        if on:
            self._dock_vis_save = {
                d: d.isVisible()
                for d in self.findChildren(QtWidgets.QDockWidget)}
            for d in self._dock_vis_save:
                d.hide()
            for tb in self.findChildren(QtWidgets.QToolBar):
                tb.hide()
            self._ctrl_widget.hide()
            self.statusBar().hide()
            self._fs_exit_btn.show()
            self._reposition_fs_btn()
        else:
            self._fs_exit_btn.hide()
            for d, vis in self._dock_vis_save.items():
                d.setVisible(vis)
            for tb in self.findChildren(QtWidgets.QToolBar):
                tb.show()
            self._ctrl_widget.show()
            self.statusBar().show()
            self._update_right_btns(
                self.right_tab.currentIndex()
                if self.right_dock.isVisible() else -1)

    def _exit_fullscreen(self):
        if self._view_only:
            self._toggle_view_only()

    def _reposition_fs_btn(self):
        b = self._fs_exit_btn
        b.adjustSize()
        b.move(self.width() - b.width() - 18, self.height() - b.height() - 18)
        b.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "_view_only", False) and hasattr(self, "_fs_exit_btn"):
            self._reposition_fs_btn()

    def _toggle_audio(self):
        if self._audio_out is None:
            self.son_btn.setChecked(False)
            return
        enabled = self.son_btn.isChecked()
        self._audio_out.setMuted(not enabled)
        self.son_btn.setIcon(icons.icon("speaker" if enabled else "speaker_muted"))
        if enabled and self.timer.isActive():
            fps_src = getattr(self.source, "fps", self.fps_spin.value() or 25.0)
            self._audio_player.setPosition(
                int(self.cur * 1000.0 / max(fps_src, 0.001)))
            self._audio_player.play()
        elif not enabled:
            self._audio_player.pause()

    def _show_aide(self):
        import html as _html

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Aide — FrameViewer")
        dlg.setStyleSheet("QDialog{background:#101013;}")

        browser = QtWidgets.QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setStyleSheet(
            "QTextBrowser{background:#15151a; border:1px solid #2a2a32; "
            "border-radius:6px; padding:4px;}")

        def sec(title, color, rows, icon=""):
            """Un bloc titre coloré + tableau raccourci/description."""
            head = (f'<div style="margin:14px 0 6px 0;padding:4px 10px;'
                     f'border-left:4px solid {color};background:{color}22;'
                     f'border-radius:3px;">'
                     f'<span style="color:{color};font-size:14px;font-weight:bold;">'
                     f'{icon} {title}</span></div>')
            body = ('<table cellspacing="0" cellpadding="3" width="100%">')
            for k, v in rows:
                body += (f'<tr><td style="color:{color};font-family:Consolas,monospace;'
                         f'white-space:nowrap;padding-right:14px;vertical-align:top;">'
                         f'<b>{k}</b></td>'
                         f'<td style="color:#dddde3;">{v}</td></tr>')
            body += "</table>"
            return head + body

        native_media = sorted(IMAGE_EXTS | VIDEO_EXTS | {".yuv"})
        optional_media = sorted(SEQUENCE_FORMAT_EXTS)
        media_text = ", ".join(native_media)
        if optional_media:
            media_text += "; formats optionnels actifs : " + ", ".join(optional_media)
        media_text = _html.escape(media_text)

        html = """
        <div style="color:#e8e8ee; font-family:Segoe UI, Arial, sans-serif; font-size:12px;">
        <h2 style="color:#5aafff; margin-bottom:2px;">FrameViewer — Aide complète</h2>
        <p style="color:#9a9aa5;">Souris, clavier, outils, calques, multivue et export —
        tout ce que fait l'application.</p>
        """

        html += sec("Souris — vue / canvas", "#5aafff", [
            ("Clic gauche (vue inactive)", "Active cette vue (outils TI + histogramme s'y rattachent)."),
            ("Clic gauche + glisser (outil ROI)", "Trace un rectangle de sélection (ROI)."),
            ("Clic gauche simple (outil ROI)", "Sur une boîte .ver : démarre le <b>suivi</b> automatique de "
                "cette boîte. Sur une autre boîte : change de suivi. Dans le vide : annule le suivi."),
            ("Clic gauche simple (REC clics actif)", "Enregistre un clic (frame, x, y) dans le fichier de clics."),
            ("Clic gauche + glisser (outil ligne)", "Trace un profil de ligne."),
            ("Clic gauche (outil règle)", "Ajoute un point à la polyligne de mesure."),
            ("Clic droit (outil règle)", "Réinitialise la polyligne de mesure."),
            ("Clic droit + glisser (bandeau [n])", "Échange le contenu de deux vues."),
            ("Double-clic (vue)", "Plein écran de cette vue / retour à la disposition normale."),
            ("Molette", "Zoom avant/arrière, centré sur le curseur."),
            ("Clic molette + glisser", "Panoramique dans une vue zoomée."),
            ("Clic molette (sans glisser)", "Réajuste la vue (fit, zoom 1×)."),
            ("Glisser un fichier / dossier sur une vue", "Ouvre cette séquence dans cette vue "
                "(un dossier de liens symboliques vers des images est accepté, même sans extension)."),
            ("Glisser un .sidecar / .ver sur une vue", "Ajoute ce calque à cette vue (l'active si besoin)."),
            ("Glisser le curseur de frame d'une vue inactive", "Cette vue devient active dès le relâchement."),
        ], "🖱️")

        html += sec("Outils TI (panneau accordéon)", "#7fd858", [
            ("ROI — sélection / mesures", "Zoom du crop + histogramme + statistiques (min/max/moyenne/σ/"
                "médiane). Bouton <b>Convertir…</b> : exporte le crop (fixe ou suivi .ver) sur toute la "
                "séquence en MP4/PNG/SPECIALIZED, avec padding réglable et heatmap d'histogrammes en option."),
            ("FFT 2D", "Spectre de Fourier 2D (magnitude, log) de la ROI courante — texture/périodicité."),
            ("Profil de ligne", "Trace le profil d'intensité le long d'une ligne ; export CSV."),
            ("Règle", "Distance et angle entre points cliqués (polyligne)."),
            ("Soustraction temporelle", "Affiche |frame N − frame N−k| pour révéler mouvements/variations."),
        ], "🧰")

        html += sec("Calques — SIDECAR / .ver", "#ff9a3d", [
            ("Case à cocher d'un calque", "Affiche / masque ce calque (overlay SIDECAR ou boîtes .ver d'un track)."),
            ("Tout / Aucun", "Coche / décoche tous les calques de la vue active d'un coup."),
            ("Suppr.", "Supprime les calques sélectionnés dans la liste (multi-sélection, touche Suppr)."),
            ("Vider calques", "Supprime TOUS les calques (SIDECAR + .ver) de la vue active, en un clic."),
            ("« Afficher les overlays »", "Interrupteur général : masque tout, même si des calques sont cochés."),
        ], "📋")

        html += sec("Multivue / Fusion", "#c98bff", [
            ("Bandeau [1] [2] …", "Sélectionne/active la vue correspondante."),
            ("⇄ (haut-droit de chaque vue)", "Convertit cette vue (menu de formats, calques/contraste inclus)."),
            ("🔗 Lier", "Ouvre le réglage des frames de départ puis synchronise frame, zoom et "
                "pan. Les écarts sont conservés au scrub, au pas-à-pas et en lecture. La plage commune "
                "empêche une vue d'arriver silencieusement en fin de source."),
            ("Mode Fusion", "Glisser une 2ᵉ source en haut-droite ; régler α ou le mode de mélange "
                "(alpha, addition pondérée, différence, damier) ; ⤓ Extraire récupère le rendu fusionné."),
            ("Mode Temporel (i-N | i)", "Même flux affiché à deux instants : la frame <b>i-N</b> à gauche "
                "(vue figée « Past Frame », bandeau vert clair) et la frame courante <b>i</b> à droite "
                "(vue moteur, bord vert). Réglez <b>N</b> dans la barre d'outils. Idéal pour comparer une "
                "frame à son passé (mouvement, dérive)."),
            ("Inter-connexion inter-vues", "Automatique dès qu'un plugin la fournit (aucun bouton) : ronds "
                "+ traits colorés reliant des points appariés entre deux vues (ex. keypoints i-N ↔ i, ou "
                "matching SIFT seq1 ↔ seq2). Voir les plugins <i>demo_2_multivue_kpts</i> / "
                "<i>demo_4_multivue_kpts</i>."),
            ("Convertir (toutes les vues)", "En multivue : assemble un MP4 de la disposition actuelle "
                "(grille ou fusion), calques et contraste inclus."),
        ], "🪟")

        html += sec("Formats média détectés", "#64d8cb", [
            ("Disponibles dans cette installation", media_text),
            ("Dossier d'images", "Les fichiers sont triés dans l'ordre naturel, sans parcours récursif."),
            ("Formats optionnels", "Ils n'apparaissent ici que si leur adaptateur backend est réellement chargé."),
        ], "📁")

        html += sec("Annotations YOLO / .ver", "#ff9a3d", [
            ("Association prioritaire", "Même nom de base : <b>images/frame_05000.png</b> ↔ "
                "<b>labels/frame_05000.txt</b>, sans tenir compte de la casse."),
            ("Repli", "Si aucun nom ne correspond, association par ordre naturel. Les labels sans image "
                "correspondante sont signalés dans la barre de statut."),
            ("classes.txt", "Optionnel, adjacent au dossier de labels, une classe par ligne. Dans la "
                "démonstration, la classe 0 se nomme <b>vehicle</b>."),
        ], "▣")

        html += """
        <div style="margin:8px 0;color:#dddde3;">
        <b style="color:#ff9a3d;">Dossier YOLO</b>
        <pre style="background:#0d0d11;border:1px solid #33333c;padding:8px;">
images/frame_05000.png
labels/frame_05000.txt

# class_id cx_norm cy_norm width_norm height_norm
0 0.201748 0.609994 0.123153 0.160014</pre>
        <b style="color:#ff9a3d;">YOLO fusionné, frames 0-based</b>
        <pre style="background:#0d0d11;border:1px solid #33333c;padding:8px;">
# frame_index class_id cx_norm cy_norm width_norm height_norm
0 0 0.201748 0.609994 0.123153 0.160014</pre>
        <b style="color:#ff9a3d;">.ver court, frames 1-based</b>
        <pre style="background:#0d0d11;border:1px solid #33333c;padding:8px;">
# frame visibility x1 y1 x2 y2
1 1 100 80 180 150</pre>
        <b style="color:#ff9a3d;">.ver long</b>
        <pre style="background:#0d0d11;border:1px solid #33333c;padding:8px;">
# frame visibility x1 y1 x2 y2 track_id class subclass...
1 1 100 80 180 150 42 vehicle moving</pre>
        </div>
        """

        html += sec("Export / Conversion", "#ff6b6b", [
            ("Extraire… (IN → OUT)", "Formats MP4 (cv2/ffmpeg), dossier PNG, dossier TIFF, SPECIALIZED. Case "
                "« tel qu'affiché » : grave calques + contraste dans l'export."),
            ("Convertir (bouton par vue / barre du bas)", "Même export, sur toute la séquence de cette source."),
            ("📷 Capture", "Capture la frame courante en PNG (avec ou sans calques, au choix)."),
        ], "🎬")

        html += sec("Clavier", "#ffd166", [
            ("← / →", "Frame précédente / suivante."),
            ("Espace", "Lecture / pause."),
            ("Origine (Home) / Fin (End)", "Première / dernière frame."),
            ("Chiffres puis Entrée", "Aller directement à la frame N."),
            ("+ / -", "Zoom avant / arrière."),
            ("Ctrl+O", "Ouvrir un fichier."),
            ("Ctrl+Maj+O", "Ouvrir un dossier."),
            ("Ctrl+S", "Sauvegarder les clics enregistrés."),
            ("Ctrl+Z", "Annuler le dernier clic."),
            ("Suppr", "Effacer tous les clics enregistrés."),
            ("Ctrl+R", "Rotation 90° horaire."),
            ("Ctrl+E", "Capturer la frame courante en PNG."),
            ("Ctrl+C", "Copier la frame dans le presse-papiers."),
            ("F11", "Vue plein cadre (masque toute l'interface)."),
            ("Échap", "Quitter le plein cadre."),
        ], "⌨️")

        html += "</div>"
        browser.setHtml(html)

        lay = QtWidgets.QVBoxLayout(dlg)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(browser)
        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        btns.rejected.connect(dlg.reject)
        btns.button(QtWidgets.QDialogButtonBox.Close).clicked.connect(dlg.accept)
        lay.addWidget(btns)
        dlg.resize(640, 620)
        dlg.exec()

    def _shortcuts(self):
        # Navigation (fleches), zoom (+/-) et saisie de frame sont geres dans
        # keyPressEvent pour fiabilite (les QShortcut sur fleches etaient captes
        # par les champs de saisie). Ici: uniquement les combinaisons Ctrl.
        def sc(seq, fn):
            QtGui.QShortcut(QtGui.QKeySequence(seq), self, activated=fn)
        sc("Ctrl+O", self.open_file_dialog)
        sc("Ctrl+Shift+O", self.open_dir_dialog)
        sc("Ctrl+S", self.save_clicks_as)
        sc("Ctrl+Z", self.undo_click)
        sc("Ctrl+R", self._rotate_90)
        sc("Ctrl+E", self._capture_frame)
        sc("Ctrl+C", self._copy_to_clipboard)

    def keyPressEvent(self, e):
        k = e.key()
        # Gauche/Droite/Espace sont routes via _kbd_target (voir _set_kbd_target) :
        # si la cible courante est une vue satellite avec sa propre source, ces
        # touches doivent marcher meme quand le moteur (vue principale) est vide.
        tgt = self._kbd_target
        sat_tgt = tgt if tgt not in ("engine", "all") else None
        if self.source is None:
            if (sat_tgt is not None and sat_tgt.has_source()
                    and k in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Space)):
                self._frame_buf = ""
                if k == Qt.Key_Left:
                    self._route_step(-1)
                elif k == Qt.Key_Right:
                    self._route_step(1)
                else:
                    self._route_play_toggle()
                return
            super().keyPressEvent(e)
            return
        txt = e.text()
        # saisie d'un numero de frame au pave numerique : les chiffres
        # s'accumulent, Entree valide (le champ "frame" affiche la saisie).
        if txt.isdigit():
            self._frame_buf = (self._frame_buf + txt)[-9:]
            self.frame_spin.blockSignals(True)
            self.frame_spin.setValue(min(int(self._frame_buf), self.frame_spin.maximum()))
            self.frame_spin.blockSignals(False)
            return
        if k in (Qt.Key_Return, Qt.Key_Enter):
            if self._frame_buf:
                self.seek(min(int(self._frame_buf), max(0, self.source.count - 1)))
            self._frame_buf = ""
            return
        self._frame_buf = ""           # toute autre touche annule la saisie
        if k == Qt.Key_Left:
            self._route_step(-1)
        elif k == Qt.Key_Right:
            self._route_step(1)
        elif k == Qt.Key_Space:
            self._route_play_toggle()
        elif k == Qt.Key_Home:
            self.seek(0)
        elif k == Qt.Key_End:
            self._go_last()
        elif k in (Qt.Key_Plus, Qt.Key_Equal):
            self.view.zoom_in()
        elif k == Qt.Key_Minus:
            self.view.zoom_out()
        elif k == Qt.Key_F11:
            self._toggle_view_only()
        elif k == Qt.Key_Escape:
            if self._view_only:
                self._toggle_view_only()
        else:
            # touche mappee a une case On/Off (manifest element_keys) : bascule.
            if self._toggle_plugin_elements_by_key(e.text()):
                return
            # touche non utilisee par l'appli -> proposee aux plugins "overlay
            # code" (hook on_view_key) ; consommee seulement si un plugin la traite.
            if self._plugin_loader.dispatch_key(self.cur, k, e.text()):
                return
            super().keyPressEvent(e)

    def _enable_playback(self, on):
        for w in (self.play_btn, self.first_btn, self.prev_btn, self.next_btn,
                  self.last_btn, self.slider, self.frame_spin, self.rec_btn,
                  self.undo_btn, self.clear_btn, self.capture_btn,
                  self.extract_in_btn, self.extract_out_btn,
                  self.extract_clip_btn):
            w.setEnabled(on)

    # ----- annotations overlay -----

    def _load_dropped_annotations(self, path):
        if not _HAS_ANNOT:
            QtWidgets.QMessageBox.warning(
                self, "Annotations", "annotation_loader.py manquant.")
            return
        w = h = 0
        if self._raw is not None:
            h, w = self._raw.shape[:2]
        is_yolo = os.path.isdir(path) or os.path.splitext(path)[1].lower() == ".txt"
        if is_yolo and (w <= 0 or h <= 0):
            # coordonnees YOLO normalisees 0..1 -> il FAUT les dimensions
            # de l'image pour les convertir en pixels, sinon chaque boite
            # devient silencieusement (0,0,1,1) (voir _yolo_norm_to_pixel).
            QtWidgets.QMessageBox.warning(
                self, "Annotations",
                "Ouvre d'abord la séquence correspondante sur cette vue : "
                "les coordonnées YOLO sont normalisées (0..1) et ont "
                "besoin des dimensions de l'image pour être converties "
                "en pixels.")
            return
        # default_track n'est utilisé QUE si le fichier ne fournit pas sa
        # propre colonne track_id (format .ver court, 6 colonnes). Les
        # fichiers longs (>=7 col) gardent LEUR(S) track_id, potentiellement
        # plusieurs par fichier -> on fusionne dans les annotations existantes.
        default_track = getattr(self, "_annot_track_next", 0)
        if os.path.isdir(path):
            # un dossier YOLO peut contenir des milliers de petits .txt --
            # les lire un par un dans le thread GUI gele l'interface (c'est
            # le freeze rapporte au drop d'un gros dossier) ; on lit donc en
            # tache de fond et on n'applique le resultat qu'a la fin, comme
            # si c'etait un .ver arrive d'un coup.
            self._start_annot_folder_load(path, w, h, default_track)
            return
        try:
            new = _load_annotations(path, w, h, default_track=default_track)
        except Exception as ex:
            QtWidgets.QMessageBox.warning(
                self, "Annotations", f"Erreur chargement : {ex}")
            return
        self._apply_loaded_annotations(path, new, is_yolo, default_track)

    def _start_annot_folder_load(self, path, w, h, default_track):
        self._stop_annot_load()
        self.statusBar().showMessage(
            f"Chargement des annotations en arrière-plan…  ({os.path.basename(path)})", 0)
        t = AnnotFolderLoadWorker(path, w, h, default_track=default_track)
        t.done.connect(lambda new, worker=t: self._on_annot_load_done(worker, path, new, default_track))
        t.failed.connect(lambda msg, worker=t: self._on_annot_load_failed(worker, msg))
        self._annot_load_thread = t
        t.start()

    def _on_annot_load_done(self, worker, path, new, default_track):
        if self._annot_load_thread is not worker:
            return   # une charge plus recente a pris le relais -- resultat perime
        self._annot_load_thread = None
        self._apply_loaded_annotations(path, new, True, default_track)

    def _on_annot_load_failed(self, worker, msg):
        if self._annot_load_thread is not worker:
            return
        self._annot_load_thread = None
        QtWidgets.QMessageBox.warning(
            self, "Annotations", f"Erreur chargement : {msg}")

    def _stop_annot_load(self):
        if self._annot_load_thread is not None:
            self._annot_load_thread.stop()
            self._annot_load_thread = None

    def _pair_yolo_by_name(self, by_stem):
        """Associe des annotations YOLO clees par le STEM du fichier label aux
        POSITIONS de la sequence d'images (Step 1) :
          1. par NOM : stem du label == stem de l'image (insensible a la casse) ;
          2. si AUCUN label ne matche une image par nom : appariement par ORDRE
             naturel (1er label <-> 1re image, 2e <-> 2e, ...).
        Renvoie un dict position(int) -> [dets]."""
        from frameviewer.core.io_utils import natural_sort
        paths = getattr(self.source, "paths", None)
        stems = [os.path.splitext(os.path.basename(p))[0] for p in paths] if paths else []
        by_lower = {}
        for i, s in enumerate(stems):
            by_lower.setdefault(s.lower(), i)   # 1re occurrence gagne
        matched = {}
        n_named = 0
        for stem, dets in by_stem.items():
            pos = by_lower.get(str(stem).lower())
            if pos is not None:
                matched.setdefault(pos, []).extend(dets)
                n_named += 1
        if n_named > 0:
            missing = len(by_stem) - n_named
            self.statusBar().showMessage(
                f"Labels associés par nom : {n_named}/{len(by_stem)} fichier(s)"
                + (f" ; {missing} sans image correspondante." if missing else "."), 7000)
            return matched
        # aucun match par nom -> appariement positionnel dans l'ordre naturel
        order = natural_sort(list(by_stem.keys()))
        limit = len(paths) if paths else len(order)
        out = {}
        for i, stem in enumerate(order):
            if paths and i >= limit:
                break
            out.setdefault(i, []).extend(by_stem[stem])
        self.statusBar().showMessage(
            f"Aucun label associé par nom -- appariement par ordre naturel "
            f"({len(out)} frame(s)).", 6000)
        return out

    def _remap_yolo_to_positions(self, new):
        """Aligne des annotations YOLO clees par le NUMERO du nom de fichier
        (frame_2760.txt -> 2760) sur les POSITIONS de la sequence d'images
        (ImageSequenceSource indexe 0-based par position, pas par numero) :
        sans ca, un .txt "frame_2760" est cherche a la frame 2760 de la
        sequence au lieu de l'image "frame_2760.png" reellement affichee (d'ou
        des boites decalees / toutes empilees au debut). Ne remappe QUE si la
        source est une sequence d'images numerotees ET qu'au moins une cle
        correspond -- sinon `new` est renvoye inchange (video/SPECIALIZED, ou schema de
        numerotation deja aligne)."""
        paths = getattr(self.source, "paths", None)
        if not paths:
            return new
        from frameviewer.core.annotation_loader import _frame_num_from_name
        num_to_pos = {}
        for pos, p in enumerate(paths):
            n = _frame_num_from_name(p)
            if n is not None and n not in num_to_pos:
                num_to_pos[n] = pos
        if not num_to_pos or not any(k in num_to_pos for k in new):
            return new
        remapped = {}
        for k, dets in new.items():
            remapped.setdefault(num_to_pos.get(k, k), []).extend(dets)
        return remapped

    def _apply_loaded_annotations(self, path, new, is_yolo, default_track):
        if not self._annotations:
            self._annotations = {}
        base = os.path.splitext(os.path.basename(path))[0]

        if is_yolo:
            # aligne les labels sur les positions de la sequence d'images :
            # un dossier YOLO arrive clee par STEM de fichier (matching par
            # NOM, repli ordre naturel) ; un .txt fusionne arrive clee par
            # frame_id numerique (remap par numero de fichier).
            if new and isinstance(next(iter(new)), str):
                new = self._pair_yolo_by_name(new)
            else:
                new = self._remap_yolo_to_positions(new)
            # YOLO n'a AUCUNE concept de piste/suivi entre frames : chaque
            # boîte ne porte qu'un id de classe (ex. "30 objets classe 0").
            # On regroupe donc les calques par classe (pas par track), en
            # tirant une clé fraîche du même compteur que .ver pour ne
            # jamais entrer en collision avec un track_id .ver déjà chargé
            # sur la vue (le namespace de clé reste partagé).
            classes_found = sorted({d[0] for dets in new.values() for d in dets})
            remap = {}
            for cls in classes_found:
                remap[cls] = self._annot_track_next
                self._annot_track_next += 1
            n_added = 0
            for fr, dets in new.items():
                remapped = [(d[0], d[1], d[2], d[3], d[4], remap[d[0]],
                             d[6] if len(d) > 6 else ())
                            for d in dets]
                self._annotations.setdefault(fr, []).extend(remapped)
                n_added += len(remapped)
            for cls, key in remap.items():
                class_label = next(
                    (d[6][0] for dets in new.values() for d in dets
                     if d[0] == cls and len(d) > 6 and d[6]),
                    f"classe {cls}")
                self._layers.append({
                    "kind": "ver", "fmt": "yolo", "name": class_label,
                    "key": key, "cls": cls, "visible": True, "file": base,
                    "path": path})
            self._annot_path = path
            self._rebuild_layer_list()
            if not remap:
                # aucune boite reconnue -- le format le plus probable en
                # cause : un .txt separe par des virgules (ex. VisDrone brut
                # "left,top,w,h,score,cat,trunc,occ") au lieu du format YOLO
                # attendu (espaces, coordonnees normalisees 0..1).
                self.statusBar().showMessage(
                    f"Aucune boîte reconnue dans {os.path.basename(path)} -- "
                    "format YOLO attendu : \"classe cx cy w h\" séparé par "
                    "des espaces, coordonnées normalisées 0..1, un .txt par "
                    "frame.", 8000)
            else:
                self.statusBar().showMessage(
                    f"{len(remap)} calque(s) YOLO (par classe) : {n_added} boîtes "
                    f"sur {len(new)} frames  ({os.path.basename(path)})", 6000)
            self._display()
            return

        n_added = 0
        for fr, dets in new.items():
            self._annotations.setdefault(fr, []).extend(dets)
            n_added += len(dets)
        # un calque par track_id RÉELLEMENT présent (et pas un seul calque
        # basé sur le compteur) : sinon la case à cocher du gestionnaire ne
        # correspond à rien pour un fichier long fournissant ses propres
        # track_id (ex. .ver multi-objets) -> c'était la cause du bug
        # « décocher le calque n'a aucun effet ».
        tracks_found = sorted({
            (d[5] if len(d) > 5 else default_track)
            for dets in new.values() for d in dets
        }) or [default_track]
        existing_keys = {L["key"] for L in self._layers if L["kind"] == "ver"}
        for tid in tracks_found:
            if tid in existing_keys:
                continue
            self._layers.append({"kind": "ver", "fmt": "ver", "name": f"track {tid}",
                                 "key": tid, "visible": True, "file": base,
                                 "path": path})
        self._annot_track_next = max(default_track + 1, max(tracks_found) + 1)
        self._annot_path = path
        self._rebuild_layer_list()
        self.statusBar().showMessage(
            f"{len(tracks_found)} calque(s) .ver : {n_added} boîtes sur "
            f"{len(new)} frames  ({os.path.basename(path)})", 6000)
        self._display()

    # ----- gestionnaire de calques (par vue) -----
    def _layer_visible(self, kind, key):
        for L in self._layers:
            if L["kind"] == kind and L["key"] == key:
                return L["visible"]
        return True

    def _layer_fmt(self, kind, key):
        """Format d'origine du calque ("ver" ou "yolo") pour ce (kind, key).
        "ver" par défaut si introuvable (comportement historique)."""
        for L in self._layers:
            if L["kind"] == kind and L["key"] == key:
                return L.get("fmt", "ver")
        return "ver"

    def _visible_annots(self, frame):
        """Annotations de la frame dont le calque (track) est visible."""
        dets = self._annotations.get(frame, [])
        if not dets:
            return []
        return [d for d in dets
                if self._layer_visible("ver", d[5] if len(d) > 5 else 0)]

    # ----- suivi ROI <-> boîte .ver -----
    def _annot_track_at(self, x, y):
        """track_id de la boîte .ver visible sous (x,y) — coordonnées de la
        vue affichée (post-rotation/échelle) — ou None si aucune. Le suivi
        ROI n'a de sens que sur un vrai .ver (identité d'objet stable entre
        frames) : une boîte issue d'un calque YOLO (juste une classe, aucun
        suivi) est ignorée ici, même si elle est visible/cliquée."""
        dets = self._visible_annots(self.cur)
        if not dets or self._raw is None:
            return None
        orig_h, orig_w = self._raw.shape[:2]
        s = getattr(self, "_fetch_scale", 1.0) or 1.0
        for d in reversed(dets):
            key = d[5] if len(d) > 5 else 0
            if self._layer_fmt("ver", key) != "ver":
                continue
            x1, y1, x2, y2 = d[1], d[2], d[3], d[4]
            if s < 1.0:
                x1, y1, x2, y2 = x1 * s, y1 * s, x2 * s, y2 * s
            if self._rotation:
                x1, y1, x2, y2 = _rotate_box_coords(x1, y1, x2, y2, orig_w, orig_h, self._rotation)
            if min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2):
                return key
        return None

    def _annot_box_for_track(self, track_id, frame):
        """Boîte (x,y,w,h) — espace affiché (rotation/échelle) — du track
        donné à la frame indiquée, ou None si absent/masqué à cette frame."""
        if self._raw is None:
            return None
        match = None
        for d in self._visible_annots(frame):
            if (d[5] if len(d) > 5 else 0) == track_id:
                match = d
                break
        if match is None:
            return None
        orig_h, orig_w = self._raw.shape[:2]
        s = getattr(self, "_fetch_scale", 1.0) or 1.0
        x1, y1, x2, y2 = match[1], match[2], match[3], match[4]
        if s < 1.0:
            x1, y1, x2, y2 = x1 * s, y1 * s, x2 * s, y2 * s
        if self._rotation:
            x1, y1, x2, y2 = _rotate_box_coords(x1, y1, x2, y2, orig_w, orig_h, self._rotation)
        x1, y1, x2, y2 = int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))
        return (min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))

    def _apply_roi_track(self, frame):
        """Si un suivi de boîte .ver est actif, aligne la ROI dessus pour la
        frame donnée. Appelé au clic de suivi puis à chaque frame affichée."""
        if self._roi_track_id is None:
            return
        box = self._annot_box_for_track(self._roi_track_id, frame)
        if box is None:
            return
        self._roi = box
        if hasattr(self, "view"):
            self.view.set_roi_rect(*box)
        if self.tools_dock.isHidden():
            self.tools_dock.show(); self.tools_dock.raise_()
        if not self.tools_panel.is_roi_active():
            self.tools_panel._set_tool("roi")
            self.view.set_tool_mode("roi")
        self.update_roi_panel()

    def _rebuild_layer_list(self):
        """Peuple l'arbre des calques (cases à cocher) de la vue active. Les
        tracks .ver d'un même fichier sont groupées sous une entrée dépliable
        (case du groupe = tout le .ver) ; SIDECAR reste une ligne simple (un seul
        calque SIDECAR actif à la fois)."""
        if not hasattr(self, "layer_list"):
            return
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        groups = {}          # fichier .ver -> [(index dans self._layers, L), ...]
        for i, L in enumerate(self._layers):
            if L["kind"] == "sidecar":
                it = QtWidgets.QTreeWidgetItem([f"[SIDECAR]  {L['name']}"])
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Checked if L["visible"] else Qt.Unchecked)
                it.setData(0, Qt.UserRole, i)
                self.layer_list.addTopLevelItem(it)
            else:
                groups.setdefault(L.get("file") or L["name"], []).append((i, L))

        for file, entries in groups.items():
            n = len(entries)
            # une entrée .ver et une entrée YOLO ne se retrouvent jamais dans
            # le même groupe (regroupées par fichier source, homogène en
            # format) -> le libellé peut se baser sur la 1ère entrée.
            is_yolo_grp = entries[0][1].get("fmt") == "yolo"
            unit = "classe" if is_yolo_grp else "track"
            parent = QtWidgets.QTreeWidgetItem(
                [f"📄 {file}  ({n} {unit}{'s' if n > 1 else ''})"])
            parent.setFlags(parent.flags() | Qt.ItemIsUserCheckable)
            parent.setData(0, Qt.UserRole, None)          # None = entrée de groupe
            parent.setData(0, Qt.UserRole + 1, file)
            all_vis = all(L["visible"] for _, L in entries)
            any_vis = any(L["visible"] for _, L in entries)
            parent.setCheckState(
                0, Qt.Checked if all_vis else (Qt.PartiallyChecked if any_vis else Qt.Unchecked))
            self.layer_list.addTopLevelItem(parent)
            for i, L in entries:
                disp = L.get("cls", L["key"]) if is_yolo_grp else L["key"]
                child = QtWidgets.QTreeWidgetItem([f"{unit} {disp}"])
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Checked if L["visible"] else Qt.Unchecked)
                child.setData(0, Qt.UserRole, i)
                c = _ann_color(L["key"])
                child.setForeground(0, QtGui.QColor(c[2], c[1], c[0]))
                parent.addChild(child)
        self.layer_list.blockSignals(False)

    def _on_layer_item_changed(self, item, column=0):
        i = item.data(0, Qt.UserRole)
        if i is not None:
            if i >= len(self._layers):
                return
            self._layers[i]["visible"] = (item.checkState(0) == Qt.Checked)
        else:
            # entrée de groupe (.ver) : sa case coche/décoche TOUS ses tracks.
            state = item.checkState(0)
            if state == Qt.PartiallyChecked:
                return
            want = (state == Qt.Checked)
            self.layer_list.blockSignals(True)
            for c in range(item.childCount()):
                ch = item.child(c)
                ch.setCheckState(0, Qt.Checked if want else Qt.Unchecked)
                idx = ch.data(0, Qt.UserRole)
                if idx is not None and idx < len(self._layers):
                    self._layers[idx]["visible"] = want
            self.layer_list.blockSignals(False)
        if self._raw is not None:
            self._display()

    def _layers_set_all(self, visible):
        for L in self._layers:
            L["visible"] = visible
        self._rebuild_layer_list()
        if self._raw is not None:
            self._display()

    def _layer_indices_from_selection(self):
        """Indices dans self._layers pour la sélection courante de l'arbre
        (sélectionner l'entrée de groupe d'un .ver = tous ses tracks)."""
        idxs = set()
        for it in self.layer_list.selectedItems():
            i = it.data(0, Qt.UserRole)
            if i is not None:
                idxs.add(i)
            else:
                for c in range(it.childCount()):
                    ci = it.child(c).data(0, Qt.UserRole)
                    if ci is not None:
                        idxs.add(ci)
        return idxs

    def _remove_layers(self, idxs):
        """Retire les calques aux indices donnés de self._layers et nettoie
        les données associées (overlays SIDECAR / annotations .ver / suivi ROI)."""
        for r in sorted(idxs, reverse=True):
            if 0 <= r < len(self._layers):
                L = self._layers.pop(r)
                if L["kind"] == "sidecar":
                    self.overlays = {}
                    self.view.set_overlays([])
                    self.sidecar_label.setText("Aucun SIDECAR charge")
                else:
                    tid = L["key"]
                    for fr in list(self._annotations):
                        self._annotations[fr] = [
                            d for d in self._annotations[fr]
                            if (d[5] if len(d) > 5 else 0) != tid]
                        if not self._annotations[fr]:
                            del self._annotations[fr]
                    if self._roi_track_id == tid:
                        self._roi_track_id = None
        self._rebuild_layer_list()
        if self._raw is not None:
            self._display()

    def _delete_selected_layers(self):
        """Supprime les calques sélectionnés : un .ver entier si son entrée
        de groupe est sélectionnée, ou seulement le(s) track(s) choisi(s)."""
        idxs = self._layer_indices_from_selection()
        if idxs:
            self._remove_layers(idxs)

    def _delete_all_layers(self):
        """Bouton « Suppr. all » : supprime TOUS les calques listés
        immédiatement, sans avoir à les sélectionner au préalable."""
        if self._layers:
            self._remove_layers(set(range(len(self._layers))))

    def _open_layer_export_dialog(self):
        """Bouton « Export custom des calques... » : plage de frames +
        reorganisation des tracks .ver (eclater / fusionner) en export.
        N'a de sens que pour de vrais .ver (identite d'objet/track) : les
        calques issus d'un dossier YOLO (juste une classe, pas de track)
        sont ignores par ce dialogue."""
        if self.source is None:
            self.statusBar().showMessage("Aucune source chargée.", 3000)
            return
        if not any(L["kind"] == "ver" and L.get("fmt", "ver") == "ver"
                   for L in self._layers):
            QtWidgets.QMessageBox.information(
                self, "Export custom des calques",
                "Aucun calque .ver chargé sur la vue active.\n\n"
                "(Cet export réorganise des tracks .ver — il ne s'applique "
                "pas aux calques issus d'un dossier YOLO, qui n'ont pas de "
                "concept de suivi entre frames.)")
            return
        dlg = LayerExportDialog(self)
        dlg.exec()

    def _open_yolo_convert_dialog(self):
        """Bouton « Convertir .ver <-> YOLO... » : export dataset YOLO
        (labels + images, split train/val/test) depuis les calques .ver
        chargés, ou import d'un dossier YOLO vers .ver."""
        dlg = YoloConvertDialog(self)
        dlg.exec()

    # ----- plugins (voir docs/plugins.md) -----
    # (Le "dialogue complet" historique (PluginDialog) a ete retire : tout se
    # passe dans l'onglet Plugins -- voir ui/plugins_panel.py.)

    def _plugin_settings(self):
        """Settings PROPRES A CHAQUE UTILISATEUR/PC : fichier .ini dans
        AppData\\FrameViewer (au lieu du registre Windows), cree a la premiere
        utilisation. Migre une seule fois l'ancien etat du registre si present."""
        from frameviewer.plugins.loader import user_config_dir
        path = os.path.join(user_config_dir(), "settings.ini")
        st = QtCore.QSettings(path, QtCore.QSettings.IniFormat)
        if not st.contains("enabled_plugins") and not st.contains("plugin_state"):
            old = QtCore.QSettings("FrameViewer", "FrameViewer")
            migrated = False
            for k in ("enabled_plugins", "plugin_state"):
                v = old.value(k, None)
                if v is not None:
                    st.setValue(k, v)
                    migrated = True
            if migrated:
                st.sync()
        return st

    def _load_enabled_plugin_ids(self):
        val = self._plugin_settings().value("enabled_plugins", [])
        if isinstance(val, str):
            val = [val] if val else []
        return [pid for pid in (val or []) if pid not in _EPHEMERAL_DEMO_PLUGIN_IDS]

    def _save_enabled_plugin_ids(self):
        self._plugin_settings().setValue(
            "enabled_plugins", [
                pid for pid in self._plugin_loader.enabled_ids()
                if pid not in _EPHEMERAL_DEMO_PLUGIN_IDS
            ])

    def _load_plugin_state(self):
        import json
        raw = self._plugin_settings().value("plugin_state", "")
        try:
            state = json.loads(raw) if raw else {}
            for pid in _EPHEMERAL_DEMO_PLUGIN_IDS:
                state.pop(pid, None)
            return state
        except (ValueError, TypeError):
            return {}

    def _save_plugin_state(self):
        import json
        persistent = {
            pid: value for pid, value in self._plugin_state.items()
            if pid not in _EPHEMERAL_DEMO_PLUGIN_IDS
        }
        self._plugin_settings().setValue("plugin_state", json.dumps(persistent))

    # ----- persistance de la disposition des panneaux (docks) -----
    def _dock_layout_targets(self):
        return (("right_dock", getattr(self, "right_dock", None)),
                ("tools_dock", getattr(self, "tools_dock", None)),
                ("hist_dock", getattr(self, "hist_dock", None)))

    def _save_dock_layout(self):
        """Memorise, par utilisateur (settings.ini), l'etat ouvert/ferme des
        panneaux + l'onglet actif du panneau droit. On NE sauve PAS le contenu
        de l'historique, juste la visibilite des docks."""
        st = self._plugin_settings()
        st.beginGroup("ui")
        for name, dock in self._dock_layout_targets():
            if dock is not None:
                st.setValue(name + "_visible", bool(dock.isVisible()))
        if hasattr(self, "right_tab"):
            st.setValue("right_tab_index", int(self.right_tab.currentIndex()))
        st.endGroup()
        st.sync()

    def _restore_dock_layout(self):
        """Applique la visibilite des panneaux memorisee (si presente)."""
        st = self._plugin_settings()
        st.beginGroup("ui")

        def _as_bool(v, default):
            if v is None:
                return default
            if isinstance(v, bool):
                return v
            return str(v).strip().lower() in ("1", "true", "yes", "on")

        for name, dock in self._dock_layout_targets():
            if dock is None:
                continue
            raw = st.value(name + "_visible", None)
            if raw is not None:
                dock.setVisible(_as_bool(raw, dock.isVisible()))
        idx = st.value("right_tab_index", None)
        st.endGroup()
        if idx is not None and hasattr(self, "right_tab"):
            try:
                i = int(idx)
                if 0 <= i < self.right_tab.count():
                    self.right_tab.setCurrentIndex(i)
            except (ValueError, TypeError):
                pass

    def _plugin_by_id(self, pid):
        for lp in self._plugin_loader.plugins:
            if lp.plugin_id == pid:
                return lp
        return None

    def _plugin_state_for(self, pid):
        """Etat GLOBAL du plugin `pid` : entrees declarees (du manifest) +
        `by_view` (jeux d'entrees et cases par element, PAR VUE). Les entrees
        sont structurelles (globales) ; les fichiers deposes et les cases On/Off
        appartiennent a la vue courante (cf. _plugin_view_state)."""
        st = self._plugin_state.get(pid)
        lp = self._plugin_by_id(pid)
        if st is None:
            st = {"inputs": [], "by_view": {}}
            self._plugin_state[pid] = st
        # migration de l'ancien format plat (contracts/elements globaux) vers un
        # bucket par vue "__default__" (retro-compat des etats QSettings existants).
        if "contracts" in st or "elements" in st:
            st.setdefault("by_view", {})
            st["by_view"].setdefault("__default__", {
                "contracts": st.pop("contracts", None) or [{"name": "", "visible": True, "files": {}}],
                "elements": st.pop("elements", None) or {}})
            st.pop("sel_contract", None)
        st.setdefault("inputs", [])
        st.setdefault("by_view", {})
        # le manifest est la source de verite des entrees declarees (les ajouts
        # via _add_plugin_input y sont ecrits) : on aligne st["inputs"] dessus
        # en conservant l'etat "masque" par nom, et on jette les entrees obsoletes.
        if lp is not None:
            prev_hidden = {i["name"]: i.get("hidden", False) for i in st["inputs"]}
            names = [i["name"] for i in lp.manifest.get("inputs", [])]
            st["inputs"] = [{"name": n, "hidden": prev_hidden.get(n, False)} for n in names]
        return st

    def _view_key_for(self, src):
        """Cle stable d'une source = par OBJET (pas par nom) : deux vues qui
        chargent le meme fichier sont deux objets source differents et recoivent
        donc des cles distinctes (data plugin dissociee). Le nom sert de base
        lisible ; un suffixe #n desambigue deux sources vivantes de meme nom."""
        if src is None:
            return "__none__"
        sid = id(src)
        rec = self._view_keys.get(sid)
        if rec is not None and rec[0] is src:
            return rec[1]
        base = getattr(src, "name", None) or "src"
        used = {k for (_o, k) in self._view_keys.values()}
        key, i = base, 1
        while key in used:
            i += 1
            key = f"{base}#{i}"
        self._view_keys[sid] = (src, key)
        return key

    def _current_view_key(self):
        """Cle de la vue courante = la SOURCE chargee dans le moteur (le clic
        sur une vue la ramene dans le moteur via move_primary_to). Les fichiers/
        cases du plugin suivent cette cle -> changer de vue change de jeu."""
        return self._view_key_for(getattr(self, "source", None))

    def _plugin_view_state(self, pid, view_key=None):
        """Etat PAR VUE du plugin (jeux d'entrees + cases par element) pour la
        vue `view_key` (None = vue courante = source du moteur). Cree si absent.
        Migre l'ancien bucket plat "__default__" vers la vue COURANTE seulement
        (jamais vers un satellite explicitement demande)."""
        st = self._plugin_state_for(pid)
        current = view_key is None
        key = self._current_view_key() if current else view_key
        bv = st["by_view"]
        if current and key not in bv and "__default__" in bv and len(bv) == 1:
            bv[key] = bv.pop("__default__")   # l'etat pre-multivue devient celui de la vue courante
        vs = bv.get(key)
        if vs is None:
            vs = {"contracts": [{"name": "", "visible": True, "files": {}}], "elements": {}}
            bv[key] = vs
        vs.setdefault("contracts", [{"name": "", "visible": True, "files": {}}])
        if not vs["contracts"]:
            vs["contracts"] = [{"name": "", "visible": True, "files": {}}]
        vs.setdefault("elements", {})
        return vs

    def _plugin_elements(self, pid, view_key=None):
        """Cases On/Off (par element) de la vue `view_key` (None = courante) --
        lues par PluginAPI.element_enabled au moment du rendu."""
        return self._plugin_view_state(pid, view_key)["elements"]

    def _visible_contracts(self, pid, view_key=None):
        """Jeux d'entrees VISIBLES et non vides de la vue `view_key` (None =
        courante) -> [{nom: chemin}, ...], entrees masquees omises. Un jeu vide
        n'est pas rendu."""
        st = self._plugin_state_for(pid)
        allowed = {i["name"] for i in st["inputs"] if not i.get("hidden")}
        out = []
        for c in self._plugin_view_state(pid, view_key)["contracts"]:
            if not c.get("visible", True):
                continue
            files = {k: v for k, v in c["files"].items() if v and k in allowed}
            if files:
                out.append(files)
        return out

    def _satellite_overlay(self, source, frame_idx, size):
        """Overlay plugin (BGRA) pour une vue SATELLITE, identifiee par son OBJET
        source (cle calculee comme la vue primaire) -- appele par
        SatelliteView._render pour que CHAQUE vue affiche son propre overlay."""
        try:
            key = self._view_key_for(source)
            return self._plugin_loader.collect_overlay_image(frame_idx, size, view_key=key)
        except Exception:
            return None

    def _normalize_plugin_groups(self, pid):
        """Maintient les jeux d'entrees de la VUE COURANTE : supprime les jeux
        vides, puis ajoute un jeu vide en fin des que le dernier est COMPLET."""
        st = self._plugin_state_for(pid)
        vs = self._plugin_view_state(pid)
        req = [i["name"] for i in st["inputs"] if not i.get("hidden")]
        groups = [c for c in vs["contracts"]
                  if any(c["files"].get(n) for n in req)] if req else []
        if req:
            last_complete = groups and all(groups[-1]["files"].get(n) for n in req)
            if not groups or last_complete:
                groups.append({"name": "", "visible": True, "files": {}})
        else:
            groups = [{"name": "", "visible": True, "files": {}}]
        vs["contracts"] = groups
        return vs

    # --- glisser-deposer generique -> entree de plugin (par vue) ---
    _PLUGIN_DATA_EXTS = (".csv", ".tsv", ".xlsx", ".xls", ".json", ".dat")

    def _is_plugin_data(self, path):
        """Fichier de donnees candidat a l'association plugin (pas un media ni un
        calque .ver/.sidecar/.txt, deja geres nativement par ailleurs)."""
        return os.path.splitext(path)[1].lower() in self._PLUGIN_DATA_EXTS

    def _assign_file_to_view(self, pid, name, path):
        """Place `path` sous l'entree `name` du plugin, dans le 1er jeu de la vue
        courante ou cette entree est libre (sinon un nouveau jeu)."""
        vs = self._plugin_view_state(pid)
        target = next((c for c in vs["contracts"] if not c["files"].get(name)), None)
        if target is None:
            target = {"name": "", "visible": True, "files": {}}
            vs["contracts"].append(target)
        target["files"][name] = os.path.normpath(path)
        self._normalize_plugin_groups(pid)

    def _offer_plugin_drop(self, path):
        """Fichier de donnees depose sur une vue : propose (popup) de l'associer
        a une entree d'un plugin CODE actif, pour la VUE courante. Renvoie True
        si pris en charge (associe ou explicitement annule)."""
        options = []   # [(lp, nom_entree)]
        for lp in self._plugin_loader.enabled_plugins():
            if lp.kind != "code":
                continue
            st = self._plugin_state_for(lp.plugin_id)
            for i in st["inputs"]:
                if not i.get("hidden"):
                    options.append((lp, i["name"]))
        if not options:
            return False
        labels = [f"{lp.name}  ->  {name}" for lp, name in options]
        item, ok = QtWidgets.QInputDialog.getItem(
            self, "Associer le fichier au plugin",
            f"Déposer « {os.path.basename(path)} » sur quelle entrée ?",
            labels, 0, False)
        if not ok:
            return True   # annulé : ne pas retomber sur une ouverture média (vouée à l'échec)
        lp, name = options[labels.index(item)]
        self._assign_file_to_view(lp.plugin_id, name, path)
        self._save_plugin_state()
        self._refresh_plugins_settings()
        self._repaint_views()
        self.statusBar().showMessage(
            f"{os.path.basename(path)} → {lp.name} / {name}", 4000)
        return True

    def _add_plugin_input(self, pid, varname):
        """Ajoute une entree declaree (variable) au plugin `pid` : met a jour
        l'etat, le manifest.json (persistance), et injecte au mieux la ligne
        self.<varname> = None dans on_load du plugin.py. Renvoie True si ajout."""
        from frameviewer.plugins import manifest as _manifest
        st = self._plugin_state_for(pid)
        if any(i["name"] == varname for i in st["inputs"]):
            return False
        st["inputs"].append({"name": varname, "hidden": False})
        lp = self._plugin_by_id(pid)
        if lp is not None:
            names = [i["name"] for i in st["inputs"]]
            _manifest.save(lp.folder, {
                "name": lp.manifest.get("name", pid),
                "kind": lp.manifest.get("kind", "code"),
                "frame_input": lp.manifest.get("frame_input", "Current Frame"),
                "inputs": [{"name": n} for n in names]})
            lp.manifest = _manifest.load(lp.folder)
            self._inject_input_attr(lp.folder, varname)
        self._save_plugin_state()
        return True

    def _remove_plugin_input(self, pid, varname):
        """Supprime une entree declaree : etat + fichiers deposes + manifest.json
        + la ligne self.<varname> = ... dans plugin.py. Renvoie True si retire."""
        from frameviewer.plugins import manifest as _manifest
        st = self._plugin_state_for(pid)
        if not any(i["name"] == varname for i in st["inputs"]):
            return False
        st["inputs"] = [i for i in st["inputs"] if i["name"] != varname]
        for vs in st["by_view"].values():
            for c in vs.get("contracts", []):
                c["files"].pop(varname, None)
        lp = self._plugin_by_id(pid)
        if lp is not None:
            names = [i["name"] for i in st["inputs"]]
            _manifest.save(lp.folder, {
                "name": lp.manifest.get("name", pid),
                "kind": lp.manifest.get("kind", "code"),
                "frame_input": lp.manifest.get("frame_input", "Current Frame"),
                "inputs": [{"name": n} for n in names]})
            lp.manifest = _manifest.load(lp.folder)
            self._remove_input_attr(lp.folder, varname)
        self._save_plugin_state()
        return True

    @staticmethod
    def _remove_input_attr(folder, varname):
        """Retire la ligne d'assignation self.<varname> = ... de plugin.py
        (best-effort ; les usages ailleurs dans le code ne sont pas touches)."""
        import re
        path = os.path.join(folder, "plugin.py")
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            pat = re.compile(r"^\s*self\." + re.escape(varname) + r"\s*=")
            kept = [ln for ln in lines if not pat.match(ln)]
            if len(kept) != len(lines):
                with open(path, "w", encoding="utf-8") as f:
                    f.writelines(kept)
        except OSError:
            pass

    @staticmethod
    def _inject_input_attr(folder, varname):
        """Insere `self.<varname> = None` juste apres `def on_load(self, api):`
        dans plugin.py, pour que le code montre l'entree. Best-effort : toute
        erreur est ignoree (l'attribut reste fourni au rendu par le loader)."""
        path = os.path.join(folder, "plugin.py")
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            for i, line in enumerate(lines):
                if line.lstrip().startswith("def on_load(self, api):"):
                    indent = " " * (len(line) - len(line.lstrip()) + 4)
                    lines.insert(i + 1, f"{indent}self.{varname} = None\n")
                    with open(path, "w", encoding="utf-8") as f:
                        f.writelines(lines)
                    return
        except OSError:
            pass

    def _repaint_views(self):
        if getattr(self, "_raw", None) is not None:
            self._display()

    # --- onglet Plugins (arbre + CSV rattaches + cases par element) ---
    def _build_plugins_tab(self):
        """Contenu de l'onglet Plugins (modele v2) : delegue a PluginsPanel."""
        from frameviewer.ui.plugins_panel import PluginsPanel
        self._plugins_panel = PluginsPanel(self)
        return self._plugins_panel

    def _refresh_plugins_settings(self):
        panel = getattr(self, "_plugins_panel", None)
        if panel is not None:
            panel.refresh()

    def _on_current_view_changed(self):
        """La vue selectionnee (donc la vue primaire) a change : l'onglet
        Plugins montre les fichiers/cases de la nouvelle vue SANS replier les
        cartes deja ouvertes."""
        panel = getattr(self, "_plugins_panel", None)
        if panel is not None:
            panel.on_view_changed()

    def _on_element_toggled(self, pid, key, on):
        self._plugin_view_state(pid)["elements"][key] = bool(on)
        self._save_plugin_state()
        self._repaint_views()

    def _app_key_toggle(self, ev):
        """Filtre applicatif : une touche mappee a un element de plugin actif
        bascule sa case, quel que soit le widget qui a le focus. Ne consomme
        RIEN quand on saisit du texte (champ, spin, combo editable, editeur) ni
        quand aucune touche mappee ne correspond -> les autres frappes passent."""
        if self.source is None:
            return False
        if ev.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False
        txt = ev.text()
        if not txt or len(txt) != 1 or not txt.isprintable() or txt.isdigit():
            return False
        fw = QtWidgets.QApplication.focusWidget()
        if isinstance(fw, (QtWidgets.QLineEdit, QtWidgets.QAbstractSpinBox,
                           QtWidgets.QPlainTextEdit, QtWidgets.QTextEdit)):
            return False
        if isinstance(fw, QtWidgets.QComboBox) and fw.isEditable():
            return False
        ch = txt.strip().lower()
        # ne consomme que si un plugin actif a REELLEMENT cette touche mappee
        mapped = any(str(v).strip().lower() == ch
                     for lp in self._plugin_loader.enabled_plugins()
                     for v in (getattr(lp, "element_keys", None) or {}).values())
        if not mapped:
            return False
        return self._toggle_plugin_elements_by_key(txt)

    def _toggle_plugin_elements_by_key(self, txt):
        """Touche mappee (manifest element_keys d'un plugin ACTIF) -> bascule les
        cases On/Off correspondantes. -> True si au moins une case a bascule."""
        ch = (txt or "").strip().lower()
        if not ch:
            return False
        hit = False
        for lp in self._plugin_loader.enabled_plugins():
            mapping = getattr(lp, "element_keys", None) or {}
            for elem_key, mapped in mapping.items():
                if str(mapped).strip().lower() == ch:
                    st = self._plugin_view_state(lp.plugin_id)["elements"]
                    st[elem_key] = not st.get(elem_key, True)
                    hit = True
        if hit:
            self._save_plugin_state()
            self._repaint_views()
            panel = getattr(self, "_plugins_panel", None)
            if panel is not None:
                panel.update_summary()   # rafraichit les cases cochees
        return hit

    # --- editeurs / creation / gestion ---
    def _open_graph_editor_for(self, folder):
        from frameviewer.ui.node_graph_editor import open_node_graph_editor
        open_node_graph_editor(self, folder, on_saved=self._after_plugins_changed)

    def _open_code_editor_for(self, entry_name):
        from frameviewer.ui.plugin_editor_dialog import PluginEditorDialog
        dlg = PluginEditorDialog(self)
        dlg.select_entry(entry_name)
        dlg.exec()
        self._after_plugins_changed()

    def _ask_plugin_root(self):
        """Demande OU creer le plugin : a cote de l'appli (livre avec l'exe) ou
        dans le dossier PERSO de l'utilisateur (AppData\\FrameViewer\\plugins).
        Renvoie le dossier racine choisi, ou None si annule."""
        from frameviewer.plugins.loader import plugins_root, user_plugins_dir
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Emplacement du plugin")
        box.setText("Où créer ce plugin ?")
        box.setInformativeText(
            "À côté de l'app : livré avec l'exe (dossier plugins/).\n"
            "Dossier utilisateur : perso, dans AppData\\FrameViewer\\plugins "
            "(propre à ce PC, conservé même si tu remplaces l'exe).")
        b_app = box.addButton("À côté de l'app", QtWidgets.QMessageBox.AcceptRole)
        b_user = box.addButton("Dossier utilisateur", QtWidgets.QMessageBox.AcceptRole)
        box.addButton("Annuler", QtWidgets.QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_app:
            return plugins_root()
        if clicked is b_user:
            return user_plugins_dir()
        return None

    def _new_graph_plugin_from_settings(self):
        import re
        from frameviewer.plugins import manifest as _manifest
        from frameviewer.plugins.loader import PLUGIN_FOLDER_PREFIX
        from frameviewer.ui.node_graph_editor import open_node_graph_editor
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Nouveau plugin graphe",
            "Nom du plugin (lettres/chiffres/underscore, commence par une lettre) :")
        if not ok or not name.strip():
            return
        slug = re.sub(r"[^a-zA-Z0-9_]", "_", name.strip().lower())
        if not slug or not slug[0].isalpha():
            QtWidgets.QMessageBox.warning(self, "Nouveau plugin graphe", "Nom invalide.")
            return
        root = self._ask_plugin_root()
        if root is None:
            return
        folder = os.path.join(root, f"{PLUGIN_FOLDER_PREFIX}{slug}")
        if os.path.isdir(folder):
            QtWidgets.QMessageBox.warning(self, "Nouveau plugin graphe", f"{folder} existe déjà.")
            return
        os.makedirs(folder, exist_ok=True)
        _manifest.save(folder, {"name": name.strip(), "kind": "graph"})
        open_node_graph_editor(self, folder, on_saved=self._after_plugins_changed)

    def _new_code_plugin_wizard(self):
        import re
        from frameviewer.plugins import manifest as _manifest
        from frameviewer.plugins.loader import PLUGIN_FOLDER_PREFIX
        from frameviewer.ui.plugin_editor_dialog import PluginEditorDialog
        from frameviewer.ui.plugins_panel import CodePluginWizard, write_code_plugin_template
        dlg = CodePluginWizard(self)
        if not dlg.exec():
            return
        name, frame_input, inputs = dlg.result_values()
        if not name.strip():
            return
        slug = re.sub(r"[^a-zA-Z0-9_]", "_", name.strip().lower())
        if not slug or not slug[0].isalpha():
            QtWidgets.QMessageBox.warning(self, "Nouveau plugin code", "Nom invalide.")
            return
        root = self._ask_plugin_root()
        if root is None:
            return
        folder = os.path.join(root, f"{PLUGIN_FOLDER_PREFIX}{slug}")
        if os.path.isdir(folder):
            QtWidgets.QMessageBox.warning(
                self, "Nouveau plugin code", f"Le plugin '{slug}' existe déjà.")
            return
        os.makedirs(folder, exist_ok=True)
        _manifest.save(folder, {"name": name.strip(), "kind": "code",
                                "frame_input": frame_input.strip() or "Current Frame",
                                "inputs": inputs})
        write_code_plugin_template(folder, slug, name.strip(), inputs)
        self._plugin_loader.reload_all()
        self._refresh_plugins_settings()
        dlg2 = PluginEditorDialog(self)
        dlg2.select_entry(f"{PLUGIN_FOLDER_PREFIX}{slug}")
        dlg2.exec()
        self._after_plugins_changed()

    def _delete_plugin(self, pid, folder):
        import shutil
        lp = self._plugin_by_id(pid)
        name = lp.name if lp is not None else pid
        r = QtWidgets.QMessageBox.question(
            self, "Supprimer le plugin",
            f"Supprimer définitivement le plugin « {name} » et son dossier ?\n{folder}",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if r != QtWidgets.QMessageBox.Yes:
            return
        try:
            shutil.rmtree(folder)
        except OSError as ex:
            QtWidgets.QMessageBox.warning(self, "Supprimer le plugin", f"Échec : {ex}")
            return
        self._plugin_state.pop(pid, None)
        self._save_plugin_state()
        self._plugin_loader.reload_all()
        self._after_plugins_changed()

    def _import_plugin(self):
        """Importe un plugin existant : on choisit un dossier contenant un
        plugin.py, il est copie dans le dossier plugins/."""
        import shutil
        from frameviewer.plugins.loader import PLUGIN_FOLDER_PREFIX, plugins_root
        src = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Choisir le dossier du plugin à importer")
        if not src:
            return
        if not os.path.isfile(os.path.join(src, "plugin.py")):
            QtWidgets.QMessageBox.warning(
                self, "Importer un plugin", "Le dossier ne contient pas de plugin.py.")
            return
        base = os.path.basename(os.path.normpath(src))
        if not base.startswith(PLUGIN_FOLDER_PREFIX):
            base = PLUGIN_FOLDER_PREFIX + base
        dst = os.path.join(plugins_root(), base)
        if os.path.isdir(dst):
            QtWidgets.QMessageBox.warning(self, "Importer un plugin", f"{base} existe déjà.")
            return
        try:
            shutil.copytree(src, dst)
        except OSError as ex:
            QtWidgets.QMessageBox.warning(self, "Importer un plugin", f"Échec : {ex}")
            return
        self._plugin_loader.reload_all()
        self._after_plugins_changed()

    def _open_folder(self, folder):
        try:
            os.startfile(folder)   # Windows
        except Exception:
            QtWidgets.QMessageBox.information(self, "Dossier du plugin", folder)

    def _open_config_dir(self):
        """Ouvre le dossier de config utilisateur (AppData\\FrameViewer) :
        settings.ini + dossier plugins/ perso (cree a la volee si besoin)."""
        from frameviewer.plugins.loader import user_config_dir, user_plugins_dir
        user_plugins_dir()          # garantit l'existence du sous-dossier plugins/
        self._open_folder(user_config_dir())

    def _reload_plugins_from_settings(self):
        self._plugin_loader.reload_all()
        self._refresh_plugins_settings()
        self._repaint_views()

    def _open_plugin_console(self):
        """Ouvre (ou ramene au premier plan) la console plugins : journal des
        logs/prints/erreurs, test sur la frame courante, diagnostic env, export
        debug VS Code."""
        from frameviewer.ui.plugin_console_dialog import PluginConsoleDialog
        dlg = getattr(self, "_plugin_console", None)
        if dlg is None:
            dlg = PluginConsoleDialog(self)
            self._plugin_console = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _open_plugin_help(self):
        """Ouvre l'aide plugin (hooks + api par theme, requis en vert)."""
        from frameviewer.ui.plugin_help_dialog import PluginHelpDialog
        dlg = getattr(self, "_plugin_help", None)
        if dlg is None:
            dlg = PluginHelpDialog(self)
            self._plugin_help = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _export_current_frame_for_debug(self):
        """Ecrit la frame courante (8-bit BGR affiche) dans le dossier debug/ DU
        PLUGIN selectionne (<plugin>/debug/frameNNNN.npy) et affiche la commande
        prete pour le runner. Le mode debug (terminal ou VS Code F5) proposera
        ensuite les frames de CE plugin."""
        import numpy as np
        if self._raw is None:
            QtWidgets.QMessageBox.information(
                self, "Export debug", "Aucune séquence ouverte.")
            return
        loader = getattr(self, "_plugin_loader", None)
        plugins = list(loader.enabled_plugins()) if loader is not None else []
        if not plugins:
            QtWidgets.QMessageBox.information(
                self, "Export debug",
                "Aucun plugin actif : coche « Actif » sur au moins un plugin dans "
                "l'onglet Plugins avant d'exporter une frame de debug.")
            return
        # plugin par defaut = celui selectionne dans l'onglet, s'il est actif.
        panel = getattr(self, "_plugins_panel", None)
        sel = panel.selected_lp() if panel is not None else None
        default_idx = next((i for i, lp in enumerate(plugins)
                            if sel is not None and lp.plugin_id == sel.plugin_id), 0)

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Debug plugin hors interface")
        v = QtWidgets.QVBoxLayout(dlg)
        row0 = QtWidgets.QHBoxLayout()
        row0.addWidget(QtWidgets.QLabel("Plugin à debugger :"))
        combo = QtWidgets.QComboBox()
        for lp in plugins:
            combo.addItem(lp.name, lp.plugin_id)
        combo.setCurrentIndex(default_idx)
        row0.addWidget(combo, 1)
        v.addLayout(row0)

        info = QtWidgets.QLabel()
        info.setWordWrap(True)
        v.addWidget(info)
        edit = QtWidgets.QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setStyleSheet("font-family:Consolas,monospace; font-size:12px;")
        edit.setMaximumHeight(80)
        v.addWidget(edit)
        v.addWidget(QtWidgets.QLabel(
            "Ou sans argument (le runner demande le plugin puis la frame), idem "
            "que la config VS Code « Debug plugin (frame courante) » (F5) :"))
        edit2 = QtWidgets.QPlainTextEdit("python -m frameviewer.plugins.debug_runner")
        edit2.setReadOnly(True)
        edit2.setStyleSheet("font-family:Consolas,monospace; font-size:12px;")
        edit2.setMaximumHeight(48)
        v.addWidget(edit2)

        state = {"cmd": "", "debug_dir": ""}

        def regen():
            lp = next((p for p in plugins if p.plugin_id == combo.currentData()), None)
            if lp is None:
                return
            debug_dir = os.path.join(lp.folder, "debug")
            os.makedirs(debug_dir, exist_ok=True)
            disp = self.process(self._apply_rotation(self._raw))   # 8-bit BGR affiche
            npy = os.path.join(debug_dir, f"frame{self.cur:04d}.npy")
            np.save(npy, disp)
            input_args = ""
            contract = (self._visible_contracts(lp.plugin_id) or [{}])[0]
            for nm, pth in contract.items():
                if pth:
                    input_args += f' --input "{nm}={pth}"'
            # chemin COMPLET du plugin (pas seulement le nom) : le runner debug
            # cible ainsi exactement le plugin utilise par l'app (a cote de l'exe
            # ou user space), jamais une autre copie re-resolue (ex. sources).
            cmd = (f'python -m frameviewer.plugins.debug_runner '
                   f'--plugin "{lp.folder}" --npy "{npy}" '
                   f'--frame-index {self.cur}{input_args}')
            state["cmd"], state["debug_dir"] = cmd, debug_dir
            info.setText(
                f"Frame enregistrée dans le dossier debug/ du plugin :\n{npy}\n\n"
                "Depuis le dossier FrameViewer (qui contient le package "
                "frameviewer/), commande directe :")
            edit.setPlainText(cmd)
            if loader is not None:
                loader.log(f"=== Export frame debug ({lp.name}) ===\nFrame -> {npy}")

        combo.currentIndexChanged.connect(lambda _=0: regen())
        regen()

        row = QtWidgets.QHBoxLayout()
        b_copy = QtWidgets.QPushButton("Copier la commande")
        b_copy.clicked.connect(
            lambda: QtWidgets.QApplication.clipboard().setText(state["cmd"]))
        row.addWidget(b_copy)
        b_open = QtWidgets.QPushButton("Ouvrir le dossier")
        b_open.clicked.connect(lambda: self._open_folder(state["debug_dir"]))
        row.addWidget(b_open)
        row.addStretch(1)
        b_close = QtWidgets.QPushButton("Fermer")
        b_close.clicked.connect(dlg.accept)
        row.addWidget(b_close)
        v.addLayout(row)
        dlg.resize(700, 320)
        dlg.exec()

    def _after_plugins_changed(self):
        self._refresh_plugins_settings()
        self._repaint_views()

    def _plugin_overlays_for(self, frame_idx):
        """Formes (dicts au schema des overlays SIDECAR) produites par les
        plugins actifs pour cette frame -- dessinees par le meme moteur
        que les calques SIDECAR (live + export), voir docs/plugins.md."""
        loader = getattr(self, "_plugin_loader", None)
        if loader is None:
            return []
        return loader.collect_overlays(frame_idx)

    def _plugin_patches_for(self, frame_idx, size):
        """Vignettes (ndarray BGR) produites par les plugins actifs pour
        cette frame, pretes pour composite_patches()."""
        loader = getattr(self, "_plugin_loader", None)
        if loader is None:
            return []
        return loader.collect_patches(frame_idx, size)

    def eventFilter(self, obj, ev):
        # filtre APPLICATIF (installe sur QApplication) : touche element_keys
        # d'un plugin actif -> bascule la case, quel que soit le widget focus,
        # SAUF quand on tape dans un champ texte (line edit, spin, editeur...).
        if ev.type() == QtCore.QEvent.KeyPress and self._app_key_toggle(ev):
            return True
        if (hasattr(self, "layer_list") and obj is self.layer_list
                and ev.type() == QtCore.QEvent.KeyPress
                and ev.key() in (Qt.Key_Delete, Qt.Key_Backspace)):
            self._delete_selected_layers()
            return True
        if (hasattr(self, "hist_list") and obj is self.hist_list
                and ev.type() == QtCore.QEvent.KeyPress
                and ev.key() in (Qt.Key_Delete, Qt.Key_Backspace)):
            # Suppr / Retour arriere / Maj+Suppr (le modificateur Maj ne
            # change pas la touche rapportee) : retire de l'historique la ou
            # les entrees selectionnees (sans toucher a la source ouverte).
            self._delete_history_selected()
            return True
        return super().eventFilter(obj, ev)

    def _rotate_box(self, x1, y1, x2, y2, orig_w, orig_h, degrees):
        """Transform box coords for rotation applied to the original image."""
        steps = (degrees // 90) % 4
        W, H = orig_w, orig_h
        for _ in range(steps):
            nx1 = H - 1 - y2
            ny1 = x1
            nx2 = H - 1 - y1
            ny2 = x2
            x1, y1, x2, y2 = nx1, ny1, nx2, ny2
            W, H = H, W
        return x1, y1, x2, y2

    def _draw_annotations(self, img, annots):
        """Dessine les boîtes d'annotation (délègue à draw_annotation_boxes)."""
        if img is None or not annots:
            return img
        if self._raw is not None:
            orig_h, orig_w = self._raw.shape[:2]
        else:
            orig_h, orig_w = img.shape[:2]
        s = getattr(self, "_fetch_scale", 1.0) or 1.0
        return draw_annotation_boxes(img, annots, orig_w, orig_h,
                                     rotation=self._rotation, scale=s)

    # ----- ouverture / drag&drop -----
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()]
        if not paths:
            return
        if len(paths) == 1 and os.path.isdir(paths[0]):
            self._open_dropped_folder(paths[0])
            return
        _is_annot = is_annotation_path if _HAS_ANNOT else (
            lambda p: os.path.splitext(p)[1].lower() in (".ver", ".txt"))
        if paths and all(_is_annot(p) for p in paths):
            # plusieurs .ver = plusieurs tracks superposés sur la séquence
            for p in paths:
                self._load_dropped_annotations(p)
        else:
            # fichier de données (CSV, Excel...) non reconnu nativement ->
            # association à une entrée de plugin (popup, vue courante), comme
            # sur une vue. Le reste part au traitement média habituel après une
            # dernière chance aux plugins legacy (accepts_drop/on_drop).
            data = [p for p in paths if self._is_plugin_data(p)]
            media = [p for p in paths if p not in data]
            for p in data:
                if not self._offer_plugin_drop(p):
                    self._plugin_loader.dispatch_drop(p)
            loader = getattr(self, "_plugin_loader", None)
            remaining = media
            if loader is not None:
                remaining = [p for p in media if not loader.dispatch_drop(p)]
            if remaining:
                self.open_paths(remaining)

    def _on_view_folder_dropped(self, pos: int, folder: str):
        """Dossier depose sur une vue (mono ou multi-vue) : meme logique que
        dropEvent (fond de fenetre), voir _open_dropped_folder."""
        w = self._split_canvas.widget_at(pos)
        if w is None:
            return
        if w is not self._primary_frame:
            self._on_activate_pos(pos)
        self._open_dropped_folder(folder)

    def _open_dropped_folder(self, folder):
        """Dossier depose (JAMAIS de recherche recursive dans les sous-
        dossiers) : ouvre en priorite les images trouvees DIRECTEMENT dans ce
        dossier (sequence), PUIS applique automatiquement les annotations
        .txt/.ver aussi presentes directement (2e etape, apres coup -- il
        faut les dimensions de l'image pour convertir les coordonnees YOLO
        normalisees). Si rien d'exploitable n'est trouve directement (tout
        est range dans des sous-dossiers style images/ + labels/), previent
        au lieu de ne rien faire silencieusement -- c'etait le bug rapporte :
        un dossier melangeant images ET .txt directement etait avant reconnu
        comme un dossier d'annotations PUR (is_annotation_path), qui tentait
        de charger les boites sans jamais ouvrir la sequence."""
        # dossier de .yuv bruts -> sequence YUV (dialogue geometrie/format)
        if list_files(folder, {".yuv"}):
            self.open_yuv(folder)
            return
        if not _HAS_ANNOT:
            self.open_folder(folder)
            return
        images, txts, vers = scan_dropped_folder(folder)
        if images:
            self.open_folder(folder, files=images)   # reutilise l'enumeration du scan
        if txts:
            self._load_dropped_annotations(folder)
        for vp in vers:
            self._load_dropped_annotations(vp)
        if not images and not txts and not vers:
            QtWidgets.QMessageBox.information(
                self, "Dossier déposé",
                "Aucune image ni annotation (.txt/.ver) trouvée directement "
                f"dans ce dossier (les sous-dossiers ne sont pas explorés) :\n{folder}")

    def open_paths(self, paths):
        paths = [p for p in paths if p]
        if not paths:
            return
        sidecar_paths = [p for p in paths if supports_path(p, "overlay_format")]
        media = [p for p in paths if p not in sidecar_paths]

        overlays = info = None
        if sidecar_paths:
            try:
                overlays, info = parse_overlay_sidecar(sidecar_paths[0])
            except Exception as ex:
                QtWidgets.QMessageBox.warning(self, "SIDECAR illisible", str(ex))
                overlays = info = None

        if media:
            self._open_media_list(media)
        elif overlays is not None and self.source is None:
            # SIDECAR seul, rien d'ouvert: on tente de retrouver la sequence
            seqp = find_sequence_for_sidecar(sidecar_paths[0]) if find_sequence_for_sidecar else None
            if seqp:
                self._open_media_list([seqp])
            else:
                self._pending = (overlays, info)
                self.sidecar_label.setText(
                    f"{len(overlays)} frames annotees (en attente de la sequence)")
                QtWidgets.QMessageBox.information(
                    self, "Sequence introuvable",
                    "SIDECAR charge. Depose aussi la sequence correspondante "
                    "(SPECIALIZED / video / dossier d'images).")
                return

        # SIDECAR depose alors qu'un media est deja ouvert -> on l'applique directement
        if overlays is not None and self.source is not None:
            self.apply_overlays(overlays, info)

    def _open_media_list(self, media):
        if len(media) == 1:
            p = media[0]
            if os.path.isdir(p):
                self.open_folder(p)
                return
            ext = os.path.splitext(p)[1].lower()
            if supports_path(p, "sequence_format"):
                self.open_specialized(p)
            elif ext == ".yuv":
                self.open_yuv(p)          # brut : dialogue geometrie/format
            elif ext == ".webp":
                self._open_webp(p)        # webp anime -> video, sinon image
            elif ext in IMAGE_EXTS:
                self.open_folder(os.path.dirname(os.path.abspath(p)), start_with=p)
            else:
                self.open_video(p)
        else:
            specialized_paths = [p for p in media if supports_path(p, "sequence_format")]
            imgs = [p for p in media if os.path.splitext(p)[1].lower() in IMAGE_EXTS]
            if specialized_paths:
                self.open_specialized(specialized_paths[0])
            elif imgs:
                self.set_source(ImageSequenceSource(natural_sort(imgs), label="(selection)"))
            else:
                self.open_video(media[0])

    def _open_webp(self, p):
        """Un .webp peut etre une image fixe OU une animation (webp 'video').
        On detecte l'animation en lisant 2 frames : si la 2e existe, on l'ouvre en
        flux video ; sinon on retombe sur l'affichage image (avec ses voisines)."""
        animated = False
        try:
            probe = VideoSource(p)
            animated = (probe.get(0) is not None and probe.get(1) is not None)
            probe.close()
        except Exception:
            animated = False
        if animated:
            self.open_video(p)
        else:
            self.open_folder(os.path.dirname(os.path.abspath(p)), start_with=p)

    def open_video(self, path):
        try:
            src = VideoSource(path)
        except Exception as ex:
            QtWidgets.QMessageBox.critical(self, "Erreur", str(ex))
            return
        self.set_source(src)

    def open_specialized(self, path):
        try:
            src = SpecializedSource(path)
        except Exception as ex:
            QtWidgets.QMessageBox.critical(self, "Erreur SPECIALIZED", str(ex))
            return
        self.set_source(src)

    @staticmethod
    def _parse_yuv_dims(name):
        """Devine (largeur, hauteur) depuis un nom du style
        'YUVframe.00001100-roi.1372.1032.yuv' : les deux derniers groupes de
        chiffres. (0, 0) si moins de deux groupes."""
        stem = os.path.splitext(name)[0]
        nums, cur = [], ""
        for ch in stem:
            if ch.isdigit():
                cur += ch
            elif cur:
                nums.append(int(cur)); cur = ""
        if cur:
            nums.append(int(cur))
        if len(nums) >= 2:
            return nums[-2], nums[-1]
        return 0, 0

    def open_yuv(self, path):
        """Ouvre un .yuv brut (ou son dossier) comme sequence : un fichier = une
        frame. Le brut n'ayant pas d'en-tete, on demande geometrie + format via
        YuvImportDialog (prerempli depuis le nom, valide contre la taille)."""
        folder = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
        files = list_files(folder, {".yuv"})
        if not files:
            QtWidgets.QMessageBox.warning(self, "YUV", f"Aucun .yuv dans:\n{folder}")
            return
        try:
            size = os.path.getsize(files[0])
        except OSError as ex:
            QtWidgets.QMessageBox.critical(self, "YUV", str(ex))
            return
        w, h = self._parse_yuv_dims(os.path.basename(files[0]))
        from frameviewer.ui.yuv_import_dialog import YuvImportDialog
        dlg = YuvImportDialog(self, size, w, h)
        if not dlg.exec():
            return
        w, h, fmt = dlg.result_params()
        label = os.path.basename(folder.rstrip("/\\")) or folder
        try:
            src = YuvSource(files, w, h, fmt, label=label, directory=folder)
        except Exception as ex:
            QtWidgets.QMessageBox.critical(self, "YUV", str(ex))
            return
        start = 0
        if not os.path.isdir(path):
            ap = os.path.abspath(path)
            for i, f in enumerate(files):
                if os.path.abspath(f) == ap:
                    start = i
                    break
        self.set_source(src, start_index=start)

    def open_folder(self, folder, start_with=None, files=None):
        # `files` deja enumere par l'appelant (ex. drop) -> on evite une 2e
        # enumeration du dossier. Sinon on liste via scandir (rapide, sans stat
        # par fichier -- crucial sur SMB / gros dossiers).
        if files is None:
            files = list_images(folder)
        if not files:
            QtWidgets.QMessageBox.warning(self, "Dossier vide",
                                          f"Aucune image (png/jpg/...) dans:\n{folder}")
            return
        label = os.path.basename(folder.rstrip("/\\")) or folder
        src = ImageSequenceSource(files, label=label, directory=folder)
        start = 0
        if start_with:
            ap = os.path.abspath(start_with)
            for i, f in enumerate(files):
                if os.path.abspath(f) == ap:
                    start = i
                    break
        self.set_source(src, start_index=start)

    def open_file_dialog(self):
        exts = " ".join("*" + e for e in sorted(VIDEO_EXTS | IMAGE_EXTS))
        media_patterns = [exts, "*.yuv"]
        filters = []
        if SEQUENCE_FORMAT_EXTS:
            seq_patterns = " ".join("*" + ext for ext in SEQUENCE_FORMAT_EXTS)
            media_patterns.append(seq_patterns)
            filters.append(f"Format de sequence ({seq_patterns})")
        if OVERLAY_FORMAT_EXTS:
            overlay_patterns = " ".join("*" + ext for ext in OVERLAY_FORMAT_EXTS)
            filters.append(f"Calques ({overlay_patterns})")
        filters.insert(0, f"Medias ({' '.join(media_patterns)})")
        filters.extend(("YUV brut (*.yuv)", "Tous les fichiers (*)"))
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Ouvrir un media", "",
            ";;".join(filters))
        if path:
            self.open_paths([path])

    def open_dir_dialog(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Ouvrir un dossier d'images")
        if not folder:
            return
        if list_files(folder, {".yuv"}):     # dossier de .yuv bruts -> sequence YUV
            self.open_yuv(folder)
        else:
            self.open_folder(folder)

    def set_source(self, src, start_index=0):
        self.stop()
        self._stop_annot_load()
        if self.source:
            self._add_to_history(self.source)
            self.source.close()
        self.source = src
        self._stop_prefetch()
        self._prefetch_cache.clear()
        self._extract_start = -1
        self._extract_end = -1
        if hasattr(self, "slider"):
            self.slider.set_extract_zone(-1, -1)
        if hasattr(self, "extract_lbl"):
            self.extract_lbl.setText("─")
        self.cur = 0
        self._raw = None
        self.overlays = {}
        self._annotations = {}
        self._annot_path = ""
        self._annot_track_next = 0
        self._layers = []
        self._rebuild_layer_list()
        self.view.set_overlays([])
        self.view.reset_view()
        self._roi = None
        self.tools_panel.roi_panel.clear()
        self.sidecar_label.setText("Aucun SIDECAR charge")
        self.sidecar_logs.clear()

        self.fps_spin.blockSignals(True)
        self.fps_spin.setValue(float(src.fps))
        self.fps_spin.blockSignals(False)

        n = src.count
        self.slider.blockSignals(True)
        self.slider.setMinimum(0)
        self.slider.setMaximum(max(0, n - 1))
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        self.frame_spin.blockSignals(True)
        self.frame_spin.setRange(0, max(0, n - 1))
        self.frame_spin.setValue(0)
        self.frame_spin.blockSignals(False)

        # configuration de l'histogramme selon le type de source
        self._configure_hist(src)

        self._rebuild_convert_menu()
        # audio : charger la source si video
        if self._audio_player is not None:
            if isinstance(src, VideoSource):
                self._audio_player.setSource(
                    QUrl.fromLocalFile(os.path.abspath(src.path)))
            else:
                self._audio_player.setSource(QUrl())
            self.son_btn.setEnabled(isinstance(src, VideoSource))

        self._setup_clicks_file()
        self._load_existing_clicks()
        self._enable_playback(True)
        self.setWindowTitle(f"FrameViewer - {src.name}")
        if hasattr(self, "_primary_frame"):
            self._primary_frame.set_name(src.name)
            self._primary_frame.mini.set_range(n)
        self.seek(start_index)
        self._add_to_history(src)     # ajoute le nouveau source (1ere seq ou apres ouverture)
        self._highlight_history()
        self.view.setFocus()          # les fleches gauche/droite agissent de suite
        self.status.showMessage(
            f"{src.name}  |  {n if n else '?'} frames  |  {src.fps:.2f} fps  |  clics -> {self.clicks_path}")

        # overlays SIDECAR deposes avant la sequence
        if self._pending is not None:
            ov, inf = self._pending
            self._pending = None
            self.apply_overlays(ov, inf)

        # mode temporel actif : le moteur a changé de flux -> re-cloner le 2e lecteur
        if (getattr(self, "_split_canvas", None) is not None
                and self._split_canvas.is_temporal()):
            self._ensure_temporal_source()
            self._sync_temporal(self.cur)

    def _configure_hist(self, src):
        """Règle domaine + fenêtre de l'histogramme selon le type de la source."""
        if src is None:
            self.hist.set_data_range(0, 255)
            self.hist.set_window(0, 255, emit=False)
            return
        if getattr(src, "is_raw", False):
            f0 = src.get(0)
            inten0 = to_intensity(f0) if f0 is not None else None
            if inten0 is not None and inten0.size:
                if np.issubdtype(inten0.dtype, np.integer):
                    info = np.iinfo(inten0.dtype)
                    dmin, dmax = float(info.min), float(info.max)
                else:
                    dmin, dmax = float(np.min(inten0)), float(np.max(inten0))
            else:
                dmin, dmax = 0.0, 255.0
            if dmax <= dmin:
                dmax = dmin + 1.0
            self.hist.set_data_range(dmin, dmax)
            lo, hi = auto_window_sigma(inten0) if inten0 is not None else (dmin, dmax)
            self.hist.set_window(lo, hi, emit=False)
        elif isinstance(src, ImageSequenceSource):
            # Detect 16-bit sequences: peek premiere frame pour connaitre le dtype
            f0 = src.get(0)
            inten0 = to_intensity(f0) if f0 is not None else None
            if inten0 is not None and inten0.size and inten0.dtype != np.uint8:
                if np.issubdtype(inten0.dtype, np.integer):
                    info = np.iinfo(inten0.dtype)
                    dmin, dmax = float(info.min), float(info.max)
                else:
                    dmin, dmax = float(np.min(inten0)), float(np.max(inten0))
                if dmax <= dmin:
                    dmax = dmin + 1.0
                self.hist.set_data_range(dmin, dmax)
                lo, hi = auto_window_sigma(inten0)
                self.hist.set_window(lo, hi, emit=False)
            else:
                self.hist.set_data_range(0, 255)
                self.hist.set_window(0, 255, emit=False)
        else:
            self.hist.set_data_range(0, 255)
            self.hist.set_window(0, 255, emit=False)

    # ----- overlays SIDECAR -----
    def apply_overlays(self, overlays, info, name="SIDECAR"):
        self.overlays = overlays or {}
        n = len(self.overlays)
        # calque SIDECAR (un seul, remplacé) dans le gestionnaire
        self._layers = [L for L in self._layers if L["kind"] != "sidecar"]
        if n:
            self._layers.insert(0, {"kind": "sidecar", "name": name,
                                    "key": "sidecar", "visible": True})
        self._rebuild_layer_list()
        # La dynamique <iddyn> du SIDECAR vise l'imagerie deja 8 bits du pipeline Java.
        # On ne l'applique que si elle recouvre vraiment la dynamique de la source
        # (sinon on garde l'auto-fenetre, indispensable pour l'IR uint16 brut).
        if info and "dynmin" in info and "dynmax" in info:
            d0, d1 = self.hist.full_range
            dn0, dn1 = float(info["dynmin"]), float(info["dynmax"])
            overlap = min(dn1, d1) - max(dn0, d0)
            if dn1 > dn0 and overlap > 0.2 * (d1 - d0):
                self.hist.set_window(dn0, dn1, emit=False)
        lut = (info or {}).get("lut", "")
        extra = f"  |  LUT SIDECAR: {lut}" if lut else ""
        self.sidecar_label.setText((f"{n} frames annotees" if n else "SIDECAR sans overlay") + extra)
        self._overlay_refresh()
        if n:
            # le panneau (calque SIDECAR / histo) s'ouvre tout seul au depot d'un SIDECAR
            self._show_right_tab(0)
            self.status.showMessage(f"Overlays SIDECAR appliques: {n} frames annotees.", 4000)

    def _overlay_refresh(self, *_):
        if self.source is None:
            self.view.set_overlays([])
            self._update_sidecar_logs([])
            self._set_multiview_overlays([])
            return
        graphs = []
        multiview = []
        # calques SIDECAR + formes plugin de la vue active : gouvernes par la case
        # "Afficher les overlays" (comportement historique inchange).
        if self.overlay_chk.isChecked():
            if self.overlays and self._layer_visible("sidecar", "sidecar"):
                key = self.cur + self.sidecar_offset.value()
                graphs = list(self.overlays.get(key, []))
            # formes plugin (meme schema/moteur -- voir docs/plugins.md) : on
            # separe la vue active (moteur) des formes multivue (ciblant une
            # autre vue ou segments inter-vues) qui passent par MultiViewOverlay.
            active, multiview = self._partition_plugin_overlays(
                self._plugin_overlays_for(self.cur))
            graphs = graphs + active
        self.view.set_overlays(graphs)
        self._update_sidecar_logs(graphs)
        self._set_multiview_overlays(self._multiview_specs(base_mv=multiview))

    def _multiview_specs(self, base_mv=None):
        """Formes de la couche multivue : formes plugin ciblant une vue /
        segments inter-vues issus de get_overlays (frame MOTEUR) + liens
        inter-vues du hook get_multiview_overlays (contexte de TOUTES les vues,
        actif par defaut des >= 2 vues). `base_mv` = formes deja partitionnees
        par l'appelant (evite un double calcul) ; None -> on les recalcule ici
        (utile quand une vue satellite bouge, sans re-rendre le moteur)."""
        if base_mv is not None:
            multiview = list(base_mv)
        else:
            multiview = []
            if self.source is not None and self.overlay_chk.isChecked():
                _, multiview = self._partition_plugin_overlays(
                    self._plugin_overlays_for(self.cur))
        ctx = self._views_context()
        if ctx.get("count", 0) >= 2:
            multiview = multiview + self._plugin_loader.collect_multiview_overlays(ctx)
        return multiview

    def _refresh_multiview_overlays(self):
        """Recalcule et repose UNIQUEMENT la couche multivue (liens inter-vues),
        sans re-rendre le moteur. Appele quand une vue satellite change de frame
        (son rendu ne passe pas par _display / _overlay_refresh)."""
        if self.source is None:
            return
        self._set_multiview_overlays(self._multiview_specs())

    @staticmethod
    def _partition_plugin_overlays(overlays):
        """Separe les formes plugin en (vue_active, multivue). Multivue = un
        segment inter-vues, ou une forme portant un `view` explicite (!=
        active). Retro-compatible : sans clef `view`, tout va a la vue active
        (comportement historique)."""
        active, multiview = [], []
        for g in overlays:
            if g.get("type") == "segment_inter_vues" or "view" in g:
                multiview.append(g)
            else:
                active.append(g)
        return active, multiview

    def _set_multiview_overlays(self, specs):
        sc = getattr(self, "_split_canvas", None)
        if sc is not None:
            sc.set_multiview_overlays(specs)

    # ----- navigation -----
    def show_index(self, idx):
        if self.source is None:
            return
        n = self.source.count
        if n > 0:
            idx = max(0, min(idx, n - 1))
        else:
            idx = max(0, idx)
        frame = self._prefetch_cache.pop(idx, None)
        if frame is None:
            frame = self.source.get(idx)
            if frame is None:
                return
            frame = self._apply_fetch_scale(frame)
        self._raw = frame
        self._prefetch_schedule(idx)
        self.cur = idx
        self._sync_temporal(idx)        # mode temporel : vue gauche à i-N (avant rendu)
        self._display()
        self._sync_view_frame(idx)      # vues liées : même frame partout
        if self._audio_player is not None:
            fps_src = getattr(self.source, "fps", self.fps_spin.value() or 25.0)
            self._audio_player.setPosition(int(idx * 1000.0 / max(fps_src, 0.001)))

    def _full_amplitude(self):
        """Amplitude max selon le type des donnees brutes (pleine plage) :
        entiers -> plage du type (8 bits 0..255, 16 bits 0..65535), flottant ->
        min/max de la frame."""
        raw = self._raw
        if raw is None:
            return self.hist.full_range
        dt = raw.dtype
        if np.issubdtype(dt, np.integer):
            info = np.iinfo(dt)
            return (float(info.min), float(info.max))
        inten = to_intensity(raw)
        if inten.size:
            return (float(np.min(inten)), float(np.max(inten)))
        return self.hist.full_range

    def _display(self):
        if self._raw is None:
            return
        inten = to_intensity(self._raw)
        mode = self.hist.per_frame_mode()
        # certains modes par frame redefinissent aussi le domaine de l'histogramme
        if mode == "Min/Max":
            self.hist.set_data_range(float(np.min(inten)), float(np.max(inten)))
        elif mode == "Pleine plage":
            self.hist.set_data_range(*self._full_amplitude())
        self.hist.set_histogram(inten)
        if not self._user_adjusting:
            if mode == "Auto":
                lo, hi = auto_window_sigma(inten)
                self.hist.set_window(lo, hi, emit=False)
            elif mode == "Min/Max":
                self.hist.set_window(float(np.min(inten)), float(np.max(inten)), emit=False)
            elif mode == "Pleine plage":
                self.hist.set_window(*self._full_amplitude(), emit=False)
        raw_rot = self._apply_rotation(self._raw)
        # source pour la sonde : intensité native après rotation (mêmes coords que l'affichage)
        try:
            self._probe_src = to_intensity(raw_rot)
        except Exception:
            self._probe_src = None
        # colorbar : LUT + fenêtre courantes
        if hasattr(self, "colorbar"):
            self.colorbar.set_lut(self.lut_combo.currentData())
            self.colorbar.set_range(self.hist.lo, self.hist.hi)
        out = self.process(raw_rot)
        if self._dyn_crop and self._roi is not None:
            _x, _y, _w, _h = self._roi
            _Hf, _Wf = out.shape[:2]
            _cx, _cy = max(0, int(_x)), max(0, int(_y))
            _cx2 = min(_Wf, _cx + int(_w))
            _cy2 = min(_Hf, _cy + int(_h))
            if _cx2 > _cx and _cy2 > _cy:
                out = out[_cy:_cy2, _cx:_cx2]
        if self._crosshair and out is not None:
            _ch, _cw = out.shape[:2]
            _col = (200, 200, 200) if out.ndim == 3 else 200
            _cx, _cy = _cw // 2, _ch // 2
            _sz = max(8, min(_cw, _ch) // 12)
            out = out.copy()
            cv2.line(out, (_cx - _sz, _cy), (_cx + _sz, _cy), _col, 1, cv2.LINE_AA)
            cv2.line(out, (_cx, _cy - _sz), (_cx, _cy + _sz), _col, 1, cv2.LINE_AA)
        # annotations .ver : gated par « Afficher les overlays » + visibilité calque
        if (self._annotations and out is not None
                and self.overlay_chk.isChecked()):
            frame_annots = self._visible_annots(self.cur)
            if frame_annots:
                out = self._draw_annotations(out, frame_annots)
        # vignettes plugin (hook render_patch) : baked dans les pixels, donc
        # propagees "gratuitement" a tout ce qui consomme _display_bgr
        # ensuite (composition multivue de la vue primaire notamment).
        if out is not None:
            hp, wp = out.shape[:2]
            patches = self._plugin_patches_for(
                self.cur, (max(1, wp // 4), max(1, hp // 4)))
            if patches:
                out = composite_patches(out, patches)
            # overlay "code" plein cadre (hook render_overlay) : compose dans
            # les pixels (suit le zoom, se propage au multivue/export). Pilote
            # par les cases par plugin/CSV/element, independant de overlay_chk.
            ov = self._plugin_loader.collect_overlay_image(self.cur, (wp, hp))
            if ov is not None:
                out = alpha_over(out, ov)
        self._display_bgr = out.copy() if out is not None else None
        self.view.set_frame(out)
        self._split_canvas.signal_primary_render(self._display_bgr)
        # apercu render_panel des cartes plugin depliees : suit la frame courante.
        _pp = getattr(self, "_plugins_panel", None)
        if _pp is not None:
            _pp.refresh_previews()
        # rafraîchi systématiquement (même en rendu dynamique/crop) : sinon la
        # visibilité par calque (case cochée/décochée du gestionnaire) peut
        # rester périmée et continuer d'afficher un calque SIDECAR masqué.
        self._overlay_refresh()
        if not self._dyn_crop:
            self._refresh_markers()
        self._apply_roi_track(self.cur)   # ROI suit la boîte .ver, si un suivi est actif
        if self._roi is not None:
            self.update_roi_panel()
        self.slider.blockSignals(True)
        self.slider.setMaximum(max(self.slider.maximum(), self.cur, self.source.count - 1))
        self.slider.setValue(self.cur)
        self.slider.blockSignals(False)
        self._update_label()

    # ----- selection rectangulaire (ROI) -----
    def on_roi(self, x, y, w, h):
        # un rectangle tracé à la main (glisser) est une ROI « libre » :
        # elle annule un éventuel suivi de boîte .ver en cours.
        self._roi_track_id = None
        if w <= 0 or h <= 0:
            self._roi = None
            self._dyn_crop = False
            self.tools_panel.roi_panel.clear()
            if self._raw is not None:
                self._display()
            return
        self._roi = (x, y, w, h)
        # afficher Outils TI avec l'onglet ROI quand on dessine un rectangle
        if self.tools_dock.isHidden():
            self.tools_dock.show()
            self.tools_dock.raise_()
        # switcher automatiquement sur l'outil ROI dans le panneau
        if not self.tools_panel.is_roi_active():
            self.tools_panel._set_tool("roi")
            self.view.set_tool_mode("roi")
        self.update_roi_panel()

    def update_roi_panel(self):
        if self._roi is None or self._raw is None:
            return
        x, y, w, h = self._roi
        raw_rot = self._apply_rotation(self._raw)
        inten = to_intensity(raw_rot)
        H, W = inten.shape[:2]
        x = max(0, min(int(x), W - 1))
        y = max(0, min(int(y), H - 1))
        x2 = min(W, x + int(w))
        y2 = min(H, y + int(h))
        if x2 <= x or y2 <= y:
            self.tools_panel.roi_panel.clear()
            return
        inten_crop = inten[y:y2, x:x2]
        disp = self.process(raw_rot)
        bgr_crop = disp[y:y2, x:x2] if disp is not None else None
        self.tools_panel.roi_panel.update_crop(bgr_crop, inten_crop)

    def refresh_frame(self):
        if self.source is not None and self._raw is not None:
            self._user_adjusting = True
            try:
                self._display()
            finally:
                self._user_adjusting = False
        # propager UNIQUEMENT les réglages globaux (LUT / filtres) aux autres vues :
        # la fenêtre lo/hi de l'histogramme ne concerne que la vue sélectionnée.
        self._split_canvas.update_sat_render(
            self.lut_combo.currentData(),
            self._cube_lut, self._cube_size,
            self.hist.full_range, self._filter_flags())

    def seek(self, idx):
        self.show_index(idx)

    def step(self, d):
        if self.source is None:
            return
        self.stop()
        self.show_index(self.cur + d)

    def _go_last(self):
        if self.source and self.source.count > 0:
            self.seek(self.source.count - 1)

    def _update_label(self):
        n = self.source.count
        fps = self.fps_spin.value() or 25.0
        total = (n - 1) if n > 0 else self.cur
        self.frame_label.setText(
            f"/ {total}   |   {fmt_time(self.cur / fps)} / {fmt_time(total / fps)}")
        self.frame_spin.blockSignals(True)
        self.frame_spin.setMaximum(max(0, total))
        self.frame_spin.setValue(self.cur)
        self.frame_spin.blockSignals(False)
        # mini-barre de la vue principale (multivue)
        if hasattr(self, "_primary_frame"):
            mb = self._primary_frame.mini
            mb.set_range(n)
            mb.set_pos(self.cur)
            mb.set_info(self.cur, total, fps)

    def _frame_spin_done(self):
        self.seek(int(self.frame_spin.value()))
        self.view.setFocus()

    # ----- lecture -----
    def toggle_play(self):
        if self.timer.isActive():
            self.stop()
        else:
            self.play()

    def play(self):
        if self.source is None:
            return
        if self._link_views and not self._split_canvas.is_temporal():
            start = self._link_start_frames[self._split_canvas.active_pos()]
            _lower, upper = self._link_base_bounds()
            if self.cur - start >= upper:
                self._apply_link_base(0)
        elif self.source.count > 0 and self.cur >= self.source.count - 1:
            self.seek(0)
        fps = self.fps_spin.value() or 25.0
        self._last_tick_t = 0.0
        self._fps_est = 0.0
        self._fps_tick_n = 0
        self.timer.start(max(1, int(round(1000.0 / fps))))
        self.play_btn.setText("Pause")
        if hasattr(self, "_primary_frame"):
            self._primary_frame.mini.set_playing(True)
        if self._audio_player is not None and self.son_btn.isChecked():
            fps_src = getattr(self.source, "fps", fps)
            pos_ms = int(self.cur * 1000.0 / max(fps_src, 0.001))
            self._audio_player.setPosition(pos_ms)
            self._audio_player.play()

    def stop(self):
        self.timer.stop()
        self.play_btn.setText("Lecture")
        self._last_tick_t = 0.0
        self.fps_label.setText("")
        if hasattr(self, "_primary_frame"):
            self._primary_frame.mini.set_playing(False)
        if self._audio_player is not None:
            self._audio_player.pause()

    def _on_tick(self):
        t0 = _time.perf_counter()
        if self.source is None:
            self.stop()
            return
        nxt = self.cur + 1
        n = self.source.count
        _in_extract = (self._extract_start >= 0
                       and self._extract_end > self._extract_start
                       and self.loop_btn.isChecked())
        if _in_extract and nxt > self._extract_end:
            nxt = self._extract_start
        elif self._link_views and not self._split_canvas.is_temporal():
            start = self._link_start_frames[self._split_canvas.active_pos()]
            _lower, upper = self._link_base_bounds()
            if nxt - start > upper:
                if self.loop_btn.isChecked():
                    nxt = start
                else:
                    self._apply_link_base(upper)
                    self.stop()
                    return
        elif n > 0 and nxt >= n:
            if self.loop_btn.isChecked():
                nxt = 0
            else:
                self.show_index(n - 1)
                self.stop()
                return
        frame = self._prefetch_cache.pop(nxt, None)
        if frame is None:
            frame = self.source.get(nxt)
            if frame is None:
                self.stop()
                return
            frame = self._apply_fetch_scale(frame)
        self._raw = frame
        self._prefetch_schedule(nxt)
        self.cur = nxt
        self._sync_temporal(nxt)        # mode temporel : vue gauche à i-N (avant rendu)
        self._display()
        self._sync_view_frame(nxt)      # vues liées : même frame partout, AUSSI en lecture
        # mesure fps reel (intervalle inter-frame)
        if self._last_tick_t > 0:
            dt = t0 - self._last_tick_t
            inst = 1.0 / max(dt, 1e-9)
            self._fps_est = 0.8 * self._fps_est + 0.2 * inst if self._fps_est > 0 else inst
            self._fps_tick_n += 1
            if self._fps_tick_n >= 8:
                self._fps_tick_n = 0
                target = self.fps_spin.value() or 1.0
                ratio = self._fps_est / target
                txt = f"→ {self._fps_est:.1f}"
                if ratio < 0.7:
                    col = "#e05050"
                    self.fps_spin.setToolTip(
                        f"Max atteint : {self._fps_est:.1f} fps"
                        f" (cible {target:.1f} fps inaccessible)\n"
                        "Cause probable : lecture reseau/SSH lente.")
                elif ratio < 0.9:
                    col = "#e0a020"
                    self.fps_spin.setToolTip(
                        f"FPS reel : {self._fps_est:.1f} / cible {target:.1f}")
                else:
                    col = "#50c060"
                    self.fps_spin.setToolTip("Cadence de lecture cible")
                self.fps_label.setText(txt)
                self.fps_label.setStyleSheet(f"color: {col};")
        self._last_tick_t = t0

    def _fps_changed(self, _):
        fps = self.fps_spin.value() or 25.0
        self._last_tick_t = 0.0
        self._fps_est = 0.0
        self.fps_label.setText("")
        self.fps_spin.setToolTip("Cadence de lecture cible (toutes les vues)")
        if self.timer.isActive():
            self.timer.start(max(1, int(round(1000.0 / fps))))
        self._bcast_fps(fps)

    # ----- slider -----
    def _slider_pressed(self):
        self._slider_user = True
        self._was_playing = self.timer.isActive()
        if self._was_playing:
            self.stop()

    def _slider_changed(self, val):
        if self._slider_user:
            self.show_index(val)

    def _slider_released(self):
        self._slider_user = False
        self.show_index(self.slider.value())
        if self._was_playing:
            self.play()

    # ----- LUT / rendu -----
    def process(self, frame):
        if frame is None:
            return None
        cube = None  # LUT .cube supprimee de l'UI
        try:
            raw = frame
            # Soustraction temporelle |N - (N-k)|
            if self._temp_sub_on and self.source is not None:
                k = max(1, self._temp_sub_k)
                ref_idx = max(0, self.cur - k)
                ref_raw = self.source.get(ref_idx)
                if ref_raw is not None:
                    a = to_intensity(raw).astype(np.float32)
                    b = to_intensity(ref_raw).astype(np.float32)
                    if a.shape == b.shape:
                        raw = np.abs(a - b).astype(np.float32)
            return render_frame(raw, self.hist.lo, self.hist.hi,
                                self.lut_combo.currentData(), cube, self._cube_size,
                                self.hist.full_range, self._filter_flags())
        except Exception:
            return frame

    # ----- export « tel qu'affiché » (LUT/contraste + calques .ver/SIDECAR) -----
    def _export_frame_as_viewed(self, idx):
        """Rend la frame `idx` de la vue active EXACTEMENT comme à l'écran :
        rotation + LUT/contraste/filtres (process) + boîtes .ver + calques SIDECAR
        visibles, indépendamment de la frame réellement affichée."""
        if self.source is None:
            return None
        frame = self.source.get(idx)
        if frame is None:
            return None
        orig_h, orig_w = frame.shape[:2]
        raw_rot = self._apply_rotation(frame)
        out = self.process(raw_rot)
        if out is None:
            return None
        if out.ndim == 2:
            out = cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)
        if self._annotations and self.overlay_chk.isChecked():
            frame_annots = self._visible_annots(idx)
            if frame_annots:
                s = getattr(self, "_fetch_scale", 1.0) or 1.0
                out = draw_annotation_boxes(out, frame_annots, orig_w, orig_h,
                                            rotation=self._rotation, scale=s)
        graphs = []
        if (self.overlay_chk.isChecked() and self.overlays
                and self._layer_visible("sidecar", "sidecar")):
            key = idx + self.sidecar_offset.value()
            graphs = list(self.overlays.get(key, []))
        if self.overlay_chk.isChecked():
            # l'export d'une frame ne peut cuire que les formes de CETTE vue ;
            # les formes multivue / segments inter-vues n'ont pas de sens dans
            # une image unique et sont donc ignorees ici.
            active, _mv = self._partition_plugin_overlays(self._plugin_overlays_for(idx))
            graphs = graphs + active
        if graphs:
            out = bake_sidecar_overlays(out, graphs)
        hp, wp = out.shape[:2]
        patches = self._plugin_patches_for(idx, (max(1, wp // 4), max(1, hp // 4)))
        if patches:
            out = composite_patches(out, patches)
        # overlay "code" plein cadre : fait partie de l'image, donc cuit aussi
        # a l'export (les hover/clic n'ont pas de sens ici, ignores).
        ov = self._plugin_loader.collect_overlay_image(idx, (wp, hp))
        if ov is not None:
            out = alpha_over(out, ov)
        return out

    def on_cube_toggle(self):
        if self.cube_btn.isChecked():
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Charger un LUT .cube", "", "LUT 3D (*.cube)")
            if not path:
                self.cube_btn.setChecked(False)
                return
            try:
                self._cube_lut, self._cube_size = load_cube(path)
            except Exception as ex:
                self._cube_lut = None
                self.cube_btn.setChecked(False)
                self.cube_btn.setText(".cube")
                QtWidgets.QMessageBox.warning(self, "LUT .cube invalide", str(ex))
                return
            self.cube_btn.setText(os.path.basename(path))
            self.status.showMessage(f"LUT chargee: {os.path.basename(path)} (taille {self._cube_size})", 4000)
        else:
            self.cube_btn.setText(".cube")
        self.refresh_frame()

    # ----- conversion (tout type -> tout autre type, menu deroulant) -----
    def _convert_targets(self):
        """Cibles disponibles pour la source courante : liste de (mode, libelle).
        On convertit un type vers les AUTRES types existants."""
        src = self.source
        specialized_ok = SpecializedWriter is not None
        if isinstance(src, SpecializedSource):
            return [("png8", "Dossier d'images PNG (8 bits, rendu)"),
                    ("png16", "Dossier d'images PNG (16 bits, brut)"),
                    ("mp4", "Video MP4")]
        if isinstance(src, VideoSource):
            t = []
            src_ext = os.path.splitext(src.path)[1].lower()
            if src_ext not in ('.mp4', '.m4v'):
                t.append(("mp4", f"Vidéo MP4  (re-encodé depuis {src_ext[1:].upper()})"))
            t.append(("png8", "Dossier d'images PNG"))
            if specialized_ok:
                t.append(("specialized", "Fichier SPECIALIZED (intensite)"))
            return t
        if isinstance(src, ImageSequenceSource):
            # Detecte si la sequence est 16-bit
            raw0 = src.get(0) if src.count > 0 else None
            is16 = (raw0 is not None and
                    to_intensity(raw0).dtype in (np.uint16, np.int16, np.float32))
            t = [("mp4",
                  "Video MP4  (rendu 3-sigma -> 8 bits)" if is16
                  else "Video MP4")]
            if is16:
                t.append(("png16", "Dossier PNG 16 bits  (donnees brutes)"))
            if specialized_ok:
                t.append(("specialized", "Fichier SPECIALIZED (intensite brute)"))
            return t
        return []

    def _source_spec(self):
        """(src_kind, src_arg) pour rouvrir la source dans le thread de conversion."""
        src = self.source
        if isinstance(src, SpecializedSource):
            return ("specialized", src.path)
        if isinstance(src, VideoSource):
            return ("video", src.path)
        if isinstance(src, ImageSequenceSource):
            return ("images", list(src.paths))
        return (None, None)

    # ----- composition multi-vues (assemble un MP4 de toute la disposition) -----
    def _compose_grid_frame(self, idx):
        """Compose une image unique reproduisant la disposition actuelle du
        SplitCanvas (grille 2/3/4 vues ou fusion), à partir du rendu « tel
        qu'affiché » (calques + contraste) de chaque vue, pour la frame `idx`."""
        sc = self._split_canvas

        def frame_of(w):
            if w is self._primary_frame:
                return self._export_frame_as_viewed(idx) if self.source is not None else None
            return w.render_as_viewed(idx) if w.has_source() else None

        mode = sc.mode()
        if mode == "fusion":
            if not sc._order:
                return None
            if len(sc._order) < 2:
                return frame_of(sc._order[0])
            f0, f1 = frame_of(sc._order[0]), frame_of(sc._order[1])
            fus = sc._fusion
            alpha = fus._alpha if fus is not None else 0.5
            bmode = fus._mode if fus is not None else "alpha"
            return blend_frames(f0, f1, alpha, bmode)

        rows, cols, n = sc.MODES[mode]
        tiles = [frame_of(w) for w in sc._order[:n]]
        if not any(t is not None for t in tiles):
            return None
        ref = next(t for t in tiles if t is not None)
        th, tw = ref.shape[:2]
        canvas = np.zeros((th * rows, tw * cols, 3), dtype=np.uint8)
        for i in range(rows * cols):
            t = tiles[i] if i < len(tiles) else None
            if t is None:
                continue
            r, c = divmod(i, cols)
            tt = t if t.ndim == 3 else cv2.cvtColor(t, cv2.COLOR_GRAY2BGR)
            if tt.shape[:2] != (th, tw):
                tt = cv2.resize(tt, (tw, th), interpolation=cv2.INTER_AREA)
            canvas[r * th:(r + 1) * th, c * tw:(c + 1) * tw] = tt
        return canvas

    def _convert_all_views(self):
        """Assemble un MP4 combinant toutes les vues visibles (disposition
        actuelle : 2/3/4 vues ou fusion), calques + contraste inclus, sur
        toute la longueur de séquence de la vue principale. Reproduit le même
        découpage que celui affiché dans le viewer (utile pour présentation)."""
        if self._split_canvas.mode() == "1":
            return   # bouton réutilisé (vue unique) : conversion normale via menu
        if self.source is None:
            QtWidgets.QMessageBox.information(
                self, "Convertir (toutes les vues)",
                "Aucune source sur la vue principale.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Exporter la composition multi-vues...",
            "multiview.mp4", "Vidéo MP4 (*.mp4)")
        if not path:
            return
        n = self.source.count
        default_fps = float(self.fps_spin.value() or getattr(self.source, "fps", 25.0) or 25.0)
        fps, ok = QtWidgets.QInputDialog.getDouble(
            self, "Cadence de sortie", "Cadence MP4 (fps) :",
            default_fps, 0.1, 480.0, 2)
        if not ok:
            return
        dlg = QtWidgets.QProgressDialog(
            "Assemblage des vues...", "Annuler", 0, max(1, n), self)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        writer = None
        written = 0
        try:
            for idx in range(n):
                frame = self._compose_grid_frame(idx)
                if frame is not None:
                    if writer is None:
                        h, w = frame.shape[:2]
                        writer = cv2.VideoWriter(
                            path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
                    writer.write(frame)
                    written += 1
                dlg.setValue(idx + 1)
                QtWidgets.QApplication.processEvents()
                if dlg.wasCanceled():
                    break
        finally:
            if writer is not None:
                writer.release()
            dlg.close()
        if written:
            self.statusBar().showMessage(
                f"Composition multi-vues exportée ({written} frames) → {path}", 6000)
        else:
            self.statusBar().showMessage("Aucune frame composée (vues vides ?).", 4000)

    def _rebuild_convert_menu(self):
        self.convert_menu.clear()
        targets = self._convert_targets()
        name = self.source.name if self.source else "?"
        # titre non cliquable rappelant la source convertie
        head = self.convert_menu.addAction(f"Source : {name}")
        head.setEnabled(False)
        self.convert_menu.addSeparator()
        for mode, label in targets:
            act = self.convert_menu.addAction(label)
            act.triggered.connect(lambda checked=False, m=mode: self._start_convert(m))
        # En multivue, le bouton est piloté par _set_split ("Convertir toutes
        # les vues", pas de menu) : ne pas écraser son texte/visibilité ici,
        # sinon chaque changement de vue active (qui appelle _rebuild_convert_menu
        # via _engine_import) le fait revenir au style vue-unique.
        if self._split_canvas.mode() != "1":
            return
        # En vue unique, le bouton du bas reste caché : redondant avec le
        # bouton « ⇄ Convertir » de la vue elle-même (bandeau, en haut).
        self.convert_btn.setVisible(False)
        short = (name[:18] + "…") if len(name) > 19 else name
        self.convert_btn.setText(f"Convertir : {short}")
        self.convert_btn.setToolTip(
            f"Convertit la vue sélectionnée « {name} » vers un autre format")

    def _start_convert(self, mode):
        if self.source is None:
            return
        dlg = ConvertDialog(self, self.source.directory, self.source.stem, mode)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        out_path, mode, step, fps = dlg.values()
        if not out_path:
            return
        src_kind, src_arg = self._source_spec()
        if src_kind is None:
            return
        cube = None  # LUT .cube supprimee de l'UI
        worker = ConvertWorker(src_kind, src_arg, out_path, mode,
                               self.hist.lo, self.hist.hi, self.lut_combo.currentData(),
                               cube, self._cube_size, step, self.hist.full_range,
                               self._filter_flags(), fps)
        labels = {"png8": "PNG 8 bits", "png16": "PNG 16 bits", "mp4": "MP4", "specialized": "SPECIALIZED"}
        label = labels.get(mode, mode)
        src_name = self.source.name
        out_base = os.path.basename(out_path)
        # Entrée dans le dock de log
        conv_id = self._conv_id_seq
        self._conv_id_seq += 1
        log_text = f"[{label}]  {src_name}  →  {out_path}  |  En cours..."
        log_item = QtWidgets.QListWidgetItem(log_text)
        self.convlog_list.insertItem(0, log_item)
        self._conv_workers[conv_id] = {"worker": worker, "item": log_item,
                                        "src": src_name, "out": out_path, "mode": label}
        if not (self.right_dock.isVisible()
                and self.right_tab.currentIndex() == 1):
            self._show_right_tab(1)
        worker.progress.connect(
            lambda c, t, cid=conv_id: self._conv_progress(cid, c, t))
        worker.finished_ok.connect(
            lambda d, k, cid=conv_id: self._convert_done(cid, d, k))
        worker.failed.connect(
            lambda m, cid=conv_id: self._convert_failed(cid, m))
        worker.start()
        self.status.showMessage(
            f"Conversion [{label}] {src_name} → {out_path} démarrée", 5000)

    def _conv_progress(self, conv_id, cur, total):
        entry = self._conv_workers.get(conv_id)
        if entry is None:
            return
        pct = int(100 * cur / total) if total > 0 else 0
        bar = ("█" * (pct // 5)).ljust(20, "░")
        entry["item"].setText(
            f"[{entry['mode']}]  {entry['src']}  →  {entry['out']}\n"
            f"  {bar}  {pct}%  ({cur}/{total})")

    def _convert_done(self, conv_id, out_path, n):
        entry = self._conv_workers.get(conv_id)
        if entry:
            entry["item"].setText(
                f"✓  [{entry['mode']}]  {entry['src']}  →  {entry['out']}\n"
                f"  Terminé : {n} frames écrites")
        self.status.showMessage(
            f"Conversion terminée : {n} frames → {out_path}", 8000)

    def _convert_failed(self, conv_id, msg):
        entry = self._conv_workers.get(conv_id)
        if entry:
            status = "Annulée" if "interrompue" in msg.lower() else f"Erreur: {msg}"
            entry["item"].setText(
                f"✗  [{entry['mode']}]  {entry['src']}  →  {entry['out']}\n"
                f"  {status}")
        self.status.showMessage(f"Conversion: {msg}", 8000)

    def _convlog_clear_done(self):
        """Retire du dock les entrées terminées/en erreur."""
        running_ids = {cid for cid, e in self._conv_workers.items()
                       if e["worker"].isRunning()}
        to_remove = []
        for cid, e in self._conv_workers.items():
            if cid not in running_ids:
                row = self.convlog_list.row(e["item"])
                if row >= 0:
                    self.convlog_list.takeItem(row)
                to_remove.append(cid)
        for cid in to_remove:
            del self._conv_workers[cid]

    # ----- clics -----
    def _setup_clicks_file(self):
        base = self.source.stem or "media"
        self.clicks_path = os.path.join(self.source.directory, f"{base}_clicks.txt")

    def _load_existing_clicks(self):
        self.clicks = []
        if self.clicks_path and os.path.isfile(self.clicks_path):
            try:
                with open(self.clicks_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or line.lower().startswith("frame"):
                            continue
                        parts = line.replace(";", ",").split(",")
                        if len(parts) >= 3:
                            try:
                                self.clicks.append((int(float(parts[0])),
                                                    int(float(parts[1])),
                                                    int(float(parts[2]))))
                            except ValueError:
                                continue
            except Exception:
                pass

    def _rec_toggled(self, on):
        self.rec_btn.setText("REC clics  [ON]" if on else "REC clics")
        if on and self.clicks_path:
            self.status.showMessage(f"Enregistrement actif. Clique l'image -> {self.clicks_path}", 4000)

    def on_click(self, x, y):
        # forward aux plugins "overlay code" (hook on_view_click) : coords image
        if self.source is not None and x >= 0 and y >= 0:
            self._plugin_loader.dispatch_click(self.cur, x, y)
        # clic ponctuel (sans glisser) avec l'outil ROI actif : démarre/annule
        # le suivi d'une boîte .ver sous le curseur.
        if self.source is not None and self.tools_panel.is_roi_active():
            tid = self._annot_track_at(x, y)
            if tid is not None:
                self._roi_track_id = tid
                self._apply_roi_track(self.cur)
                self.statusBar().showMessage(
                    f"Suivi ROI activé sur le track {tid}.", 3000)
            elif self._roi_track_id is not None:
                self._roi_track_id = None
                self.statusBar().showMessage("Suivi ROI annulé.", 2000)
        if self.source is None or not self.rec_btn.isChecked():
            return
        self.clicks.append((self.cur, x, y))
        self._append_click(self.cur, x, y)
        self._refresh_markers()
        self.status.showMessage(f"Clic: frame={self.cur}, x={x}, y={y}  ->  {self.clicks_path}", 4000)

    def _append_click(self, fr, x, y):
        try:
            new = (not os.path.isfile(self.clicks_path)) or os.path.getsize(self.clicks_path) == 0
            with open(self.clicks_path, "a", encoding="utf-8") as f:
                if new:
                    f.write(f"# FrameViewer - clics pour {self.source.name}\nframe,x,y\n")
                f.write(f"{fr},{x},{y}\n")
        except Exception as ex:
            QtWidgets.QMessageBox.warning(self, "Ecriture impossible", str(ex))

    def _rewrite_file(self):
        if not self.clicks_path:
            return
        try:
            with open(self.clicks_path, "w", encoding="utf-8") as f:
                f.write(f"# FrameViewer - clics pour {self.source.name}\nframe,x,y\n")
                for (fr, x, y) in self.clicks:
                    f.write(f"{fr},{x},{y}\n")
        except Exception as ex:
            QtWidgets.QMessageBox.warning(self, "Ecriture impossible", str(ex))

    def undo_click(self):
        if not self.clicks:
            return
        self.clicks.pop()
        self._rewrite_file()
        self._refresh_markers()
        self.status.showMessage("Dernier clic annule.", 2000)

    def clear_clicks(self):
        if not self.clicks:
            return
        if QtWidgets.QMessageBox.question(
                self, "Effacer", "Effacer tous les clics enregistres ?") != QtWidgets.QMessageBox.Yes:
            return
        self.clicks = []
        self._rewrite_file()
        self._refresh_markers()
        self.status.showMessage("Clics effaces.", 2000)

    def save_clicks_as(self):
        if not self.clicks_path:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Enregistrer les clics sous...", self.clicks_path, "Texte (*.txt)")
        if path:
            self.clicks_path = path
            self._rewrite_file()
            self.status.showMessage(f"Clics -> {path}", 3000)

    def _refresh_markers(self):
        pts = [(x, y, i) for i, (fr, x, y) in enumerate(self.clicks) if fr == self.cur]
        self.view.set_markers(pts)

    def closeEvent(self, e):
        self._save_dock_layout()
        self._stop_prefetch()
        if self._annot_load_thread is not None:
            t = self._annot_load_thread
            t.stop()
            t.wait(2000)
            self._annot_load_thread = None
        self.stop()
        for entry in self._conv_workers.values():
            w = entry["worker"]
            if w.isRunning():
                w.stop()
                w.wait(3000)
        if self.source:
            self.source.close()
        super().closeEvent(e)
