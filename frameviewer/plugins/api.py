# -*- coding: utf-8 -*-
"""Stable plugin contract and the restricted ``PluginAPI`` facade.

Plugin packages expose a ``FrameViewerPlugin`` subclass as ``PLUGIN``. Hooks
are optional and loader-side error isolation disables a failing plugin without
terminating the application. Plugins must use this facade rather than importing
UI internals. See ``docs/plugins.md`` for the complete contract.
"""
import csv
import json
import os


def _env_version_hint(paths):
    """Warn when an external site-packages directory targets another Python."""
    import subprocess
    import sys
    app_ver = "%d.%d" % sys.version_info[:2]
    for sp in paths or []:
        # Resolve the environment interpreter from its site-packages path.
        env = os.path.dirname(os.path.dirname(os.path.normpath(str(sp))))
        pyexe = os.path.join(env, "python.exe")
        if not os.path.isfile(pyexe):
            continue
        try:
            r = subprocess.run(
                [pyexe, "-c", "import sys;print('%d.%d'%sys.version_info[:2])"],
                capture_output=True, text=True, timeout=10)
            env_ver = (r.stdout or "").strip()
        except Exception:
            continue
        if env_ver and env_ver != app_ver:
            return (f"cet env est en Python {env_ver}, l'app tourne en Python "
                    f"{app_ver} : une lib compilee (pandas/numpy/polars) NE peut "
                    f"PAS se charger en meme-process. Utilise run_external (le "
                    f"python.exe de cet env), ou un env en Python {app_ver}.")
    return None


class FrameViewerPlugin:
    """Base class for plugins; subclasses should define a stable ``name``."""

    name = "plugin"
    description = ""
    # Corner used by render_patch: tl, tr, bl, or br.
    corner = "tr"
    margin = 8

    def on_load(self, api):
        """Run once when the plugin is loaded or reloaded."""

    def on_unload(self, api):
        """Release resources before a reload."""

    def get_overlays(self, api, frame_idx):
        """Return shape mappings for the current frame, or an empty list."""
        return []

    def get_multiview_overlays(self, api, views):
        """Return canonical cross-view overlay shapes.

        This hook runs once per refresh when at least two views are visible.

        ``views`` is the same context returned by ``api.views``:
          {"mode": str, "count": int, "n_offset": int,
           "sync_base_frame": int,
           "views": [ {"index": int,        # Geometric slot, top-left first.
                       "role": str,
                       "view_key": str,      # Stable source identity.
                       "source_name": str,
                        "frame_idx": int|None,
                        "start_frame": int,
                       "w": int, "h": int}, ...]}

        Return a list of shapes in any order. Coordinates are image pixels:
          Shapes targeting one view include ``"view": index``:
            {"type":"rectangle","view":i,"points":[(x1,y1),(x2,y2)]}   # bbox (2 coins)
            {"type":"cercle","view":i,"points":[(x,y)],"a":rayon_px}
            {"type":"points","view":i,"points":[(x,y),...]}
            {"type":"polygone","view":i,"points":[(x,y),...]}          # ferme
            {"type":"ligne_brisee","view":i,"points":[(x,y),...]}      # ouverte
            {"type":"croix","view":i,"points":[(x,y),...]}
            {"type":"texte_ecran","view":i,"label":"12 correspondances"}
              # Text anchored to the viewport, independent of zoom and pan.
          A link between two views:
            {"type":"segment_inter_vues","va":i,"pa":(x,y),"vb":j,"pb":(x,y)}
        Common keys are ``color`` and ``thickness``. Shapes targeting missing
        slots are ignored. These overlays are display-only. Always use image
        coordinates; the app applies each view's zoom and pan and clips shapes
        to their viewport. Cross-view links are visible only while both ends
        remain in view.

        Match data through stable view keys or source names, then emit geometry
        with the positional index from the same context.
        """
        return []

    def render_patch(self, api, frame_idx, size):
        """Return an optional BGR corner patch for the current frame."""
        return None

    def build_panel(self, api):
        """Return an optional widget to expose through ``api.add_dock``."""
        return None

    def menu_actions(self, api):
        """Return actions shown in the plugin panel."""
        return []

    def accepts_drop(self, api, path):
        """Return whether this plugin accepts an otherwise unhandled drop."""
        return False

    def on_drop(self, api, path):
        """Handle a path previously accepted by ``accepts_drop``."""

    # Full-frame code-overlay model and image-pointer events.
    def overlay_elements(self, api):
        """Return toggleable overlay elements as ``(key, label)`` pairs."""
        return []

    def render_overlay(self, api, frame_idx, size):
        """Return an optional full-frame BGRA overlay in image coordinates."""
        return None

    def render_panel(self, api, frame_idx, size):
        """Return an optional image for the plugin-card preview area."""
        return None

    def on_view_hover(self, api, frame_idx, x, y):
        """Handle pointer hover in image coordinates."""

    def on_view_click(self, api, frame_idx, x, y):
        """Handle a click in image coordinates."""

    def on_view_key(self, api, frame_idx, key, text):
        """Handle an application-unclaimed key and report whether it was used."""
        return False


class PluginAPI:
    """Restricted application facade supplied to every plugin hook."""

    def __init__(self, mw, plugin_dir, plugin_id=None, contract=None, view_key=None):
        self._mw = mw
        self._plugin_dir = plugin_dir
        self._plugin_id = plugin_id
        # Current input contract, bound by the loader before rendering.
        self._contract = contract or {}
        # Source key for the view currently being rendered.
        self._view_key = view_key

    # Sequence and frame access.
    def frame_count(self):
        return self._mw.source.count if self._mw.source else 0

    def current_frame_index(self):
        return self._mw.cur

    def raw_frame(self, idx=None):
        """Return a native frame before display processing."""
        if self._mw.source is None:
            return None
        i = self._mw.cur if idx is None else int(idx)
        try:
            return self._mw.source.get(i)
        except Exception:
            return None

    def displayed_frame(self, idx=None):
        """Return a defensive copy of the processed BGR frame without overlays."""
        raw = self.raw_frame(idx)
        if raw is None:
            return None
        rot = self._mw._apply_rotation(raw)
        out = self._mw.process(rot)
        return out.copy() if out is not None else None

    def image_size(self):
        """Return source image dimensions, or ``(0, 0)`` when unavailable."""
        if self._mw._raw is None:
            return (0, 0)
        h, w = self._mw._raw.shape[:2]
        return (int(w), int(h))

    def source_name(self):
        return self._mw.source.name if self._mw.source else ""

    def views(self):
        """Return mode, synchronization state, and visible-view descriptors."""
        fn = getattr(self._mw, "_views_context", None)
        if fn is None:
            return {"mode": "1", "count": 0, "views": []}
        try:
            return fn()
        except Exception:
            return {"mode": "1", "count": 0, "views": []}

    # Read-only access to loaded annotations.
    def ver_boxes(self, frame_idx=None):
        """Return visible annotation boxes for one frame."""
        i = self._mw.cur if frame_idx is None else int(frame_idx)
        try:
            return list(self._mw._visible_annots(i))
        except Exception:
            return []

    def sidecar_graphs(self, frame_idx=None):
        """Return vector overlay shapes for one frame."""
        i = self._mw.cur if frame_idx is None else int(frame_idx)
        try:
            key = i + self._mw.sidecar_offset.value()
            return list(self._mw.overlays.get(key, []))
        except Exception:
            return []

    # Files.
    def plugin_dir(self):
        """Return the plugin package directory."""
        return self._plugin_dir

    def read_csv_dict(self, path, **kwargs):
        """Read CSV rows as dictionaries with the standard library."""
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f, **kwargs))

    # Backward-compatible access to the current input contract.
    def inputs(self):
        """Return the current input-name to path mapping."""
        return dict(self._contract)

    def input(self, name):
        """Return the current path for one named input."""
        return self._contract.get(name)

    def attached_csvs(self):
        """Return all non-empty paths in the current input contract."""
        return [p for p in self._contract.values() if p]

    def element_enabled(self, key, default=True):
        """Return the per-view enabled state of a declared overlay element."""
        pid = self._plugin_id
        # Prefer per-view state and fall back to the legacy flat model.
        fn = getattr(self._mw, "_plugin_elements", None)
        if fn is not None and pid is not None:
            try:
                return bool(fn(pid, self._view_key).get(key, default))
            except Exception:
                pass
        state = getattr(self._mw, "_plugin_state", {}).get(pid, {})
        return bool(state.get("elements", {}).get(key, default))

    def map_csv_columns(self, csv_path, roles, optional_roles=None,
                        remember_key=None, title=None):
        """Ask the user to map semantic roles to actual CSV columns.

        Optional mappings may remain empty. A remember key stores the mapping
        beside the CSV and reuses it while the header remains unchanged.
        """
        # Normalize native-dialog and drag-and-drop path separators so both
        # access routes share one remembered mapping file.
        csv_path = os.path.normpath(csv_path)
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, [])
            sample_rows = []
            for i, row in enumerate(reader):
                if i >= 3:
                    break
                sample_rows.append(row)

        mapping_path = f"{csv_path}.{remember_key}.mapping.json" if remember_key else None
        if mapping_path and os.path.isfile(mapping_path):
            try:
                with open(mapping_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                if saved.get("header") == header:
                    return saved.get("mapping")
            except Exception:
                pass

        from frameviewer.ui.csv_mapper_dialog import CsvColumnMapperDialog
        dlg = CsvColumnMapperDialog(
            self._mw, header, roles, sample_rows=sample_rows,
            optional_roles=optional_roles or [],
            title=title or "Associer les colonnes du CSV")
        if not dlg.exec():
            return None
        mapping = dlg.result_mapping()
        if mapping_path:
            try:
                with open(mapping_path, "w", encoding="utf-8") as f:
                    json.dump({"header": header, "mapping": mapping}, f)
            except Exception:
                pass
        return mapping

    # Interface extension points.
    def add_dock(self, widget, title, area="right"):
        """Add a plugin-owned dock and tabify it with existing plugin docks."""
        from PySide6 import QtCore, QtWidgets
        areas = {
            "left": QtCore.Qt.LeftDockWidgetArea,
            "right": QtCore.Qt.RightDockWidgetArea,
            "top": QtCore.Qt.TopDockWidgetArea,
            "bottom": QtCore.Qt.BottomDockWidgetArea,
        }
        qt_area = areas.get(area, QtCore.Qt.RightDockWidgetArea)
        widget.setProperty("frameviewer_plugin_owned", True)
        dock = QtWidgets.QDockWidget(title, self._mw)
        dock.setProperty("frameviewer_plugin_owned", True)
        dock.setWidget(widget)
        dock.setMinimumWidth(220)
        from frameviewer.ui import icons as _icons
        _icons.restyle_dock_titlebar_buttons(dock)
        last = getattr(self._mw, "_plugin_last_dock", None)
        if last is not None:
            try:
                self._mw.tabifyDockWidget(last, dock)
            except RuntimeError:
                last = None
        if last is None:
            self._mw.addDockWidget(qt_area, dock)
        dock.show()
        dock.raise_()
        self._mw._plugin_last_dock = dock
        return dock

    def add_menu_action(self, label, callback):
        """Add a loader-protected callback to the plugin action section."""
        self._mw._plugin_menu_actions.append((label, callback))

    def status(self, msg, timeout=3000):
        self._mw.statusBar().showMessage(str(msg), timeout)

    def log(self, msg, level="info"):
        """Write a structured message to the plugin console."""
        import os
        import sys
        lineno = src = None
        try:
            f = sys._getframe(1)
            lineno = f.f_lineno
            src = os.path.basename(f.f_code.co_filename)
        except Exception:
            pass
        self._mw._plugin_loader.log(str(msg), level=level, pid=self._plugin_id,
                                    lineno=lineno, src=src)

    def request_repaint(self):
        """Request a repaint of the active view."""
        if self._mw._raw is not None:
            self._mw._display()

    # External environment helpers keep optional heavy libraries out of the app.
    def add_site_packages(self, path):
        """Append an external site-packages path without shadowing bundled modules."""
        import os
        import sys
        if path and os.path.isdir(path) and path not in sys.path:
            sys.path.append(path)

    def external_libs_path(self):
        """Return the external library path from ``FV_EXTERNAL_LIBS``.

            pd = api.external_import("pandas", api.external_libs_path())

        Source environments with an importable dependency need no path.
        """
        import os
        return os.environ.get("FV_EXTERNAL_LIBS", "")

    def external_import(self, module, site_packages=None):
        """Import a module after appending optional external package paths.

            pd = api.external_import("pandas", api.external_libs_path() or None)

        Binary extensions must target the same Python version. Use
        ``run_external`` when process isolation is required.
        """
        import importlib
        paths = []
        if site_packages:
            paths = [site_packages] if isinstance(site_packages, str) else list(site_packages)
            for sp in paths:
                self.add_site_packages(sp)
        try:
            return importlib.import_module(module)
        except ImportError as e:
            hint = _env_version_hint(paths)
            if hint:
                raise ImportError(f"{e} -- {hint}") from e
            raise

    def env_report(self):
        """Return executable, frozen-state, and loaded-module diagnostics."""
        from frameviewer.plugins.loader import environment_report
        return environment_report()

    def run_external(self, python_exe, code, timeout=30):
        """Execute code in an isolated Python interpreter and return stdout."""
        import subprocess
        r = subprocess.run([python_exe, "-c", code], capture_output=True,
                           text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "subprocess a echoue")
        return r.stdout
