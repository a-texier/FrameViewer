# -*- coding: utf-8 -*-
"""Decouverte/chargement des plugins depuis un dossier plugins/ voisin de
l'executable (jamais dans sys._MEIPASS, qui est temporaire et nettoye a la
fermeture). Un plugin = un sous-dossier plugins/plugins_<nom>/plugin.py
exposant `PLUGIN = <classe FrameViewerPlugin>`.

Aucun sandbox d'execution reel (hors de portee pour une appli desktop
PyInstaller) : le code plugin tourne dans le process de l'app -- usage
interne/de confiance, pas des plugins tiers non verifies. Chaque import et
chaque appel de hook est protege individuellement : un plugin casse se
desactive avec une erreur consultable (dialogue Plugins), il ne fait
jamais planter l'app ni bloquer le rendu des autres calques/plugins."""
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

# prefixe [xxx] en tete d'un message -> nom du plugin (logs systeme / prints).
_TAG_RE = re.compile(r"^\s*\[([^\]]+)\]\s*")
# mots qui trahissent une erreur -> niveau "error" par defaut.
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
    """Retire de sys.modules les modules helper deja charges depuis `folder`
    (fichiers .py voisins de plugin.py). Assure qu'un `import mon_helper` dans
    plugin.py reprend le code A JOUR a chaque rechargement, et que deux plugins
    au meme nom de helper ne se marchent pas dessus."""
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
    """Resume court d'un resultat de hook pour le compte rendu de test."""
    import numpy as np
    if res is None:
        return "None (rien a dessiner)"
    if isinstance(res, np.ndarray):
        return f"ndarray shape={res.shape} dtype={res.dtype}"
    if isinstance(res, list):
        return f"list de {len(res)} element(s)"
    return type(res).__name__


class _StreamTee:
    """Flux d'ecriture qui recopie vers un flux d'origine (s'il existe) et emet
    chaque ligne complete vers un journal. Rend visibles les print() des plugins
    dans la console plugins, y compris dans le build fenetre ou sys.stdout vaut
    None (un simple print() y leverait sinon une exception)."""

    def __init__(self, prefix, tag, sink, original):
        self._prefix = prefix
        self._tag = tag
        self._sink = sink            # callable(str)
        self._original = original    # flux d'origine ou None
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
    """Diagnostic 'ou tourne reellement le code' : executable, mode frozen, et
    surtout d'ou chaque module est CHARGE. Piege classique : numpy vient du
    bundle de l'exe tandis que pandas vient d'un env externe -> versions
    incompatibles. Sert au bouton Diagnostic et a PluginAPI.env_report()."""
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
    """Compose deux overlays BGRA (top au-dessus de base) -> BGRA. Utilise
    pour empiler les overlays de plusieurs plugins avant de les poser sur
    l'image. Tolerant aux tailles differentes (recadre au plus petit)."""
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
    """Dossier plugins/ livre avec l'appli : a cote de l'exe si frozen
    (PyInstaller onefile), a cote de la racine du depot en mode source --
    meme comportement, testable sans rebuild. C'est aussi ou sont crees les
    nouveaux plugins."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "plugins")


def user_config_dir():
    """Dossier de config PROPRE A CHAQUE UTILISATEUR / PC (jamais en dur) :
    %APPDATA%\\FrameViewer sous Windows, equivalents standards ailleurs. Cree
    a la premiere utilisation ; contient settings.ini + un dossier plugins/."""
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
    """Dossier plugins/ PERSONNEL de l'utilisateur (dans user_config_dir),
    scanne EN PLUS de celui livre avec l'appli. Cree a la demande."""
    d = os.path.join(user_config_dir(), "plugins")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def plugins_roots():
    """Tous les dossiers ou chercher des plugins, dans l'ordre de priorite :
    d'abord ceux livres avec l'appli (a cote de l'exe), puis ceux perso de
    l'utilisateur (AppData). Un plugin_id vu deux fois : le premier gagne."""
    return [plugins_root(), user_plugins_dir()]


class LoadedPlugin:
    def __init__(self, plugin_id, folder):
        self.plugin_id = plugin_id
        self.folder = folder
        self.instance = None
        self.enabled = False
        self.error = None
        self.manifest = _manifest.load(folder)   # contrat d'entree (v2)
        self.last_run = None                       # heure derniere execution
        self.element_keys = {}                      # {cle_element: touche}, cf. sync

    @property
    def kind(self):
        return self.manifest.get("kind", "code")

    @property
    def name(self):
        return self.instance.name if self.instance is not None else self.plugin_id


class PluginLoader:
    """Vit sur MainWindow (self._plugin_loader) -- etat GLOBAL, pas par
    vue : les plugins s'appliquent a la vue active/primaire (voir
    docs/plugins.md, limite documentee). Un seul point d'etat evite le
    piege connu de ce depot ou main_window.py et multiview.py finissent
    par diverger sur la meme logique."""

    def __init__(self, mw):
        self._mw = mw
        self.plugins = []          # [LoadedPlugin, ...]
        self._log_records = []     # [{ts,pid,level,lineno,src,text}, ...]
        # ids des plugins ACTIFS -- source de verite conservee a travers les
        # rechargements (reload_all re-decouvre tout et remettrait sinon
        # chaque plugin a "desactive"). Vide au demarrage : les plugins sont
        # decoches par defaut (voir docs/plugins.md), l'utilisateur les active
        # depuis Parametres > Plugins ; MainWindow restaure l'etat memorise
        # via set_enabled_ids() avant discover_and_load().
        self._enabled_ids = set()

    def log(self, msg, level="info", pid=None, lineno=None, src=None):
        """Journalise un message. `level` : info|ok|warn|error (colore la
        Console). `pid`/`lineno`/`src` sont remplis par PluginAPI.log ; pour un
        message brut, on devine le plugin d'un prefixe [xxx] et le niveau erreur
        de mots-cles."""
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
        # borne le journal pour ne pas grossir indefiniment en session longue.
        if len(self._log_records) > 5000:
            del self._log_records[: len(self._log_records) - 5000]

    def log_lines(self):
        """Journal en texte brut : 'HH:MM:SS  [plugin]  [Lnn]  - message'."""
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
        """Journal structure (pour la Console coloree)."""
        return list(self._log_records)

    def clear_log(self):
        self._log_records = []

    @contextlib.contextmanager
    def _capture(self, prefix):
        """Redirige stdout/stderr vers le journal le temps d'un appel de plugin,
        pour que print() et tracebacks soient visibles dans la console plugins
        (essentiel dans le build fenetre ou sys.stdout vaut None). stderr ->
        niveau error."""
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
        seen = set()   # plugin_id deja charges (le 1er dossier gagne)
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
            # Compilation EN MEMOIRE, jamais via le cache bytecode __pycache__ :
            # exec_module() reutilise le .pyc tant que mtime+taille du source
            # n'ont pas change ; or un enregistrement suivi d'un rechargement
            # dans la meme seconde (granularite mtime) rechargeait l'ancien
            # bytecode -> "Actualiser" n'appliquait le changement qu'une fois
            # sur deux. compile() lit toujours le source a jour.
            code = compile(src, mod_path, "exec")
            api = PluginAPI(self._mw, lp.folder, lp.plugin_id)
            # Permet `import mon_helper` depuis plugin.py (fichier .py voisin) :
            # on met le dossier du plugin en tete de sys.path le temps du
            # chargement, apres avoir purge d'eventuels helpers deja charges
            # (sinon un reload garderait leur ancien code -- meme piege que le
            # .pyc). Nomme tes helpers de facon unique (ex. demo_utils.py).
            _purge_sibling_modules(lp.folder)
            sys.path.insert(0, lp.folder)
            try:
                with self._capture(f"[{lp.plugin_id}]"):
                    exec(code, module.__dict__)
                    cls = module.__dict__.get("PLUGIN")
                    if cls is None:
                        raise ImportError(f"{mod_path} : pas d'attribut PLUGIN = <classe>")
                    instance = cls()      # certains plugins importent une lib externe ici
                    instance.on_load(api)
            finally:
                try:
                    sys.path.remove(lp.folder)
                except ValueError:
                    pass
            lp.instance = instance
            self._sync_element_keymap(lp, api)
            # decoche par defaut : n'est actif que si l'utilisateur l'a active
            # (etat memorise dans self._enabled_ids).
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
        """Ids des plugins actuellement actifs -- a persister (QSettings)."""
        return sorted(self._enabled_ids)

    def set_enabled_ids(self, ids):
        """Restaure l'etat actif memorise. A appeler AVANT discover_and_load()
        (au demarrage) pour que les plugins reviennent dans l'etat ou ils
        etaient a la fermeture."""
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
        """Execute a la demande les hooks de rendu du plugin sur la frame
        courante (meme s'il est desactive), en capturant sortie et exceptions.
        Test rapide depuis la console plugins ; ne modifie pas l'etat actif."""
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
                continue   # hook non surcharge
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

    # --- hooks agreges, appeles par MainWindow (vue active/primaire) ---
    def collect_overlays(self, frame_idx):
        out = []
        for lp in self.enabled_plugins():
            api = PluginAPI(self._mw, lp.folder, lp.plugin_id)
            res = self._safe_call(lp, "get_overlays", api, frame_idx, default=[])
            if res:
                out.extend(res)
        return out

    def collect_multiview_overlays(self, views_context):
        """Formes inter-vues des plugins actifs (hook get_multiview_overlays),
        appele une fois par rafraichissement avec le contexte multivue. Meme
        robustesse que collect_overlays (chaque plugin isole par _safe_call)."""
        from frameviewer.plugins.api import FrameViewerPlugin
        out = []
        # cles de TOUTES les vues affichees (pour retrouver un fichier depose sur
        # n'importe laquelle) : un plugin multivue "connecte les deux", donc son
        # entree ne doit pas dependre de quelle vue est active.
        view_keys = [v.get("view_key") for v in (views_context or {}).get("views", [])]
        for lp in self.enabled_plugins():
            if type(lp.instance).get_multiview_overlays is \
                    FrameViewerPlugin.get_multiview_overlays:
                continue   # hook non surcharge -> rien a faire
            merged = self._merged_contract(lp, view_keys)
            self._bind_inputs(lp, merged)
            api = PluginAPI(self._mw, lp.folder, lp.plugin_id, contract=merged)
            res = self._safe_call(lp, "get_multiview_overlays", api,
                                  views_context, default=[])
            if res:
                out.extend(res)
        return out

    def _merged_contract(self, lp, view_keys):
        """Fusionne les fichiers d'entree du plugin sur TOUTES les vues donnees
        (+ la vue active) : {nom_entree: 1er chemin non vide trouve}. Ainsi un
        CSV depose sur la vue gauche OU droite alimente le hook multivue."""
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
        """-> [(libelle, callback), ...] du hook menu_actions du plugin `lp`,
        ou []. Chaque callback sera protege par _safe_call a l'appel."""
        api = PluginAPI(self._mw, lp.folder, lp.plugin_id)
        return self._safe_call(lp, "menu_actions", api, default=[]) or []

    def _sync_element_keymap(self, lp, api):
        """Synchronise manifest['element_keys'] avec overlay_elements() : ajoute
        les nouvelles cles (defaut 'm'), retire les disparues, CONSERVE les choix
        existants de l'utilisateur. N'ecrit manifest.json que si l'ensemble des
        cles a change (donc pas a chaque lancement). Resultat sur lp.element_keys
        = {cle_element: touche}."""
        from frameviewer.plugins.api import FrameViewerPlugin
        lp.element_keys = {}
        inst = lp.instance
        if inst is None or type(inst).overlay_elements is FrameViewerPlugin.overlay_elements:
            return
        # appel DIRECT (pas _safe_call) : la sync tourne au chargement, avant que
        # le plugin soit active, et _safe_call renvoie [] pour un plugin inactif.
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

    # --- modele "overlay code" (render_overlay/render_panel + souris) ---
    def _bind_inputs(self, lp, contract):
        """Injecte les chemins du contrat courant sur l'instance du plugin :
        pour chaque entree declaree <nom>, self.<nom> = chemin_du_fichier (ou
        None si non renseignee). C'est le SEUL "magique" du modele -- il rend
        les entrees disponibles en clair dans le code du plugin (self.csv_plots
        etc.), pour que le plugin ouvre lui-meme le fichier. Appele avant chaque
        hook, pour chaque contrat visible."""
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
        """Liste des contrats VISIBLES du plugin (chacun = {nom_entree:
        chemin}) pour la vue `view_key` (None = vue courante). Delegue a
        MainWindow ; repli sur un contrat vide si l'etat n'est pas construit."""
        fn = getattr(self._mw, "_visible_contracts", None)
        if fn is None:
            return [{}]
        return fn(lp.plugin_id, view_key) or []

    def collect_overlay_image(self, frame_idx, size, view_key=None):
        """Compose (alpha) les overlays BGRA des plugins actifs qui surchargent
        render_overlay, UNE FOIS PAR CONTRAT VISIBLE, pour la vue `view_key`
        (None = vue courante ; une cle explicite -> une vue satellite, chacune
        avec SES fichiers et SES cases). Renvoie un BGRA (H,W,4) ou None."""
        from frameviewer.plugins.api import FrameViewerPlugin
        w, h = int(size[0]), int(size[1])
        if w <= 0 or h <= 0:
            return None
        acc = None
        for lp in self.enabled_plugins():
            if type(lp.instance).render_overlay is FrameViewerPlugin.render_overlay:
                continue
            # `or [{}]` : si aucune entree n'est deposee, on appelle quand meme
            # render_overlay une fois avec un contrat vide (self.<entree> = None),
            # comme le fait deja render_panel. Un plugin qui exige un fichier
            # renvoie None de lui-meme ; un plugin a fallback (demo) peut dessiner.
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
        """Image (BGR/BGRA) de la ZONE plugin pour le plugin `plugin_id`
        (1er contrat visible), ou None. Voir hook render_panel."""
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
                continue   # plugin sans hover -> pas d'appel a chaque survol
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
        """Transmet une touche (que l'appli n'a pas consommee) aux plugins qui
        surchargent on_view_key. Renvoie True des qu'un plugin renvoie True
        (touche traitee) -> l'appelant peut alors ne pas la propager."""
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
        """Propose `path` (fichier/dossier glisse-depose, non reconnu par
        l'app -- pas un .sidecar/.ver/YOLO/media) a chaque plugin actif via
        accepts_drop, dans l'ordre de decouverte ; le premier qui accepte
        recoit on_drop et le chemin est considere traite. Renvoie True si
        un plugin a pris en charge `path`, False sinon (l'appelant peut
        alors retomber sur le comportement habituel / signaler que le
        fichier n'est pas reconnu)."""
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
