# -*- coding: utf-8 -*-
"""Unicode-safe image I/O, natural sorting, and duration formatting."""
import os
import re

import cv2
import numpy as np

from frameviewer.core.constants import IMAGE_EXTS


def dirent_image_path(entry):
    """Return an image path for an ``os.DirEntry``, otherwise ``None``.

    Normal files are classified from the name extension without a stat call.
    This keeps large and remote directory scans cheap. Only an extensionless
    symbolic link requires resolving its target.
    """
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
    """List folder images in natural order with one ``os.scandir`` pass.

    Names with extensions require no per-file stat call. Symbolic links,
    including extensionless links, are supported. Return an empty list when
    the directory cannot be read.
    """
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
    """List files whose lowercase extension belongs to ``exts``.

    The result is naturally sorted and produced without per-file stat calls.
    Return an empty list when the directory cannot be read.
    """
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
    """Read an image from a Unicode Windows path."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, flags)
    except Exception:
        return None


def imwrite_unicode(path, img):
    """Write an image to a Unicode path through encode-and-tofile."""
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

