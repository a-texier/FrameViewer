# -*- coding: utf-8 -*-
"""Formats de sortie des conversions.

Le dialogue propose d'abord un format, puis seulement les options qui ont un
sens pour la donnee d'entree : une source 8 bits ne peut pas devenir 16 bits,
une source 16 bits peut rester brute ou passer en 8 bits avec une regle de
conversion explicite (+/- 3 sigma, min/max, pleine plage, plage choisie).

Ce module ne depend pas de Qt : choix proposes, conversion des frames et
ecrivains (dossier d'images, fichier SPECIALIZED, video MP4 via OpenCV ou ffmpeg).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

import cv2
import numpy as np

from frameviewer.core.feature_registry import operation_for_kind
from frameviewer.core.io_utils import imwrite_unicode
from frameviewer.core.pipeline import auto_window_sigma, finite_range, to_intensity, window8

SpecializedWriter = operation_for_kind("sequence_format", "writer")
type_img_for_dtype = operation_for_kind("sequence_format", "type_for_dtype")

IMAGE_EXTENSIONS = {"png": ".png", "tiff": ".tiff", "jpg": ".jpg"}

FORMAT_CHOICES = (
    ("png", "Images PNG (dossier)"),
    ("tiff", "Images TIFF (dossier)"),
    ("jpg", "Images JPEG (dossier)"),
    ("specialized", "Fichier SPECIALIZED (.specialized)"),
    ("mp4", "Vidéo MP4"),
    ("copy", "Copie des fichiers originaux (sans modification)"),
)

MAPPING_CHOICES = (
    ("sigma", "Moyenne +/- 3 sigma (par frame)"),
    ("minmax", "Min / max (par frame)"),
    ("full", "Pleine plage du type (ex. 0-65535)"),
    ("custom", "Plage personnalisée (min / max)"),
)

ENCODER_CHOICES = (
    ("mp4v", "MPEG-4 Part 2 (OpenCV, toujours disponible)"),
    ("h264", "H.264 / AVC (ffmpeg)"),
    ("h265", "H.265 / HEVC (ffmpeg)"),
    ("copy", "Copie du flux, sans re-compression (ffmpeg)"),
)

QUALITY_CHOICES = (
    (0, "Sans perte (CRF 0, fichier très lourd)"),
    (12, "Très haute (CRF 12)"),
    (18, "Haute (CRF 18)"),
    (23, "Standard (CRF 23)"),
    (28, "Compacte (CRF 28)"),
)

SPEED_CHOICES = (
    ("veryfast", "Rapide (fichier plus gros)"),
    ("medium", "Équilibrée"),
    ("slow", "Lente (fichier plus compact)"),
)

# extensions dont le flux video peut etre recopie tel quel dans un .mp4
STREAM_COPY_EXTENSIONS = (".mp4", ".m4v", ".mov")

_TIFF_DTYPES = {
    np.dtype(t) for t in (np.uint8, np.int8, np.uint16, np.int16, np.int32, np.float32, np.float64)
}


@dataclass
class ExportSpec:
    """Tout ce que l'utilisateur a choisi pour une conversion."""

    fmt: str = "png"
    depth: str = "8"  # "8" ou "native" (profondeur d'origine conservee)
    mapping: str = "sigma"
    custom_range: tuple[float, float] = (0.0, 255.0)
    as_viewed: bool = False
    jpeg_quality: int = 95
    encoder: str = "mp4v"
    crf: int = 18
    speed: str = "medium"
    fps: float = 25.0

    @property
    def is_folder(self) -> bool:
        return self.fmt in IMAGE_EXTENSIONS or self.fmt == "copy"

    @property
    def eight_bit(self) -> bool:
        return self.fmt in ("jpg", "mp4") or self.depth == "8"

    @property
    def renders_view(self) -> bool:
        """Frames rendues comme a l'ecran (contraste/LUT + calques)."""
        return self.as_viewed and self.eight_bit and not self.stream_copy

    @property
    def stream_copy(self) -> bool:
        return self.fmt == "mp4" and self.encoder == "copy"

    def label(self, english: bool = False) -> str:
        """Nom court du format choisi, pour les messages de fin."""
        if self.fmt == "copy":
            return "Original files" if english else "Fichiers originaux"
        if self.fmt == "mp4":
            names = {"mp4v": "MPEG-4", "h264": "H.264", "h265": "H.265", "copy": "copy"}
            return f"MP4 ({names.get(self.encoder, self.encoder)})"
        name = {"png": "PNG", "tiff": "TIFF", "jpg": "JPEG", "specialized": "SPECIALIZED"}[self.fmt]
        if self.renders_view:
            return f"{name} 8 bits, " + ("as displayed" if english else "tel qu'affiché")
        if self.eight_bit:
            return f"{name} 8 bits"
        return f"{name} " + ("raw" if english else "brut")


# -- donnee d'entree -----------------------------------------------------------


def is_eight_bit(dtype) -> bool:
    return np.dtype(dtype) == np.uint8


def native_depth_allowed(fmt: str, dtype) -> bool:
    """Le format peut-il garder la profondeur d'origine de l'entree ?"""
    dt = np.dtype(dtype)
    if dt == np.uint8 or fmt in ("jpg", "mp4", "copy"):
        return False
    if fmt == "png":
        return bool(np.issubdtype(dt, np.integer))
    return fmt in ("tiff", "specialized")


def native_depth_label(dtype) -> str:
    dt = np.dtype(dtype)
    bits = dt.itemsize * 8
    if np.issubdtype(dt, np.floating):
        return f"{bits} bits flottants (données brutes)"
    return f"{bits} bits (données brutes)"


def depth_reason(fmt: str, dtype) -> str:
    """Pourquoi la sortie est forcee en 8 bits ("" si le choix est libre)."""
    dt = np.dtype(dtype)
    if fmt == "copy":
        return ""
    if dt == np.uint8:
        return "Entrée 8 bits : la sortie reste en 8 bits."
    if fmt == "jpg":
        return "JPEG est limité à 8 bits."
    if fmt == "mp4":
        return "MP4 est limité à 8 bits."
    if fmt == "png" and np.issubdtype(dt, np.floating):
        return "PNG ne stocke pas de flottants : sortie 8 bits."
    if fmt == "png" and dt != np.uint16:
        return "PNG est limité à 16 bits : valeurs écrêtées à 0-65535."
    return ""


def mapping_choices(dtype):
    """Regles 8 bits utiles pour ce type (la pleine plage d'un flottant n'existe pas)."""
    floating = np.issubdtype(np.dtype(dtype), np.floating)
    return [(k, lbl) for k, lbl in MAPPING_CHOICES if not (floating and k == "full")]


def find_ffmpeg():
    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")


def stream_copy_possible(source) -> bool:
    """Coupe sans re-compression : une vraie video dans un conteneur compatible."""
    path = getattr(source, "path", "") or ""
    return (
        hasattr(source, "cap")
        and os.path.splitext(path)[1].lower() in STREAM_COPY_EXTENSIONS
        and find_ffmpeg() is not None
    )


# -- conversion des frames -----------------------------------------------------


def _squeeze(raw):
    if raw.ndim == 3 and raw.shape[2] == 1:
        return raw[:, :, 0]
    return raw


def to_8bit(raw, mapping="sigma", custom_range=None):
    """Ramene ``raw`` sur 8 bits selon la regle choisie (couleurs conservees).

    Une entree deja 8 bits est renvoyee telle quelle. La fenetre est calculee
    sur l'intensite, puis appliquee a chaque canal.
    """
    if raw.dtype == np.uint8:
        return raw
    data = _squeeze(raw)
    if data.ndim == 3 and data.shape[2] == 4:
        data = data[:, :, :3]
    if mapping == "custom" and custom_range is not None:
        lo, hi = custom_range
    elif mapping == "full" and np.issubdtype(data.dtype, np.integer):
        info = np.iinfo(data.dtype)
        lo, hi = info.min, info.max
    elif mapping == "minmax":
        lo, hi = finite_range(to_intensity(data))
    else:
        lo, hi = auto_window_sigma(to_intensity(data))
    return window8(data, lo, hi)


def to_native(raw, fmt):
    """Donnees d'origine, adaptees seulement a ce que le format sait stocker."""
    data = _squeeze(raw)
    if fmt == "png" and data.dtype not in (np.uint8, np.uint16):
        if np.issubdtype(data.dtype, np.floating):
            data = np.nan_to_num(data, nan=0.0, posinf=65535.0, neginf=0.0)
        data = np.clip(data, 0, 65535).astype(np.uint16)
    elif fmt == "tiff" and data.dtype not in _TIFF_DTYPES:
        data = data.astype(np.float64)
    return data


def convert_frame(raw, spec: ExportSpec):
    """Frame brute -> frame a ecrire (hors rendu « tel qu'affiché »)."""
    if raw is None:
        return None
    if spec.eight_bit:
        return to_8bit(raw, spec.mapping, spec.custom_range)
    return to_native(raw, spec.fmt)


# -- ecrivains -----------------------------------------------------------------


class _FixedSize:
    """Une sequence SPECIALIZED/MP4 a une taille unique : on aligne sur la 1re frame
    (une ROI suivie change de taille d'une frame a l'autre)."""

    _size = None

    def _fit(self, frame):
        size = (frame.shape[1], frame.shape[0])
        if self._size is None:
            self._size = size
        elif size != self._size:
            frame = cv2.resize(frame, self._size, interpolation=cv2.INTER_AREA)
        return frame


class ImageFolderWriter:
    def __init__(self, folder, fmt, prefix="frame", jpeg_quality=95):
        self._folder = folder
        self._ext = IMAGE_EXTENSIONS[fmt]
        self._prefix = prefix
        self._params = [cv2.IMWRITE_JPEG_QUALITY, int(jpeg_quality)] if fmt == "jpg" else []
        self.count = 0

    def write(self, index, frame):
        path = os.path.join(self._folder, f"{self._prefix}_{index:05d}{self._ext}")
        if self._params:
            ok, buf = cv2.imencode(self._ext, frame, self._params)
            if ok:
                buf.tofile(path)
        else:
            ok = imwrite_unicode(path, frame)
        if not ok:
            raise RuntimeError(f"Écriture impossible : {path}")
        self.count += 1

    def abort(self):
        pass

    def close(self):
        pass


class SpecializedFileWriter(_FixedSize):
    def __init__(self, path):
        if SpecializedWriter is None:
            raise RuntimeError("Écriture SPECIALIZED indisponible dans cette version.")
        self._path = path
        self._writer = None
        self.count = 0

    def write(self, index, frame):
        frame = self._fit(frame)
        if self._writer is None:
            rows, cols = frame.shape[:2]
            channels = frame.shape[2] if frame.ndim == 3 else 1
            self._writer = SpecializedWriter(
                self._path, rows, cols, type_img_for_dtype(frame.dtype), channels
            )
        self._writer.write(frame[:, :, None] if frame.ndim == 2 else frame)
        self.count += 1

    def abort(self):
        self.close()

    def close(self):
        if self._writer is not None:
            self._writer.close()
            self._writer = None


def ffmpeg_encode_args(spec: ExportSpec):
    """Arguments d'encodage ffmpeg pour H.264 / H.265."""
    args = ["-c:v", "libx264" if spec.encoder == "h264" else "libx265", "-preset", spec.speed]
    if spec.encoder == "h265":
        # en HEVC, CRF 0 n'est pas sans perte : il faut le mode lossless
        params = "log-level=error" + (":lossless=1" if spec.crf == 0 else "")
        args += ["-x265-params", params]
        if spec.crf:
            args += ["-crf", str(spec.crf)]
        args += ["-tag:v", "hvc1"]
    else:
        args += ["-crf", str(spec.crf)]
    return args + ["-pix_fmt", "yuv420p", "-movflags", "+faststart"]


class Mp4Writer(_FixedSize):
    """MP4 8 bits : OpenCV (mpeg-4 part 2) ou ffmpeg alimente en images brutes."""

    def __init__(self, path, spec: ExportSpec):
        self._path = path
        self._spec = spec
        self._cv = None
        self._proc = None
        self._err = None
        self.count = 0

    def _open(self, width, height):
        fps = float(self._spec.fps or 25.0)
        if self._spec.encoder == "mp4v":
            self._cv = cv2.VideoWriter(
                self._path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
            )
            if not self._cv.isOpened():
                raise RuntimeError(f"Encodeur MPEG-4 indisponible : {self._path}")
            return
        ff = find_ffmpeg()
        if ff is None:
            raise RuntimeError("ffmpeg introuvable : installez-le ou choisissez MPEG-4.")
        cmd = [
            ff, "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
            "-r", f"{fps:.6g}", "-i", "-", "-an",
            # yuv420p exige des dimensions paires
            "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            *ffmpeg_encode_args(self._spec),
            self._path,
        ]  # fmt: skip
        self._err = tempfile.TemporaryFile()
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=self._err,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def _ffmpeg_error(self):
        if self._err is None:
            return "erreur inconnue"
        self._err.seek(0)
        text = self._err.read().decode("utf-8", errors="replace").strip()
        return text.splitlines()[-1] if text else "erreur inconnue"

    def write(self, index, frame):
        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        elif frame.shape[2] == 4:
            frame = frame[:, :, :3]
        frame = self._fit(np.ascontiguousarray(frame))
        if self._cv is None and self._proc is None:
            self._open(frame.shape[1], frame.shape[0])
        if self._cv is not None:
            self._cv.write(frame)
        else:
            try:
                self._proc.stdin.write(frame.tobytes())
            except OSError as ex:
                # ffmpeg s'est arrete (option refusee, disque plein...)
                self._stop_process(kill=True)
                raise RuntimeError(f"ffmpeg : {self._ffmpeg_error()}") from ex
        self.count += 1

    def _stop_process(self, kill):
        proc, self._proc = self._proc, None
        if proc is None:
            return 0
        if kill and proc.poll() is None:
            proc.kill()
        try:
            proc.stdin.close()
        except OSError:
            pass  # tube deja casse : ffmpeg est arrete
        return proc.wait()

    def _output_complete(self):
        """Certains builds de libx265 plantent en se fermant alors que le
        fichier est deja complet : on l'accepte s'il se relit en entier."""
        cap = cv2.VideoCapture(self._path)
        try:
            ok = cap.isOpened() and cap.read()[0]
            return bool(ok) and cap.get(cv2.CAP_PROP_FRAME_COUNT) >= self.count
        finally:
            cap.release()

    def abort(self):
        self._stop_process(kill=True)
        self.close()

    def close(self):
        if self._cv is not None:
            self._cv.release()
            self._cv = None
        try:
            if self._stop_process(kill=False) != 0 and not self._output_complete():
                raise RuntimeError(f"ffmpeg : {self._ffmpeg_error()}")
        finally:
            if self._err is not None and self._proc is None:
                self._err.close()
                self._err = None


def open_writer(spec: ExportSpec, dest: str, prefix: str = "frame"):
    if spec.fmt in IMAGE_EXTENSIONS:
        return ImageFolderWriter(dest, spec.fmt, prefix, spec.jpeg_quality)
    if spec.fmt == "specialized":
        return SpecializedFileWriter(dest)
    if spec.fmt == "mp4":
        return Mp4Writer(dest, spec)
    raise ValueError(f"format sans ecrivain : {spec.fmt}")
