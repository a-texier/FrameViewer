# -*- coding: utf-8 -*-
"""Draw tracked and YOLO annotation boxes on a BGR frame."""
import cv2
import numpy as np

# ------------------------------- annotations -------------------------------

def _rotate_box_coords(x1, y1, x2, y2, orig_w, orig_h, degrees):
    """Transform box coordinates for an image rotation."""
    steps = (degrees // 90) % 4
    W, H = orig_w, orig_h
    for _ in range(steps):
        x1, y1, x2, y2 = H - 1 - y2, x1, H - 1 - y1, x2
        W, H = H, W
    return x1, y1, x2, y2


def _ann_color(idx):
    """Return a stable BGR color per track identifier using golden-ratio spacing."""
    h = (idx * 0.618033988749895) % 1.0
    i = int(h * 6); f = h * 6 - i
    v, s = 0.95, 0.85
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    r, g, b = [(v, t, p), (q, v, p), (p, v, t),
               (p, q, v), (t, p, v), (v, p, q)][i % 6]
    return (int(b * 255), int(g * 255), int(r * 255))


def draw_annotation_boxes(img, annots, orig_w, orig_h, rotation=0, scale=1.0):
    """Draw annotations on a copy of ``img``.

    ``annots`` contains ``(cls, x1, y1, x2, y2, track_id, labels)`` rows and
    can be reused by every view.
    """
    if img is None or not annots:
        return img
    h_img, w_img = img.shape[:2]
    s = scale or 1.0
    img = img.copy()
    for det in annots:
        cls = det[0]
        x1, y1, x2, y2 = det[1], det[2], det[3], det[4]
        if s < 1.0:
            x1, y1, x2, y2 = x1 * s, y1 * s, x2 * s, y2 * s
        x1, y1, x2, y2 = int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))
        track_id = det[5] if len(det) > 5 else 0
        labels = det[6] if len(det) > 6 else ()
        color = _ann_color(track_id if track_id else cls)
        if rotation:
            x1, y1, x2, y2 = _rotate_box_coords(x1, y1, x2, y2, orig_w, orig_h, rotation)
        x1 = max(0, min(x1, w_img - 1)); y1 = max(0, min(y1, h_img - 1))
        x2 = max(0, min(x2, w_img - 1)); y2 = max(0, min(y2, h_img - 1))
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        for i, lbl in enumerate(labels):
            ty = min(h_img - 2, y2 + 14 + i * 14)
            cv2.putText(img, lbl, (x1 + 2, ty),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)
    return img



def bake_sidecar_overlays(bgr, graphs):
    """Bake vector overlays into an exported image with OpenCV.

    This is the pixel-based counterpart of ``VideoWidget._draw_overlays`` and
    lives in the backend to avoid a circular UI import.
    """
    out = bgr.copy()
    if out.ndim == 2:
        out = cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)
    for g in graphs:
        rgb = g.get("color", (40, 220, 60))
        col = (int(rgb[2]), int(rgb[1]), int(rgb[0]))   # QColor RGB to OpenCV BGR
        thick = max(1, int(g.get("thickness", 1)))
        pts = [(int(round(x)), int(round(y))) for (x, y) in g.get("points", [])]
        t = g.get("type", "")
        if t == "points":
            for (x, y) in pts:
                cv2.circle(out, (x, y), 3, col, -1, cv2.LINE_AA)
        elif t in ("croix", "croixx", "x"):
            d = 5
            for (x, y) in pts:
                cv2.line(out, (x - d, y - d), (x + d, y + d), col, thick, cv2.LINE_AA)
                cv2.line(out, (x - d, y + d), (x + d, y - d), col, thick, cv2.LINE_AA)
        elif t in ("ligne_brisee_fermee", "polygone", "polygone_ferme"):
            if len(pts) >= 2:
                cv2.polylines(out, [np.array(pts, dtype=np.int32)], True, col, thick, cv2.LINE_AA)
        elif t in ("ligne_brisee", "polyligne", "ligne", "segment"):
            if len(pts) >= 2:
                cv2.polylines(out, [np.array(pts, dtype=np.int32)], False, col, thick, cv2.LINE_AA)
        elif t in ("ellipse", "cercle"):
            if pts:
                a = int(round(g.get("a", 0.0))) or 1
                b = int(round(g.get("b", 0.0))) or a
                cv2.ellipse(out, pts[0], (max(1, a), max(1, b)),
                           g.get("rotation", 0.0), 0, 360, col, thick, cv2.LINE_AA)
        else:
            for (x, y) in pts:
                cv2.circle(out, (x, y), 2, col, -1, cv2.LINE_AA)
        tx = g.get("text")
        if tx and tx.get("label") and pts:
            anchor = (pts[0][0] + int(tx.get("x", 6)), pts[0][1] - int(tx.get("y", -6)))
            cv2.putText(out, str(tx["label"]), anchor, cv2.FONT_HERSHEY_SIMPLEX,
                       max(0.3, tx.get("size", 10) / 24.0), col, 1, cv2.LINE_AA)
    return out
