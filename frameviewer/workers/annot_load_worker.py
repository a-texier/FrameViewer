# -*- coding: utf-8 -*-
"""Load a potentially large folder of per-frame annotations in the background.

Directory loading used to block the GUI because it may require thousands of
small reads. A single merged annotation file remains synchronous because it
only requires one sequential read.
"""
from PySide6 import QtCore

from frameviewer.core.annotation_loader import load_annotations


class AnnotFolderLoadWorker(QtCore.QThread):
    # PySide6 6.8 may silently marshal Signal(dict) as an empty mapping across
    # threads. Signal(object) preserves the original Python object.
    done = QtCore.Signal(object)   # frame_id -> annotation rows
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
