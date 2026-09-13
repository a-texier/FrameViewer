# -*- coding: utf-8 -*-
"""Constantes partagees : extensions reconnues, palettes LUT, couleurs nommees."""
import cv2

VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".m4v", ".wmv", ".flv", ".webm",
              ".mpg", ".mpeg", ".ts", ".m2ts", ".mts"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".jp2"}

COLORMAPS = {
    "Aucune (couleur)": None,
    "Niveaux de gris": "gray",
    "Viridis": cv2.COLORMAP_VIRIDIS,
    "Jet": cv2.COLORMAP_JET,
    "Turbo": cv2.COLORMAP_TURBO,
    "Inferno": cv2.COLORMAP_INFERNO,
    "Magma": cv2.COLORMAP_MAGMA,
    "Plasma": cv2.COLORMAP_PLASMA,
    "Hot": cv2.COLORMAP_HOT,
    "Bone": cv2.COLORMAP_BONE,
    "Ocean": cv2.COLORMAP_OCEAN,
    "Rainbow": cv2.COLORMAP_RAINBOW,
    "HSV": cv2.COLORMAP_HSV,
    "Parula": cv2.COLORMAP_PARULA,
}

# couleurs nommees (francais) -> RGB 0..255
FRENCH_COLORS = {
    "rouge": (230, 40, 40), "vert": (40, 220, 60), "bleu": (60, 120, 240),
    "jaune": (240, 220, 40), "blanc": (240, 240, 240), "noir": (20, 20, 20),
    "cyan": (40, 220, 220), "magenta": (230, 60, 200), "orange": (245, 150, 30),
    "violet": (170, 80, 220), "rose": (245, 120, 170), "gris": (150, 150, 150),
    "marron": (140, 80, 40), "turquoise": (40, 210, 180),
}
