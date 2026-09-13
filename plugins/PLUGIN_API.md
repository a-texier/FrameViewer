# Reference API plugin FrameViewer

Aide-memoire pour ecrire un plugin, lisible meme quand on n'a que
`FrameViewer.exe` + ce dossier `plugins/` (la vraie classe mere vit dans
l'exe : `frameviewer/plugins/api.py`). Sur une machine de dev, ouvre ce
fichier `api.py` directement pour les docstrings completes.

## Structure d'un plugin

```
plugins/
  plugins_mon_plugin/
    plugin.py          # PLUGIN = MaClasse (sous-classe de FrameViewerPlugin)
    manifest.json      # optionnel : name / kind / frame_input / inputs
```

`manifest.json` :

```json
{
  "name": "Mon Plugin",
  "kind": "code",
  "frame_input": "Current Frame",
  "inputs": [{"name": "csv_plots"}, {"name": "txt_labels"}]
}
```

- `inputs[].name` = un **identifiant Python** (ex. `csv_plots`). On ne declare
  AUCUN type/format : on ne transmet que le chemin, le plugin reconnait le
  format lui-meme.
- Le viewer pose, **avant chaque rendu**, le chemin du fichier depose sous
  chaque entree dans un attribut du meme nom : `self.csv_plots` (str), ou
  `None` si rien n'est depose. On lit le fichier soi-meme avec le module
  standard `csv` (embarque). Pour pandas / une lib lourde, voir "Libs externes".
- **Par vue** : les fichiers deposes et les cases On/Off appartiennent a la vue
  courante (l'etat Actif reste global). Deposer un CSV non reconnu nativement
  sur une vue ouvre un popup pour choisir a quelle entree de plugin l'associer.

## Modele "overlay code" : la boucle interactive

- `render_overlay(api, frame_idx, size)` **DESSINE** a partir d'un etat interne.
  Il ne connait PAS la souris (seulement la frame et la taille).
- `on_view_hover` / `on_view_click` **METTENT A JOUR** l'etat depuis la souris
  (coords image), `on_view_key` depuis le **clavier** ; puis
  `api.request_repaint()`.
- `api.request_repaint()` = "l'etat a change, redessine" -> le viewer rappelle
  `render_overlay`, qui relit l'etat (ex. `self._hover`, `self._selected`) et
  dessine autrement.

Ce qui relie le survol/clic au dessin, c'est un **index** (ou une cle) range
dans l'etat, PAS les coordonnees : les coords servent juste au test "sous le
curseur ?", et `render_overlay` relit la meme liste (meme ordre) pour retrouver
l'element par son index. Sans `request_repaint()`, rien n'est redessine tant
que la frame ne change pas. Ne repeins que si l'etat a reellement change
(garde `if new != self._hover:`), sinon ca rame au survol.

Exemple complet dans `plugins_demo_showcase/plugin.py` : survol (jaune) via
`on_view_hover`, clic = selection persistante (magenta + sigma force meme case
off) via `on_view_click`, touche `c` = deselection via `on_view_key`.

## Hooks (tous optionnels ; n'implemente que ce dont tu as besoin)

### Cycle de vie
- `on_load(self, api)` : appele une fois au (re)chargement. Init des attributs.
- `on_unload(self, api)` : avant un rechargement. Liberer des ressources.

### Overlay code (le modele recommande)
- `overlay_elements(self, api) -> [(cle, libelle), ...]` : elements dessinables
  toggleables. FrameViewer affiche une case On/Off par element sous la carte.
- `render_overlay(self, api, frame_idx, size) -> ndarray BGRA (H,W,4) | None` :
  overlay transparent plein cadre, compose au niveau pixel (suit le zoom, va a
  l'export et au multivue). `size = (w, h)`. Dessine en cv2 sur
  `np.zeros((h, w, 4), np.uint8)` les elements dont `api.element_enabled(cle)`
  est vrai. Renvoie None si rien a dessiner. Appele une fois par jeu visible.
- `render_panel(self, api, frame_idx, size) -> ndarray | None` : image affichee
  DANS la carte du plugin (apercu/tableau propre au plugin). Si non defini,
  aucune zone d'apercu n'est montree.
- `on_view_hover(self, api, frame_idx, x, y)` : curseur en (x, y) coords IMAGE.
- `on_view_click(self, api, frame_idx, x, y)` : clic en (x, y) coords IMAGE.
- `on_view_key(self, api, frame_idx, key, text) -> bool` : **binding sur touche**.
  Appele quand une touche est pressee et que l'appli ne l'utilise pas deja
  (fleches/espace/zoom/chiffres/Entree restent a l'appli). `key` = code Qt (int),
  `text` = le caractere ('s', 'c'...) ou '' pour une touche speciale. Renvoyer
  **True** si tu traites la touche (sinon elle poursuit son chemin). Souvent
  combine a une selection posee au clic / au survol.

### Anciens hooks (toujours supportes)
- `get_overlays(self, api, frame_idx) -> [dict, ...]` : formes au schema SIDECAR
  ({"type","points","color","thickness","text":{...}}), dessinees par le moteur
  SIDECAR (affichage + export). Voir docs/overlays.md.
- `render_patch(self, api, frame_idx, size) -> ndarray BGR | None` : vignette
  incrustee dans un coin (attributs de classe `corner` = tl|tr|bl|br, `margin`).
- `build_panel(self, api) -> QWidget | None` : panneau a ajouter en dock
  (via `api.add_dock`).
- `menu_actions(self, api) -> [(libelle, callback), ...]`.
- `accepts_drop(self, api, path) -> bool` + `on_drop(self, api, path)` : traiter
  un fichier glisse sur la fenetre que l'app ne reconnait pas nativement.

## Boite a outils `api` (PluginAPI)

### Frontend (le seul contact recommande pour un overlay code)
- `api.element_enabled(cle, default=True) -> bool` : etat de la case On/Off.
- `api.request_repaint()` : force un redessin de la vue active.
- `api.status(msg, timeout=3000)` : message dans la barre d'etat.
- `api.log(msg)` : ligne de log (consultable dans l'onglet Plugins).

### Sequence / frame
- `api.frame_count() -> int`
- `api.current_frame_index() -> int`
- `api.image_size() -> (w, h)`
- `api.source_name() -> str`
- `api.raw_frame(idx=None) -> ndarray | None` : frame brute (avant LUT/contraste).
- `api.displayed_frame(idx=None) -> ndarray BGR | None` : frame telle qu'affichee
  (LUT/contraste/rotation appliques), sans les overlays (copie defensive).

### Annotations deja chargees (lecture seule)
- `api.ver_boxes(frame_idx=None) -> [(cls, x1, y1, x2, y2, track_id, labels), ...]`
- `api.sidecar_graphs(frame_idx=None) -> [dict, ...]` (schema get_overlays)

### Fichiers
- `api.plugin_dir() -> str` : dossier du plugin (pour lire un fichier livre a cote).
- `api.read_csv_dict(path, **kwargs) -> [dict, ...]` : helper csv.DictReader
  (stdlib). Optionnel : rien n'empeche d'ouvrir le fichier soi-meme.
- `api.map_csv_columns(csv_path, roles, optional_roles=None, remember_key=None,
  title=None)` : dialogue pour associer des roles semantiques aux colonnes
  reelles d'un CSV (memorise le mapping). Pour les CSV a colonnes variables.

### Interface
- `api.add_dock(widget, title, area="right")` : ajoute un QDockWidget.
- `api.add_menu_action(label, callback)` : bouton dans la section Actions.

### Libs externes (conda / venv) -- l'exe reste minimal
L'exe n'embarque que le coeur (numpy, opencv, PySide6). Une lib lourde ou
inhabituel (pandas, scipy, sklearn...) vit dans un env EXTERNE que TU fournis ;
ton plugin ne marche que si le chemin est renseigne.

- `api.add_site_packages(path)` : ajoute un `site-packages` externe en **fin**
  de `sys.path` (append).
- `api.external_import(module, site_packages=None)` : ajoute le(s) chemin(s)
  puis importe. Ex. `pd = api.external_import("pandas", r"...\\IA_env\\Lib\\site-packages")`.
- `api.run_external(python_exe, code, timeout=30)` : execute du code dans un
  interpreteur EXTERNE (isolation totale), renvoie stdout.

**append vs insert** : `append` met l'externe APRES le bundle -> numpy/PySide6
DU BUNDLE gardent la priorite (c'est voulu). `insert(0)` mettrait l'externe
avant et pourrait masquer ces modules par d'autres versions -> crash ; en plus
numpy est deja importe au demarrage (fige dans `sys.modules`), donc `insert(0)`
serait de toute facon sans effet sur lui. **Toujours `append`.**

**Conflit numpy ?** Un process = UN seul numpy. Avec `external_import` (meme
process), la lib externe REUTILISE le numpy du bundle : pas de 2e numpy, donc
pas de conflit *tant que* la lib accepte cette version de numpy (le cas quand
l'exe est construit depuis le meme env). Garde des objets simples a la
frontiere (ex. `df.to_dict("records")`) pour ne pas trainer d'objets numpy
partout. Si la lib EXIGE un numpy different -> `run_external` (sous-process avec
SON propre numpy, isolation totale, echange par stdout/JSON). Exemple concret :
`plugins/plugins_demo_showcase/plugin.py` (pandas externe + repli `csv`).

**Procedure (tu n'as PAS besoin des 3 : external_import suffit presque toujours) :**

```
Besoin d'une lib externe ?
   |
   +-- essaie : api.external_import("lib", chemin)
   |        |
   |        +-- ca marche ? -> FINI (cas normal, ex. pandas chez toi)
   |        |
   |        +-- crash type "numpy ... failed to import" (la lib exige un
   |            autre numpy que celui du bundle) ?
   |               |
   |               +-- alors : api.run_external(python.exe, "code...")
   |                   (le code tourne dans l'env externe, tu recuperes
   |                    le resultat en texte/JSON)
```

`add_site_packages` est juste le rouage interne de `external_import` (a ignorer
le plus souvent). Place l'appel dans `on_load` : il ne s'execute qu'une fois,
au lancement de l'app et a chaque Actualiser/Recharger.

### Entrees (back-compat ; prefere self.<nom>)
- `api.inputs() -> {nom: chemin|None}`
- `api.input(nom) -> chemin | None`
- `api.attached_csvs() -> [chemin, ...]`

## Squelette minimal

```python
import os, csv
import cv2, numpy as np
from frameviewer.plugins.api import FrameViewerPlugin


class MonPlugin(FrameViewerPlugin):
    name = "mon_plugin"

    def on_load(self, api):
        self.csv_plots = None   # chemin injecte par le viewer (ou None)
        self._hover = None

    def overlay_elements(self, api):
        return [("boites", "Boites")]

    def render_overlay(self, api, frame_idx, size):
        path = self.csv_plots
        if not path or not os.path.isfile(path):
            return None
        w, h = int(size[0]), int(size[1])
        canvas = np.zeros((h, w, 4), np.uint8)
        if api.element_enabled("boites"):
            with open(path, encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    ...  # cv2.rectangle(canvas, ...)
        return canvas


PLUGIN = MonPlugin
```
