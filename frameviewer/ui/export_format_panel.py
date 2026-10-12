# -*- coding: utf-8 -*-
"""Choix du format de sortie, puis des options de ce format.

Les options affichees dependent de la donnee d'entree (voir
``core.export_formats``) : profondeur 8 bits ou d'origine, regle de passage
en 8 bits, rendu « tel qu'affiché », qualite JPEG, encodeur et qualite MP4.
"""

from PySide6 import QtCore, QtWidgets

from frameviewer.core.export_formats import (
    ENCODER_CHOICES,
    FORMAT_CHOICES,
    QUALITY_CHOICES,
    SPEED_CHOICES,
    ExportSpec,
    SpecializedWriter,
    depth_reason,
    find_ffmpeg,
    mapping_choices,
    native_depth_allowed,
    native_depth_label,
)
from frameviewer.ui.i18n import set_ui_text

_FORMAT_NOTES = {
    "png": "PNG : sans perte, 8 ou 16 bits par canal.",
    "tiff": "TIFF : sans perte, garde la profondeur d'origine (flottants compris).",
    "jpg": "JPEG : avec perte, 8 bits. Qualité basse = fichier plus léger.",
    "specialized": "SPECIALIZED : un seul fichier contenant toute la séquence.",
    "copy": "Copie des fichiers images originaux, sans aucune modification.",
}

_ENCODER_NOTES = {
    "mp4v": "MPEG-4 (OpenCV) : qualité fixe, non réglable.",
    "h264": "H.264 via ffmpeg : le plus compatible. CRF bas = meilleure qualité.",
    "h265": "H.265 via ffmpeg : plus compact que H.264, lecteurs récents.",
    "copy": "Coupe la vidéo source sans la ré-encoder : qualité et cadence "
    "d'origine, sans calques ni contraste.",
}

_NO_FFMPEG_NOTE = "ffmpeg introuvable : installez-le pour H.264 / H.265 et le réglage de qualité."


class ExportFormatPanel(QtWidgets.QGroupBox):
    """Format puis options. ``spec()`` renvoie le choix courant.

    sample : une frame brute de l'entree (son type decide des options), ou
    None quand le contenu est impose (composition multi-vues, deja rendue).
    """

    changed = QtCore.Signal()

    def __init__(
        self,
        sample,
        *,
        fps=25.0,
        window=(0.0, 255.0),
        allow_copy=False,
        allow_stream_copy=False,
        formats=None,
        parent=None,
    ):
        super().__init__("Format et options", parent)
        self._content_fixed = sample is None
        self._dtype = sample.dtype if sample is not None else None
        form = QtWidgets.QFormLayout(self)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        self._form = form

        self._fmt = QtWidgets.QComboBox()
        for key, label in FORMAT_CHOICES:
            if formats is not None and key not in formats:
                continue
            if key == "specialized" and SpecializedWriter is None:
                continue
            if key == "copy" and not allow_copy:
                continue
            self._fmt.addItem(label, key)
        form.addRow("Format :", self._fmt)

        self._input = QtWidgets.QLabel("")
        self._input.setStyleSheet("color:#7fb8ff; font-size:11px;")
        if sample is not None:
            channels = sample.shape[2] if sample.ndim == 3 else 1
            dims = f"{sample.shape[1]}x{sample.shape[0]}"
            set_ui_text(self._input, f"{sample.dtype}, {channels} canal(aux), {dims}")
        form.addRow("Entrée :", self._input)

        self._depth = QtWidgets.QComboBox()
        self._depth.addItem("8 bits", "8")
        if self._dtype is not None and self._dtype.itemsize > 1:
            self._depth.addItem(native_depth_label(self._dtype), "native")
        self._depth_reason = QtWidgets.QLabel("")
        self._depth_reason.setWordWrap(True)
        self._depth_reason.setStyleSheet("color:#999; font-size:11px;")
        depth_box = QtWidgets.QWidget()
        depth_lay = QtWidgets.QVBoxLayout(depth_box)
        depth_lay.setContentsMargins(0, 0, 0, 0)
        depth_lay.addWidget(self._depth)
        depth_lay.addWidget(self._depth_reason)
        form.addRow("Profondeur :", depth_box)
        self._depth_box = depth_box

        self._as_viewed = QtWidgets.QCheckBox("Vue telle qu'affichée (contraste, LUT, calques)")
        self._as_viewed.setToolTip(
            "Écrit le rendu de la vue : contraste, LUT et filtres courants,\n"
            "boîtes .ver et calques visibles. Disponible en sortie 8 bits."
        )
        form.addRow(self._as_viewed)

        self._mapping = QtWidgets.QComboBox()
        for key, label in mapping_choices(self._dtype or "uint16"):
            self._mapping.addItem(label, key)
        self._mapping.setToolTip(
            "+/- 3 sigma : moyenne +/- 3 écarts-types de chaque frame (comme le mode Auto).\n"
            "Min / max : du plus petit au plus grand pixel de chaque frame.\n"
            "Pleine plage : toute la plage du type (0-65535 en 16 bits), sans étirement.\n"
            "Personnalisée : la même plage fixe pour toutes les frames."
        )
        form.addRow("Passage en 8 bits :", self._mapping)

        lo, hi = float(window[0]), float(window[1])
        self._lo = self._range_spin(lo)
        self._hi = self._range_spin(hi)
        range_box = QtWidgets.QWidget()
        range_lay = QtWidgets.QHBoxLayout(range_box)
        range_lay.setContentsMargins(0, 0, 0, 0)
        range_lay.addWidget(QtWidgets.QLabel("min"))
        range_lay.addWidget(self._lo, 1)
        range_lay.addWidget(QtWidgets.QLabel("max"))
        range_lay.addWidget(self._hi, 1)
        form.addRow("Plage :", range_box)
        self._range_box = range_box

        self._jpeg_quality = QtWidgets.QSpinBox()
        self._jpeg_quality.setRange(1, 100)
        self._jpeg_quality.setValue(95)
        self._jpeg_quality.setToolTip("100 = meilleure qualité, fichier plus gros.")
        form.addRow("Qualité JPEG :", self._jpeg_quality)

        has_ff = find_ffmpeg() is not None
        self._encoder = QtWidgets.QComboBox()
        for key, label in ENCODER_CHOICES:
            if key in ("h264", "h265") and not has_ff:
                continue
            if key == "copy" and not allow_stream_copy:
                continue
            self._encoder.addItem(label, key)
        form.addRow("Encodeur :", self._encoder)
        self._has_ff = has_ff

        self._crf = QtWidgets.QComboBox()
        for crf, label in QUALITY_CHOICES:
            self._crf.addItem(label, crf)
        self._crf.setCurrentIndex(self._crf.findData(18))
        self._crf.setToolTip("CRF : 0 = sans perte, 18 = visuellement sans perte, 28 = compact.")
        form.addRow("Qualité :", self._crf)

        self._speed = QtWidgets.QComboBox()
        for key, label in SPEED_CHOICES:
            self._speed.addItem(label, key)
        self._speed.setCurrentIndex(self._speed.findData("medium"))
        form.addRow("Vitesse d'encodage :", self._speed)

        self._fps = QtWidgets.QDoubleSpinBox()
        self._fps.setRange(0.1, 480.0)
        self._fps.setDecimals(2)
        self._fps.setSuffix(" fps")
        self._fps.setValue(float(fps or 25.0))
        form.addRow("Cadence :", self._fps)

        self._note = QtWidgets.QLabel("")
        self._note.setWordWrap(True)
        self._note.setStyleSheet("color:#999; font-size:11px;")
        form.addRow(self._note)

        for combo in (self._fmt, self._depth, self._mapping, self._encoder):
            combo.currentIndexChanged.connect(self._refresh)
        self._as_viewed.toggled.connect(self._refresh)
        self._refresh()

    @staticmethod
    def _range_spin(value):
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(-1e12, 1e12)
        spin.setDecimals(3)
        spin.setValue(value)
        return spin

    # -- etat ------------------------------------------------------------------
    def fmt(self):
        return self._fmt.currentData()

    def offers(self, key):
        return self._fmt.findData(key) >= 0

    def set_format(self, key):
        index = self._fmt.findData(key)
        if index >= 0:
            self._fmt.setCurrentIndex(index)

    def spec(self):
        fmt = self.fmt()
        spec = ExportSpec(
            fmt=fmt,
            depth=self._depth.currentData() or "8",
            mapping=self._mapping.currentData() or "sigma",
            custom_range=(self._lo.value(), self._hi.value()),
            as_viewed=self._content_fixed or self._as_viewed.isChecked(),
            jpeg_quality=self._jpeg_quality.value(),
            encoder=self._encoder.currentData() or "mp4v",
            crf=int(self._crf.currentData()),
            speed=self._speed.currentData() or "medium",
            fps=self._fps.value(),
        )
        if fmt in ("jpg", "mp4"):
            spec.depth = "8"
        return spec

    def _row(self, field, visible):
        self._form.setRowVisible(field, bool(visible))

    def _refresh(self, *_args):
        fmt = self.fmt()
        dtype = self._dtype
        is_video = fmt == "mp4"
        encoder = self._encoder.currentData()
        stream_copy = is_video and encoder == "copy"
        has_content = fmt != "copy" and not stream_copy and not self._content_fixed

        native_ok = dtype is not None and native_depth_allowed(fmt, dtype)
        if not native_ok:
            self._depth.setCurrentIndex(0)
        self._depth.setEnabled(native_ok)
        reason = depth_reason(fmt, dtype) if dtype is not None else ""
        set_ui_text(self._depth_reason, reason)
        self._depth_reason.setVisible(bool(reason))

        eight = fmt in ("jpg", "mp4") or self._depth.currentData() == "8"
        as_viewed = self._as_viewed.isChecked()
        needs_mapping = dtype is not None and dtype.itemsize > 1
        mapping_on = has_content and eight and needs_mapping and not as_viewed

        self._row(self._input, has_content)
        self._row(self._depth_box, has_content)
        self._row(self._as_viewed, has_content and eight)
        self._row(self._mapping, mapping_on)
        self._row(self._range_box, mapping_on and self._mapping.currentData() == "custom")
        self._row(self._jpeg_quality, fmt == "jpg")
        self._row(self._encoder, is_video)
        ffmpeg_encoder = is_video and encoder in ("h264", "h265")
        self._row(self._crf, ffmpeg_encoder)
        self._row(self._speed, ffmpeg_encoder)
        self._row(self._fps, is_video and not stream_copy)

        if is_video:
            note = _ENCODER_NOTES.get(encoder, "")
            if encoder == "mp4v" and not self._has_ff:
                note = _NO_FFMPEG_NOTE
        else:
            note = _FORMAT_NOTES.get(fmt, "")
        set_ui_text(self._note, note)
        self.changed.emit()
