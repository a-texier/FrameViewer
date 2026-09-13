# -*- coding: utf-8 -*-
"""Convert between tracked-box layers and frame-centric YOLO datasets.

The layouts model different concepts: tracked rows preserve object identity
across frames, while YOLO rows describe independent detections in one frame.
Converting from YOLO therefore cannot reconstruct real temporal identity.

This module has no PySide6 dependency and is reusable from workers or scripts.
"""
import os
import random

from frameviewer.core.annotation_loader import load_annotations
from frameviewer.core.io_utils import imwrite_unicode


def class_names_from_annotations(annotations):
    """Build stable generic class names, preferring labels carried by rows."""
    by_id = {}
    maximum = -1
    for detections in annotations.values():
        for detection in detections:
            cls = int(detection[0]) if detection else 0
            maximum = max(maximum, cls)
            labels = detection[6] if len(detection) > 6 else ()
            if labels and labels[0]:
                by_id.setdefault(cls, str(labels[0]))
    return [by_id.get(index, f"class {index}") for index in range(maximum + 1)] or ["class 0"]


def split_frame_ids(frame_ids, ratios=(1.0, 0.0, 0.0), seed=0):
    """Split frame identifiers into train, validation, and test groups.

    Splitting occurs at frame level so boxes from one image cannot leak across
    groups. Ratios are normalized and need not sum to one.
    """
    ids = sorted(set(frame_ids))
    total_ratio = sum(ratios) or 1.0
    r = [max(0.0, x) / total_ratio for x in ratios]
    rng = random.Random(seed)
    shuffled = list(ids)
    rng.shuffle(shuffled)
    n = len(shuffled)
    n_train = round(n * r[0])
    n_val = round(n * r[1])
    n_train = min(n_train, n)
    n_val = min(n_val, n - n_train)
    train = shuffled[:n_train]
    val = shuffled[n_train:n_train + n_val]
    test = shuffled[n_train + n_val:]
    return {"train": sorted(train), "val": sorted(val), "test": sorted(test)}


def export_yolo(annotations, frame_ids, img_w, img_h, out_dir,
                 class_names=None, ratios=(1.0, 0.0, 0.0), seed=0,
                 include_empty=True, frame_provider=None, progress_cb=None):
    """Write a YOLO dataset from annotations already loaded in memory.

    Output layout:
        out_dir/labels/{train,val,test}/frame_NNNNNN.txt
        out_dir/images/{train,val,test}/frame_NNNNNN.png   (with frame_provider)
        out_dir/data.yaml

    A frame provider also exports images. ``include_empty`` writes empty label
    files for negative samples. The optional progress callback runs after each
    frame. Return the number of frames written per split.
    """
    class_names = list(class_names) if class_names else class_names_from_annotations(annotations)
    if img_w <= 0 or img_h <= 0:
        raise ValueError("Dimensions image invalides (img_w/img_h <= 0).")
    splits = split_frame_ids(frame_ids, ratios, seed)
    counts = {}
    total = sum(len(v) for v in splits.values())
    done = 0
    any_images = frame_provider is not None
    for split_name, ids in splits.items():
        written = 0
        if not ids:
            counts[split_name] = 0
            continue
        lbl_dir = os.path.join(out_dir, "labels", split_name)
        os.makedirs(lbl_dir, exist_ok=True)
        img_dir = None
        if any_images:
            img_dir = os.path.join(out_dir, "images", split_name)
            os.makedirs(img_dir, exist_ok=True)
        for idx in ids:
            dets = annotations.get(idx, [])
            if not dets and not include_empty:
                done += 1
                if progress_cb:
                    progress_cb(done, total)
                continue
            lines = []
            for det in dets:
                cls = int(det[0]) if len(det) > 0 else 0
                x1, y1, x2, y2 = det[1], det[2], det[3], det[4]
                cx = ((x1 + x2) / 2.0) / img_w
                cy = ((y1 + y2) / 2.0) / img_h
                bw = (x2 - x1) / float(img_w)
                bh = (y2 - y1) / float(img_h)
                lines.append(f"{cls} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
            name = f"frame_{idx:06d}"
            with open(os.path.join(lbl_dir, name + ".txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            if img_dir is not None:
                frame = frame_provider(idx)
                if frame is not None:
                    imwrite_unicode(os.path.join(img_dir, name + ".png"), frame)
            written += 1
            done += 1
            if progress_cb:
                progress_cb(done, total)
        counts[split_name] = written

    yaml_lines = [f"path: {out_dir}"]
    for split_name in ("train", "val", "test"):
        if counts.get(split_name):
            yaml_lines.append(f"{split_name}: images/{split_name}" if any_images
                               else f"{split_name}: labels/{split_name}")
    yaml_lines.append(f"nc: {len(class_names)}")
    yaml_lines.append("names: [" + ", ".join(f"'{c}'" for c in class_names) + "]")
    with open(os.path.join(out_dir, "data.yaml"), "w", encoding="utf-8") as f:
        f.write("\n".join(yaml_lines) + "\n")
    return counts


def yolo_to_ver_lines(annotations, frame_ids=None, class_names=None):
    """Convert YOLO annotations to long, tab-separated tracked-box rows.

    YOLO provides no cross-frame object identity. The generated track
    identifier is the row position inside each frame and is not real temporal
    tracking; an object may receive another identifier in the next frame.
    """
    class_names = list(class_names) if class_names else class_names_from_annotations(annotations)
    ids = sorted(frame_ids) if frame_ids is not None else sorted(annotations.keys())
    lines = []
    for idx in ids:
        for pos, det in enumerate(annotations.get(idx, [])):
            cls = int(det[0]) if len(det) > 0 else 0
            x1, y1, x2, y2 = det[1], det[2], det[3], det[4]
            cls_name = class_names[cls] if 0 <= cls < len(class_names) else "unknown"
            parts = [f"{idx + 1:.6e}", "1.000000e+00",
                     f"{x1:.6e}", f"{y1:.6e}", f"{x2:.6e}", f"{y2:.6e}",
                     f"{pos:.6e}", cls_name]
            lines.append("\t".join(parts))
    return lines


def yolo_to_ver(yolo_path, out_ver_path, img_w, img_h, class_names=None):
    """Convert a YOLO path to one merged tracked-box file.

    Return ``(box_count, frame_count)``. See ``yolo_to_ver_lines`` for the
    positional identifier limitation.
    """
    annots = load_annotations(yolo_path, img_w, img_h)
    lines = yolo_to_ver_lines(annots, class_names=class_names)
    with open(out_ver_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return len(lines), len(annots)
