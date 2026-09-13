# -*- coding: utf-8 -*-
"""I/O image tolerante Unicode (Windows), tri naturel, formatage de duree."""
import os
import re

import cv2
import numpy as np

from frameviewer.core.constants import IMAGE_EXTS


def dirent_image_path(entry):
    """-> chemin de `entry` (os.DirEntry, issu de os.scandir) si c'est une image,
    sinon None. On se fie a l'EXTENSION DU NOM (aucun acces disque -> rapide sur
    gros dossiers et surtout sur SMB, ou un stat par fichier coute un aller-retour
    reseau). Seul cas qui resout la cible : un symlink dont le nom n'a PAS
    d'extension (rare) -- 1 stat uniquement pour ceux-la."""
    ext = os.path.splitext(entry.name)[1].lower()
    if ext in IMAGE_EXTS:
        return entry.path
    if ext == "":
        try:
            if entry.is_symlink() and \
                    os.path.splitext(os.path.realpath(entry.path))[1].lower() in IMAGE_EXTS:
                return entry.path
        except OSError:
            return None
    return None


def list_images(folder):
    """Liste triee (ordre naturel) des images d'un dossier, via os.scandir :
    UNE enumeration, aucun stat par fichier pour les noms a extension. Gere les
    dossiers de symlinks (y compris sans extension). [] si le dossier est
    illisible."""
    out = []
    try:
        with os.scandir(folder) as it:
            for e in it:
                p = dirent_image_path(e)
                if p:
                    out.append(p)
    except OSError:
        return []
    return natural_sort(out)


def list_files(folder, exts):
    """Liste triee (ordre naturel) des fichiers d'un dossier dont l'extension est
    dans `exts` (set de '.ext' minuscules), via os.scandir (pas de stat par
    fichier). [] si le dossier est illisible."""
    out = []
    try:
        with os.scandir(folder) as it:
            for e in it:
                if os.path.splitext(e.name)[1].lower() in exts:
                    out.append(e.path)
    except OSError:
        return []
    return natural_sort(out)


def imread_unicode(path, flags=cv2.IMREAD_COLOR):
    """imread tolerant aux chemins Windows avec accents/unicode."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, flags)
    except Exception:
        return None


def imwrite_unicode(path, img):
    """imwrite tolerant aux chemins unicode (encode + tofile)."""
    ext = os.path.splitext(path)[1] or ".png"
    try:
        ok, buf = cv2.imencode(ext, img)
        if not ok:
            return False
        buf.tofile(path)
        return True
    except Exception:
        return False


def natural_key(s):
    name = os.path.basename(s)
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def natural_sort(lst):
    return sorted(lst, key=natural_key)


def fmt_time(sec):
    sec = max(0.0, float(sec))
    m = int(sec // 60)
    s = int(sec % 60)
    return f"{m:02d}:{s:02d}"


