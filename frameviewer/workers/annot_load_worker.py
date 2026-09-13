# -*- coding: utf-8 -*-
"""Charge un dossier d'annotations (YOLO -- potentiellement des milliers de
petits .txt, un par frame) en tache de fond. Lire ca directement dans le
thread GUI est ce qui gelait l'interface au drop d'un gros dossier -- un
seul fichier .ver/.txt fusionne reste charge de facon synchrone (cf.
MainWindow._load_dropped_annotations), une seule lecture sequentielle n'a
pas besoin de tache de fond."""
from PySide6 import QtCore

from frameviewer.core.annotation_loader import load_annotations


class AnnotFolderLoadWorker(QtCore.QThread):
    # Signal(object), PAS Signal(dict) : le marshaling inter-thread de
    # Signal(dict) sous PySide6 6.8 corrompt silencieusement le contenu (le
    # slot recoit {} alors que run() a bien construit un dict complet,
    # verifie empiriquement) -- Signal(object) transporte le Python object
    # sans essayer de le convertir.
    done = QtCore.Signal(object)   # dict : frame_id -> [(cls,x1,y1,x2,y2,track_id,labels), ...]
    failed = QtCore.Signal(str)

    def __init__(self, path, img_w, img_h, default_track=0):
        super().__init__()
        self._path = path
        self._img_w = img_w
        self._img_h = img_h
        self._default_track = default_track
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            annots = load_annotations(
                self._path, self._img_w, self._img_h,
                default_track=self._default_track)
        except Exception as ex:
            if not self._stop:
                self.failed.emit(str(ex))
            return
        if not self._stop:
            self.done.emit(annots)
