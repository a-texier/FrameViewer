# -*- coding: utf-8 -*-
"""
annotation_loader.py
Charge des annotations depuis differents formats :
  - .ver  : format Tracker (frames 1-based -> 0-based)
  - .txt  : YOLO merge (frame_id cls cx cy w h par ligne)
  - dossier : YOLO dossier (un .txt par frame, ordre alphabetique)

Tuple de sortie : (cls, x1, y1, x2, y2, track_id, labels)
  labels = tuple de strings (class, subclass, subsubclass...) pour .ver,
           nom de classe issu de classes.txt (ou "class N") pour YOLO
"""
import glob
import os
import re

from frameviewer.core.io_utils import dirent_image_path, natural_sort

_VER_CLASS_MAP = {
    "drone": 0,
    "bird": 1,
    "plane": 2,
    "helicopter": 3,
    "unknown": 4,
}

_EMPTY_LABEL = {"-", "none", "?", ""}


def is_annotation_path(path):
    """True si `path` est reconnu comme une annotation deposable : un .ver,
    un .txt isole (fusion YOLO), ou un dossier contenant des .txt (YOLO --
    un fichier par frame). Utilise a la fois par MainWindow.dropEvent et par
    BaseViewFrame.dropEvent (multiview.py) -- source unique pour eviter que
    les deux implementations divergent (deja arrive une fois)."""
    if os.path.isdir(path):
        return next(glob.iglob(os.path.join(path, "*.txt")), None) is not None
    return os.path.splitext(path)[1].lower() in (".ver", ".txt")


def scan_dropped_folder(folder):
    """Inspecte un dossier depose -- JAMAIS de recherche recursive dans les
    sous-dossiers -- et separe ce qu'il contient DIRECTEMENT en 3 listes :
    (images, fichiers .txt, fichiers .ver). Utilise pour decider quoi faire
    d'un dossier depose : ouvrir la sequence d'images si presentes, PUIS
    appliquer automatiquement les annotations si aussi presentes (dans cet
    ordre -- charger des boites avant d'avoir les dimensions de l'image les
    rendrait degenerees). Si les 3 listes sont vides, le dossier ne contient
    rien d'exploitable directement (ex. tout est dans des sous-dossiers
    images/ + labels/) -- a l'appelant de le signaler plutot que de ne rien
    faire silencieusement."""
    images, txts, vers = [], [], []
    try:
        with os.scandir(folder) as it:   # 1 enumeration, pas de stat par fichier
            for e in it:
                p = dirent_image_path(e)
                if p:
                    images.append(p)
                    continue
                ext = os.path.splitext(e.name)[1].lower()
                if ext == ".txt":
                    txts.append(e.path)
                elif ext == ".ver":
                    vers.append(e.path)
    except OSError:
        return [], [], []
    return natural_sort(images), txts, vers


def load_annotations(annotation_file, image_width=0, image_height=0, default_track=0):
    """Auto-detecte le format et charge les annotations.

    Returns dict[int, list[tuple]] :
        frame_idx -> [(cls, x1, y1, x2, y2, track_id, labels), ...]
        labels = tuple de strings pour .ver, () pour YOLO

    `default_track` : track_id attribue quand le fichier n'en fournit pas
    (format .ver court a 6 colonnes = un seul objet par fichier).
    """
    p = str(annotation_file)
    if os.path.isdir(p):
        return _load_yolo_folder(p, image_width, image_height)
    ext = os.path.splitext(p)[1].lower()
    if ext == ".ver":
        return _load_ver(p, default_track)
    if ext == ".txt":
        return _load_yolo_merged(p, image_width, image_height)
    return {}


def _load_ver(path, default_track=0):
    """Format Tracker .ver. Deux variantes acceptees :
    - courte (6 col) : frame_id  visibility  x1 y1 x2 y2
      (pas de track_id ni de classe -> un fichier = un objet ; track = default_track)
    - longue (>=7 col) : ... x1 y1 x2 y2  track_id  [classe [sous [sous-sous]]]
    frame_id est 1-based en entree -> 0-based en sortie ; notation scientifique OK.
    La colonne 2 (visibilite) n'est pas fiable selon les generateurs de .ver
    (valeurs sans signification homogene d'un format a l'autre) : elle est lue
    mais n'est PLUS utilisee pour filtrer -> toutes les boites sont affichees.
    """
    annots = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cols = line.split()
            if len(cols) < 6:
                continue
            try:
                frame_id = int(float(cols[0])) - 1
                x1 = int(float(cols[2]))
                y1 = int(float(cols[3]))
                x2 = int(float(cols[4]))
                y2 = int(float(cols[5]))
            except (ValueError, IndexError):
                continue
            if len(cols) >= 7:
                try:
                    track_id = int(float(cols[6]))
                except (ValueError, IndexError):
                    track_id = default_track
                cls_str = cols[7].lower() if len(cols) > 7 else "unknown"
                cls = _VER_CLASS_MAP.get(cls_str, 4)
                labels = tuple(
                    cols[i] for i in range(7, len(cols))
                    if cols[i].lower() not in _EMPTY_LABEL
                )
            else:
                track_id = default_track      # format court : 1 fichier = 1 track
                cls = 4
                labels = ()
            annots.setdefault(frame_id, []).append(
                (cls, x1, y1, x2, y2, track_id, labels)
            )
    return annots


def _load_class_names(folder):
    """Read the optional YOLO ``classes.txt`` next to a label set."""
    path = os.path.join(folder, "classes.txt")
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return [line.strip() for line in f if line.strip() and not line.lstrip().startswith("#")]
    except OSError:
        return []


def _class_labels(cls, class_names):
    if 0 <= cls < len(class_names):
        return (class_names[cls],)
    return (f"class {cls}",)


def _load_yolo_merged(path, img_w, img_h):
    """YOLO merge : chaque ligne = frame_id cls cx_norm cy_norm w_norm h_norm."""
    annots = {}
    class_names = _load_class_names(os.path.dirname(path))
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cols = line.split()
            if len(cols) < 6:
                continue
            try:
                frame_id = int(cols[0])
                cls = int(cols[1])
                x1, y1, x2, y2 = _yolo_norm_to_pixel(
                    float(cols[2]), float(cols[3]),
                    float(cols[4]), float(cols[5]),
                    img_w, img_h)
            except (ValueError, IndexError):
                continue
            annots.setdefault(frame_id, []).append(
                (cls, x1, y1, x2, y2, 0, _class_labels(cls, class_names)))
    return annots


_TRAILING_NUM_RE = re.compile(r"(\d+)(?!.*\d)")


def _frame_num_from_name(path):
    """Dernier nombre trouve dans le nom de fichier (sans extension), ex.
    "frame_0037.txt" -> 37, "0037.txt" -> 37, "img12_v2.txt" -> 2. None si
    aucun chiffre. Sert a faire correspondre un .txt a SA frame (meme id que
    l'image correspondante), plutot que de deviner par simple position."""
    stem = os.path.splitext(os.path.basename(path))[0]
    m = _TRAILING_NUM_RE.search(stem)
    return int(m.group(1)) if m else None


def _read_yolo_txt(args):
    txt_path, img_w, img_h, class_names = args
    dets = []
    try:
        with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                cols = line.split()
                if len(cols) < 5:
                    continue
                try:
                    cls = int(cols[0])
                    x1, y1, x2, y2 = _yolo_norm_to_pixel(
                        float(cols[1]), float(cols[2]),
                        float(cols[3]), float(cols[4]),
                        img_w, img_h)
                except (ValueError, IndexError):
                    continue
                dets.append(
                    (cls, x1, y1, x2, y2, 0, _class_labels(cls, class_names)))
    except OSError:
        pass
    # clef = STEM du fichier label (nom sans extension) : l'association a la
    # frame se fait par NOM cote MainWindow (stem label == stem image), avec
    # repli sur l'ordre naturel si aucun nom ne correspond (voir
    # MainWindow._pair_yolo_by_name). Un simple numero extrait du nom est
    # ambigu quand le nom contient plusieurs nombres (ex.
    # "frame_10846_1970-01-01T01_18_37" -> 10846 ou 37 ?).
    stem = os.path.splitext(os.path.basename(txt_path))[0]
    return stem, dets


def _load_yolo_folder(folder, img_w, img_h):
    """Dossier YOLO : un fichier .txt par frame -- typiquement UN SEUL par
    frame ANNOTEE (convention Ultralytics : pas de .txt pour une frame vide),
    donc PAS forcement un fichier par frame de la sequence. frame_id est donc
    pris depuis le nombre dans le nom du fichier ("frame_0037.txt" -> 37),
    pas depuis sa position dans la liste triee : une simple position se
    decale des qu'il manque des frames vides (cas courant), un id explicite
    reste correct meme avec des trous. Repli sur la position si le nom ne
    contient aucun chiffre.

    Lecture SEQUENTIELLE, deliberement : un ThreadPoolExecutor a ete essaye
    ici et mesure PLUS LENT (profilage : sur des milliers de tout petits
    fichiers, l'overhead de synchronisation entre threads -- creation,
    Lock.acquire, thread.join -- domine largement le temps reel d'I/O une
    fois les fichiers en cache disque ; ~0.9s threade contre ~0.03s pour un
    .ver equivalent, contre bien moins une fois revenu au sequentiel). Le
    cout reel et difficilement compressible en pur Python reste le nombre
    d'appels open()/close() (un par fichier) : c'est structurel au format
    "un fichier par frame", pas un defaut d'implementation.
    """
    txts = natural_sort(glob.glob(os.path.join(folder, "*.txt")))
    txts = [path for path in txts if os.path.basename(path).lower() != "classes.txt"]
    class_names = _load_class_names(folder)
    annots = {}   # STEM (nom de fichier sans extension) -> [dets, ...]
    for txt_path in txts:
        stem, dets = _read_yolo_txt((txt_path, img_w, img_h, class_names))
        if not dets:
            continue
        annots.setdefault(stem, []).extend(dets)
    return annots


def _yolo_norm_to_pixel(cx_n, cy_n, w_n, h_n, img_w, img_h):
    """Convertit des coordonnees YOLO normalisees en pixels (x1, y1, x2, y2)."""
    if img_w <= 0 or img_h <= 0:
        return 0, 0, 1, 1
    x1 = int((cx_n - w_n / 2) * img_w)
    y1 = int((cy_n - h_n / 2) * img_h)
    x2 = int((cx_n + w_n / 2) * img_w)
    y2 = int((cy_n + h_n / 2) * img_h)
    return x1, y1, x2, y2
