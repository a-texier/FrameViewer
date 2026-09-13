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
