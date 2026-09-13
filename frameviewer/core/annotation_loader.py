# -*- coding: utf-8 -*-
"""Load annotations from tracked-box and YOLO layouts.

Merged YOLO files use one ``frame_id class cx cy width height`` row per box.
YOLO folders contain one naturally ordered text file per annotated frame.
All readers return ``(class, x1, y1, x2, y2, track_id, labels)`` tuples.
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
    """Return whether ``path`` is a supported drop target for annotations.

    Both the main and multi-view drop handlers use this function so format
    recognition cannot diverge.
    """
    if os.path.isdir(path):
        return next(glob.iglob(os.path.join(path, "*.txt")), None) is not None
    return os.path.splitext(path)[1].lower() in (".ver", ".txt")


def scan_dropped_folder(folder):
    """Inspect only the immediate contents of a dropped directory.

    Return separate image, text-label, and tracked-box file lists. Images must
    be opened before normalized annotations are applied because box conversion
    requires the image dimensions. Empty lists let the caller report that no
    directly usable content was found.
    """
    images, txts, vers = [], [], []
    try:
        with os.scandir(folder) as it:   # One enumeration, no per-file stat.
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
    """Detect the annotation layout and load it.

    Returns ``dict[int, list[tuple]]``:
        frame_idx -> [(cls, x1, y1, x2, y2, track_id, labels), ...]
        labels contains optional class metadata.

    ``default_track`` is used when the source row has no track identifier.
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
    """Read short and long tracked-box rows.

    Short rows contain ``frame visibility x1 y1 x2 y2``. Long rows append a
    track identifier and optional class hierarchy. Input frames are 1-based
    and output frames are 0-based. The visibility column is not used as a
    filter because producers do not assign it consistently.
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
                track_id = default_track      # Short layout: one file per track.
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
    """Read merged YOLO rows: frame, class, center, width, and height."""
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
    """Return the last integer in a filename stem, or ``None``."""
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
    # Use the complete label stem as the key. MainWindow first pairs labels and
    # images by equal stems, then falls back to natural order if no names match.
    # Extracting one integer would be ambiguous when a stem contains a frame
    # number and a timestamp.
    stem = os.path.splitext(os.path.basename(txt_path))[0]
    return stem, dets


def _load_yolo_folder(folder, img_w, img_h):
    """Read a YOLO directory with one text file per annotated frame.

    Empty frames commonly have no file, so list position cannot identify a
    frame reliably. Complete stems are retained for name-based pairing.

    Reading is deliberately sequential. Profiling thousands of tiny cached
    files showed a thread pool to be slower because synchronization overhead
    dominated I/O. One open/close pair per annotated frame is inherent to this
    storage layout.
    """
    txts = natural_sort(glob.glob(os.path.join(folder, "*.txt")))
    txts = [path for path in txts if os.path.basename(path).lower() != "classes.txt"]
    class_names = _load_class_names(folder)
    annots = {}   # Label stem -> detection rows.
    for txt_path in txts:
        stem, dets = _read_yolo_txt((txt_path, img_w, img_h, class_names))
        if not dets:
            continue
        annots.setdefault(stem, []).extend(dets)
    return annots


def _yolo_norm_to_pixel(cx_n, cy_n, w_n, h_n, img_w, img_h):
    """Convert normalized YOLO coordinates to pixel corner coordinates."""
    if img_w <= 0 or img_h <= 0:
        return 0, 0, 1, 1
    x1 = int((cx_n - w_n / 2) * img_w)
    y1 = int((cy_n - h_n / 2) * img_h)
    x2 = int((cx_n + w_n / 2) * img_w)
    y2 = int((cy_n + h_n / 2) * img_h)
    return x1, y1, x2, y2
