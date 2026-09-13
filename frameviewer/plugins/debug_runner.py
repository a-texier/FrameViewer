# -*- coding: utf-8 -*-
"""Runner headless pour debugger un plugin FrameViewer HORS interface.

But : tester un plugin comme un simple backend -- on lui donne une frame (celle
exportee depuis l'app, ou une image), on appelle ses hooks de rendu, et on
ecrit la sortie sur disque. Aucun Qt, aucune fenetre : ca tourne dans un
interpreteur normal (ton env Python), donc pandas & co marchent, et surtout on
peut poser des BREAKPOINTS dans plugin.py et lancer sous le debugger VS Code.
Rien en dur : aucun chemin machine, tout vient des arguments et de sys.path.

Ca tourne dans l'interpreteur qui le lance (ton env Python habituel).

Depuis l'app : bouton « Console » puis « Exporter frame + commande VS Code »
ecrit la frame courante dans %APPDATA%/FrameViewer/debug/ et affiche la commande
prete a coller (ou utilise .vscode/launch.json « Debug plugin (frame courante) »).

Exemple (depuis le dossier FrameViewer, celui qui contient le package
frameviewer/) :

    python -m frameviewer.plugins.debug_runner \
        --plugin plugins_demo_showcase \
        --npy "%APPDATA%/FrameViewer/debug/displayed.npy" \
        --input "csv_data=C:/chemin/vers/data.csv" \
        --frame-index 0

Sorties ecrites dans <dossier de la frame>/out/ (ou --out) :
  overlay.png     : le BGRA renvoye par render_overlay (fond transparent)
  composited.png  : cet overlay compose PAR-DESSUS la frame (rendu final)
  panel.png       : l'image renvoyee par render_panel (carte du plugin)
"""
import argparse
import glob
import os
import sys
import traceback
import types


# --------------------------------------------------------------------------
# Faux MainWindow : uniquement ce que PluginAPI touche, adosse a une frame.
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
    """Stand-in minimal de MainWindow pour executer un plugin sans interface.
    Pipeline d'affichage neutre (raw == displayed) : suffisant pour tester les
    overlays ; les elements (cases On/Off) sont tous consideres actifs."""

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
# Helpers frame / image
# --------------------------------------------------------------------------
def _to_bgr8(img):
    """Normalise une frame quelconque (gris/BGR/BGRA, uint8/uint16/float) en
    BGR 8 bits, pour composer et enregistrer sans surprise."""
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
    """Compose un overlay BGRA par-dessus une base BGR (alpha par pixel)."""
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
    """Accepte un chemin de dossier, un nom de dossier (plugins_xxx) ou un id.
    Cherche dans les dossiers de plugins connus (livre + perso)."""
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
    """Importe plugin.py par compilation directe (memes regles que le loader,
    sans cache .pyc) et renvoie (instance, manifest)."""
    from frameviewer.plugins import manifest as _manifest
    mod_path = os.path.join(folder, "plugin.py")
    with open(mod_path, "r", encoding="utf-8") as f:
        src = f.read()
    module = types.ModuleType("frameviewer_plugin_debug")
    module.__file__ = mod_path
    # filename = chemin reel -> les breakpoints VS Code dans plugin.py se lient.
    code = compile(src, mod_path, "exec")
    # meme mecanique que le loader : dossier du plugin sur sys.path -> `import
    # mon_helper` (fichier voisin) fonctionne.
    if folder not in sys.path:
        sys.path.insert(0, folder)
    exec(code, module.__dict__)
    cls = module.__dict__.get("PLUGIN")
    if cls is None:
        raise SystemExit(f"{mod_path} : pas d'attribut PLUGIN = <classe>")
    return cls(), _manifest.load(folder)


def _debug_dir(folder):
    """Dossier debug/ propre au plugin : la ou l'app exporte les frames de test
    et ou le runner va les chercher pour le choix interactif."""
    return os.path.join(folder, "debug")


def _deployed_plugin_roots():
    """Racines de plugins DEPLOYES, comme les verrait l'app installee : user
    config (AppData) et 'a cote de l'exe'. En mode source (F5 depuis le depot),
    'a cote de l'exe' pointe sur les dossiers d'exe construits par les scripts de
    build (output_compacte / output_with_folder) s'ils existent. La copie source
    editable du depot (FrameViewer_Local/plugins) est volontairement EXCLUE quand
    un exe est construit : on debugge le plugin livre, pas la copie source. Repli
    sur la source uniquement si aucune racine deployee n'existe."""
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
    """[(label, folder)] : plugins DEPLOYES (user AppData + a cote de l'exe) ;
    dedup par nom de dossier (le 1er vu gagne). Voir _deployed_plugin_roots."""
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
    """Choix interactif en terminal : items = [(label, valeur)]. 1 seul choix ->
    auto. Renvoie la valeur choisie ; SystemExit si vide ou entree invalide."""
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
    """Deduit l'index de frame du nom de fichier exporte par l'app
    (frame<NNNN>.npy). Renvoie l'int ou None si le pattern ne matche pas."""
    if not path:
        return None
    import re
    m = re.search(r"frame(\d+)", os.path.basename(path))
    return int(m.group(1)) if m else None


def _input_candidates(folder):
    """Fichiers proposables comme entrees du plugin : tout ce qui est dans data/
    et debug/ SAUF les frames .npy et les sorties du runner (debug/out/)."""
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

    # 1) Plugin : argument, sinon choix interactif. On importe d'abord le STRICT
    #    minimum (leger) pour que le menu s'affiche TOUT DE SUITE ; cv2/PySide6 et
    #    le rapport d'environnement (lents) sont importes plus bas, apres les choix.
    if args.plugin:
        folder = _resolve_plugin_folder(args.plugin)
    else:
        folder = _pick("Quel plugin debugger ?", _list_plugins_ordered())
    dbg = _debug_dir(folder)

    # 2) Frame : --npy/--image explicite, sinon choix parmi les .npy exportes
    #    dans <plugin>/debug/ (associes a CE plugin).
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
            # Aucune frame exportee : au lieu de s'arreter, on donne une frame de
            # test grise pour que le plugin s'execute quand meme (les demos qui
            # generent/dessinent leurs donnees marchent directement sous F5).
            print(f"Aucune frame exportee dans {dbg}\n"
                  "-> frame de test synthetique (gris 720x1280). Pour une vraie "
                  "frame : app > Console > Exporter frame courante.")
            frame = np.full((720, 1280, 3), 90, np.uint8)
            frame_src = None
    contract = _parse_inputs(args.input)
    # Entrees declarees NON fournies en ligne de commande -> choix interactif :
    # on propose les fichiers trouves dans data/ et debug/ du plugin (le .npy de
    # frame est exclu). Sans candidat ou si l'utilisateur passe, l'entree reste
    # None (le plugin utilise alors son fallback / ne dessine rien).
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
    # index explicite prioritaire ; sinon deduit du nom (frameNNNN.npy) ; sinon 0.
    # L'app nomme l'export frame<cur:04d>.npy et fait num_image = frame_idx + 1 :
    # sans cette deduction, le debug dessinait la frame 0 sur une autre image.
    frame_idx = args.frame_index
    if frame_idx is None:
        frame_idx = _frame_index_from_name(frame_src)
        if frame_idx is None:
            frame_idx = 0
        else:
            print(f"(frame-index deduit du nom : {frame_idx})")

    # imports lourds + rapport d'environnement : APRES les choix, pour ne pas
    # retarder l'affichage du menu (cv2/PySide6/pandas mettent plusieurs secondes).
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
    # injecte self.<nom> = chemin APRES on_load (le loader lie les entrees avant
    # CHAQUE rendu, donc a l'etat courant ; on_load les met souvent a None).
    for inp in manifest.get("inputs", []) or []:
        nm = inp.get("name")
        if nm:
            setattr(inst, nm, contract.get(nm))

    out_dir = args.out or os.path.join(dbg, "out")
    os.makedirs(out_dir, exist_ok=True)
    h, w = frame.shape[:2]
    cls = type(inst)
    wrote = []

    # composited.png = la VUE COMPLETE telle qu'incrustee dans l'app : frame +
    # render_patch (coin) + render_overlay (BGRA plein cadre) + get_overlays
    # (formes SIDECAR : croix, points...). On accumule les 3 couches dans le meme
    # ordre que MainWindow._display (patch bake, puis overlay alpha, puis SIDECAR).
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
