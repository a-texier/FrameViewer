# -*- coding: utf-8 -*-
"""Exports non bloquants.

Un export ne doit ni figer la fenetre principale, ni changer de resultat si
l'utilisateur continue a travailler pendant qu'il tourne (navigation, autre
fichier, autre vue, autre contraste). D'ou deux pieces :

- ``ExportSnapshot`` fige, au lancement, tout ce qui definit le rendu d'une
  vue : un lecteur a soi sur le meme flux (le decodeur de lecture n'est jamais
  deplace), fenetre noir/blanc, LUT, filtres, rotation, soustraction
  temporelle, calques visibles. Les ROI/crops sont convertis en coordonnees
  natives (la vue peut etre affichee en resolution reduite).
- ``JobRunner`` execute un export ecrit comme un generateur (un ``yield`` par
  frame) par petites tranches sur le thread Qt : pas de boucle modale, la
  fenetre reste utilisable et plusieurs exports peuvent tourner ensemble.
"""

import time

import cv2
import numpy as np
from PySide6 import QtCore

from frameviewer.core.annotations import (
    _rotate_box_coords,
    bake_sidecar_overlays,
    draw_annotation_boxes,
)
from frameviewer.core.pipeline import render_frame, to_intensity
from frameviewer.core.plugin_render import alpha_over, composite_patches

_ROTATIONS = {
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


class ExportSnapshot:
    """Etat fige d'une vue pour un export (voir le docstring du module)."""

    def __init__(
        self,
        mw,
        source,
        *,
        engine,
        rotation=0,
        window=(0.0, 255.0),
        lut=None,
        full_range=(0, 255),
        filters=None,
        temporal_k=0,
        fetch_scale=1.0,
        show_overlays=True,
        annotations=None,
        layers=None,
        overlays=None,
        sidecar_offset=0,
        view_key=None,
        annot_path="",
    ):
        self._mw = mw
        self.source = source
        reopened = mw._reopen_source_like(source) if source is not None else None
        self.reader = reopened or source
        self._ref_reader = None
        self.engine = engine
        self.rotation = int(rotation) % 360
        self.lo, self.hi = float(window[0]), float(window[1])
        self.lut = lut
        self.full_range = full_range
        self.filters = dict(filters or {})
        self.temporal_k = max(0, int(temporal_k))
        self.fetch_scale = float(fetch_scale or 1.0)
        self.show_overlays = bool(show_overlays)
        self.annotations = annotations or {}
        self.layers = [dict(layer) for layer in (layers or [])]
        self.overlays = overlays or {}
        self.sidecar_offset = int(sidecar_offset)
        self.view_key = view_key
        self.annot_path = annot_path or ""
        self._native_dims = None

    # -- construction ---------------------------------------------------------
    @classmethod
    def from_engine(cls, mw):
        """Vue active (moteur de MainWindow)."""
        return cls(
            mw,
            mw.source,
            engine=True,
            rotation=mw._rotation,
            window=(mw.hist.lo, mw.hist.hi),
            lut=mw.lut_combo.currentData(),
            full_range=mw.hist.full_range,
            filters=mw._filter_flags(),
            temporal_k=mw._temp_sub_k if mw._temp_sub_on else 0,
            fetch_scale=getattr(mw, "_fetch_scale", 1.0),
            show_overlays=mw.overlay_chk.isChecked(),
            annotations=mw._annotations,
            layers=mw._layers,
            overlays=mw.overlays,
            sidecar_offset=mw.sidecar_offset.value(),
            view_key=mw._current_view_key(),
            annot_path=getattr(mw, "_annot_path", ""),
        )

    @classmethod
    def from_satellite(cls, mw, view):
        """Vue secondaire (SatelliteView) : sa source, sa fenetre, ses calques."""
        lo, hi, lut, _cube, _cube_size, full_range, filters = view._rp
        return cls(
            mw,
            view._source,
            engine=False,
            window=(lo, hi),
            lut=lut,
            full_range=full_range,
            filters=filters,
            fetch_scale=view._fetch_scale,
            annotations=view._annots,
            layers=view._layers,
            overlays=view._overlays,
            view_key=mw._view_key_for(view._source),
            annot_path=view._annot_path,
        )

    def close(self):
        for reader in (self.reader, self._ref_reader):
            if reader is not None and reader is not self.source:
                try:
                    reader.close()
                except Exception:
                    pass  # lecteur jetable : rien a recuperer
        self.reader = self.source
        self._ref_reader = None

    # -- geometrie ------------------------------------------------------------
    def native_dims(self):
        """(largeur, hauteur) native des frames, avant rotation."""
        if self._native_dims is None:
            frame = self.reader.get(0) if self.reader is not None else None
            self._native_dims = (frame.shape[1], frame.shape[0]) if frame is not None else (1, 1)
        return self._native_dims

    def native_rect(self, rect):
        """Rectangle (x, y, w, h) de la vue affichee -> pixels natifs."""
        if rect is None:
            return None
        s = self.fetch_scale if self.engine else 1.0
        if s >= 1.0:
            return tuple(int(v) for v in rect)
        return tuple(int(round(v / s)) for v in rect)

    def _rotate(self, frame):
        code = _ROTATIONS.get(self.rotation)
        return cv2.rotate(frame, code) if code is not None and frame is not None else frame

    # -- calques ---------------------------------------------------------------
    def _track_visible(self, track_id):
        for layer in self.layers:
            if layer["kind"] == "ver" and layer["key"] == track_id:
                return layer["visible"]
        # moteur : un track sans calque est visible ; satellite : seuls les
        # calques coches sont dessines (meme regle qu'a l'ecran)
        return self.engine

    def _sidecar_visible(self):
        if self.engine:
            for layer in self.layers:
                if layer["kind"] == "sidecar" and layer["key"] == "sidecar":
                    return layer["visible"]
            return True
        return any(layer["kind"] == "sidecar" and layer["visible"] for layer in self.layers)

    def visible_annotations(self, idx):
        dets = self.annotations.get(idx, [])
        return [d for d in dets if self._track_visible(d[5] if len(d) > 5 else 0)]

    def track_box(self, track_id, idx):
        """Boite (x, y, w, h) native et tournee du track a la frame idx, ou None."""
        match = next(
            (d for d in self.visible_annotations(idx) if (d[5] if len(d) > 5 else 0) == track_id),
            None,
        )
        if match is None:
            return None
        x1, y1, x2, y2 = match[1], match[2], match[3], match[4]
        if self.rotation:
            w0, h0 = self.native_dims()
            x1, y1, x2, y2 = _rotate_box_coords(x1, y1, x2, y2, w0, h0, self.rotation)
        x1, y1, x2, y2 = (int(round(v)) for v in (x1, y1, x2, y2))
        return (min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))

    # -- frames ---------------------------------------------------------------
    def raw(self, idx):
        """Donnees natives (type d'origine), rotation appliquee."""
        frame = self.reader.get(idx) if self.reader is not None else None
        return self._rotate(frame) if frame is not None else None

    def _temporal(self, frame_rot, idx):
        if self._ref_reader is None:
            self._ref_reader = self._mw._reopen_source_like(self.source) or self.reader
        ref = self._ref_reader.get(max(0, idx - self.temporal_k))
        if ref is None:
            return frame_rot
        current = to_intensity(frame_rot).astype(np.float32)
        reference = to_intensity(self._rotate(ref)).astype(np.float32)
        if current.shape != reference.shape:
            return frame_rot
        return current - reference

    def _render(self, frame_rot, idx):
        analysis = self._temporal(frame_rot, idx) if self.temporal_k else frame_rot
        return render_frame(
            analysis, self.lo, self.hi, self.lut, None, 0, self.full_range, self.filters
        )

    def rendered(self, idx):
        """Rendu 8 bits (LUT / contraste / filtres), sans calques."""
        frame = self.raw(idx)
        return self._render(frame, idx) if frame is not None else None

    def viewed(self, idx):
        """Rendu « tel qu'affiche » : contraste + calques .ver/SIDECAR + plugins."""
        frame = self.reader.get(idx) if self.reader is not None else None
        if frame is None:
            return None
        h0, w0 = frame.shape[:2]
        out = self._render(self._rotate(frame), idx)
        if out.ndim == 2:
            out = cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)
        mw = self._mw
        graphs = []
        if self.show_overlays:
            annots = self.visible_annotations(idx)
            if annots:
                out = draw_annotation_boxes(out, annots, w0, h0, rotation=self.rotation, scale=1.0)
            if self.overlays and self._sidecar_visible():
                key = idx + self.sidecar_offset if self.engine else idx
                graphs = list(self.overlays.get(key, []))
            if self.engine:
                active, _multiview = mw._partition_plugin_overlays(mw._plugin_overlays_for(idx))
                graphs += active
        if graphs:
            out = bake_sidecar_overlays(out, graphs)
        hp, wp = out.shape[:2]
        if self.engine:
            patches = mw._plugin_patches_for(idx, (max(1, wp // 4), max(1, hp // 4)))
            if patches:
                out = composite_patches(out, patches)
        loader = getattr(mw, "_plugin_loader", None)
        if loader is not None:
            ov = loader.collect_overlay_image(idx, (wp, hp), view_key=self.view_key)
            if ov is not None:
                out = alpha_over(out, ov)
        return out


class JobRunner(QtCore.QObject):
    """Execute un generateur d'export par tranches de ~40 ms (thread Qt)."""

    finished = QtCore.Signal(bool, str)  # (succes, message d'erreur)

    SLICE_S = 0.04

    def __init__(self, parent=None):
        super().__init__(parent)
        self._job = None
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._step)

    def busy(self):
        return self._job is not None

    def start(self, job):
        if self._job is not None:
            return False
        self._job = job
        self._timer.start()
        return True

    def cancel(self):
        """Arrete l'export : le ``finally`` du generateur ferme les fichiers."""
        job, self._job = self._job, None
        self._timer.stop()
        if job is not None:
            job.close()
            self.finished.emit(False, "Export annulé.")

    def _step(self):
        deadline = time.perf_counter() + self.SLICE_S
        try:
            while time.perf_counter() < deadline:
                next(self._job)
        except StopIteration:
            self._done(True, "")
        except Exception as ex:
            self._done(False, str(ex))

    def _done(self, ok, message):
        self._timer.stop()
        self._job = None
        self.finished.emit(ok, message)


def run_sync(job):
    """Execute un generateur d'export jusqu'au bout (tests, scripts)."""
    for _ in job:
        pass
