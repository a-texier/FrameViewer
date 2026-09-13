# -*- coding: utf-8 -*-
"""FrameViewer — point d'entree.

Toute l'application vit dans le package `frameviewer/` (frameviewer.core =
backend, frameviewer.workers = taches de fond, frameviewer.ui = interface
PySide6). Voir docs/architecture.md pour la carte du code complete.
"""
import sys

from PySide6 import QtCore, QtWidgets

from frameviewer.ui.main_window import MainWindow
from frameviewer.ui.theme import DARK_QSS


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("FrameViewer")
    app.setStyleSheet(DARK_QSS)

    def excepthook(t, v, tb):
        import traceback
        msg = "".join(traceback.format_exception(t, v, tb))
        try:
            QtWidgets.QMessageBox.critical(None, "Erreur FrameViewer", msg)
        except Exception:
            pass
        sys.__excepthook__(t, v, tb)

    sys.excepthook = excepthook

    win = MainWindow()
    win.show()

    args = [a for a in sys.argv[1:] if a and not a.startswith("-")]
    if args:
        QtCore.QTimer.singleShot(60, lambda: win.open_paths(args))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
