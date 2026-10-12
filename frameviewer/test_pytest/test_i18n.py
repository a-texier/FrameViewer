from __future__ import annotations

from PySide6 import QtWidgets

from frameviewer.ui.csv_mapper_dialog import CsvColumnMapperDialog
from frameviewer.ui.i18n import set_ui_text
from frameviewer.ui.main_window import MainWindow


def test_dynamic_text_keeps_current_state_across_language_round_trip(qapp):
    window = MainWindow()
    label = QtWidgets.QLabel(window)

    window._set_ui_language("en")
    set_ui_text(label, "Actif")
    assert label.text() == "Enabled"

    window._set_ui_language("fr")
    assert label.text() == "Actif"
    window._set_ui_language("en")
    assert label.text() == "Enabled"
    window.close()


def test_csv_mapping_uses_item_identity_in_english(qapp):
    window = MainWindow()
    window._set_ui_language("en")
    dialog = CsvColumnMapperDialog(
        window,
        header=["frame", "x"],
        roles=["frame"],
        optional_roles=["x"],
        sample_rows=[["0", "12"]],
    )
    window._i18n._translate_widget(dialog)

    assert dialog._combos["x"].itemText(0) == "(none)"
    dialog._combos["x"].setCurrentIndex(0)
    assert dialog.result_mapping()["x"] is None
    dialog.close()
    window.close()


def test_help_content_is_built_directly_in_selected_language(qapp, monkeypatch):
    window = MainWindow()
    window._set_ui_language("en")
    captured = {}

    def capture(dialog):
        captured["dialog"] = dialog
        return QtWidgets.QDialog.Rejected

    monkeypatch.setattr(QtWidgets.QDialog, "exec", capture)
    window._show_aide()
    browser = captured["dialog"].findChild(QtWidgets.QTextBrowser)
    text = browser.toPlainText()

    assert "FrameViewer — Complete help" in text
    assert "Mouse — view / canvas" in text
    assert "FrameViewer — Aide complète" not in text
    window.close()


def test_labels_added_in_1_1_have_english_text():
    from frameviewer.core.pipeline import BLEND_MODES
    from frameviewer.ui.i18n import translate_text
    from frameviewer.ui.shortcuts import SHORTCUT_ACTIONS

    labels = [label for _aid, label, _keys, _kind in SHORTCUT_ACTIONS]
    labels += [label for key, label in BLEND_MODES if key in ("wipe", "max", "mult")]
    labels += [
        "Affichage",
        "Ligne d'infos sous chaque vue",
        "Thème :",
        "Sombre",
        "Clair",
        "Raccourcis clavier...",
        "Réinitialiser les réglages...",
        "Calque / Hist / Plugins / Param",
        "Vues",
        "Vues : 2",
        "Lier",
        "Rot.",
        "Suppr. tout",
        "Convertir",
        "Extraire",
        "Convertir (toutes les vues)",
        "Clavier (modifiable dans Paramètres)",
        "Continuer en arrière-plan",
        "Annuler l'export",
        "Un export est en cours. Le laisser finir en arrière-plan ?",
    ]
    missing = [
        text
        for text in labels
        if translate_text(text, "en") == text and text not in ("Maximum (A, B)", "Capture")
    ]
    assert missing == []


def test_conversion_options_have_english_text(qapp):
    import numpy as np

    from frameviewer.core import export_formats as ef
    from frameviewer.ui import export_format_panel as panel_mod
    from frameviewer.ui.i18n import translate_text

    labels = []
    for choices in (
        ef.FORMAT_CHOICES,
        ef.MAPPING_CHOICES,
        ef.ENCODER_CHOICES,
        ef.QUALITY_CHOICES,
        ef.SPEED_CHOICES,
    ):
        labels += [label for _key, label in choices]
    labels += list(panel_mod._FORMAT_NOTES.values()) + list(panel_mod._ENCODER_NOTES.values())
    labels.append(panel_mod._NO_FFMPEG_NOTE)
    for dtype in (np.uint8, np.int16, np.uint16, np.float32):
        labels.append(ef.native_depth_label(dtype))
        labels += [ef.depth_reason(fmt, dtype) for fmt in ("png", "jpg", "mp4")]
    panel = panel_mod.ExportFormatPanel(np.zeros((2, 2), np.uint16))
    for widget in panel.findChildren(panel_mod.QtWidgets.QWidget):
        labels.append(widget.toolTip())
        if isinstance(widget, (panel_mod.QtWidgets.QLabel, panel_mod.QtWidgets.QCheckBox)):
            labels.append(widget.text())
    labels.append(panel.title())
    same_in_english = {
        "8 bits",
        "min",
        "max",
        "H.264 / AVC (ffmpeg)",
        "H.265 / HEVC (ffmpeg)",
        "Standard (CRF 23)",
    }
    missing = sorted(
        {
            text
            for text in labels
            if text
            and text not in same_in_english
            and "canal" not in text
            and translate_text(text, "en") == text
        }
    )
    assert missing == []


def test_conversion_dialog_notes_follow_the_window_language(qapp, tmp_path):
    import cv2
    import numpy as np

    from frameviewer.core.sources import ImageSequenceSource
    from frameviewer.ui.export_dialogs import ClipExtractDialog
    from frameviewer.ui.main_window import MainWindow

    paths = []
    for index in range(2):
        path = tmp_path / f"f{index}.png"
        cv2.imwrite(str(path), np.full((4, 6), 1000 * (index + 1), np.uint16))
        paths.append(str(path))
    window = MainWindow()
    try:
        window._set_ui_language("en")
        window.set_source(ImageSequenceSource(paths, directory=str(tmp_path)))
        dialog = ClipExtractDialog(window, 0, 1, has_in_out=False)
        dialog.show()
        qapp.processEvents()
        panel = dialog._panel
        panel.set_format("jpg")
        assert panel._note.text().startswith("JPEG: lossy")
        assert panel._depth_reason.text() == "JPEG is limited to 8 bits."
        assert "channel(s)" in panel._input.text()
        assert dialog._symlink_note.text().startswith("Symlinks are available only")
        dialog.close()
    finally:
        window.close()
