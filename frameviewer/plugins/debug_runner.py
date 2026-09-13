# -*- coding: utf-8 -*-
"""Run and debug a FrameViewer plugin outside the Qt interface.

The runner accepts an exported frame or image, calls rendering hooks, and
writes their output to disk. It uses the selected Python environment, which
makes breakpoints and external dependencies straightforward. Paths come only
from arguments and runtime discovery.

Example from the directory containing the ``frameviewer`` package:

    python -m frameviewer.plugins.debug_runner \
        --plugin plugins_demo_showcase \
        --npy "%APPDATA%/FrameViewer/debug/displayed.npy" \
        --input "csv_data=C:/chemin/vers/data.csv" \
        --frame-index 0

Outputs are written under the frame directory's ``out`` folder or ``--out``.
"""
import argparse
import glob
import os
import sys
import traceback
import types


# --------------------------------------------------------------------------
# Minimal MainWindow substitute backed by one frame.
# --------------------------------------------------------------------------
class _DummyStatusBar:
    def showMessage(self, *a, **k):
        pass


class _DummyLoader:
    def log(self, msg, level="info", pid=None, lineno=None, src=None):
        tag = f"[{pid}] " if pid else ""
        loc = ""
        if lineno:
            loc = f"[{src}:{lineno}] " if src else f"[L{lineno}] "
        lv = "" if level == "info" else f"({level}) "
        print(f"{tag}{loc}{lv}{msg}")


class _SidecarOffset:
    def value(self):
        return 0


class _SingleFrameSource:
    def __init__(self, frame, name):
        self._frame = frame
        self.name = name
        self.count = 1

    def get(self, _i):
        return self._frame


class HeadlessMW:
    """Minimal MainWindow substitute for running a plugin without the UI."""

    def __init__(self, frame, name="debug", elements=None):
        self._raw = frame
        self.cur = 0
        self.source = _SingleFrameSource(frame, name)
        self.overlays = {}
        self.sidecar_offset = _SidecarOffset()
        self._plugin_menu_actions = []
        self._plugin_loader = _DummyLoader()
        self._elements = dict(elements or {})

    def _apply_rotation(self, raw):
        return raw

    def process(self, rot):
        return rot

    def _visible_annots(self, _i):
        return []

    def _plugin_elements(self, _pid, _view_key):
        return dict(self._elements)

    def statusBar(self):
        return _DummyStatusBar()

    def _display(self):
        pass


# --------------------------------------------------------------------------
# Frame and image helpers.
# --------------------------------------------------------------------------
def _to_bgr8(img):
    """Normalize grayscale/BGR/BGRA arrays to uint8 BGR."""
    import cv2
    import numpy as np
    if img.dtype != np.uint8:
        mx = float(img.max()) if img.size else 1.0
        scale = 255.0 / mx if mx > 0 else 1.0
        img = (img.astype(np.float32) * scale).clip(0, 255).astype(np.uint8)
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return img


def _composite(base_bgr, overlay_bgra):
    """Alpha-composite a BGRA overlay over a BGR base."""
    import numpy as np
    base = _to_bgr8(base_bgr)
    h = min(base.shape[0], overlay_bgra.shape[0])
    w = min(base.shape[1], overlay_bgra.shape[1])
    out = base.copy()
    ov = overlay_bgra[:h, :w].astype(np.float32)
    a = ov[:, :, 3:4] / 255.0
    out[:h, :w] = (ov[:, :, :3] * a + out[:h, :w].astype(np.float32) * (1 - a)).astype(np.uint8)
    return out


def _load_frame(args):
    import numpy as np
    if args.npy:
        if not os.path.isfile(args.npy):
            raise SystemExit(f"Frame introuvable : {args.npy}\n"
                             "Exporte-la depuis l'app (Console > Exporter frame).")
        return np.load(args.npy)
    if args.image:
        import cv2
        img = cv2.imread(args.image, cv2.IMREAD_UNCHANGED)
        if img is None:
            raise SystemExit(f"Image illisible : {args.image}")
        return img
    raise SystemExit("Fournis une frame : --npy <fichier.npy> ou --image <fichier>.")


def _resolve_plugin_folder(name):
    """Resolve a plugin path, package name, or identifier from known roots."""
    from frameviewer.plugins.loader import (PLUGIN_FOLDER_PREFIX, plugins_roots)
    if os.path.isdir(name) and os.path.isfile(os.path.join(name, "plugin.py")):
        return os.path.abspath(name)
    candidates = [name]
    if not name.startswith(PLUGIN_FOLDER_PREFIX):
        candidates.append(PLUGIN_FOLDER_PREFIX + name)
    for root in plugins_roots():
        for cand in candidates:
            folder = os.path.join(root, cand)
            if os.path.isfile(os.path.join(folder, "plugin.py")):
                return folder
    raise SystemExit(f"Plugin '{name}' introuvable dans : {', '.join(plugins_roots())}")


def _load_plugin_instance(folder):
    """Compile plugin.py directly and return its instance and manifest."""
    from frameviewer.plugins import manifest as _manifest
    mod_path = os.path.join(folder, "plugin.py")
    with open(mod_path, "r", encoding="utf-8") as f:
        src = f.read()
    module = types.ModuleType("frameviewer_plugin_debug")
    module.__file__ = mod_path
    # The real filename lets debugger breakpoints bind to plugin.py.
    code = compile(src, mod_path, "exec")
    # Match loader behavior so sibling helper imports work.
    if folder not in sys.path:
        sys.path.insert(0, folder)
    exec(code, module.__dict__)
    cls = module.__dict__.get("PLUGIN")
    if cls is None:
        raise SystemExit(f"{mod_path} : pas d'attribut PLUGIN = <classe>")
    return cls(), _manifest.load(folder)


def _debug_dir(folder):
    """Return the plugin-specific debug directory."""
    return os.path.join(folder, "debug")


def _deployed_plugin_roots():
    """Return deployed plugin roots as seen by the installed application."""
    from frameviewer.plugins.loader import plugins_root, user_plugins_dir
    roots = [(user_plugins_dir(), "utilisateur")]
    if getattr(sys, "frozen", False):
        roots.append((os.path.join(os.path.dirname(sys.executable), "plugins"),
                      "a cote de l'exe"))
    else:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for rel in (os.path.join("output_compacte", "exe", "plugins"),
                    os.path.join("output_with_folder", "app", "FrameViewer", "plugins")):
            roots.append((os.path.join(base, rel), "a cote de l'exe"))
    if not any(os.path.isdir(r) for r, _ in roots):
        roots.append((plugins_root(), "sources (repli)"))
    return roots


def _list_plugins_ordered():
    """List deployed plugin folders in priority order without duplicates."""
    from frameviewer.plugins.loader import PLUGIN_FOLDER_PREFIX
    out, seen = [], set()
    for root, tag in _deployed_plugin_roots():
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            folder = os.path.join(root, entry)
            if (entry.startswith(PLUGIN_FOLDER_PREFIX)
                    and os.path.isfile(os.path.join(folder, "plugin.py"))
                    and entry not in seen):
                seen.add(entry)
                out.append((f"{entry}  [{tag}]", folder))
    return out


def _pick(title, items):
    """Select one labeled value interactively, choosing a sole item directly."""
    if not items:
        raise SystemExit(f"{title} : aucune option disponible.")
    if len(items) == 1:
        print(f"{title} -> {items[0][0]} (seul choix)")
        return items[0][1]
    print(title)
    for i, (label, _v) in enumerate(items, 1):
        print(f"  {i}. {label}")
    try:
        raw = input("Choix [1] : ").strip() or "1"
        idx = int(raw)
    except (EOFError, ValueError):
        raise SystemExit("Choix invalide.")
    if not (1 <= idx <= len(items)):
        raise SystemExit("Choix hors bornes.")
    return items[idx - 1][1]


def _frame_index_from_name(path):
    """Extract a frame index from an exported frame filename."""
    if not path:
        return None
    import re
    m = re.search(r"frame(\d+)", os.path.basename(path))
    return int(m.group(1)) if m else None


def _input_candidates(folder):
    """List candidate plugin inputs while excluding frames and runner outputs."""
    cands = []
    for sub in ("data", "debug"):
        d = os.path.join(folder, sub)
        if not os.path.isdir(d):
            continue
        for p in sorted(glob.glob(os.path.join(d, "**", "*"), recursive=True)):
            if not os.path.isfile(p):
                continue
            low = p.lower()
            if low.endswith(".npy"):
                continue
            if os.path.basename(os.path.dirname(p)) == "out":
                continue
            cands.append(p)
    return cands


def _parse_inputs(pairs):
    contract = {}
    for item in pairs or []:
        if "=" not in item:
            raise SystemExit(f"--input attend nom=chemin, recu : {item!r}")
        name, path = item.split("=", 1)
        contract[name.strip()] = os.path.normpath(path.strip())
    return contract


# --------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Debugger un plugin FrameViewer hors interface (frame -> sortie).")
    parser.add_argument("--plugin",
                        help="dossier plugins_xxx (nom, id ou chemin) ; "
                             "omis -> choix interactif (user d'abord, puis exe)")
    parser.add_argument("--npy", help="frame exportee depuis l'app (.npy)")
    parser.add_argument("--image", help="image quelconque a utiliser comme frame")
    parser.add_argument("--input", action="append", default=[], metavar="nom=chemin",
                        help="fichier d'entree du plugin (repetable) -> self.<nom>")
    parser.add_argument("--frame-index", type=int, default=None,
                        help="numero de frame passe aux hooks ; si omis, deduit du "
                             "nom du .npy (frameNNNN.npy) sinon 0")
    parser.add_argument("--out", help="dossier de sortie (defaut : <frame>/out)")
    parser.add_argument("--site-packages",
                        help="site-packages externe a ajouter a sys.path (pandas...)")
    args = parser.parse_args(argv)

    if args.site_packages and os.path.isdir(args.site_packages):
        sys.path.append(args.site_packages)

    from frameviewer.plugins.loader import PLUGIN_FOLDER_PREFIX

    # Resolve the plugin before importing heavy diagnostics dependencies.
    if args.plugin:
        folder = _resolve_plugin_folder(args.plugin)
    else:
        folder = _pick("Quel plugin debugger ?", _list_plugins_ordered())
    dbg = _debug_dir(folder)

    # Resolve an explicit frame or select one from the plugin debug directory.
    frame_src = args.npy or args.image
    if args.npy or args.image:
        frame = _load_frame(args)
    else:
        import numpy as np
        npys = [(os.path.basename(p), p)
                for p in sorted(glob.glob(os.path.join(dbg, "*.npy")))]
        if npys:
            frame_src = _pick("Quelle frame ?", npys)
            frame = np.load(frame_src)
        else:
            # A neutral synthetic frame keeps no-input plugin debugging usable.
            print(f"Aucune frame exportee dans {dbg}\n"
                  "-> frame de test synthetique (gris 720x1280). Pour une vraie "
                  "frame : app > Console > Exporter frame courante.")
            frame = np.full((720, 1280, 3), 90, np.uint8)
            frame_src = None
    contract = _parse_inputs(args.input)
    # Offer discovered data files for declared inputs not supplied explicitly.
    if not args.input:
        from frameviewer.plugins import manifest as _manifest
        declared = [i.get("name") for i in _manifest.load(folder).get("inputs", [])
                    if i.get("name")]
        if declared:
            cands = _input_candidates(folder)
            for name in declared:
                items = [(os.path.relpath(p, folder), p) for p in cands]
                items.append(("(aucune -> fallback du plugin)", None))
                chosen = _pick(f"Fichier pour l'entree self.{name} ?", items)
                if chosen:
                    contract[name] = chosen
    # Prefer an explicit frame index, then the exported filename, then zero.
    frame_idx = args.frame_index
    if frame_idx is None:
        frame_idx = _frame_index_from_name(frame_src)
        if frame_idx is None:
            frame_idx = 0
        else:
            print(f"(frame-index deduit du nom : {frame_idx})")

    # Import heavy diagnostics dependencies only after interactive choices.
    import cv2
    from frameviewer.plugins.api import FrameViewerPlugin, PluginAPI
    from frameviewer.plugins.loader import environment_report
    print()
    print(environment_report())
    print()
    print(f"Plugin  : {folder}")
    print(f"Frame   : shape={frame.shape} dtype={frame.dtype}")
    if contract:
        print("Entrees :")
        for k, v in contract.items():
            print(f"  self.{k} = {v}  ({'ok' if os.path.isfile(v) else 'ABSENT'})")
    print()

    inst, manifest = _load_plugin_instance(folder)
    pid = os.path.basename(folder)
    if pid.startswith(PLUGIN_FOLDER_PREFIX):
        pid = pid[len(PLUGIN_FOLDER_PREFIX):]

    mw = HeadlessMW(frame, name=pid)
    mw.cur = frame_idx
    api = PluginAPI(mw, folder, pid, contract=contract)
    try:
        inst.on_load(api)
    except Exception:
        print("on_load a leve une exception :")
        traceback.print_exc()
    # Bind current input paths after on_load, matching the rendering contract.
    for inp in manifest.get("inputs", []) or []:
        nm = inp.get("name")
        if nm:
            setattr(inst, nm, contract.get(nm))

    out_dir = args.out or os.path.join(dbg, "out")
    os.makedirs(out_dir, exist_ok=True)
    h, w = frame.shape[:2]
    cls = type(inst)
    wrote = []

    # Build composited.png in the same patch, alpha-overlay, shape order as UI.
    from frameviewer.core.annotations import bake_sidecar_overlays
    from frameviewer.core.plugin_render import composite_patches
    full = _to_bgr8(frame)
    drew = False

    if cls.render_patch is not FrameViewerPlugin.render_patch:
        try:
            pimg = inst.render_patch(api, frame_idx, (max(1, w // 4), max(1, h // 4)))
            if pimg is None:
                print("render_patch -> None")
            else:
                full = composite_patches(full, [{
                    "image": pimg,
                    "corner": getattr(inst, "corner", "tr"),
                    "margin": getattr(inst, "margin", 8)}])
                drew = True
                print(f"render_patch -> shape={pimg.shape} dtype={pimg.dtype}")
        except Exception:
            print("render_patch EXCEPTION :")
            traceback.print_exc()

    if cls.render_overlay is not FrameViewerPlugin.render_overlay:
        try:
            ov = inst.render_overlay(api, frame_idx, (w, h))
            if ov is None:
                print("render_overlay -> None (rien a dessiner pour cette frame)")
            else:
                p_ov = os.path.join(out_dir, "overlay.png")
                cv2.imwrite(p_ov, ov)
                wrote.append(p_ov)
                full = _composite(full, ov)
                drew = True
                print(f"render_overlay -> shape={ov.shape} dtype={ov.dtype}")
        except Exception:
            print("render_overlay EXCEPTION :")
            traceback.print_exc()

    if cls.get_overlays is not FrameViewerPlugin.get_overlays:
        try:
            res = inst.get_overlays(api, frame_idx) or []
            print(f"get_overlays -> {len(res)} forme(s)")
            if res:
                full = bake_sidecar_overlays(full, res)
                drew = True
        except Exception:
            print("get_overlays EXCEPTION :")
            traceback.print_exc()

    if drew:
        p_co = os.path.join(out_dir, "composited.png")
        cv2.imwrite(p_co, full)
        wrote.append(p_co)

    if cls.render_panel is not FrameViewerPlugin.render_panel:
        try:
            pan = inst.render_panel(api, frame_idx, (w, h))
            if pan is None:
                print("render_panel -> None")
            else:
                p_pn = os.path.join(out_dir, "panel.png")
                cv2.imwrite(p_pn, _to_bgr8(pan))
                wrote.append(p_pn)
                print(f"render_panel -> shape={pan.shape} dtype={pan.dtype}")
        except Exception:
            print("render_panel EXCEPTION :")
            traceback.print_exc()

    print()
    if wrote:
        print("Sorties ecrites :")
        for p in wrote:
            print(f"  {p}")
    else:
        print("Aucune image ecrite (le plugin ne dessine pas d'overlay/panel "
              "pour cette frame, ou renvoie None).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
