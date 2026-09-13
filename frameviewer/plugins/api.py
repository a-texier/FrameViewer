# -*- coding: utf-8 -*-
"""Contrat de plugin FrameViewer + facade PluginAPI.

Un plugin = un dossier plugins/plugins_<nom>/plugin.py definissant une
sous-classe de FrameViewerPlugin, exposee au niveau module via
`PLUGIN = MaClasse`. Tous les hooks sont optionnels (no-op par defaut) :
un plugin n'implemente que ce dont il a besoin. Chaque appel de hook est
protege individuellement par le loader (frameviewer/plugins/loader.py) --
un plugin qui plante ne doit jamais casser l'affichage ni l'app.

Un plugin ne doit jamais importer frameviewer.ui.* ni toucher a MainWindow
directement : tout passe par PluginAPI, pour rester stable si l'appli est
refactoree plus tard. Voir docs/plugins.md pour la doc complete + exemple.
"""
import csv
import json
import os


def _env_version_hint(paths):
    """Si un des site-packages donnes appartient a un env dont le Python n'est PAS
    celui de l'app, renvoie un message clair (cause n1 des echecs meme-process :
    un .pyd compile pour Python X ne charge pas dans Python Y). Sinon None."""
    import subprocess
    import sys
    app_ver = "%d.%d" % sys.version_info[:2]
    for sp in paths or []:
        # site-packages = <env>/Lib/site-packages -> python.exe = <env>/python.exe
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
    """Classe de base a sous-classer. `name` sert d'identifiant stable
    (case a cocher dans le dialogue Plugins) -- a definir explicitement."""

    name = "plugin"
    description = ""
    # coin d'incrustation du hook render_patch : "tl"|"tr"|"bl"|"br"
    corner = "tr"
    margin = 8

    def on_load(self, api):
        """Appele une fois au chargement (ou rechargement) du plugin."""

    def on_unload(self, api):
        """Appele avant un rechargement -- liberer des ressources ouvertes."""

    def get_overlays(self, api, frame_idx):
        """-> list[dict] au meme schema que les overlays SIDECAR existants
        (voir docs/overlays.md) : {"type": "points"|"croix"|"ligne_brisee"|
        "ellipse"|..., "points": [(x,y),...], "color": (r,g,b),
        "thickness": int, "text": {...} (optionnel)}. Dessine par le
        moteur SIDECAR deja en place -- affichage live ET export."""
        return []

    def get_multiview_overlays(self, api, views):
        """FORMAT CANONIQUE du dessin inter-vues (reference unique).

        Appele UNE fois par rafraichissement (pas par vue), automatiquement des
        qu'au moins deux vues sont affichees.

        ENTREE `views` = contexte multivue (== api.views()) :
          {"mode": str, "count": int, "n_offset": int (si temporel),
           "sync_base_frame": int,
           "views": [ {"index": int,        # slot geometrique, 0 = haut-gauche
                       "role": str,          # primary|satellite|temporal_ref|temporal_cur
                       "view_key": str,      # identite STABLE de la source
                       "source_name": str,
                        "frame_idx": int|None,
                        "start_frame": int,
                       "w": int, "h": int}, ...]}   # taille image de la vue

        SORTIE -> list[dict] : AUCUNE structure imposee. Tu renvoies la liste de
        formes que tu veux, dans n'importe quel ordre, construites a partir de
        n'importe quelle source (1 CSV, 2 CSV joints sur une cle, calcul, ...).
        Chaque dict est une forme parmi (coordonnees en pixels IMAGE) :
          forme ciblant UNE vue (ajouter "view": index) :
            {"type":"rectangle","view":i,"points":[(x1,y1),(x2,y2)]}   # bbox (2 coins)
            {"type":"cercle","view":i,"points":[(x,y)],"a":rayon_px}
            {"type":"points","view":i,"points":[(x,y),...]}
            {"type":"polygone","view":i,"points":[(x,y),...]}          # ferme
            {"type":"ligne_brisee","view":i,"points":[(x,y),...]}      # ouverte
            {"type":"croix","view":i,"points":[(x,y),...]}
            {"type":"texte_ecran","view":i,"label":"12 correspondances"}
              # texte ancre en haut-gauche du viewport, independant zoom/pan
          LIEN entre deux vues :
            {"type":"segment_inter_vues","va":i,"pa":(x,y),"vb":j,"pb":(x,y)}
        Clefs communes : "color":(r,g,b), "thickness":int. Une forme visant un
        slot absent/non pret est ignoree (jamais d'erreur). Overlay ECRAN
        uniquement (ignore a l'export). Optionnel : ne rien surcharger = rien.

        ZOOM/PAN : tu travailles TOUJOURS en pixels IMAGE (comme si la vue etait
        au 1:1) ; l'app applique le zoom/pan de CHAQUE vue en direct. Chaque forme
        est confinee (clippee) a sa propre vue -> jamais de debord sur une vue
        voisine. Un lien inter-vues n'est trace que si SES DEUX extremites sont
        visibles dans leur cadre (une extremite zoomee/pannee hors champ masque
        le lien au lieu de partir n'importe ou).

        Regle "qui est qui" : lie tes DONNEES par view_key/source_name (robuste
        aux swaps) ; emets la GEOMETRIE avec l'`index` du meme contexte."""
        return []

    def render_patch(self, api, frame_idx, size):
        """-> ndarray BGR (ou None) : mini-image incrustee dans un coin de
        la vue (voir attributs `corner`/`margin`). `size` = (w, h)
        indicatif ; toute taille renvoyee est recadree aux dimensions de
        la frame par le compositeur."""
        return None

    def build_panel(self, api):
        """-> QWidget optionnel, a ajouter comme dock via api.add_dock()."""
        return None

    def menu_actions(self, api):
        """-> list[(label, callback)] : actions listees dans le dialogue
        Plugins, section "Actions"."""
        return []

    def accepts_drop(self, api, path):
        """True si CE plugin veut traiter le fichier/dossier `path` glisse-
        depose sur la fenetre principale (ex. un .csv que l'app ne sait pas
        interpreter nativement). Le premier plugin actif qui renvoie True
        recoit ensuite on_drop(api, path) -- les .sidecar/.ver/YOLO/media
        restent geres par l'app elle-meme, les plugins ne voient que ce
        qu'aucun gestionnaire existant n'a reclame."""
        return False

    def on_drop(self, api, path):
        """Appele si accepts_drop(api, path) a renvoye True pour ce chemin."""

    # --- modele "overlay code" (voir docs/plugins.md) : le plugin dessine en
    # cv2 un overlay transparent pleine image, pilote par des cases par
    # element, avec un/des CSV rattaches et les evenements souris image. ---
    def overlay_elements(self, api):
        """-> list[(key, label)] : elements dessinables toggleables du plugin
        (ex. [("bbox","Boites"), ("cov","Covariance"), ("label","Labels")]).
        FrameViewer affiche une case On/Off par element dans la zone du
        plugin ; render_overlay lit leur etat via api.element_enabled(key)."""
        return []

    def render_overlay(self, api, frame_idx, size):
        """-> ndarray BGRA (H, W, 4) ou None : overlay transparent pleine image
        compose PAR-DESSUS l'image affichee (au niveau pixel : suit le zoom, se
        propage a l'export/multivue). `size` = (w, h) de l'image affichee.

        Le plugin lit ses entrees via les attributs self.<nom> (le viewer y
        pose, avant chaque appel, le chemin du fichier depose sous l'entree
        <nom> declaree dans manifest.json, ou None). Il ouvre/interprete le
        fichier lui-meme (module csv, pandas...). Il filtre sur `frame_idx`,
        dessine en cv2 sur un canvas np.zeros((h, w, 4), uint8) uniquement les
        elements dont api.element_enabled(key) est vrai. Renvoie None si rien a
        dessiner. Appele une fois par JEU d'entrees (contrat) visible."""
        return None

    def render_panel(self, api, frame_idx, size):
        """-> ndarray BGR/BGRA ou None : image affichee dans la ZONE du plugin
        (carte de l'onglet Plugins), redessinee a chaque frame. Pour un
        aperçu/tableau/graphe propre au plugin. `size` = (w, h) indicatif de la
        zone. Optionnel."""
        return None

    def on_view_hover(self, api, frame_idx, x, y):
        """Curseur survole la vue en (x, y) coordonnees IMAGE. Le plugin peut
        memoriser un etat (ex. index de la boite sous le curseur) puis appeler
        api.request_repaint() pour redessiner l'overlay. Optionnel."""

    def on_view_click(self, api, frame_idx, x, y):
        """Clic sur la vue en (x, y) coordonnees IMAGE. Meme principe que
        on_view_hover. Optionnel."""

    def on_view_key(self, api, frame_idx, key, text):
        """Touche pressee pendant que la vue a le focus, UNIQUEMENT si l'appli
        ne l'utilise pas deja (fleches/espace/zoom/chiffres/Entree restent a
        l'appli). `key` = code Qt (int, ex. QtCore.Qt.Key_Delete) ; `text` = le
        caractere ('a', 's'...) ou '' pour une touche speciale. Memoriser un
        etat (souvent combine a self._hover / une selection posee au clic) puis
        api.request_repaint(). RENVOYER True si la touche est traitee (sinon
        elle poursuit son chemin normal). Optionnel."""
        return False


class PluginAPI:
    """Facade fournie a chaque hook de plugin -- seul point de contact
    avec l'application. Une instance neuve est construite par le loader
    pour chaque appel de plugin (cout negligeable), afin que
    `plugin_dir()` reste toujours correct meme quand plusieurs plugins
    sont actifs en meme temps."""

    def __init__(self, mw, plugin_dir, plugin_id=None, contract=None, view_key=None):
        self._mw = mw
        self._plugin_dir = plugin_dir
        self._plugin_id = plugin_id
        # contrat courant = {nom_entree: chemin|None} du package d'entrees en
        # cours de rendu (positionne par le loader avant render_overlay/panel).
        self._contract = contract or {}
        # vue en cours de rendu (cle = source) : les cases On/Off sont par vue,
        # donc element_enabled doit lire l'etat de CETTE vue (None = vue courante).
        self._view_key = view_key

    # --- sequence / frame ---
    def frame_count(self):
        return self._mw.source.count if self._mw.source else 0

    def current_frame_index(self):
        return self._mw.cur

    def raw_frame(self, idx=None):
        """Frame brute (ndarray natif, avant LUT/contraste), ou None."""
        if self._mw.source is None:
            return None
        i = self._mw.cur if idx is None else int(idx)
        try:
            return self._mw.source.get(i)
        except Exception:
            return None

    def displayed_frame(self, idx=None):
        """Frame BGR telle qu'affichee (LUT/contraste/filtres/rotation
        appliques), SANS les overlays/patches -- copie defensive, jamais
        un buffer partage avec le moteur de rendu."""
        raw = self.raw_frame(idx)
        if raw is None:
            return None
        rot = self._mw._apply_rotation(raw)
        out = self._mw.process(rot)
        return out.copy() if out is not None else None

    def image_size(self):
        """(largeur, hauteur) de la sequence ouverte, (0, 0) si aucune."""
        if self._mw._raw is None:
            return (0, 0)
        h, w = self._mw._raw.shape[:2]
        return (int(w), int(h))

    def source_name(self):
        return self._mw.source.name if self._mw.source else ""

    def views(self):
        """-> contexte multivue : dict {"mode","count","views":[...]}. Chaque
        entree de "views" decrit un slot AFFICHE (ordre de lecture, 0 = haut-
        gauche) : {"index":int, "role":"primary"|"satellite" (+ "temporal_ref"/
        "temporal_cur" en mode temporel), "view_key":str, "source_name":str,
        "frame_idx":int|None, "w":int, "h":int}. `w`/`h` = taille image de la vue
        (0 si pas encore rendue). Sert a get_multiview_overlays pour savoir "qui
        est qui" (quelle source, quelle frame, quelle taille par vue). Le dict de
        tete porte aussi "n_offset" en mode temporel. {"mode":"1","count":1,...}
        quand une seule vue est visible."""
        fn = getattr(self._mw, "_views_context", None)
        if fn is None:
            return {"mode": "1", "count": 0, "views": []}
        try:
            return fn()
        except Exception:
            return {"mode": "1", "count": 0, "views": []}

    # --- annotations deja chargees (lecture seule) ---
    def ver_boxes(self, frame_idx=None):
        """Boites .ver/YOLO visibles de la frame -- tuples (cls, x1, y1,
        x2, y2, track_id, labels), memes coordonnees que draw_annotation_boxes."""
        i = self._mw.cur if frame_idx is None else int(frame_idx)
        try:
            return list(self._mw._visible_annots(i))
        except Exception:
            return []

    def sidecar_graphs(self, frame_idx=None):
        """Graphiques SIDECAR de la frame -- meme schema de dict que get_overlays."""
        i = self._mw.cur if frame_idx is None else int(frame_idx)
        try:
            key = i + self._mw.sidecar_offset.value()
            return list(self._mw.overlays.get(key, []))
        except Exception:
            return []

    # --- fichiers ---
    def plugin_dir(self):
        """Dossier du plugin (plugins/plugins_<nom>/) -- pour lire un CSV,
        une config, etc. livres a cote de plugin.py."""
        return self._plugin_dir

    def read_csv_dict(self, path, **kwargs):
        """csv.DictReader (stdlib -- pandas est exclu du build). Renvoie
        une liste de dicts {nom_colonne: valeur_str}."""
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f, **kwargs))

    # --- entrees du jeu courant (back-compat ; le modele recommande est
    # self.<nom>, injecte par le loader avant chaque hook -- voir render_overlay) ---
    def inputs(self):
        """-> {nom_entree: chemin|None} du jeu d'entrees en cours de rendu.
        Back-compat : preferer les attributs self.<nom>."""
        return dict(self._contract)

    def input(self, name):
        """-> chemin du fichier de l'entree `name` pour le jeu courant, ou None.
        Back-compat : preferer self.<name>."""
        return self._contract.get(name)

    def attached_csvs(self):
        """-> list[str] : chemins des fichiers renseignes dans le jeu courant.
        Back-compat : preferer les attributs self.<nom>."""
        return [p for p in self._contract.values() if p]

    def element_enabled(self, key, default=True):
        """-> bool : etat de la case On/Off de l'element `key` declare par
        overlay_elements(). `default` si l'utilisateur ne l'a pas encore
        touchee."""
        pid = self._plugin_id
        # cases par element de la VUE COURANTE (voir MainWindow._plugin_elements) ;
        # repli sur l'ancien format plat si le modele par vue n'est pas la.
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
        """Ouvre un dialogue qui demande a l'utilisateur, pour chaque role
        semantique donne (ex. roles=["frame_id","x1","y1","x2","y2"]),
        quelle colonne REELLE du CSV l'alimente -- pour ne pas coder en dur
        des noms de colonnes qui varient d'un fichier a l'autre.

        `optional_roles` : roles pouvant rester non associes ("(aucune)").
        `remember_key` : si fourni, le mapping choisi est sauvegarde a cote
        du CSV (<csv_path>.<remember_key>.mapping.json) et reutilise
        automatiquement au prochain appel avec le MEME csv_path/entete
        (evite de rouvrir le dialogue a chaque rechargement).

        Renvoie {role: nom_de_colonne | None}, ou None si l'utilisateur
        annule."""
        # normalise : un chemin issu d'un glisser-deposer (QUrl, slashs
        # avant) et le meme fichier choisi via un selecteur natif (slashs
        # arriere Windows) doivent partager le MEME fichier de mapping
        # memorise, sinon le mapping est redemande selon la provenance.
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

    # --- interface ---
    def add_dock(self, widget, title, area="right"):
        """Ajoute `widget` comme QDockWidget (pattern deja utilise par les
        docks internes : right/tools/history). `area` :
        "left"|"right"|"top"|"bottom".

        Les panneaux plugin ouverts sont regroupes en ONGLETS entre eux
        (jamais empiles verticalement) : plusieurs docks colles les uns
        sous les autres finissent ecrases en bandes trop etroites pour
        etre lisibles -- symptome deja rencontre."""
        from PySide6 import QtCore, QtWidgets
        areas = {
            "left": QtCore.Qt.LeftDockWidgetArea,
            "right": QtCore.Qt.RightDockWidgetArea,
            "top": QtCore.Qt.TopDockWidgetArea,
            "bottom": QtCore.Qt.BottomDockWidgetArea,
        }
        qt_area = areas.get(area, QtCore.Qt.RightDockWidgetArea)
        dock = QtWidgets.QDockWidget(title, self._mw)
        dock.setWidget(widget)
        dock.setMinimumWidth(220)
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
        """Ajoute une entree (bouton) dans le dialogue Plugins, section
        "Actions" -- `callback()` est appele sans argument, proteges par
        le loader (une exception desactive le plugin, jamais l'app)."""
        self._mw._plugin_menu_actions.append((label, callback))

    def status(self, msg, timeout=3000):
        self._mw.statusBar().showMessage(str(msg), timeout)

    def log(self, msg, level="info"):
        """Ecrit dans la Console plugins. `level` : "info" | "ok" | "warn" |
        "error" -- colore le message et le fait ressortir. Le nom du plugin et
        le numero de ligne appelant sont ajoutes automatiquement."""
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
        """Force un redessin de la vue active (ex. apres un changement de
        config lu depuis build_panel)."""
        if self._mw._raw is not None:
            self._mw._display()

    # --- libs externes (conda/venv) : l'exe reste minimal, les libs lourdes
    #     ou inhabituel vivent dans un env exterieur fourni par l'utilisateur ---
    def add_site_packages(self, path):
        """Ajoute un dossier site-packages externe (conda/venv) en FIN de
        sys.path (append). Les modules DU BUNDLE (numpy, cv2, PySide6...) gardent
        la priorite : l'externe ne fait que combler ce qui manque, donc aucun
        second numpy n'est charge -> pas de conflit d'ABI tant que la lib externe
        accepte le numpy du bundle.

        append vs insert(0) : `append` met l'externe APRES le bundle (le bundle
        gagne, c'est ce qu'on veut). `insert(0)` le mettrait AVANT et risquerait
        de masquer numpy/PySide6 du bundle par des versions differentes -> crash.
        De plus, numpy etant deja importe au demarrage, il reste celui du bundle
        (cache dans sys.modules) quoi qu'il arrive : insert(0) est donc a la fois
        inefficace et dangereux. On utilise TOUJOURS append."""
        import os
        import sys
        if path and os.path.isdir(path) and path not in sys.path:
            sys.path.append(path)

    def external_libs_path(self):
        """Chemin d'un site-packages externe (conda/venv) SANS RIEN CODER EN
        DUR : lu depuis la variable d'environnement FV_EXTERNAL_LIBS, propre a
        chaque machine (chaine vide si non definie). Convention portable pour
        brancher pandas & co dans l'exe minimal, sans chemin en dur dans le
        plugin :

            pd = api.external_import("pandas", api.external_libs_path())

        En mode source (lance depuis un env qui a deja pandas), la lib est deja
        importable : external_import fonctionne meme avec un chemin vide."""
        import os
        return os.environ.get("FV_EXTERNAL_LIBS", "")

    def external_import(self, module, site_packages=None):
        """Importe `module` depuis un env externe (non embarque dans l'exe).
        `site_packages` : un dossier (str) ou une liste de dossiers a ajouter
        avant l'import (voir add_site_packages). Leve ImportError si introuvable.

            pd = api.external_import("pandas", api.external_libs_path() or None)

        MEME PROCESS = meme interpreteur que l'app : l'env pointe DOIT etre la
        MEME version de Python que l'app (les .pyd compiles sont lies a la version
        3.x). Sinon ImportError avec un diagnostic clair -> passe par
        run_external() (le python.exe de l'env, isole)."""
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
        """Diagnostic environnement (str) : executable, mode frozen, et d'ou
        numpy/pandas/cv2/PySide6 sont REELLEMENT charges + site-packages
        externes actifs. Utile pour verifier qu'une lib externe est bien
        branchee et compatible : api.log(api.env_report())."""
        from frameviewer.plugins.loader import environment_report
        return environment_report()

    def run_external(self, python_exe, code, timeout=30):
        """Execute du code Python dans un interpreteur EXTERNE (le python.exe
        d'un env conda/venv) : isolation TOTALE -- la lib lourde tourne avec SON
        propre numpy/pandas, aucun contact avec le bundle, donc zero conflit de
        versions. Communication par stdout (le code imprime p.ex. du JSON que tu
        relis). Renvoie stdout (str), leve RuntimeError si echec. A privilegier
        pour une lib inhabituel/lourde incompatible avec le numpy du bundle."""
        import subprocess
        r = subprocess.run([python_exe, "-c", code], capture_output=True,
                           text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "subprocess a echoue")
        return r.stdout
