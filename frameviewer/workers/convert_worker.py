# -*- coding: utf-8 -*-
"""Conversion d'une source (SPECIALIZED/video/images) vers un autre format, en tache
de fond (QThread) pour garder l'UI reactive sur des fichiers de plusieurs Go."""
import os

import cv2
import numpy as np
from PySide6 import QtCore

from frameviewer.core.io_utils import imwrite_unicode
from frameviewer.core.feature_registry import operation_for_kind
from frameviewer.core.pipeline import render_frame, to_intensity
from frameviewer.core.sources import ImageSequenceSource, SpecializedSource, VideoSource

SpecializedWriter = operation_for_kind("sequence_format", "writer")
type_img_for_dtype = operation_for_kind("sequence_format", "type_for_dtype")

class ConvertWorker(QtCore.QThread):
    """Convertit une source (SPECIALIZED / video / dossier d'images) vers un autre format :
    dossier PNG 8 bits (rendu), dossier PNG 16 bits (brut), video MP4, ou SPECIALIZED.
    La source est rouverte DANS le thread pour ne pas partager de handle avec l'UI."""
    progress = QtCore.Signal(int, int)        # (courant, total)
    finished_ok = QtCore.Signal(str, int)     # (sortie, nb_ecrits)
    failed = QtCore.Signal(str)

    def __init__(self, src_kind, src_arg, out_path, mode, lo, hi, lut_data,
                 cube_lut, cube_size, step, full_range,
                 filters=None, fps=25.0, parent=None):
        super().__init__(parent)
        self.src_kind = src_kind    # "specialized" | "video" | "images"
        self.src_arg = src_arg      # chemin (specialized/video) ou liste de chemins (images)
        self.out_path = out_path
        self.mode = mode            # "png8" | "png16" | "mp4" | "specialized"
        self.lo, self.hi = lo, hi
        self.lut_data = lut_data
        self.cube_lut, self.cube_size = cube_lut, cube_size
        self.step = max(1, int(step))
        self.full_range = full_range
        self.filters = filters or {}
        self.fps = float(fps) if fps and fps > 0 else 25.0
        self._stop = False

    def stop(self):
        self._stop = True

    def _open_source(self):
        if self.src_kind == "specialized":
            return SpecializedSource(self.src_arg)
        if self.src_kind == "video":
            return VideoSource(self.src_arg)
        if self.src_kind == "images":
            return ImageSequenceSource(list(self.src_arg))
        raise IOError(f"Type de source inconnu: {self.src_kind}")

    def run(self):
        if self.mode == "specialized" and SpecializedWriter is None:
            self.failed.emit("Module specialized_reader indisponible (ecriture SPECIALIZED).")
            return
        try:
            src = self._open_source()
        except Exception as ex:
            self.failed.emit(str(ex))
            return
        writer = None          # cv2.VideoWriter (mp4)
        specialized_w = None           # SpecializedWriter (specialized)
        try:
            if self.mode in ("mp4", "specialized"):
                d = os.path.dirname(self.out_path)
                if d:
                    os.makedirs(d, exist_ok=True)
            else:
                os.makedirs(self.out_path, exist_ok=True)
            n = src.count
            written = 0
            for i in range(0, n, self.step):
                if self._stop:
                    break
                raw = src.get(i)
                if raw is None:
                    continue
                if self.mode == "specialized":
                    inten = to_intensity(raw)
                    if inten.ndim == 3 and inten.shape[2] == 1:
                        inten = inten[:, :, 0]
                    if not np.issubdtype(inten.dtype, np.integer):
                        inten = np.clip(inten, 0, 255).astype(np.uint8)  # video/images -> 8 bits
                    if specialized_w is None:
                        rows, cols = inten.shape[:2]
                        specialized_w = SpecializedWriter(self.out_path, rows, cols,
                                          type_img_for_dtype(inten.dtype), channels=1)
                    specialized_w.write(inten)
                    written += 1
                elif self.mode == "png16":
                    img = raw
                    if img.ndim == 3 and img.shape[2] == 1:
                        img = img[:, :, 0]
                    if img.dtype not in (np.uint8, np.uint16):
                        img = np.clip(img, 0, 65535).astype(np.uint16)
                    out = os.path.join(self.out_path, f"frame_{i:06d}.png")
                    if imwrite_unicode(out, img):
                        written += 1
                else:
                    img = render_frame(raw, self.lo, self.hi, self.lut_data,
                                       self.cube_lut, self.cube_size,
                                       self.full_range, self.filters)
                    if self.mode == "mp4":
                        if img.ndim == 2:
                            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
                        if writer is None:
                            h, w = img.shape[:2]
                            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                            writer = cv2.VideoWriter(self.out_path, fourcc, self.fps, (w, h))
                            if not writer.isOpened():
                                raise IOError("Impossible d'ouvrir le MP4 en ecriture.")
                        writer.write(img)
                        written += 1
                    else:  # png8
                        out = os.path.join(self.out_path, f"frame_{i:06d}.png")
                        if imwrite_unicode(out, img):
                            written += 1
                if (i % 10) == 0 or (i + self.step) >= n:
                    self.progress.emit(min(i + self.step, n), n)
            if writer is not None:
                writer.release()
                writer = None
            if specialized_w is not None:
                specialized_w.close()
                specialized_w = None
            src.close()
        except Exception as ex:
            try:
                if writer is not None:
                    writer.release()
            except Exception:
                pass
            try:
                if specialized_w is not None:
                    specialized_w.close()
            except Exception:
                pass
            try:
                src.close()
            except Exception:
                pass
            self.failed.emit(str(ex))
            return
        if self._stop:
            self.failed.emit(f"Conversion interrompue ({written} images ecrites).")
        else:
            self.finished_ok.emit(self.out_path, written)

