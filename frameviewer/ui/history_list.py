# -*- coding: utf-8 -*-
"""Liste des sources ouvertes cette session (historique), avec badge
type/bit-depth + pastille gris/couleur, et glisser vers un bloc du split."""
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

class _ColorModeBadge(QtWidgets.QWidget):
    """Pastille arrondie, tout à droite d'une ligne d'historique : dégradé
    noir→blanc si la source est en niveaux de gris, dégradé rouge→vert→bleu
    si elle est en couleur (RVB, canaux visiblement différents — voir
    MainWindow._probe_src_badge)."""

    def __init__(self, is_color, parent=None):
        super().__init__(parent)
        self._is_color = bool(is_color)
        self.setFixedSize(20, 13)
        self.setToolTip("Couleur (RVB)" if is_color else "Niveaux de gris")

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        grad = QtGui.QLinearGradient(rect.topLeft(), rect.topRight())
        if self._is_color:
            grad.setColorAt(0.0, QtGui.QColor(215, 70, 70))
            grad.setColorAt(0.5, QtGui.QColor(70, 185, 100))
            grad.setColorAt(1.0, QtGui.QColor(80, 130, 230))
        else:
            grad.setColorAt(0.0, QtGui.QColor(28, 28, 30))
            grad.setColorAt(1.0, QtGui.QColor(228, 228, 230))
        p.setPen(QtGui.QPen(QtGui.QColor(90, 90, 100), 1))
        p.setBrush(grad)
        p.drawRoundedRect(rect, 3, 3)


class _HistRow(QtWidgets.QWidget):
    """Une ligne de l'historique : libellé (icône type + bit-depth + nom) à
    gauche, carré gris/couleur (_ColorModeBadge) plaque a droite."""

    def __init__(self, text, is_color, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 1, 4, 1)
        lay.setSpacing(6)
        self.label = QtWidgets.QLabel(text)
        lay.addWidget(self.label, 1)
        lay.addWidget(_ColorModeBadge(is_color))


class HistoryList(QtWidgets.QListWidget):
    """Liste des sources ouvertes, avec glisser vers un bloc du split."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path_resolver = None   # callable(row) -> chemin déposable ou None
        self.setDragEnabled(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragOnly)

    def startDrag(self, actions):
        row = self.currentRow()
        path = None
        if callable(self.path_resolver):
            path = self.path_resolver(row)
        if not path:
            return
        mime = QtCore.QMimeData()
        mime.setUrls([QtCore.QUrl.fromLocalFile(path)])
        mime.setText(path)
        drag = QtGui.QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)
