"""Interactive, action-driven onboarding for FrameViewer."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from frameviewer.tutorial_data import is_tutorial_source, stage_tutorial_data


@dataclass(frozen=True)
class TutorialStep:
    chapter: str
    title: str
    body: str | Callable[[], str]
    target: Callable[[], Optional[QtWidgets.QWidget]]
    prepare: Optional[Callable[[], None]] = None
    ready: Optional[Callable[[], bool]] = None
    ready_text: str = "Action attendue dans l'interface"
    progress: Optional[Callable[[], list[tuple[str, bool]]]] = None


class TutorialFileList(QtWidgets.QListWidget):
    """Small file-browser facsimile that performs real Qt URL drags."""

    PATH_ROLE = Qt.UserRole + 31
    DRAG_ENABLED_ROLE = Qt.UserRole + 32

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("tutorial_file_list")
        self.setDragEnabled(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setIconSize(QtCore.QSize(28, 28))
        self.setStyleSheet(
            "QListWidget{background:#17171c;border:1px solid #4a4a55;}"
            "QListWidget::item{padding:9px;}"
            "QListWidget::item:selected{background:#6b4518;color:white;}"
        )

    def add_path(self, label: str, path: Path, *, drag_enabled: bool = True):
        item = QtWidgets.QListWidgetItem(
            self.style().standardIcon(QtWidgets.QStyle.SP_DirIcon), label
        )
        item.setData(self.PATH_ROLE, str(path))
        item.setData(self.DRAG_ENABLED_ROLE, drag_enabled)
        item.setToolTip(str(path))
        if not drag_enabled:
            item.setFlags(item.flags() & ~Qt.ItemIsDragEnabled)
            item.setForeground(QtGui.QColor("#777780"))
        self.addItem(item)
        return item

    def set_drag_enabled(self, item, enabled: bool) -> None:
        item.setData(self.DRAG_ENABLED_ROLE, enabled)
        item.setFlags(
            item.flags() | Qt.ItemIsDragEnabled
            if enabled else item.flags() & ~Qt.ItemIsDragEnabled
        )
        item.setForeground(QtGui.QColor("#ededf2" if enabled else "#777780"))

    def startDrag(self, supported_actions):
        item = self.currentItem()
        if item is None or not item.data(self.DRAG_ENABLED_ROLE):
            return
        mime = self.mime_for_item(item)
        drag = QtGui.QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(item.icon().pixmap(28, 28))
        drag.exec(Qt.CopyAction)

    def mime_for_item(self, item):
        mime = QtCore.QMimeData()
        mime.setUrls([QtCore.QUrl.fromLocalFile(item.data(self.PATH_ROLE))])
        return mime


class TutorialExplorer(QtWidgets.QDialog):
    def __init__(self, workspace: Path, parent=None):
        super().__init__(parent, Qt.Tool)
        self.workspace = workspace
        self.setObjectName("tutorial_explorer")
        self.setWindowTitle("Fichiers du tutoriel")
        self.setModal(False)
        self.resize(310, 310)
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("Glisse les dossiers vers la vue indiquee")
        title.setStyleSheet("font-weight:bold;color:#ffad42;")
        layout.addWidget(title)
        self.list = TutorialFileList()
        self._rgb = self.list.add_path("Trafic RGB", workspace / "traffic_rgb")
        self._ir = self.list.add_path(
            "Trafic IR (disponible a l'etape multivue)",
            workspace / "traffic_ir",
            drag_enabled=False,
        )
        self._annotations = QtWidgets.QListWidgetItem(
            self.style().standardIcon(QtWidgets.QStyle.SP_DirIcon),
            "Annotations YOLO",
        )
        self._annotations.setData(
            TutorialFileList.PATH_ROLE, str(workspace / "annotations_yolo")
        )
        self._annotations.setData(TutorialFileList.DRAG_ENABLED_ROLE, True)
        self._annotations.setToolTip(str(workspace / "annotations_yolo"))
        self._showcase_csv = self.list.add_path(
            "CSV boites du plugin", workspace / "showcase_boxes.csv"
        )
        self.list.takeItem(self.list.row(self._showcase_csv))
        self._kpts_csv = self.list.add_path(
            "CSV correspondances multivues", workspace / "multiview_keypoints.csv"
        )
        self.list.takeItem(self.list.row(self._kpts_csv))
        layout.addWidget(self.list, 1)
        hint = QtWidgets.QLabel(
            "Ce panneau simule un explorateur. Le depot utilise le meme chemin "
            "que depuis l'explorateur du systeme."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#a8a8b0;font-size:11px;")
        layout.addWidget(hint)

    def reveal_annotations(self) -> None:
        if self.list.row(self._annotations) < 0:
            self.list.addItem(self._annotations)

    def reveal_ir(self) -> None:
        self._ir.setText("Trafic IR")
        self._ir.setToolTip(str(self.workspace / "traffic_ir"))
        self.list.set_drag_enabled(self._ir, True)

    def reveal_showcase_csv(self) -> None:
        if self.list.row(self._showcase_csv) < 0:
            self.list.addItem(self._showcase_csv)

    def reveal_kpts_csv(self) -> None:
        if self.list.row(self._kpts_csv) < 0:
            self.list.addItem(self._kpts_csv)


class TutorialBubble(QtWidgets.QFrame):
    previousRequested = QtCore.Signal()
    nextRequested = QtCore.Signal()
    quitRequested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("tutorial_bubble")
        self.setStyleSheet(
            "#tutorial_bubble{background:#17171d;border:2px solid #ff982b;"
            "border-radius:6px;} QLabel{color:#ededf2;}"
            "QPushButton{padding:5px 12px;}"
        )
        self.setMinimumWidth(310)
        self.setMaximumWidth(370)
        layout = QtWidgets.QVBoxLayout(self)
        self.chapter = QtWidgets.QLabel()
        self.chapter.setStyleSheet("color:#ffad42;font-size:11px;font-weight:bold;")
        self.title = QtWidgets.QLabel()
        self.title.setStyleSheet("font-size:16px;font-weight:bold;")
        self.body = QtWidgets.QLabel()
        self.body.setWordWrap(True)
        self.waiting = QtWidgets.QLabel()
        self.waiting.setWordWrap(True)
        self.waiting.setStyleSheet("color:#ffbf73;font-size:11px;")
        layout.addWidget(self.chapter)
        layout.addWidget(self.title)
        layout.addWidget(self.body)
        layout.addWidget(self.waiting)
        buttons = QtWidgets.QHBoxLayout()
        quit_button = QtWidgets.QPushButton("Quitter")
        self.previous = QtWidgets.QPushButton("←  Precedent")
        self.next = QtWidgets.QPushButton("Suivant  →")
        self.next.setDefault(True)
        buttons.addWidget(quit_button)
        buttons.addStretch(1)
        buttons.addWidget(self.previous)
        buttons.addWidget(self.next)
        layout.addLayout(buttons)
        quit_button.clicked.connect(self.quitRequested)
        self.previous.clicked.connect(self.previousRequested)
        self.next.clicked.connect(self.nextRequested)

    def set_step(self, step: TutorialStep, index: int, total: int, ready: bool) -> None:
        self.chapter.setText(f"{index + 1}/{total}  {step.chapter}")
        self.title.setText(step.title)
        self.body.setText(step.body() if callable(step.body) else step.body)
        if step.progress is not None:
            lines = [f"[{'x' if done else ' '}] {label}" for label, done in step.progress()]
            self.waiting.setText("A faire :\n" + "\n".join(lines))
            self.waiting.setVisible(True)
        else:
            self.waiting.setText("" if ready else "A faire : " + step.ready_text)
            self.waiting.setVisible(not ready)
        self.previous.setEnabled(index > 0)
        self.next.setEnabled(ready)
        self.next.setText("Terminer  ✓" if index == total - 1 else "Suivant  →")
        self.adjustSize()


class FrameViewerTutorial(QtCore.QObject):
    """Spotlight tour whose blocking steps are validated from real app state."""

    SETTINGS_KEY = "tutorial/frameviewer/v1/completed"

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.index = -1
        self.workspace: Optional[Path] = None
        self.explorer: Optional[TutorialExplorer] = None
        self.bubble = TutorialBubble(window)
        self.bubble.hide()
        self.bubble.previousRequested.connect(self.previous)
        self.bubble.nextRequested.connect(self.next)
        self.bubble.quitRequested.connect(self.stop)
        self._masks = [QtWidgets.QWidget(window) for _ in range(4)]
        for mask in self._masks:
            mask.setObjectName("tutorial_mask")
            mask.setAttribute(Qt.WA_TransparentForMouseEvents)
            mask.setStyleSheet("background:rgba(4,4,8,150);")
            mask.hide()
        self._outline = QtWidgets.QFrame(window)
        self._outline.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._outline.setStyleSheet(
            "background:transparent;border:3px solid #ff982b;border-radius:5px;"
        )
        self._outline.hide()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._poll)
        self._canvas_actions = {"wheel": False, "pan": False, "fit": False}
        self._linked_canvas_clicked = False
        self._link_navigation_initial = ()
        QtWidgets.QApplication.instance().installEventFilter(self)
        self._install_glow()

    def _settings(self):
        return self.window._plugin_settings()

    def completed(self) -> bool:
        raw = self._settings().value(self.SETTINGS_KEY, False)
        return raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true", "yes")

    def _install_glow(self) -> None:
        if self.completed():
            return
        effect = QtWidgets.QGraphicsDropShadowEffect(self.window.tutorial_btn)
        effect.setColor(QtGui.QColor("#ff8a1f"))
        effect.setOffset(0, 0)
        effect.setBlurRadius(8)
        self.window.tutorial_btn.setGraphicsEffect(effect)
        animation = QtCore.QPropertyAnimation(effect, b"blurRadius", self)
        animation.setStartValue(6.0)
        animation.setEndValue(24.0)
        animation.setDuration(900)
        animation.setEasingCurve(QtCore.QEasingCurve.InOutSine)
        animation.setLoopCount(-1)
        animation.start()
        self._glow_animation = animation

    def _remove_glow(self) -> None:
        animation = getattr(self, "_glow_animation", None)
        if animation is not None:
            animation.stop()
        self.window.tutorial_btn.setGraphicsEffect(None)

    def start(self) -> None:
        if self.index >= 0:
            self.bubble.raise_()
            return
        try:
            self.workspace = stage_tutorial_data()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self.window, "Tutoriel indisponible", str(exc))
            return
        self._reset_demo_plugins()
        self.explorer = TutorialExplorer(self.workspace, self.window)
        self._canvas_actions = {"wheel": False, "pan": False, "fit": False}
        self._initial_lut = self.window.lut_hist_combo.currentIndex()
        self._initial_hist_window = (self.window.hist.lo, self.window.hist.hi)
        self._initial_clicks = len(self.window.clicks)
        self.steps = self._build_steps()
        self.index = 0
        self._timer.start()
        self._enter_step()

    def stop(self) -> None:
        self._timer.stop()
        self.index = -1
        self.bubble.hide()
        self._outline.hide()
        for mask in self._masks:
            mask.hide()
        if self.explorer is not None:
            self.explorer.close()
            self.explorer.deleteLater()
            self.explorer = None

    def finish(self) -> None:
        settings = self._settings()
        settings.setValue(self.SETTINGS_KEY, True)
        settings.sync()
        self._remove_glow()
        self.window.statusBar().showMessage(
            "Tutoriel termine. Il reste disponible depuis le bouton Tutoriel.", 5000
        )
        self.stop()

    def previous(self) -> None:
        if self.index > 0:
            self.index -= 1
            self._enter_step()

    def next(self) -> None:
        if self.index < 0 or not self._is_ready():
            return
        if self.index == len(self.steps) - 1:
            self.finish()
            return
        self.index += 1
        self._enter_step()

    def _enter_step(self) -> None:
        step = self.steps[self.index]
        if step.prepare is not None:
            step.prepare()
        self._poll()

    def _is_ready(self) -> bool:
        if self.index < 0:
            return False
        predicate = self.steps[self.index].ready
        try:
            return predicate is None or bool(predicate())
        except RuntimeError:
            return False

    def _poll(self) -> None:
        if self.index < 0 or not self.window.isVisible():
            return
        self._close_explorer_after_drop()
        step = self.steps[self.index]
        target = step.target()
        if target is None or not target.isVisible():
            target = self.window.centralWidget()
        self._place_spotlight(target)
        self.bubble.set_step(step, self.index, len(self.steps), self._is_ready())
        self._place_bubble(target)
        for widget in self._masks + [self._outline, self.bubble]:
            widget.raise_()

    def _target_rect(self, target) -> QtCore.QRect:
        top_left = target.mapToGlobal(QtCore.QPoint(0, 0))
        local = self.window.mapFromGlobal(top_left)
        rect = QtCore.QRect(local, target.size()).adjusted(-5, -5, 5, 5)
        return rect.intersected(self.window.rect().adjusted(2, 2, -2, -2))

    def _place_spotlight(self, target) -> None:
        full = self.window.rect()
        hole = self._target_rect(target)
        rects = (
            QtCore.QRect(0, 0, full.width(), max(0, hole.top())),
            QtCore.QRect(0, hole.bottom() + 1, full.width(), max(0, full.bottom() - hole.bottom())),
            QtCore.QRect(0, hole.top(), max(0, hole.left()), hole.height()),
            QtCore.QRect(hole.right() + 1, hole.top(), max(0, full.right() - hole.right()), hole.height()),
        )
        for mask, rect in zip(self._masks, rects):
            mask.setGeometry(rect)
            mask.setVisible(rect.width() > 0 and rect.height() > 0)
        self._outline.setGeometry(hole)
        self._outline.show()

    def _place_bubble(self, target) -> None:
        hole = self._target_rect(target)
        bounds = self.window.rect().adjusted(8, 36, -8, -28)
        self.bubble.setMaximumWidth(min(370, max(260, bounds.width())))
        self.bubble.adjustSize()
        size = self.bubble.size().boundedTo(bounds.size())
        self.bubble.resize(size)
        margin = 14
        candidates = [
            QtCore.QPoint(hole.right() + margin, hole.top()),
            QtCore.QPoint(hole.left(), hole.bottom() + margin),
            QtCore.QPoint(hole.left() - size.width() - margin, hole.top()),
            QtCore.QPoint(hole.left(), hole.top() - size.height() - margin),
        ]
        point = candidates[0]
        for candidate in candidates:
            test = QtCore.QRect(candidate, size)
            if bounds.contains(test):
                point = candidate
                break
        point.setX(max(bounds.left(), min(point.x(), bounds.right() - size.width())))
        point.setY(max(bounds.top(), min(point.y(), bounds.bottom() - size.height())))
        self.bubble.move(point)
        self.bubble.show()

    def _show_right(self, index: int) -> None:
        self.window.right_tab.setCurrentIndex(index)
        self.window.right_dock.show()
        self.window.right_dock.raise_()
        self.window._update_right_btns(index)

    def _show_right_dock(self) -> None:
        self.window.right_dock.show()
        self.window.right_dock.raise_()

    def _reset_demo_plugins(self) -> None:
        demo_ids = ("demo_showcase", "demo_2_multivue_kpts")
        for plugin_id in demo_ids:
            self.window._plugin_loader.set_enabled(plugin_id, False)
            self.window._plugin_state.pop(plugin_id, None)
            dock = getattr(self.window, "_plugin_docks", {}).get(plugin_id)
            if dock is not None:
                dock.hide()
        self.window._save_enabled_plugin_ids()
        self.window._save_plugin_state()
        panel = getattr(self.window, "_plugins_panel", None)
        if panel is not None:
            panel.refresh()
        if self.window.source is not None:
            self.window._repaint_views()

    def _show_explorer(self, annotations=False, ir=False, plugin_data=None) -> None:
        if self.explorer is None:
            return
        if annotations and self._annotations_loaded():
            return
        if ir and self._satellite_has("traffic_ir"):
            return
        if plugin_data and self._plugin_has_input(*plugin_data):
            return
        if (not annotations and not ir and not plugin_data
                and is_tutorial_source(self.window.source, "traffic_rgb")):
            return
        if annotations:
            self.explorer.reveal_annotations()
        if ir:
            self.explorer.reveal_ir()
        if plugin_data == ("demo_showcase", "boxes"):
            self.explorer.reveal_showcase_csv()
        if plugin_data == ("demo_2_multivue_kpts", "pairs_csv"):
            self.explorer.reveal_kpts_csv()
        self.explorer.show()
        pos = self.window.mapToGlobal(QtCore.QPoint(18, 90))
        self.explorer.move(pos)
        self.explorer.raise_()

    def _close_explorer_after_drop(self) -> None:
        if self.explorer is None or not self.explorer.isVisible():
            return
        title = self.steps[self.index].title
        rgb_done = title == "Deposer Trafic RGB" and is_tutorial_source(
            self.window.source, "traffic_rgb")
        annotations_done = title == "Deposer les annotations YOLO" and self._annotations_loaded()
        ir_done = title == "Deposer Trafic IR" and self._satellite_has("traffic_ir")
        showcase_done = title == "Deposer le CSV des boites" and self._plugin_has_input(
            "demo_showcase", "boxes")
        kpts_done = title == "Deposer le CSV des correspondances" and self._plugin_has_input(
            "demo_2_multivue_kpts", "pairs_csv")
        if kpts_done:
            self._connect_multiview_tutorial_input()
        if rgb_done or annotations_done or ir_done or showcase_done or kpts_done:
            self.explorer.hide()

    def _enable_plugin(self, plugin_id: str) -> bool:
        return plugin_id in self.window._plugin_loader.enabled_ids()

    def _plugin_card(self, plugin_id: str):
        panel = getattr(self.window, "_plugins_panel", None)
        if panel is None:
            return None
        return next((card for card in panel._cards if card.pid == plugin_id), None)

    def _plugin_control(self, plugin_id: str, prefix: str):
        card = self._plugin_card(plugin_id)
        if card is None:
            return self.window._plugins_panel
        return card.findChild(QtWidgets.QWidget, f"{prefix}_{plugin_id}") or card

    def _plugin_input_zone(self, plugin_id: str, input_name: str):
        card = self._plugin_card(plugin_id)
        if card is None:
            return self.window._plugins_panel
        return card.findChild(QtWidgets.QWidget, f"plugin_input_{input_name}") or card

    def _plugin_expanded(self, plugin_id: str) -> bool:
        card = self._plugin_card(plugin_id)
        return bool(card is not None and card._body.isVisible())

    def _plugin_has_input(self, plugin_id: str, input_name: str) -> bool:
        try:
            contracts = self.window._plugin_view_state(plugin_id)["contracts"]
        except (KeyError, TypeError):
            return False
        return any(bool(group.get("files", {}).get(input_name)) for group in contracts)

    def _plugin_input_path(self, plugin_id: str, input_name: str) -> str:
        try:
            contracts = self.window._plugin_view_state(plugin_id)["contracts"]
        except (KeyError, TypeError):
            return ""
        return next((group.get("files", {}).get(input_name, "")
                     for group in contracts
                     if group.get("files", {}).get(input_name)), "")

    def _connect_multiview_tutorial_input(self) -> None:
        """Partage le CSV entre les deux vues pendant ce tutoriel seulement."""
        path = self._plugin_input_path("demo_2_multivue_kpts", "pairs_csv")
        if not path:
            return
        for view in self.window._views_context().get("views", []):
            key = view.get("view_key")
            if not key or key == "__none__":
                continue
            state = self.window._plugin_view_state("demo_2_multivue_kpts", key)
            contracts = state.setdefault("contracts", [])
            if any(item.get("files", {}).get("pairs_csv") == path for item in contracts):
                continue
            target = next((item for item in contracts
                           if not item.get("files", {}).get("pairs_csv")), None)
            if target is None:
                target = {"name": "", "visible": True, "files": {}}
                contracts.append(target)
            target.setdefault("files", {})["pairs_csv"] = path
        # Le plugin de demonstration est exclu de la persistance : ce partage
        # disparait a la fermeture et ne touche pas les plugins utilisateur.
        self.window._save_plugin_state()

    def _histogram_adjusted(self) -> bool:
        initial_lo, initial_hi = self._initial_hist_window
        moved_handle = (abs(self.window.hist.lo - initial_lo) > 1e-6
                        or abs(self.window.hist.hi - initial_hi) > 1e-6)
        return moved_handle or self.window.lut_hist_combo.currentIndex() != self._initial_lut

    def _prepare_histogram_step(self) -> None:
        self._show_right(0)
        self._initial_lut = self.window.lut_hist_combo.currentIndex()
        self._initial_hist_window = (self.window.hist.lo, self.window.hist.hi)

    def _visible_view_frames(self) -> tuple:
        return tuple(view.get("frame_idx") for view in self.window._views_context()["views"])

    def _prepare_link_navigation_test(self) -> None:
        self._linked_canvas_clicked = False
        self._link_navigation_initial = self._visible_view_frames()

    def _linked_navigation_done(self) -> bool:
        return (self._linked_canvas_clicked
                and self._visible_view_frames() != self._link_navigation_initial)

    def _plugin_dock_visible(self, plugin_id: str) -> bool:
        dock = getattr(self.window, "_plugin_docks", {}).get(plugin_id)
        return bool(dock is not None and dock.isVisible())

    def _plugin_dock(self, plugin_id: str):
        return getattr(self.window, "_plugin_docks", {}).get(plugin_id) or self.window

    def _show_plugin_card(self, plugin_id: str) -> None:
        self._show_right(1)
        card = self._plugin_card(plugin_id)
        if card is not None:
            self.window._plugins_panel._scroll.ensureWidgetVisible(card, 8, 8)

    def _show_params_widget(self, widget) -> None:
        self._show_right(2)
        self.window.params_scroll.ensureWidgetVisible(widget, 8, 8)

    def _close_plugin_console(self) -> None:
        dialog = getattr(self.window, "_plugin_console", None)
        if dialog is not None:
            dialog.hide()

    def _plugin_console_visible(self) -> bool:
        dialog = getattr(self.window, "_plugin_console", None)
        return bool(dialog is not None and dialog.isVisible())

    def _annotation_layers_visible(self) -> bool:
        layers = [layer for layer in self.window._layers if layer.get("fmt") == "yolo"]
        return bool(layers) and all(layer.get("visible", True) for layer in layers)

    def _demo_plugins_inactive(self) -> bool:
        return not self._enable_plugin("demo_showcase") and not self._enable_plugin(
            "demo_2_multivue_kpts"
        )

    def _satellite_has(self, folder: str) -> bool:
        for index, widget, primary in self.window._split_canvas.ordered_views():
            if index == 1:
                source = self.window.source if primary else getattr(widget, "_source", None)
                return is_tutorial_source(source, folder)
        return False

    def _annotations_loaded(self) -> bool:
        return bool(self.window._annotations)

    def _link_starts_are(self, values) -> bool:
        return self.window._link_views and list(self.window._link_start_frames) == list(values)

    def _build_steps(self):
        w = self.window
        central = lambda: w._split_canvas
        explorer_target = lambda: self.explorer.list if self.explorer else central()
        return [
            TutorialStep(
                "Orientation", "Les zones de travail",
                "La barre regroupe les commandes, le canvas affiche la source, les mini-barres naviguent par vue et les panneaux restent ancrables.",
                lambda: w.findChild(QtWidgets.QToolBar, "main_toolbar")),
            TutorialStep(
                "Orientation", "Calques, histogramme et parametres",
                "Le panneau droit contient Hist / Calque, Plugins et Parametres. Chaque onglet agit sur la vue selectionnee ou sur toutes les vues lorsque cela est precise.",
                lambda: w.right_tab, prepare=lambda: self._show_right(0)),
            TutorialStep(
                "Explorateur", "Donnees de demonstration",
                "Les donnees sont des copies ecrites dans le workspace utilisateur. Seul Trafic RGB est deposable maintenant; IR et les CSV seront deverrouilles au moment utile.",
                explorer_target, prepare=lambda: self._show_explorer()),
            TutorialStep(
                "Chargement", "Deposer Trafic RGB",
                "Glisse Trafic RGB depuis le pseudo-explorateur vers la vue unique. La fenetre de fichiers se fermera des que la source sera chargee.",
                central, prepare=lambda: self._show_explorer(),
                ready=lambda: is_tutorial_source(w.source, "traffic_rgb"),
                ready_text="glisser Trafic RGB dans le canvas"),
            TutorialStep(
                "Formats", "Sources acceptees",
                "Les dossiers d'images, images isolees, MP4, WebM, TIFF, JPEG, PNG et YUV sont natifs. L'Aide enumere les extensions optionnelles vraiment disponibles.",
                lambda: w.open_file_btn),
            TutorialStep(
                "Canvas", "Zoom, panoramique et ajustement",
                "Realise les trois gestes dans l'ordre de ton choix. Chaque case se coche des que le geste est detecte.",
                lambda: w.view, ready=lambda: all(self._canvas_actions.values()),
                ready_text="realiser les trois gestes",
                progress=lambda: [
                    ("Zoomer avec la molette", self._canvas_actions["wheel"]),
                    ("Deplacer avec le bouton central maintenu", self._canvas_actions["pan"]),
                    ("Recadrer avec un clic central", self._canvas_actions["fit"]),
                ]),
            TutorialStep(
                "Rendu", "Histogramme et LUT",
                "Toute la zone est mise en avant : l'histogramme mesure la vue active et le bloc Rendu applique LUT, contraste et filtres. Deplace simplement la barre gauche ou droite de l'histogramme; choisir une LUT predefinie fonctionne aussi.",
                lambda: w.hist_render_panel, prepare=self._prepare_histogram_step,
                ready=self._histogram_adjusted,
                ready_text="deplacer une poignee de l'histogramme ou changer la LUT"),
            TutorialStep(
                "Parametres", "Ouvrir l'onglet Parametres",
                "Clique sur Parametres. Les commandes REC clics et Extraction IN / OUT se trouvent dans cet onglet.",
                lambda: w.right_tab.tabBar(), prepare=self._show_right_dock,
                ready=lambda: w.right_dock.isVisible() and w.right_tab.currentIndex() == 2,
                ready_text="cliquer sur l'onglet Parametres"),
            TutorialStep(
                "REC clics", "Activer et enregistrer un point",
                lambda: (
                    "Active REC clics puis clique dans l'image. Le point est ajoute immediatement dans :\n"
                    + (w.clicks_path or "<dossier de la source>/<nom_source>_clicks.txt")
                    + "\nLe fichier contient frame,x,y. Annuler, Effacer et Ctrl+S completent le flux."
                ),
                lambda: w.clicks_group,
                prepare=lambda: self._show_params_widget(w.clicks_group),
                ready=lambda: w.rec_btn.isChecked() and len(w.clicks) > self._initial_clicks,
                ready_text="activer REC clics puis cliquer dans l'image"),
            TutorialStep(
                "REC clics", "Arreter l'enregistrement",
                lambda: (
                    "Le point est enregistre dans "
                    + (w.clicks_path or "le fichier de clics")
                    + ". Desactive maintenant REC clics pour retrouver un clic normal sur le canvas."
                ),
                lambda: w.rec_btn,
                prepare=lambda: self._show_params_widget(w.clicks_group),
                ready=lambda: not w.rec_btn.isChecked() and len(w.clicks) > self._initial_clicks,
                ready_text="decocher REC clics"),
            TutorialStep(
                "Extraction", "Marquer le debut IN",
                "Place la timeline sur la frame de debut souhaitee, puis clique IN.",
                lambda: w.extraction_group,
                prepare=lambda: self._show_params_widget(w.extraction_group),
                ready=lambda: w._extract_start >= 0,
                ready_text="choisir une frame puis cliquer IN"),
            TutorialStep(
                "Extraction", "Naviguer puis marquer OUT",
                "La zone IN / OUT reste eclairee. Deplace d'abord la timeline sous les images, puis clique OUT ici. OUT doit etre strictement apres IN.",
                lambda: w.extraction_group,
                prepare=lambda: self._show_params_widget(w.extraction_group),
                ready=lambda: w._extract_start >= 0 and w._extract_end > w._extract_start,
                ready_text="deplacer la timeline puis cliquer OUT",
                progress=lambda: [
                    ("IN defini", w._extract_start >= 0),
                    ("Timeline placee apres IN", w._extract_start >= 0 and w.cur > w._extract_start),
                    ("OUT defini apres IN", w._extract_end > w._extract_start >= 0),
                ]),
            TutorialStep(
                "Extraction", "Extraire ou ajouter un morceau",
                "Extraire ouvre directement l'export de la plage IN/OUT. Pour construire plusieurs plages, clique Ajouter morceau; definis ensuite un nouveau couple IN/OUT.",
                lambda: w.extraction_group,
                prepare=lambda: self._show_params_widget(w.extraction_group),
                ready=lambda: bool(w._segments),
                ready_text="cliquer Ajouter morceau pour cette demonstration"),
            TutorialStep(
                "Extraction", "Liste et export des morceaux",
                "La liste montre toutes les plages ajoutees. Extraire morceaux ouvre un export unique dans le format choisi et traite toutes les plages en une fois.",
                lambda: w.extraction_group,
                prepare=lambda: self._show_params_widget(w.extraction_group)),
            TutorialStep(
                "Annotations", "Ouvrir Hist / Calque",
                "Les fichiers YOLO .txt sont des annotations natives, pas des donnees plugin. Ouvre donc Hist / Calque avant leur chargement.",
                lambda: w.right_tab.tabBar(), prepare=self._show_right_dock,
                ready=lambda: w.right_dock.isVisible() and w.right_tab.currentIndex() == 0,
                ready_text="cliquer sur Hist / Calque"),
            TutorialStep(
                "Annotations", "Deposer les annotations YOLO",
                "Glisse Annotations YOLO sur le canvas. Chaque .txt porte le meme nom que son image et classes.txt nomme la classe vehicle.",
                central, prepare=lambda: (self._show_right(0), self._show_explorer(annotations=True)),
                ready=self._annotations_loaded,
                ready_text="glisser Annotations YOLO dans le canvas"),
            TutorialStep(
                "Annotations", "Masquer uniquement le calque YOLO",
                "Dans Hist / Calque, decoche annotations_yolo (ou son enfant vehicle). Le bouton global Afficher les overlays n'est pas utilise : les overlays des plugins restent independants.",
                lambda: w.layer_list, prepare=lambda: self._show_right(0),
                ready=lambda: self._annotations_loaded() and not self._annotation_layers_visible(),
                ready_text="decocher le calque annotations_yolo"),
            TutorialStep(
                "Plugin monovue", "Ouvrir Plugins",
                "Laisse le calque YOLO masque pour eviter toute superposition. Ouvre Plugins; les deux demos doivent etre Inactif et sans fichier restaure.",
                lambda: w.right_tab.tabBar(), prepare=self._show_right_dock,
                ready=lambda: (w.right_dock.isVisible() and w.right_tab.currentIndex() == 1
                               and not self._annotation_layers_visible()
                               and self._demo_plugins_inactive()),
                ready_text="laisser YOLO masque puis cliquer sur Plugins"),
            TutorialStep(
                "Plugin monovue", "Activer le showcase",
                "Clique Actif sur Demo showcase. Sans fichier dans l'entree boxes, son overlay reste vide.",
                lambda: self._plugin_control("demo_showcase", "plugin_active"),
                prepare=lambda: self._show_plugin_card("demo_showcase"),
                ready=lambda: self._enable_plugin("demo_showcase"),
                ready_text="cocher Actif sur Demo showcase"),
            TutorialStep(
                "Plugin monovue", "Deplier la carte",
                "Clique sur la fleche de la carte pour afficher son entree boxes, son apercu et ses actions.",
                lambda: self._plugin_control("demo_showcase", "plugin_expand"),
                prepare=lambda: self._show_plugin_card("demo_showcase"),
                ready=lambda: self._plugin_expanded("demo_showcase"),
                ready_text="deplier la carte Demo showcase"),
            TutorialStep(
                "Plugin monovue", "Deposer le CSV des boites",
                "Glisse CSV boites du plugin dans l'entree boxes. Ce fichier est fourni dans le pseudo-explorateur, mais aucune entree n'est remplie automatiquement.",
                lambda: self._plugin_input_zone("demo_showcase", "boxes"),
                prepare=lambda: self._show_explorer(plugin_data=("demo_showcase", "boxes")),
                ready=lambda: self._plugin_has_input("demo_showcase", "boxes"),
                ready_text="deposer le CSV dans l'entree boxes"),
            TutorialStep(
                "Plugin monovue", "Ouvrir le panneau du plugin",
                "Clique Ouvrir le panneau (dock). Il contient opacite globale, remplissage, epaisseur des boites et epaisseur de grille.",
                lambda: self._plugin_control("demo_showcase", "plugin_open_dock"),
                prepare=lambda: self._show_plugin_card("demo_showcase"),
                ready=lambda: self._plugin_dock_visible("demo_showcase"),
                ready_text="cliquer Ouvrir le panneau (dock)"),
            TutorialStep(
                "Plugin monovue", "Regler puis exporter",
                "Teste les quatre reglages du dock. La capture et les exports peuvent graver les overlays visibles dans l'image produite.",
                lambda: self._plugin_dock("demo_showcase")),
            TutorialStep(
                "Console", "Ouvrir les logs plugins",
                "Le bouton Console ouvre les logs, prints, erreurs, diagnostics et exports de debug, isoles par plugin. Clique-le maintenant.",
                lambda: w._plugins_panel.findChild(QtWidgets.QWidget, "plugin_console_button"),
                prepare=lambda: self._show_plugin_card("demo_showcase"),
                ready=self._plugin_console_visible,
                ready_text="cliquer sur Console"),
            TutorialStep(
                "Configuration", "Dossiers et plugins personnels",
                "Config donne acces a settings.ini, au dossier de plugins utilisateur et a la documentation. Un dossier plugins voisin de l'executable est egalement detecte.",
                lambda: w.config_btn, prepare=self._close_plugin_console),
            TutorialStep(
                "Plugin monovue", "Desactiver le showcase",
                "Avant la multivue, decoche Actif sur Demo showcase. Aucun overlay du plugin monovue ne doit rester sur les vues suivantes.",
                lambda: self._plugin_control("demo_showcase", "plugin_active"),
                prepare=lambda: self._show_plugin_card("demo_showcase"),
                ready=lambda: not self._enable_plugin("demo_showcase"),
                ready_text="decocher Actif sur Demo showcase"),
            TutorialStep(
                "Multivue", "Choisir Empile haut / bas",
                "La console est fermee. Utilise le bouton de disposition des vues et choisis Empile haut / bas.",
                lambda: w.split_btn,
                prepare=lambda: (self._close_plugin_console(), w.right_dock.hide()),
                ready=lambda: w._split_canvas.mode() == "v2",
                ready_text="choisir la disposition Empile haut / bas"),
            TutorialStep(
                "Multivue", "Deposer Trafic IR",
                "Trafic IR est maintenant deverrouille. Glisse-le dans la vue inferieure; le plugin showcase etant inactif, il ne dessine rien.",
                central, prepare=lambda: self._show_explorer(ir=True),
                ready=lambda: w._split_canvas.mode() == "v2" and self._satellite_has("traffic_ir"),
                ready_text="glisser Trafic IR dans la vue inferieure"),
            TutorialStep(
                "Liaison", "Meme base temporelle",
                "Clique Lier, conserve Liaison active et les departs [0, 0], puis clique Appliquer. Les compteurs +/- et la saisie clavier sont actifs.",
                lambda: w.link_btn,
                ready=lambda: self._link_starts_are([0, 0]),
                ready_text="appliquer la liaison [0, 0]"),
            TutorialStep(
                "Liaison", "Tester un decalage",
                "Rouvre Lier : Liaison active et les anciennes valeurs restent memorisees. Utilise les fleches +/- ou saisis [2, 0], puis Appliquer. La vue haute commence a 2, celle du bas a 0.",
                lambda: w.link_btn,
                ready=lambda: self._link_starts_are([2, 0]),
                ready_text="appliquer la liaison [2, 0]"),
            TutorialStep(
                "Liaison", "Verifier la navigation synchronisee",
                "Clique d'abord dans une des deux vues, puis deplace sa mini-timeline. Les deux vues avancent ensemble en conservant exactement deux frames d'ecart.",
                central, prepare=self._prepare_link_navigation_test,
                ready=self._linked_navigation_done,
                ready_text="cliquer une vue puis deplacer sa mini-timeline",
                progress=lambda: [
                    ("Cliquer dans une vue", self._linked_canvas_clicked),
                    ("Changer de frame", self._visible_view_frames() != self._link_navigation_initial),
                ]),
            TutorialStep(
                "Liaison", "Revenir a [0, 0]",
                "Remets les departs a [0, 0] avant les correspondances. Le CSV contient des appariements pour chacune des frames 0 a 9 avec les deux vues au meme index.",
                lambda: w.link_btn,
                ready=lambda: self._link_starts_are([0, 0]),
                ready_text="reappliquer [0, 0]"),
            TutorialStep(
                "Plugin multivue", "Rouvrir le panneau Plugins",
                "Ouvre le panneau droit puis l'onglet Plugins. Le plugin multivue doit encore etre Inactif.",
                lambda: w.right_panel_btn,
                ready=lambda: w.right_dock.isVisible() and w.right_tab.currentIndex() == 1
                and not self._enable_plugin("demo_2_multivue_kpts"),
                ready_text="ouvrir le panneau Plugins"),
            TutorialStep(
                "Plugin multivue", "Activer les correspondances",
                "Clique Actif sur Demo 2 vues. Sans CSV depose, aucun point ni lien n'est dessine.",
                lambda: self._plugin_control("demo_2_multivue_kpts", "plugin_active"),
                prepare=lambda: self._show_plugin_card("demo_2_multivue_kpts"),
                ready=lambda: self._enable_plugin("demo_2_multivue_kpts"),
                ready_text="cocher Actif sur le plugin multivue"),
            TutorialStep(
                "Plugin multivue", "Deplier la carte multivue",
                "Deplie la carte pour afficher l'entree pairs_csv et les actions.",
                lambda: self._plugin_control("demo_2_multivue_kpts", "plugin_expand"),
                prepare=lambda: self._show_plugin_card("demo_2_multivue_kpts"),
                ready=lambda: self._plugin_expanded("demo_2_multivue_kpts"),
                ready_text="deplier la carte multivue"),
            TutorialStep(
                "Plugin multivue", "Deposer le CSV des correspondances",
                "Glisse CSV correspondances multivues dans pairs_csv. Les appariements pre-calcules couvrent les dix couples RGB/IR.",
                lambda: self._plugin_input_zone("demo_2_multivue_kpts", "pairs_csv"),
                prepare=lambda: self._show_explorer(
                    plugin_data=("demo_2_multivue_kpts", "pairs_csv")),
                ready=lambda: self._plugin_has_input("demo_2_multivue_kpts", "pairs_csv"),
                ready_text="deposer le CSV dans pairs_csv"),
            TutorialStep(
                "Plugin multivue", "Ouvrir les reglages multivues",
                "Clique Ouvrir le panneau (dock). Il distingue les 600 lignes fixes du CSV du nombre de correspondances visibles sur la frame courante.",
                lambda: self._plugin_control("demo_2_multivue_kpts", "plugin_open_dock"),
                prepare=lambda: self._show_plugin_card("demo_2_multivue_kpts"),
                ready=lambda: self._plugin_dock_visible("demo_2_multivue_kpts"),
                ready_text="ouvrir le panneau du plugin multivue"),
            TutorialStep(
                "Plugin multivue", "Naviguer avec les correspondances",
                "Clique dans une vue puis deplace sa mini-timeline. Le compteur affiche sur la vue haute et dans le dock s'adapte a chaque paire de frames.",
                central, prepare=self._prepare_link_navigation_test,
                ready=self._linked_navigation_done,
                ready_text="cliquer une vue puis changer de frame",
                progress=lambda: [
                    ("Cliquer dans une vue", self._linked_canvas_clicked),
                    ("Changer de frame", self._visible_view_frames() != self._link_navigation_initial),
                ]),
            TutorialStep(
                "Plugin multivue", "Confiance, taille et couleur",
                "La taille de base vaut 11 px. Taille proportionnelle et Coolwarm_r utilisent la meme normalisation : adaptative a la frame, ou absolue sur tout le CSV. Un score faible est rouge, un score fort bleu.",
                lambda: self._plugin_dock("demo_2_multivue_kpts")),
            TutorialStep(
                "Fin", "Flux complet maitrise",
                "Tu as charge deux vues, enregistre des clics, annote, decoupe, lie des offsets et alimente deux plugins par drag-and-drop. Convertir exporte la composition; l'Aide reste la reference detaillee.",
                lambda: w.aide_btn),
        ]

    def eventFilter(self, watched, event):
        if self.index < 0:
            return False
        if event.type() == QtCore.QEvent.KeyPress:
            key = event.key()
            editing = isinstance(
                watched,
                (QtWidgets.QAbstractSpinBox, QtWidgets.QLineEdit,
                 QtWidgets.QSlider, QtWidgets.QAbstractItemView),
            )
            if (not editing and QtWidgets.QApplication.activeModalWidget() is None
                    and key in (Qt.Key_Left, Qt.Key_Right)):
                if key == Qt.Key_Left:
                    self.previous()
                elif self._is_ready():
                    self.next()
                return True
        try:
            videos = self.window._all_view_videos()
        except RuntimeError:
            videos = []
        if (watched in videos and event.type() == QtCore.QEvent.MouseButtonPress
                and event.button() in (Qt.LeftButton, Qt.MiddleButton)):
            self._linked_canvas_clicked = True
        video = self.window.view
        if watched is video:
            if event.type() == QtCore.QEvent.Wheel:
                self._canvas_actions["wheel"] = True
            elif event.type() == QtCore.QEvent.MouseMove and event.buttons() & Qt.MiddleButton:
                self._canvas_actions["pan"] = True
            elif event.type() == QtCore.QEvent.MouseButtonRelease and event.button() == Qt.MiddleButton:
                if not getattr(video, "_pan_moved", False):
                    self._canvas_actions["fit"] = True
        return False
