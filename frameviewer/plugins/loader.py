# -*- coding: utf-8 -*-
"""Discover and load adjacent or user-specific FrameViewer plugins.

Each package exposes ``PLUGIN = <FrameViewerPlugin subclass>`` from its
``plugin.py`` module. Plugin code runs in the application process and must be
trusted; this is not a security sandbox. Imports and hook calls are isolated
individually so one failing plugin is disabled without stopping the app or
other overlays.
"""
import contextlib
import os
import re
import sys
import traceback
import types

from frameviewer.plugins import manifest as _manifest
from frameviewer.plugins.api import PluginAPI

PLUGIN_FOLDER_PREFIX = "plugins_"
PLUGIN_MODULE_FILE = "plugin.py"

# A leading [tag] identifies the plugin in captured output.
_TAG_RE = re.compile(r"^\s*\[([^\]]+)\]\s*")
# Error-like words promote captured output to the error level.
_ERR_RE = re.compile(r"ERREUR|EXCEPTION|Traceback|echec|échec|failed|Error", re.I)


def _now_hms():
    import time
    return time.strftime("%H:%M:%S")


def _perf():
    import time
    return time.perf_counter()


def _indent(text, pad="    "):
    return "\n".join(pad + ln for ln in text.splitlines())


def _purge_sibling_modules(folder):
    """Remove loaded sibling helpers so reloads always use current source."""
    base = os.path.abspath(folder) + os.sep
    try:
        local = {os.path.splitext(f)[0] for f in os.listdir(folder)
                 if f.endswith(".py") and f != PLUGIN_MODULE_FILE}
    except OSError:
        local = set()
    for name in list(sys.modules):
        if name in local:
            del sys.modules[name]
            continue
        mod = sys.modules.get(name)
        fp = getattr(mod, "__file__", None)
        if fp and os.path.abspath(fp).startswith(base):
            del sys.modules[name]


def _describe_result(res):
    """Return a compact hook-result description for test reports."""
    import numpy as np
    if res is None:
        return "None (rien a dessiner)"
    if isinstance(res, np.ndarray):
        return f"ndarray shape={res.shape} dtype={res.dtype}"
    if isinstance(res, list):
        return f"list de {len(res)} element(s)"
    return type(res).__name__


class _StreamTee:
    """Tee complete output lines to the plugin log and an optional stream."""

    def __init__(self, prefix, tag, sink, original):
        self._prefix = prefix
        self._tag = tag
        self._sink = sink            # callable(str)
        self._original = original    # Original stream or None.
        self._buf = ""

    def write(self, s):
        if self._original is not None:
            try:
                self._original.write(s)
            except Exception:
                pass
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._sink(f"{self._prefix} {self._tag}{line}")
        return len(s)

    def flush(self):
        if self._original is not None:
            try:
                self._original.flush()
            except Exception:
                pass

    def close(self):
        if self._buf.strip():
            self._sink(f"{self._prefix} {self._tag}{self._buf}")
        self._buf = ""

    def isatty(self):
        return False


def environment_report(modules=("numpy", "cv2", "pandas", "polars", "PySide6")):
    """Report the executable, frozen state, and actual module locations."""
    import importlib
    lines = []
    lines.append(f"Python       : {sys.version.split()[0]}")
    lines.append(f"Executable   : {sys.executable}")
    lines.append(f"Frozen (exe) : {getattr(sys, 'frozen', False)}")
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        lines.append(f"_MEIPASS     : {meipass}")
    lines.append("")
    lines.append("Modules (version + fichier reellement charge) :")
    for name in modules:
        try:
            m = importlib.import_module(name)
            ver = getattr(m, "__version__", "?")
            loc = getattr(m, "__file__", "?")
            lines.append(f"  {name:<9} v{ver}")
            lines.append(f"            {loc}")
        except Exception as e:
            lines.append(f"  {name:<9} NON IMPORTABLE : {e}")
    lines.append("")
    lines.append("site-packages externes sur sys.path :")
    ext = [p for p in sys.path if "site-packages" in p.replace("\\", "/").lower()]
    if ext:
        for p in ext:
            lines.append(f"  [{'ok' if os.path.isdir(p) else 'ABSENT'}] {p}")
    else:
        lines.append("  (aucun -- aucune lib externe branchee via add_site_packages)")
    return "\n".join(lines)


def _alpha_stack(base, top):
    """Alpha-stack two BGRA overlays, clipping to common dimensions."""
    import numpy as np
    h = min(base.shape[0], top.shape[0])
    w = min(base.shape[1], top.shape[1])
    if h <= 0 or w <= 0:
        return base
    out = base.copy()
    b = out[:h, :w].astype(np.float32)
    t = top[:h, :w].astype(np.float32)
    ta = t[:, :, 3:4] / 255.0
    ba = b[:, :, 3:4] / 255.0
    oa = ta + ba * (1.0 - ta)
    rgb = t[:, :, :3] * ta + b[:, :, :3] * ba * (1.0 - ta)
    safe = np.where(oa > 0, oa, 1.0)
    out[:h, :w, :3] = np.clip(rgb / safe, 0, 255).astype(np.uint8)
    out[:h, :w, 3:4] = np.clip(oa * 255.0, 0, 255).astype(np.uint8)
    return out


def plugins_root():
    """Return the application plugin directory in source or frozen mode."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "plugins")


def user_config_dir():
    """Return and create the platform-standard per-user configuration path."""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    d = os.path.join(base, "FrameViewer")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def user_plugins_dir():
    """Return and create the per-user plugin directory."""
    d = os.path.join(user_config_dir(), "plugins")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def plugins_roots():
    """Return plugin search roots in priority order; first duplicate wins."""
    return [plugins_root(), user_plugins_dir()]


class LoadedPlugin:
    def __init__(self, plugin_id, folder):
        self.plugin_id = plugin_id
        self.folder = folder
        self.instance = None
        self.enabled = False
        self.error = None
        self.manifest = _manifest.load(folder)   # Version-two input contract.
        self.last_run = None                     # Last execution time.
        self.element_keys = {}                   # Overlay element key bindings.

    @property
    def kind(self):
        return self.manifest.get("kind", "code")

    @property
    def name(self):
        return self.instance.name if self.instance is not None else self.plugin_id


class PluginLoader:
    """Own plugin discovery, execution, state, and error isolation."""

    def __init__(self, mw):
        self._mw = mw
        self.plugins = []          # [LoadedPlugin, ...]
        self._log_records = []     # [{ts,pid,level,lineno,src,text}, ...]
        # Enabled identifiers survive reloads and are restored before discovery.
        self._enabled_ids = set()

    def log(self, msg, level="info", pid=None, lineno=None, src=None):
        """Append a structured log record and infer missing metadata."""
        text = str(msg)
        if pid is None:
            m = _TAG_RE.match(text)
            if m:
                pid = m.group(1)
                text = text[m.end():]
        if level == "info" and _ERR_RE.search(text):
            level = "error"
        self._log_records.append({"ts": _now_hms(), "pid": pid, "level": level,
                                  "lineno": lineno, "src": src, "text": text})
        # Bound memory use during long sessions.
        if len(self._log_records) > 5000:
            del self._log_records[: len(self._log_records) - 5000]

    def log_lines(self):
        """Return plain-text log lines with timestamps and source locations."""
        out = []
        for r in self._log_records:
            tag = f"[{r['pid']}] " if r.get("pid") else ""
            if r.get("lineno"):
                loc = f"{r['src']}:{r['lineno']}" if r.get("src") else f"L{r['lineno']}"
                ln = f"[{loc}] "
            else:
                ln = ""
            out.append(f"{r['ts']}  {tag}{ln}- {r['text']}")
        return out

    def log_records(self):
        """Return structured records for the colored console."""
        return list(self._log_records)

    def clear_log(self):
        self._log_records = []

    @contextlib.contextmanager
    def _capture(self, prefix):
        """Capture a plugin call's stdout and stderr into the plugin log."""
        out = _StreamTee(prefix, "", lambda s: self.log(s, level="info"), sys.stdout)
        err = _StreamTee(prefix, "", lambda s: self.log(s, level="error"), sys.stderr)
        old_o, old_e = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            yield
        finally:
            out.close()
            err.close()
            sys.stdout, sys.stderr = old_o, old_e

    def discover_and_load(self):
        self.plugins = []
        seen = set()   # Loaded identifiers; the first search root wins.
        for root in plugins_roots():
            if not os.path.isdir(root):
                continue
            for entry in sorted(os.listdir(root)):
                folder = os.path.join(root, entry)
                if not os.path.isdir(folder) or not entry.startswith(PLUGIN_FOLDER_PREFIX):
                    continue
                mod_path = os.path.join(folder, PLUGIN_MODULE_FILE)
                if not os.path.isfile(mod_path):
                    continue
                plugin_id = entry[len(PLUGIN_FOLDER_PREFIX):] or entry
                if plugin_id in seen:
                    self.log(f"[{plugin_id}] doublon ignore ({folder}).")
                    continue
                seen.add(plugin_id)
                lp = LoadedPlugin(plugin_id, folder)
                self.plugins.append(lp)
                self._load_one(lp, mod_path)
        if not self.plugins:
            self.log("Aucun plugin decouvert dans : " + ", ".join(plugins_roots()))

    def _load_one(self, lp, mod_path):
        try:
            with open(mod_path, "r", encoding="utf-8") as f:
                src = f.read()
            mod_name = f"frameviewer_plugin_{lp.plugin_id}"
            module = types.ModuleType(mod_name)
            module.__file__ = mod_path
            # Compile directly from source. Timestamp-granularity issues in the
            # bytecode cache otherwise make rapid save/reload cycles stale.
            code = compile(src, mod_path, "exec")
            api = PluginAPI(self._mw, lp.folder, lp.plugin_id)
            # Temporarily prepend the package directory so plugin.py can import
            # sibling helpers, after purging stale sibling modules.
            _purge_sibling_modules(lp.folder)
            sys.path.insert(0, lp.folder)
            try:
                with self._capture(f"[{lp.plugin_id}]"):
                    exec(code, module.__dict__)
                    cls = module.__dict__.get("PLUGIN")
                    if cls is None:
                        raise ImportError(f"{mod_path} : pas d'attribut PLUGIN = <classe>")
                    instance = cls()      # Some plugins import dependencies here.
                    instance.on_load(api)
            finally:
                try:
                    sys.path.remove(lp.folder)
                except ValueError:
                    pass
            lp.instance = instance
            self._sync_element_keymap(lp, api)
            # A plugin is active only when its identifier was explicitly enabled.
            lp.enabled = lp.plugin_id in self._enabled_ids
            lp.error = None
            self.log(f"[{lp.plugin_id}] charge.")
        except Exception:
            lp.error = traceback.format_exc()
            lp.enabled = False
            self.log(f"[{lp.plugin_id}] ERREUR au chargement :\n{lp.error}")

    def reload_all(self):
        for lp in self.plugins:
            if lp.instance is not None:
                api = PluginAPI(self._mw, lp.folder, lp.plugin_id)
                self._safe_call(lp, "on_unload", api)
        self._mw._plugin_menu_actions = []
        self.discover_and_load()

    def set_enabled(self, plugin_id, enabled):
        if enabled:
            self._enabled_ids.add(plugin_id)
        else:
            self._enabled_ids.discard(plugin_id)
        for lp in self.plugins:
            if lp.plugin_id == plugin_id and lp.instance is not None:
                lp.enabled = bool(enabled)

    def enabled_ids(self):
        """Return enabled plugin identifiers for persistence."""
        return sorted(self._enabled_ids)

    def set_enabled_ids(self, ids):
        """Restore enabled identifiers before discovery at startup."""
        self._enabled_ids = set(ids or [])

    def enabled_plugins(self):
        return [lp for lp in self.plugins if lp.enabled and lp.instance is not None]

    def _safe_call(self, lp, method_name, *args, default=None):
        if lp.instance is None or not lp.enabled:
            return default
        try:
            fn = getattr(lp.instance, method_name, None)
            if fn is None:
                return default
            with self._capture(f"[{lp.plugin_id}]"):
                return fn(*args)
        except Exception:
            lp.error = traceback.format_exc()
            lp.enabled = False
            self.log(f"[{lp.plugin_id}] ERREUR dans {method_name} -- désactivé :\n{lp.error}")
            return default

    def run_plugin_test(self, plugin_id, frame_idx, size):
        """Run rendering hooks on demand without changing enabled state."""
        from frameviewer.plugins.api import FrameViewerPlugin, PluginAPI
        lp = next((l for l in self.plugins if l.plugin_id == plugin_id), None)
        if lp is None:
            return f"Plugin '{plugin_id}' introuvable."
        if lp.instance is None:
            return f"Plugin '{plugin_id}' non charge :\n{_indent(lp.error or '')}"
        w, h = int(size[0]), int(size[1])
        out = [f"=== Test '{lp.name}' | frame {frame_idx} | taille {w}x{h} ==="]
        contract = (self._visible_contracts(lp) or [{}])[0]
        self._bind_inputs(lp, contract)
        api = PluginAPI(self._mw, lp.folder, lp.plugin_id, contract=contract)
        cls = type(lp.instance)
        hooks = (
            ("get_overlays", (api, frame_idx)),
            ("render_overlay", (api, frame_idx, (w, h))),
            ("render_panel", (api, frame_idx, (w, h))),
        )
        ran = False
        for name, args in hooks:
            if getattr(cls, name) is getattr(FrameViewerPlugin, name):
                continue   # Hook not overridden.
            ran = True
            t0 = _perf()
            try:
                with self._capture(f"[{lp.plugin_id}][test]"):
                    res = getattr(lp.instance, name)(*args)
                dt = (_perf() - t0) * 1000.0
                out.append(f"  {name} -> {_describe_result(res)}  ({dt:.1f} ms)")
            except Exception:
                out.append(f"  {name} -> EXCEPTION :\n{_indent(traceback.format_exc())}")
        if not ran:
            out.append("  (aucun hook de rendu surcharge : "
                       "get_overlays / render_overlay / render_panel)")
        return "\n".join(out)

    # Aggregated hooks called by MainWindow for the active/primary view.
    def collect_overlays(self, frame_idx):
        out = []
        for lp in self.enabled_plugins():
            api = PluginAPI(self._mw, lp.folder, lp.plugin_id)
            res = self._safe_call(lp, "get_overlays", api, frame_idx, default=[])
            if res:
                out.extend(res)
        return out

    def collect_multiview_overlays(self, views_context):
        """Collect cross-view shapes from active plugins with isolated calls."""
        from frameviewer.plugins.api import FrameViewerPlugin
        out = []
        # Merge keys from every visible view so multi-view inputs do not depend
        # on which view is active.
        view_keys = [v.get("view_key") for v in (views_context or {}).get("views", [])]
        for lp in self.enabled_plugins():
            if type(lp.instance).get_multiview_overlays is \
                    FrameViewerPlugin.get_multiview_overlays:
                continue   # Hook not overridden.
            merged = self._merged_contract(lp, view_keys)
            self._bind_inputs(lp, merged)
            api = PluginAPI(self._mw, lp.folder, lp.plugin_id, contract=merged)
            res = self._safe_call(lp, "get_multiview_overlays", api,
                                  views_context, default=[])
            if res:
                out.extend(res)
        return out

    def _merged_contract(self, lp, view_keys):
        """Merge plugin input paths across all supplied and active views."""
        merged = {}
        keys = [None] + [k for k in view_keys if k and k != "__none__"]
        for vk in keys:
            for contract in (self._visible_contracts(lp, vk) or []):
                for name, path in contract.items():
                    if path and not merged.get(name):
                        merged[name] = path
        return merged

    def collect_patches(self, frame_idx, size):
        out = []
        for lp in self.enabled_plugins():
            api = PluginAPI(self._mw, lp.folder, lp.plugin_id)
            img = self._safe_call(lp, "render_patch", api, frame_idx, size, default=None)
            if img is not None:
                out.append({
                    "image": img,
                    "corner": getattr(lp.instance, "corner", "tr"),
                    "margin": getattr(lp.instance, "margin", 8),
                })
        return out

    def build_panel(self, lp):
        api = PluginAPI(self._mw, lp.folder, lp.plugin_id)
        return self._safe_call(lp, "build_panel", api, default=None)

    def collect_menu_actions(self, lp):
        """Return menu actions declared by one plugin."""
        api = PluginAPI(self._mw, lp.folder, lp.plugin_id)
        return self._safe_call(lp, "menu_actions", api, default=[]) or []

    def _sync_element_keymap(self, lp, api):
        """Synchronize manifest key bindings with declared overlay elements."""
        from frameviewer.plugins.api import FrameViewerPlugin
        lp.element_keys = {}
        inst = lp.instance
        if inst is None or type(inst).overlay_elements is FrameViewerPlugin.overlay_elements:
            return
        # Call directly during loading because inactive plugins are skipped by
        # the normal safe-call path.
        try:
            elems = inst.overlay_elements(api) or []
        except Exception as e:
            self.log(f"[{lp.plugin_id}] overlay_elements echoue (sync touches) : {e}",
                     level="warn")
            return
        keys = []
        for item in elems:
            try:
                k = item[0]
            except (TypeError, IndexError):
                continue
            if isinstance(k, str) and k and k not in keys:
                keys.append(k)
        current = dict(lp.manifest.get("element_keys") or {})
        synced = {k: current.get(k, "m") for k in keys}
        lp.element_keys = synced
        if synced != current:
            if synced:
                lp.manifest["element_keys"] = synced
            else:
                lp.manifest.pop("element_keys", None)
            try:
                _manifest.save(lp.folder, lp.manifest)
                self.log(f"[{lp.plugin_id}] element_keys synchronise -> manifest.json")
            except OSError as e:
                self.log(f"[{lp.plugin_id}] manifest non ecrit ({e})", level="warn")

    # Code-overlay model: rendering and pointer hooks.
    def _bind_inputs(self, lp, contract):
        """Bind current contract paths to same-named plugin attributes."""
        if lp.instance is None:
            return
        for inp in lp.manifest.get("inputs", []) or []:
            name = inp.get("name")
            if name:
                try:
                    setattr(lp.instance, name, contract.get(name))
                except (AttributeError, TypeError):
                    pass

    def _visible_contracts(self, lp, view_key=None):
        """Return visible input contracts for a view, or an empty contract."""
        fn = getattr(self._mw, "_visible_contracts", None)
        if fn is None:
            return [{}]
        return fn(lp.plugin_id, view_key) or []

    def collect_overlay_image(self, frame_idx, size, view_key=None):
        """Alpha-compose active plugin overlays for each visible contract."""
        from frameviewer.plugins.api import FrameViewerPlugin
        w, h = int(size[0]), int(size[1])
        if w <= 0 or h <= 0:
            return None
        acc = None
        for lp in self.enabled_plugins():
            if type(lp.instance).render_overlay is FrameViewerPlugin.render_overlay:
                continue
            # Invoke once with an empty contract when no input exists. Plugins
            # can return None or provide an intentional fallback.
            for contract in (self._visible_contracts(lp, view_key) or [{}]):
                self._bind_inputs(lp, contract)
                api = PluginAPI(self._mw, lp.folder, lp.plugin_id, contract=contract, view_key=view_key)
                img = self._safe_call(lp, "render_overlay", api, frame_idx, (w, h), default=None)
                if img is not None:
                    lp.last_run = _now_hms()
                if img is None or getattr(img, "ndim", 0) != 3 or img.shape[2] != 4:
                    continue
                acc = img if acc is None else _alpha_stack(acc, img)
        return acc

    def render_plugin_panel(self, plugin_id, frame_idx, size):
        """Render the plugin-panel image for the first visible contract."""
        from frameviewer.plugins.api import FrameViewerPlugin
        lp = next((l for l in self.enabled_plugins() if l.plugin_id == plugin_id), None)
        if lp is None or type(lp.instance).render_panel is FrameViewerPlugin.render_panel:
            return None
        contracts = self._visible_contracts(lp) or [{}]
        self._bind_inputs(lp, contracts[0])
        api = PluginAPI(self._mw, lp.folder, lp.plugin_id, contract=contracts[0])
        return self._safe_call(lp, "render_panel", api, frame_idx, size, default=None)

    def dispatch_hover(self, frame_idx, x, y):
        from frameviewer.plugins.api import FrameViewerPlugin
        for lp in self.enabled_plugins():
            if type(lp.instance).on_view_hover is FrameViewerPlugin.on_view_hover:
                continue   # Avoid calls when hover is not implemented.
            contracts = self._visible_contracts(lp) or [{}]
            self._bind_inputs(lp, contracts[0])
            api = PluginAPI(self._mw, lp.folder, lp.plugin_id, contract=contracts[0])
            self._safe_call(lp, "on_view_hover", api, frame_idx, x, y)

    def dispatch_click(self, frame_idx, x, y):
        from frameviewer.plugins.api import FrameViewerPlugin
        for lp in self.enabled_plugins():
            if type(lp.instance).on_view_click is FrameViewerPlugin.on_view_click:
                continue
            contracts = self._visible_contracts(lp) or [{}]
            self._bind_inputs(lp, contracts[0])
            api = PluginAPI(self._mw, lp.folder, lp.plugin_id, contract=contracts[0])
            self._safe_call(lp, "on_view_click", api, frame_idx, x, y)

    def dispatch_key(self, frame_idx, key, text):
        """Dispatch an unconsumed key and report whether a plugin handled it."""
        from frameviewer.plugins.api import FrameViewerPlugin
        handled = False
        for lp in self.enabled_plugins():
            if type(lp.instance).on_view_key is FrameViewerPlugin.on_view_key:
                continue
            contracts = self._visible_contracts(lp) or [{}]
            self._bind_inputs(lp, contracts[0])
            api = PluginAPI(self._mw, lp.folder, lp.plugin_id, contract=contracts[0])
            if self._safe_call(lp, "on_view_key", api, frame_idx, key, text, default=False):
                handled = True
        return handled

    def dispatch_drop(self, path):
        """Offer an unrecognized dropped path to active plugins in load order."""
        accepting = []
        for lp in self.enabled_plugins():
            api = PluginAPI(self._mw, lp.folder, lp.plugin_id)
            if self._safe_call(lp, "accepts_drop", api, path, default=False):
                accepting.append((lp, api))
        if not accepting:
            return False
        lp, api = accepting[0]
        if len(accepting) > 1:
            others = ", ".join(l.name for l, _ in accepting[1:])
            self.log(f"Plusieurs plugins acceptent {path} -- '{lp.name}' "
                    f"prend la main (autres candidats : {others}).")
        self._safe_call(lp, "on_drop", api, path)
        return True
