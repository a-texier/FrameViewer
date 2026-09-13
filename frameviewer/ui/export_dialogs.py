# -*- coding: utf-8 -*-
"""Dialogues de conversion/extraction sur toute une sequence :
ConvertDialog (ancien menu "Convertir" bas de barre, PNG8/PNG16/MP4/SPECIALIZED),
ClipExtractDialog (bouton "Convertir" par vue + extraction IN->OUT),
RoiConvertDialog (export de la ROI, fixe ou suivie, sur toute la sequence).

Ces dialogues recoivent l'instance MainWindow (`mw`) en parametre et
appellent ses methodes/attributs (mw.source, mw._raw, mw._apply_rotation,
mw._export_frame_as_viewed, ...) -- aucun import de MainWindow necessaire
ici, `mw` est utilise en duck-typing a l'execution."""
import os
import shutil

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from frameviewer.core.feature_registry import operation_for_kind
from frameviewer.core.sources import describe_source

SpecializedWriter = operation_for_kind("sequence_format", "writer")
type_img_for_dtype = operation_for_kind("sequence_format", "type_for_dtype")
write_overlay_sidecar = operation_for_kind("overlay_format", "write")


def _apply_symlink_state(chk, note, is_copy):
    """Etat de l'option symlink selon le format d'export. Le lien n'a de sens
    qu'en « Copie des fichiers originaux » (entree = sortie, un fichier source
    unique a lier). Sinon on grise ET on BARRE le libelle, avec une explication
    juste en dessous."""
    chk.setEnabled(is_copy)
    f = chk.font()
    f.setStrikeOut(not is_copy)
    chk.setFont(f)
    if is_copy:
        note.setText("")
        return
    chk.setChecked(False)
    note.setText(
        "Symlink possible uniquement avec « Copie des fichiers originaux » "
        "(séquence extraite telle quelle, entrée = sortie). Les autres formats "
        "ré-encodent chaque image : il n'existe plus de fichier source unique à "
        "lier, donc aucun lien n'est possible.")

class ConvertDialog(QtWidgets.QDialog):
    """Configure UNE conversion (la cible est deja choisie dans le menu deroulant
    'Convertir'). Demande le chemin de sortie, l'echantillonnage, la cadence MP4."""

    _LABELS = {
        "png8": "Dossier d'images PNG (8 bits - LUT / contraste / filtres actuels)",
        "png16": "Dossier d'images PNG (16 bits - donnees brutes)",
        "mp4": "Video MP4 (LUT / contraste / filtres actuels)",
        "specialized": "Fichier SPECIALIZED (intensite brute)",
    }

    def __init__(self, parent, base_dir, stem, mode):
        super().__init__(parent)
        self.mode = mode
        self._base_dir = base_dir
        self._stem = stem
        self.setWindowTitle("Convertir")
        self.setMinimumWidth(520)

        self.dir_edit = QtWidgets.QLineEdit()
        browse = QtWidgets.QPushButton("Parcourir...")
        browse.clicked.connect(self._browse)
        drow = QtWidgets.QHBoxLayout()
        drow.addWidget(self.dir_edit, 1)
        drow.addWidget(browse)

        self.step = QtWidgets.QSpinBox()
        self.step.setRange(1, 1000000)
        self.step.setValue(1)
        self.step.setPrefix("1 image sur ")

        self.fps_spin = QtWidgets.QDoubleSpinBox()
        self.fps_spin.setRange(0.1, 480.0)
        self.fps_spin.setValue(25.0)
        self.fps_spin.setDecimals(2)
        self.fps_spin.setSuffix(" fps")
        self.fps_spin.setEnabled(mode == "mp4")

        form = QtWidgets.QFormLayout()
        form.addRow("Cible:", QtWidgets.QLabel(self._LABELS.get(mode, mode)))
        form.addRow("Sortie:", drow)
        form.addRow("Echantillonnage:", self.step)
        form.addRow("Cadence MP4:", self.fps_spin)
        if mode == "specialized":
            form.setRowVisible(2, False)   # Echantillonnage
            form.setRowVisible(3, False)   # Cadence MP4

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(btns)
        self.dir_edit.setText(self._default_out())

    def _default_out(self):
        m = self.mode
        if m == "mp4":
            return os.path.join(self._base_dir, self._stem + "_export.mp4")
        if m == "specialized":
            return os.path.join(self._base_dir, self._stem + "_export.specialized")
        if m == "png16":
            return os.path.join(self._base_dir, self._stem + "_png16")
        return os.path.join(self._base_dir, self._stem + "_png")

    def _browse(self):
        if self.mode in ("mp4", "specialized"):
            filt = "Video MP4 (*.mp4)" if self.mode == "mp4" else "Sequence SPECIALIZED (*.specialized)"
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Fichier de sortie", self.dir_edit.text(), filt)
            if path:
                self.dir_edit.setText(path)
        else:
            d = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Dossier de sortie", self.dir_edit.text())
            if d:
                self.dir_edit.setText(d)

    def values(self):
        return (self.dir_edit.text().strip(), self.mode,
                self.step.value(), self.fps_spin.value())


# --------------------- panneau de selection (ROI) -------------------------



class ClipExtractDialog(QtWidgets.QDialog):
    """Dialogue d'extraction d'un clip (frames IN->OUT sans degradation)."""

    def __init__(self, mw, start, end, has_in_out=True, crop_rect=None):
        super().__init__(mw)
        self._mw = mw
        self._start = start
        self._end = end
        # crop ROI optionnel (x, y, w, h) en coords image affichee (apres
        # rotation, meme repere que process()) : n'exporte que cette sous-zone.
        self._crop = tuple(crop_rect) if crop_rect else None
        # has_in_out=False : dialogue ouvert depuis le bouton "Convertir" d'une
        # vue (toute la sequence, start/end ne sont PAS une vraie plage IN/OUT
        # choisie par l'utilisateur) -> pas de case "export calques IN->OUT".
        self._has_in_out = has_in_out
        n = end - start + 1
        self.setWindowTitle(f"Extraire clip  ─  {n} frames")
        self.setMinimumWidth(440)

        vbox = QtWidgets.QVBoxLayout(self)
        src_name = getattr(mw.source, 'name', '?')
        fps_src = float(getattr(mw.source, 'fps', mw.fps_spin.value()) or 25.0)
        dur = n / max(fps_src, 0.001)
        info = QtWidgets.QLabel(
            f"<b>{src_name}</b><br>"
            f"Frames {start} → {end}  ({n} frames, {dur:.2f} s @ {fps_src:.1f} fps)")
        info.setWordWrap(True)
        vbox.addWidget(info)
        if self._crop is not None:
            cx, cy, cw, ch = self._crop
            crop_lbl = QtWidgets.QLabel(
                f"Crop ROI actif : {cw}×{ch} px @ ({cx}, {cy}) — "
                "seule cette sous-zone est exportée.")
            crop_lbl.setStyleSheet("color:#3fd07a; font-size:11px; font-weight:bold;")
            crop_lbl.setWordWrap(True)
            vbox.addWidget(crop_lbl)
        src_desc = QtWidgets.QLabel(describe_source(mw.source))
        src_desc.setStyleSheet("color:#7fb8ff; font-size:11px;")
        src_desc.setWordWrap(True)
        vbox.addWidget(src_desc)

        grp = QtWidgets.QGroupBox("Format de sortie")
        gl = QtWidgets.QVBoxLayout(grp)
        self._fmt = QtWidgets.QComboBox()
        has_ff = bool(shutil.which("ffmpeg") or shutil.which("ffmpeg.exe"))
        is_vid = hasattr(mw.source, 'fps') and hasattr(mw.source, 'path')
        # MP4 (cv2) : disponible pour N'IMPORTE QUELLE source (vidéo, séquence
        # d'images, SPECIALIZED...) puisqu'il repasse par process()/source.get(idx) —
        # c'est aussi le seul format MP4 capable de graver calques/contraste.
        self._fmt.addItem("MP4  (cv2 — calques/contraste possibles)", "mp4_cv2")
        # avec un crop ROI, ffmpeg stream-copy et la copie de fichiers originaux
        # n'ont pas de sens (ils ne peuvent pas rogner) : on les masque.
        if is_vid and has_ff and self._crop is None:
            self._fmt.addItem("MP4 sans re-compression  (ffmpeg, vidéo source uniquement)", "mp4_ff")
        is_seq = hasattr(mw.source, 'paths')
        if is_seq and self._crop is None:
            self._fmt.addItem("Copie des fichiers originaux  (sans dégradation)", "copy")
        self._fmt.addItem("Dossier PNG  (frames rendues 8 bits)", "png_folder")
        self._fmt.addItem("Dossier TIFF  (données brutes, 16 bits conservés)", "tiff_raw")
        self._fmt.addItem("Dossier PNG 16 bits  (données brutes)", "png16_raw")
        if SpecializedWriter is not None:
            self._fmt.addItem("Fichier SPECIALIZED  (.specialized — données brutes)", "specialized")
        gl.addWidget(self._fmt)
        self._note = QtWidgets.QLabel("")
        self._note.setWordWrap(True)
        self._note.setStyleSheet("color:#999; font-size:11px;")
        gl.addWidget(self._note)
        self._fmt.currentIndexChanged.connect(self._upd_note)
        self._upd_note()
        vbox.addWidget(grp)

        # cadence de sortie -- uniquement pertinente pour un MP4 re-encode
        # (mp4_ff = "sans re-compression" garde volontairement la cadence
        # source, c'est tout l'interet de ce mode).
        fps_row = QtWidgets.QHBoxLayout()
        fps_row.addWidget(QtWidgets.QLabel("Cadence MP4 :"))
        self.fps_spin = QtWidgets.QDoubleSpinBox()
        self.fps_spin.setRange(0.1, 480.0)
        self.fps_spin.setValue(fps_src)
        self.fps_spin.setDecimals(2)
        self.fps_spin.setSuffix(" fps")
        fps_row.addWidget(self.fps_spin)
        fps_row.addStretch(1)
        self._fps_row_w = QtWidgets.QWidget()
        self._fps_row_w.setLayout(fps_row)
        self._fmt.currentIndexChanged.connect(self._upd_fps_visible)
        self._upd_fps_visible()
        vbox.addWidget(self._fps_row_w)

        self._export_annot_chk = QtWidgets.QCheckBox(
            "Exporter aussi les calques SIDECAR + annotations (IN→OUT)")
        self._export_annot_chk.setChecked(True)
        self._export_annot_chk.setToolTip(
            "Écrit à côté du clip, ré-indexés sur la plage IN→OUT :\n"
            "• les calques SIDECAR (overlays.sidecar)\n"
            "• les annotations .ver / YOLO")
        has_side = bool(getattr(mw, "_annotations", {})) or bool(getattr(mw, "overlays", {}))
        if not has_side or not self._has_in_out:
            self._export_annot_chk.setVisible(False)
            if not self._has_in_out:
                # pas de vraie plage IN->OUT ici : desactive aussi la logique
                # d'export (pas seulement l'affichage de la case).
                self._export_annot_chk.setChecked(False)
        vbox.addWidget(self._export_annot_chk)

        # export « tel qu'affiché » : calques (.ver + SIDECAR) + contraste/LUT
        # gravés directement dans les frames (utile pour dossier PNG / SPECIALIZED).
        self._as_viewed_chk = QtWidgets.QCheckBox(
            "Exporter la vue telle qu'affichée (calques + contraste gravés)")
        self._as_viewed_chk.setToolTip(
            "Grave directement dans chaque frame exportée :\n"
            "• le contraste/LUT/filtres courants\n"
            "• les boîtes .ver visibles\n"
            "• le calque SIDECAR (si coché)\n"
            "S'applique aux formats Dossier PNG, Fichier SPECIALIZED et MP4 re-encodé.")
        self._as_viewed_chk.setChecked(False)
        self._fmt.currentIndexChanged.connect(self._upd_as_viewed_visible)
        self._upd_as_viewed_visible()
        vbox.addWidget(self._as_viewed_chk)

        # option symlink : uniquement pour une sequence d'images extraite dans
        # son format d'origine (format "copy"), sinon grise + explication rouge.
        self._symlink_chk = QtWidgets.QCheckBox(
            "Créer des liens au lieu de copier (symlink, sans duplication)")
        self._symlink_chk.setToolTip(
            "Pour une séquence d'images extraite dans son format d'origine :\n"
            "crée un lien vers chaque fichier source au lieu de le copier.\n"
            "Essaie symlink, puis lien physique, puis copie en dernier recours.")
        vbox.addWidget(self._symlink_chk)
        self._symlink_note = QtWidgets.QLabel("")
        self._symlink_note.setWordWrap(True)
        self._symlink_note.setStyleSheet("color:#e05555; font-size:11px;")
        vbox.addWidget(self._symlink_note)
        self._fmt.currentIndexChanged.connect(self._upd_symlink_state)
        self._upd_symlink_state()

        self._prog = QtWidgets.QProgressBar()
        self._prog.setVisible(False)
        vbox.addWidget(self._prog)

        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        vbox.addWidget(self._status)

        row = QtWidgets.QHBoxLayout()
        self._go = QtWidgets.QPushButton("Extraire...")
        self._go.clicked.connect(self._extract)
        row.addWidget(self._go)
        row.addStretch(1)
        btn_close = QtWidgets.QPushButton("Fermer")
        btn_close.clicked.connect(self.accept)
        row.addWidget(btn_close)
        vbox.addLayout(row)

    def _apply_crop(self, img):
        """Rogne `img` (deja dans le repere affiche/rotation) au crop ROI, si actif."""
        if self._crop is None or img is None:
            return img
        H, W = img.shape[:2]
        x, y, w, h = self._crop
        x = max(0, min(int(x), W - 1)); y = max(0, min(int(y), H - 1))
        x2 = min(W, x + int(w)); y2 = min(H, y + int(h))
        if x2 <= x or y2 <= y:
            return img
        return img[y:y2, x:x2]

    def _upd_symlink_state(self):
        _apply_symlink_state(self._symlink_chk, self._symlink_note,
                             self._fmt.currentData() == "copy")

    def _upd_as_viewed_visible(self):
        self._as_viewed_chk.setVisible(
            self._fmt.currentData() in ("png_folder", "specialized", "mp4_cv2"))

    def _upd_fps_visible(self):
        self._fps_row_w.setVisible(self._fmt.currentData() == "mp4_cv2")

    def _upd_note(self):
        notes = {
            "mp4_ff":     "Coupe précise sans re-encodage via ffmpeg. Qualité d'origine préservée.",
            "mp4_cv2":    "Re-encodage MPEG-4 par cv2. Légère perte acceptable.",
            "copy":       "Copie des fichiers images originaux (sans aucune modification).",
            "png_folder": "Chaque frame rendue (LUT, filtres, rotation) sauvegardée en PNG.",
            "tiff_raw":   "Données brutes lues depuis la source, 16 bits si disponible.",
            "png16_raw":  "Données brutes lues depuis la source, en PNG 16 bits si disponible.",
            "specialized":        "Écrit un .specialized (données brutes) depuis n'importe quelle source "
                          "(SPECIALIZED, MP4, dossier PNG).",
        }
        self._note.setText(notes.get(self._fmt.currentData(), ""))

    def _set_ui(self, running):
        self._go.setEnabled(not running)
        self._prog.setVisible(running)

    def _extract(self):
        fmt = self._fmt.currentData()
        start, end = self._start, self._end
        mw = self._mw
        as_viewed = self._as_viewed_chk.isChecked() and self._as_viewed_chk.isVisible()

        if fmt in ("mp4_ff", "mp4_cv2"):
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Enregistrer le clip...",
                f"clip_{start:05d}-{end:05d}.mp4", "Vidéo MP4 (*.mp4)")
            if not path:
                return
            if fmt == "mp4_ff":
                ok = self._ffmpeg(path, start, end)
                if not ok:
                    self._status.setText("ffmpeg échoué — basculement cv2.")
                    self._cv2_mp4(path, start, end, as_viewed)
            else:
                self._cv2_mp4(path, start, end, as_viewed)
            if self._export_annot_chk.isChecked():
                self._export_sidecars(path, start, end, False)

        elif fmt == "copy":
            folder = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Dossier de destination...")
            if not folder:
                return
            self._file_copy(folder, start, end)
            if self._export_annot_chk.isChecked():
                self._export_sidecars(folder, start, end, True)

        elif fmt == "png_folder":
            folder = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Dossier de destination pour les PNG...")
            if not folder:
                return
            self._rendered_folder(folder, start, end, ".png", as_viewed)
            if self._export_annot_chk.isChecked():
                self._export_sidecars(folder, start, end, True)

        elif fmt == "tiff_raw":
            folder = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Dossier de destination pour les TIFF...")
            if not folder:
                return
            self._raw_folder(folder, start, end, ".tiff")
            if self._export_annot_chk.isChecked():
                self._export_sidecars(folder, start, end, True)

        elif fmt == "png16_raw":
            folder = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Dossier de destination pour les PNG 16 bits...")
            if not folder:
                return
            self._raw_folder(folder, start, end, ".png", png16=True)
            if self._export_annot_chk.isChecked():
                self._export_sidecars(folder, start, end, True)

        elif fmt == "specialized":
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Enregistrer le clip SPECIALIZED...",
                f"clip_{start:05d}-{end:05d}.specialized", "SPECIALIZED (*.specialized)")
            if not path:
                return
            self._specialized_export(path, start, end, as_viewed)
            if self._export_annot_chk.isChecked():
                self._export_sidecars(path, start, end, False)

    def _export_annot_slice(self, dest, start, end, is_folder):
        """Ecrit les annotations IN->OUT dans un fichier .ver ou YOLO .txt.
        Les frame IDs sont re-indices a partir de 0 (0-based dans le clip).
        """
        mw = self._mw
        annots = getattr(mw, "_annotations", {})
        if not annots:
            return
        annot_src = getattr(mw, "_annot_path", "")
        is_ver = os.path.splitext(annot_src)[1].lower() == ".ver"

        # Dimensions courantes pour normalisation YOLO
        if mw._raw is not None:
            h_img, w_img = mw._raw.shape[:2]
        else:
            h_img = w_img = 1

        lines = []
        for frame_idx in range(start, end + 1):
            clip_idx = frame_idx - start  # 0-based dans le clip
            for det in annots.get(frame_idx, []):
                cls = det[0]
                x1, y1, x2, y2 = det[1], det[2], det[3], det[4]
                track_id = det[5] if len(det) > 5 else 0
                labels = det[6] if len(det) > 6 else ()
                if is_ver:
                    # Format .ver : frame(1-based) vis x1 y1 x2 y2 track cls [sub [subsub]]
                    parts = [
                        f"{clip_idx + 1:.6e}", "1.000000e+00",
                        f"{x1:.6e}", f"{y1:.6e}",
                        f"{x2:.6e}", f"{y2:.6e}",
                        f"{track_id:.6e}",
                    ]
                    parts.extend(labels if labels else ["unknown"])
                    lines.append("\t".join(parts))
                else:
                    # Format YOLO merge : frame cls cx cy w h (normalise)
                    cx = ((x1 + x2) / 2) / max(w_img, 1)
                    cy = ((y1 + y2) / 2) / max(h_img, 1)
                    wn = abs(x2 - x1) / max(w_img, 1)
                    hn = abs(y2 - y1) / max(h_img, 1)
                    lines.append(
                        f"{clip_idx} {cls} {cx:.6f} {cy:.6f} {wn:.6f} {hn:.6f}")

        ext_out = ".ver" if is_ver else ".txt"
        if is_folder:
            out_file = os.path.join(dest, f"annotations{ext_out}")
        else:
            out_file = os.path.splitext(dest)[0] + ext_out

        try:
            with open(out_file, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
            cur = self._status.text()
            self._status.setText(cur + f"\nAnnotations ({len(lines)} det.) → {out_file}")
        except Exception as ex:
            self._status.setText(self._status.text() + f"\nErreur annot : {ex}")

    def _export_sidecars(self, dest, start, end, is_folder):
        """Exporte, à côté du clip et ré-indexés sur IN→OUT : les calques SIDECAR,
        les annotations .ver/YOLO, PUIS les données déposées sur les plugins
        actifs (CSV/TSV, ré-indexées si une colonne de frame est détectée)."""
        self._export_sidecar_slice(dest, start, end, is_folder)
        self._export_annot_slice(dest, start, end, is_folder)
        self._export_plugin_data_slice(dest, start, end, is_folder)

    # colonnes de frame reconnues dans un CSV/TSV de plugin (minuscules)
    _FRAME_COLS = {
        "frame", "frames", "numimage", "num_image", "numimg", "frame_id",
        "frameid", "frame_index", "frameindex", "numframe", "num_frame",
        "idframe", "id_frame", "image", "img", "n", "num", "numero",
    }

    def _export_plugin_data_slice(self, dest, start, end, is_folder):
        """Découpe les fichiers déposés sur les plugins ACTIFS (entrées de la vue
        courante) sur la plage IN→OUT. Best-effort : si une colonne de frame est
        reconnue, les lignes hors plage sont retirées et la frame ré-indexée dans
        le clip ; sinon le fichier est copié tel quel (avec un avertissement)."""
        mw = self._mw
        loader = getattr(mw, "_plugin_loader", None)
        if loader is None:
            return
        view_key = mw._current_view_key() if hasattr(mw, "_current_view_key") else None
        seen = set()
        for lp in loader.enabled_plugins():
            try:
                contracts = mw._visible_contracts(lp.plugin_id, view_key)
            except Exception:
                continue
            # _visible_contracts -> [{nom_entree: chemin}, ...]
            for files in contracts:
                for var, path in (files or {}).items():
                    if not path or path in seen or not os.path.isfile(path):
                        continue
                    seen.add(path)
                    try:
                        self._slice_one_plugin_file(
                            path, dest, start, end, is_folder, lp.plugin_id, var)
                    except Exception as ex:
                        self._status.setText(
                            self._status.text() + f"\nErreur données plugin ({var}) : {ex}")

    def _slice_one_plugin_file(self, path, dest, start, end, is_folder, pid, var):
        ext = os.path.splitext(path)[1].lower()
        base = os.path.basename(path)
        if is_folder:
            out_file = os.path.join(dest, f"{pid}_{var}_{base}")
        else:
            out_file = os.path.splitext(dest)[0] + f"_{pid}_{var}{ext}"

        if ext not in (".csv", ".tsv", ".txt", ".dat"):
            shutil.copy2(path, out_file)   # binaire/inconnu : copie verbatim
            self._status.setText(self._status.text()
                                 + f"\nDonnées plugin copiées (non tabulaire) → {out_file}")
            return

        with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
            raw = fh.read()
        lines = raw.splitlines()
        if not lines:
            return
        # delimiteur : ; puis \t puis ,
        header = lines[0]
        delim = ";" if ";" in header else ("\t" if "\t" in header else ",")
        cols = [c.strip().strip('"').lower() for c in header.split(delim)]
        fcol = next((i for i, c in enumerate(cols) if c in self._FRAME_COLS), -1)
        if fcol < 0:
            shutil.copy2(path, out_file)
            self._status.setText(
                self._status.text()
                + f"\nDonnées plugin ({var}) copiées SANS ré-indexation "
                  "(aucune colonne de frame reconnue) → " + out_file)
            return

        def _parse(cell):
            try:
                return float(cell.strip().strip('"'))
            except (ValueError, AttributeError):
                return None

        # base 0 vs 1 : si une valeur vaut 0 -> 0-based, sinon on suppose 1-based
        vals = []
        for ln in lines[1:]:
            if not ln.strip():
                continue
            parts = ln.split(delim)
            if fcol < len(parts):
                v = _parse(parts[fcol])
                if v is not None:
                    vals.append(v)
        onebase = 0 if any(abs(v) < 1e-9 for v in vals) else 1

        out_lines = [header]
        kept = 0
        for ln in lines[1:]:
            if not ln.strip():
                continue
            parts = ln.split(delim)
            if fcol >= len(parts):
                continue
            v = _parse(parts[fcol])
            if v is None:
                continue
            app_frame = v - onebase
            if not (start <= app_frame <= end + 0.0):
                continue
            new_v = v - start
            # conserve le format entier si la valeur d'origine etait entiere
            parts[fcol] = (str(int(round(new_v))) if abs(v - round(v)) < 1e-9
                           else repr(new_v))
            out_lines.append(delim.join(parts))
            kept += 1

        with open(out_file, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(out_lines))
        self._status.setText(
            self._status.text()
            + f"\nDonnées plugin ({var}) : {kept} ligne(s) ré-indexée(s) "
              f"(base {onebase}) → {out_file}")

    def _export_sidecar_slice(self, dest, start, end, is_folder):
        """Écrit tous les calques SIDECAR de la vue présents entre IN et OUT,
        ré-indexés 0-based dans le clip (overlays.sidecar à côté du clip)."""
        if write_overlay_sidecar is None:
            return
        mw = self._mw
        ov = getattr(mw, "overlays", {}) or {}
        if not ov:
            return
        offset = mw.sidecar_offset.value() if hasattr(mw, "sidecar_offset") else 0
        sub = {}
        for key, graphs in ov.items():
            f = key - offset                    # frame d'affichage du calque
            if start <= f <= end:
                sub[f - start] = graphs         # ré-indexe dans le clip (0-based)
        if not sub:
            return
        out_file = (os.path.join(dest, "overlays.sidecar") if is_folder
                    else os.path.splitext(dest)[0] + ".sidecar")
        try:
            write_overlay_sidecar(sub, out_file)
            cur = self._status.text()
            n = sum(len(v) for v in sub.values())
            self._status.setText(
                (cur + "\n" if cur else "") +
                f"Calques SIDECAR ({len(sub)} frames, {n} formes) → {out_file}")
        except Exception as ex:
            self._status.setText(self._status.text() + f"\nErreur SIDECAR : {ex}")

    def _ffmpeg(self, out_path, start, end):
        ff = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
        if not ff:
            return False
        src = self._mw.source
        if not hasattr(src, 'cap'):
            return False  # ffmpeg ne peut lire que les vraies videos (VideoSource)
        fps = float(getattr(src, 'fps', 25.0) or 25.0)
        t0 = start / fps
        td = (end - start + 1) / fps
        src_ext = os.path.splitext(src.path)[1].lower()
        # Stream-copy seulement si la source est deja MP4 (meme codec, meme container)
        if src_ext in ('.mp4', '.m4v'):
            codec_args = ["-c", "copy"]
            label = "sans re-encodage"
        else:
            # WebM (VP8/VP9), AVI (MJPEG/XVID...), MKV, MOV... -> re-encoder H.264
            codec_args = ["-c:v", "libx264", "-crf", "23", "-preset", "fast",
                          "-c:a", "aac", "-b:a", "192k"]
            label = "re-encodé H.264"
        import subprocess
        try:
            r = subprocess.run(
                [ff, "-y", "-i", src.path,
                 "-ss", f"{t0:.6f}", "-t", f"{td:.6f}",
                 *codec_args, out_path],
                capture_output=True, timeout=300)
            if r.returncode == 0:
                self._status.setText(f"Extrait {label} → {out_path}")
                return True
        except Exception:
            pass
        return False

    def _cv2_mp4(self, out_path, start, end, as_viewed=False):
        mw = self._mw
        src = mw.source
        fps = float(self.fps_spin.value() or getattr(src, 'fps', 25.0) or 25.0)
        if as_viewed:
            # rendu complet (LUT/contraste + calques .ver/SIDECAR) : TOUJOURS via
            # source.get(idx), quel que soit le type de source (vidéo ou
            # séquence) — cap.read() ci-dessous ne sait pas graver de calques.
            out0 = self._apply_crop(mw._export_frame_as_viewed(start))
            if out0 is None:
                self._status.setText("Impossible de traiter la frame source.")
                return
            h, w = out0.shape[:2]
            writer = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
            n = end - start + 1
            self._prog.setMaximum(n)
            self._set_ui(True)
            for i, idx in enumerate(range(start, end + 1)):
                out = self._apply_crop(mw._export_frame_as_viewed(idx))
                if out is not None:
                    if out.ndim == 2:
                        out = cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)
                    if (out.shape[1], out.shape[0]) != (w, h):
                        out = cv2.resize(out, (w, h), interpolation=cv2.INTER_AREA)
                    writer.write(out)
                self._prog.setValue(i + 1)
                QtWidgets.QApplication.processEvents()
            writer.release()
            self._set_ui(False)
            self._status.setText(f"Extrait (tel qu'affiché, calques inclus) → {out_path}")
            return
        if hasattr(src, 'cap') and self._crop is None:
            cap = cv2.VideoCapture(src.path)
            if not cap.isOpened():
                self._status.setText("Impossible d'ouvrir la source vidéo.")
                return
            cap.set(cv2.CAP_PROP_POS_FRAMES, start)
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            writer = cv2.VideoWriter(
                out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
            n = end - start + 1
            self._prog.setMaximum(n)
            self._set_ui(True)
            for i in range(n):
                ret, frame = cap.read()
                if not ret:
                    break
                writer.write(frame)
                self._prog.setValue(i + 1)
                QtWidgets.QApplication.processEvents()
            writer.release()
            cap.release()
        else:
            out0 = self._apply_crop(mw.process(mw._apply_rotation(src.get(start))))
            if out0 is None:
                self._status.setText("Impossible de traiter la frame source.")
                return
            h, w = out0.shape[:2]
            writer = cv2.VideoWriter(
                out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
            n = end - start + 1
            self._prog.setMaximum(n)
            self._set_ui(True)
            for i, idx in enumerate(range(start, end + 1)):
                frame = src.get(idx)
                out = self._apply_crop(
                    mw.process(mw._apply_rotation(frame))) if frame is not None else None
                if out is not None:
                    if out.ndim == 2:
                        out = cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)
                    if (out.shape[1], out.shape[0]) != (w, h):
                        out = cv2.resize(out, (w, h), interpolation=cv2.INTER_AREA)
                    writer.write(out)
                self._prog.setValue(i + 1)
                QtWidgets.QApplication.processEvents()
            writer.release()
        self._set_ui(False)
        self._status.setText(f"Extrait (re-encodé) → {out_path}")

    def _link_or_copy(self, src, dst):
        """Tente symlink, puis lien physique, puis copie. -> 'sym'|'hard'|'copy'."""
        if os.path.lexists(dst):
            try:
                os.remove(dst)
            except OSError:
                pass
        try:
            os.symlink(os.path.abspath(src), dst)
            return "sym"
        except (OSError, RuntimeError, AttributeError):
            pass
        try:
            os.link(src, dst)
            return "hard"
        except (OSError, RuntimeError, AttributeError):
            pass
        shutil.copy2(src, dst)
        return "copy"

    def _file_copy(self, folder, start, end):
        paths = getattr(self._mw.source, 'paths', [])
        use_link = (hasattr(self, "_symlink_chk")
                    and self._symlink_chk.isChecked() and self._symlink_chk.isEnabled())
        n = end - start + 1
        self._prog.setMaximum(n)
        self._set_ui(True)
        tally = {"sym": 0, "hard": 0, "copy": 0}
        for i, idx in enumerate(range(start, end + 1)):
            if idx < len(paths):
                src_p = paths[idx]
                ext = os.path.splitext(src_p)[1]
                dst = os.path.join(folder, f"frame_{i:05d}{ext}")
                if use_link:
                    tally[self._link_or_copy(src_p, dst)] += 1
                else:
                    shutil.copy2(src_p, dst)
            self._prog.setValue(i + 1)
            QtWidgets.QApplication.processEvents()
        self._set_ui(False)
        if use_link:
            self._status.setText(
                f"Liens créés → {folder}  "
                f"(symlink {tally['sym']}, lien physique {tally['hard']}, "
                f"copie {tally['copy']})")
        else:
            self._status.setText(f"Fichiers copiés → {folder}")

    def _rendered_folder(self, folder, start, end, ext, as_viewed=False):
        mw = self._mw
        n = end - start + 1
        self._prog.setMaximum(n)
        self._set_ui(True)
        for i, idx in enumerate(range(start, end + 1)):
            if as_viewed:
                out = mw._export_frame_as_viewed(idx)
            else:
                frame = mw.source.get(idx)
                out = mw.process(mw._apply_rotation(frame)) if frame is not None else None
            out = self._apply_crop(out)
            if out is not None:
                cv2.imwrite(os.path.join(folder, f"frame_{i:05d}{ext}"), out)
            self._prog.setValue(i + 1)
            QtWidgets.QApplication.processEvents()
        self._set_ui(False)
        self._status.setText(f"PNG exportés → {folder}")

    def _raw_folder(self, folder, start, end, ext, png16=False):
        mw = self._mw
        n = end - start + 1
        self._prog.setMaximum(n)
        self._set_ui(True)
        for i, idx in enumerate(range(start, end + 1)):
            frame = mw.source.get(idx)
            if frame is not None:
                raw_rot = mw._apply_rotation(frame)
                if png16:
                    # PNG ne connait que 8 ou 16 bits/canal : on squeeze le
                    # canal unique et on ramene tout ce qui n'est pas deja
                    # uint8/uint16 (int16/uint32/float...) sur 16 bits.
                    if raw_rot.ndim == 3 and raw_rot.shape[2] == 1:
                        raw_rot = raw_rot[:, :, 0]
                    if raw_rot.dtype not in (np.uint8, np.uint16):
                        raw_rot = np.clip(raw_rot, 0, 65535).astype(np.uint16)
                raw_rot = self._apply_crop(raw_rot)
                cv2.imwrite(os.path.join(folder, f"frame_{i:05d}{ext}"), raw_rot)
            self._prog.setValue(i + 1)
            QtWidgets.QApplication.processEvents()
        self._set_ui(False)
        self._status.setText(
            f"{'PNG 16 bits' if png16 else 'TIFF'} exportés → {folder}")

    def _specialized_export(self, out_path, start, end, as_viewed=False):
        """Écrit un .specialized depuis n'importe quelle source : données brutes par
        défaut, ou rendu « tel qu'affiché » (8 bits, calques gravés) si
        as_viewed est coché."""
        if SpecializedWriter is None:
            self._status.setText("SpecializedWriter indisponible (specialized_reader manquant).")
            return
        mw = self._mw
        n = end - start + 1
        self._prog.setMaximum(n)
        self._set_ui(True)
        writer = None
        written = 0
        try:
            for i, idx in enumerate(range(start, end + 1)):
                if as_viewed:
                    raw = mw._export_frame_as_viewed(idx)
                else:
                    frame = mw.source.get(idx)
                    raw = mw._apply_rotation(frame) if frame is not None else None
                raw = self._apply_crop(raw)
                if raw is not None:
                    if writer is None:
                        rows, cols = raw.shape[:2]
                        channels = raw.shape[2] if raw.ndim == 3 else 1
                        writer = SpecializedWriter(out_path, rows, cols,
                                           type_img_for_dtype(raw.dtype), channels)
                    writer.write(raw[:, :, None] if raw.ndim == 2 else raw)
                    written += 1
                self._prog.setValue(i + 1)
                QtWidgets.QApplication.processEvents()
        except Exception as ex:
            self._status.setText(f"Erreur SPECIALIZED : {ex}")
            return
        finally:
            if writer:
                writer.close()
            self._set_ui(False)
        self._status.setText(f"SPECIALIZED écrit ({written} frames) → {out_path}")


class SplitExtractDialog(QtWidgets.QDialog):
    """Extraction par morceaux : une liste de plages IN→OUT est exportee en une
    seule fois, chaque morceau nomme {base}_{in}_{out}. Reutilise les ecritures
    par clip de ClipExtractDialog (mp4 / SPECIALIZED / PNG / TIFF), crop ROI compris."""

    def __init__(self, mw, segments, crop_rect=None):
        super().__init__(mw)
        self._mw = mw
        # copie triee/nettoyee des morceaux valides
        self._segments = [(int(s), int(e)) for (s, e) in segments if e > s]
        self._crop = tuple(crop_rect) if crop_rect else None
        self.setWindowTitle(f"Extraire les morceaux  ─  {len(self._segments)} plage(s)")
        self.setMinimumWidth(460)

        vbox = QtWidgets.QVBoxLayout(self)
        total_f = sum(e - s + 1 for (s, e) in self._segments)
        info = QtWidgets.QLabel(
            f"<b>{getattr(mw.source, 'name', '?')}</b><br>"
            f"{len(self._segments)} morceau(x), {total_f} frames au total.")
        info.setWordWrap(True)
        vbox.addWidget(info)

        lst = QtWidgets.QListWidget()
        lst.setMaximumHeight(120)
        for i, (s, e) in enumerate(self._segments, 1):
            lst.addItem(f"#{i}   {s} → {e}   ({e - s + 1} frames)")
        vbox.addWidget(lst)

        if self._crop is not None:
            cx, cy, cw, ch = self._crop
            cl = QtWidgets.QLabel(f"Crop ROI actif : {cw}×{ch} px @ ({cx}, {cy}).")
            cl.setStyleSheet("color:#3fd07a; font-size:11px; font-weight:bold;")
            vbox.addWidget(cl)

        grp = QtWidgets.QGroupBox("Format de sortie (identique pour tous les morceaux)")
        gl = QtWidgets.QVBoxLayout(grp)
        self._fmt = QtWidgets.QComboBox()
        self._fmt.addItem("MP4  (cv2 — calques/contraste possibles)", "mp4_cv2")
        self._fmt.addItem("Dossier PNG  (frames rendues 8 bits)", "png_folder")
        self._fmt.addItem("Dossier TIFF  (données brutes, 16 bits conservés)", "tiff_raw")
        self._fmt.addItem("Dossier PNG 16 bits  (données brutes)", "png16_raw")
        if SpecializedWriter is not None:
            self._fmt.addItem("Fichier SPECIALIZED  (.specialized — données brutes)", "specialized")
        self._is_seq = hasattr(mw.source, 'paths') and self._crop is None
        if self._is_seq:
            self._fmt.addItem(
                "Copie des fichiers originaux  (sans dégradation)", "copy")
        gl.addWidget(self._fmt)
        vbox.addWidget(grp)

        # option symlink (comme en extraction simple) : active seulement pour le
        # format "copy" (séquence, même format entrée=sortie).
        self._symlink_chk = QtWidgets.QCheckBox(
            "Créer des liens au lieu de copier (symlink, sans duplication)")
        vbox.addWidget(self._symlink_chk)
        self._symlink_note = QtWidgets.QLabel("")
        self._symlink_note.setWordWrap(True)
        self._symlink_note.setStyleSheet("color:#e05555; font-size:11px;")
        vbox.addWidget(self._symlink_note)
        self._fmt.currentIndexChanged.connect(self._upd_symlink_state)
        self._upd_symlink_state()

        fps_src = float(getattr(mw.source, 'fps', mw.fps_spin.value()) or 25.0)
        fps_row = QtWidgets.QHBoxLayout()
        fps_row.addWidget(QtWidgets.QLabel("Cadence MP4 :"))
        self.fps_spin = QtWidgets.QDoubleSpinBox()
        self.fps_spin.setRange(0.1, 480.0)
        self.fps_spin.setValue(fps_src)
        self.fps_spin.setDecimals(2)
        self.fps_spin.setSuffix(" fps")
        fps_row.addWidget(self.fps_spin)
        fps_row.addStretch(1)
        vbox.addLayout(fps_row)

        self._as_viewed_chk = QtWidgets.QCheckBox(
            "Exporter tel qu'affiché (calques + contraste gravés)")
        self._as_viewed_chk.setChecked(False)
        vbox.addWidget(self._as_viewed_chk)

        self._sidecar_chk = QtWidgets.QCheckBox(
            "Exporter aussi les calques (SIDECAR, .ver) et données plugin par morceau")
        self._sidecar_chk.setToolTip(
            "Écrit à côté de CHAQUE morceau, ré-indexés sur sa plage :\n"
            "• les calques SIDECAR (overlays.sidecar)\n"
            "• les annotations .ver / YOLO\n"
            "• les CSV/TSV déposés sur les plugins actifs (si colonne de frame)")
        has_side = (bool(getattr(mw, "_annotations", {}))
                    or bool(getattr(mw, "overlays", {})))
        self._sidecar_chk.setChecked(has_side)
        vbox.addWidget(self._sidecar_chk)

        name_row = QtWidgets.QHBoxLayout()
        name_row.addWidget(QtWidgets.QLabel("Nom commun :"))
        self._name_edit = QtWidgets.QLineEdit(getattr(mw.source, 'stem', 'clip') or "clip")
        self._name_edit.setToolTip(
            "Chaque morceau est nommé {nom}_{frameIN}_{frameOUT}.")
        name_row.addWidget(self._name_edit, 1)
        vbox.addLayout(name_row)

        self._prog = QtWidgets.QProgressBar()
        self._prog.setVisible(False)
        vbox.addWidget(self._prog)
        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        vbox.addWidget(self._status)

        row = QtWidgets.QHBoxLayout()
        self._go = QtWidgets.QPushButton("Extraire tous les morceaux...")
        self._go.clicked.connect(self._extract_all)
        row.addWidget(self._go)
        row.addStretch(1)
        btn_close = QtWidgets.QPushButton("Fermer")
        btn_close.clicked.connect(self.accept)
        row.addWidget(btn_close)
        vbox.addLayout(row)

    def _upd_symlink_state(self):
        _apply_symlink_state(self._symlink_chk, self._symlink_note,
                             self._fmt.currentData() == "copy")

    def _sanitize(self, name):
        keep = "-_.() "
        return "".join(c for c in name if c.isalnum() or c in keep).strip() or "clip"

    def _extract_all(self):
        if not self._segments:
            self._status.setText("Aucun morceau valide à extraire.")
            return
        fmt = self._fmt.currentData()
        base = self._sanitize(self._name_edit.text())
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Dossier de destination des morceaux...")
        if not folder:
            return
        as_viewed = self._as_viewed_chk.isChecked()
        self._go.setEnabled(False)
        done = 0
        errors = 0
        for (s, e) in self._segments:
            tag = f"{base}_{s}_{e}"
            # dialogue-clip cache reutilise pour l'ecriture d'un morceau ; ses
            # widgets de progression/statut sont routes vers les notres.
            d = ClipExtractDialog(self._mw, s, e, has_in_out=False,
                                  crop_rect=self._crop)
            d._prog = self._prog
            d._status = self._status
            d._go = self._go
            d.fps_spin = self.fps_spin
            try:
                if fmt == "mp4_cv2":
                    side_dest = os.path.join(folder, tag + ".mp4")
                    side_folder = False
                    d._cv2_mp4(side_dest, s, e, as_viewed)
                elif fmt == "specialized":
                    side_dest = os.path.join(folder, tag + ".specialized")
                    side_folder = False
                    d._specialized_export(side_dest, s, e, as_viewed)
                elif fmt == "png_folder":
                    side_dest = os.path.join(folder, tag)
                    side_folder = True
                    os.makedirs(side_dest, exist_ok=True)
                    d._rendered_folder(side_dest, s, e, ".png", as_viewed)
                elif fmt == "tiff_raw":
                    side_dest = os.path.join(folder, tag)
                    side_folder = True
                    os.makedirs(side_dest, exist_ok=True)
                    d._raw_folder(side_dest, s, e, ".tiff")
                elif fmt == "png16_raw":
                    side_dest = os.path.join(folder, tag)
                    side_folder = True
                    os.makedirs(side_dest, exist_ok=True)
                    d._raw_folder(side_dest, s, e, ".png", png16=True)
                elif fmt == "copy":
                    side_dest = os.path.join(folder, tag)
                    side_folder = True
                    os.makedirs(side_dest, exist_ok=True)
                    # propage le choix symlink au dialogue-clip cache
                    d._symlink_chk.setEnabled(True)
                    d._symlink_chk.setChecked(self._symlink_chk.isChecked())
                    d._file_copy(side_dest, s, e)
                if self._sidecar_chk.isChecked():
                    d._export_sidecars(side_dest, s, e, side_folder)
                done += 1
            except Exception as ex:
                errors += 1
                self._status.setText(f"Erreur sur {tag} : {ex}")
            finally:
                d.deleteLater()
            self._status.setText(
                f"{done}/{len(self._segments)} morceau(x) exporté(s) → {folder}"
                + (f"  ({errors} erreur(s))" if errors else ""))
            QtWidgets.QApplication.processEvents()
        self._go.setEnabled(True)
        self._prog.setVisible(False)


class RoiConvertDialog(QtWidgets.QDialog):
    """Exporte la ROI courante (fixe, ou suivant une boîte .ver si un suivi
    est actif) sur TOUTE la séquence : MP4 / dossier PNG / SPECIALIZED, avec option
    heatmap des histogrammes (X=frame, Y=niveau de gris, couleur=comptage)."""

    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw
        self.setWindowTitle("Convertir la ROI")
        self.setMinimumWidth(440)
        vbox = QtWidgets.QVBoxLayout(self)

        tracked = mw._roi_track_id is not None
        x, y, w, h = mw._roi
        mode_txt = (f"Suivi de la boîte .ver — track {mw._roi_track_id}\n"
                    f"(la zone exportée suit la boîte à chaque frame)"
                    if tracked else
                    f"ROI fixe — {w}×{h} px @ ({x},{y})\n"
                    f"(même zone exportée à chaque frame)")
        info = QtWidgets.QLabel(mode_txt)
        info.setWordWrap(True)
        vbox.addWidget(info)
        src_desc = QtWidgets.QLabel(describe_source(mw.source))
        src_desc.setStyleSheet("color:#7fb8ff; font-size:11px;")
        src_desc.setWordWrap(True)
        vbox.addWidget(src_desc)

        grp = QtWidgets.QGroupBox("Format de sortie")
        gl = QtWidgets.QVBoxLayout(grp)
        self._fmt = QtWidgets.QComboBox()
        self._fmt.addItem("MP4 (rendu, contraste/LUT inclus)", "mp4")
        self._fmt.addItem("Dossier PNG (rendu)", "png_folder")
        if SpecializedWriter is not None:
            self._fmt.addItem("Fichier SPECIALIZED (données brutes)", "specialized")
        gl.addWidget(self._fmt)
        vbox.addWidget(grp)

        pad_row = QtWidgets.QHBoxLayout()
        pad_row.addWidget(QtWidgets.QLabel("Padding autour du crop (px) :"))
        self._pad_spin = QtWidgets.QSpinBox()
        self._pad_spin.setRange(0, 512)
        self._pad_spin.setValue(0)
        self._pad_spin.setToolTip(
            "Marge ajoutée de chaque côté du rectangle (fixe ou suivi .ver),\n"
            "sur toute la séquence. Ex. 2 = 2 px de fond en plus tout autour.")
        pad_row.addWidget(self._pad_spin)
        pad_row.addStretch(1)
        vbox.addLayout(pad_row)

        self._heat_chk = QtWidgets.QCheckBox(
            "Exporter aussi la heatmap des histogrammes (PNG, à côté)")
        self._heat_chk.setToolTip(
            "Image X = frame, Y = niveau de gris (0-255), couleur = nombre\n"
            "de pixels : histogramme de la ROI empilé sur toute la séquence.")
        vbox.addWidget(self._heat_chk)

        self._prog = QtWidgets.QProgressBar()
        self._prog.setVisible(False)
        vbox.addWidget(self._prog)
        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        vbox.addWidget(self._status)

        row = QtWidgets.QHBoxLayout()
        self._go = QtWidgets.QPushButton("Exporter...")
        self._go.clicked.connect(self._export)
        row.addWidget(self._go)
        row.addStretch(1)
        close_btn = QtWidgets.QPushButton("Fermer")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        vbox.addLayout(row)

    # -- géométrie de la ROI par frame --
    def _roi_at(self, idx):
        mw = self._mw
        box = (mw._annot_box_for_track(mw._roi_track_id, idx)
               if mw._roi_track_id is not None else mw._roi)
        if box is None:
            return None
        pad = self._pad_spin.value() if hasattr(self, "_pad_spin") else 0
        if pad:
            x, y, w, h = box
            box = (x - pad, y - pad, w + 2 * pad, h + 2 * pad)
        return box

    def _crop_rendered_at(self, idx):
        """Crop « tel qu'affiché » (LUT/contraste) à la frame idx, ou None."""
        mw = self._mw
        box = self._roi_at(idx)
        if box is None:
            return None
        frame = mw.source.get(idx)
        if frame is None:
            return None
        raw_rot = mw._apply_rotation(frame)
        out = mw.process(raw_rot)
        if out is None:
            return None
        H, W = out.shape[:2]
        x, y, w, h = box
        x = max(0, min(int(x), W - 1)); y = max(0, min(int(y), H - 1))
        x2 = min(W, x + int(w)); y2 = min(H, y + int(h))
        if x2 <= x or y2 <= y:
            return None
        crop = out[y:y2, x:x2]
        return crop if crop.ndim == 3 else cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)

    def _crop_raw_at(self, idx):
        """Crop de données brutes (rotation seule, sans LUT) à la frame idx."""
        mw = self._mw
        box = self._roi_at(idx)
        if box is None:
            return None
        frame = mw.source.get(idx)
        if frame is None:
            return None
        raw_rot = mw._apply_rotation(frame)
        H, W = raw_rot.shape[:2]
        x, y, w, h = box
        x = max(0, min(int(x), W - 1)); y = max(0, min(int(y), H - 1))
        x2 = min(W, x + int(w)); y2 = min(H, y + int(h))
        if x2 <= x or y2 <= y:
            return None
        return raw_rot[y:y2, x:x2]

    def _set_ui(self, running, n=1):
        self._go.setEnabled(not running)
        self._prog.setVisible(running)
        self._prog.setMaximum(max(1, n))

    # -- export --
    def _export(self):
        mw = self._mw
        if mw.source is None:
            return
        fmt = self._fmt.currentData()
        n = mw.source.count
        base = None
        if fmt == "mp4":
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Exporter le crop ROI...", "roi_crop.mp4", "Vidéo MP4 (*.mp4)")
            if not path:
                return
            self._export_mp4(path, n)
            base = os.path.splitext(path)[0]
        elif fmt == "png_folder":
            folder = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Dossier de destination pour les PNG...")
            if not folder:
                return
            self._export_png_folder(folder, n)
            base = os.path.join(folder, "roi")
        else:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Exporter le crop ROI (SPECIALIZED)...", "roi_crop.specialized", "SPECIALIZED (*.specialized)")
            if not path:
                return
            self._export_specialized(path, n)
            base = os.path.splitext(path)[0]
        if self._heat_chk.isChecked() and base:
            self._export_heatmap(base + "_heatmap.png", n)

    def _export_mp4(self, path, n):
        fps = float(getattr(self._mw.source, "fps", 25.0) or 25.0)
        self._set_ui(True, n)
        writer = None
        ref_size = None
        written = 0
        for idx in range(n):
            crop = self._crop_rendered_at(idx)
            if crop is not None:
                if ref_size is None:
                    ref_size = (crop.shape[1], crop.shape[0])
                    writer = cv2.VideoWriter(
                        path, cv2.VideoWriter_fourcc(*"mp4v"), fps, ref_size)
                if (crop.shape[1], crop.shape[0]) != ref_size:
                    crop = cv2.resize(crop, ref_size, interpolation=cv2.INTER_AREA)
                writer.write(crop)
                written += 1
            self._prog.setValue(idx + 1)
            QtWidgets.QApplication.processEvents()
        if writer is not None:
            writer.release()
        self._set_ui(False, n)
        self._status.setText(f"Crop ROI exporté ({written} frames) → {path}")

    def _export_png_folder(self, folder, n):
        self._set_ui(True, n)
        written = 0
        for idx in range(n):
            crop = self._crop_rendered_at(idx)
            if crop is not None:
                cv2.imwrite(os.path.join(folder, f"roi_{idx:05d}.png"), crop)
                written += 1
            self._prog.setValue(idx + 1)
            QtWidgets.QApplication.processEvents()
        self._set_ui(False, n)
        self._status.setText(f"PNG exportés ({written} frames) → {folder}")

    def _export_specialized(self, path, n):
        if SpecializedWriter is None:
            self._status.setText("SpecializedWriter indisponible (specialized_reader manquant).")
            return
        self._set_ui(True, n)
        writer = None
        ref_hw = None
        written = 0
        try:
            for idx in range(n):
                crop = self._crop_raw_at(idx)
                if crop is not None:
                    if ref_hw is None:
                        ref_hw = crop.shape[:2]
                    elif crop.shape[:2] != ref_hw:
                        crop = cv2.resize(crop, (ref_hw[1], ref_hw[0]),
                                          interpolation=cv2.INTER_AREA)
                    if writer is None:
                        rows, cols = crop.shape[:2]
                        channels = crop.shape[2] if crop.ndim == 3 else 1
                        writer = SpecializedWriter(path, rows, cols,
                                           type_img_for_dtype(crop.dtype), channels)
                    writer.write(crop[:, :, None] if crop.ndim == 2 else crop)
                    written += 1
                self._prog.setValue(idx + 1)
                QtWidgets.QApplication.processEvents()
        except Exception as ex:
            self._status.setText(f"Erreur SPECIALIZED : {ex}")
            return
        finally:
            if writer:
                writer.close()
            self._set_ui(False, n)
        self._status.setText(f"SPECIALIZED (ROI) écrit ({written} frames) → {path}")

    def _export_heatmap(self, path, n):
        rows = []
        for idx in range(n):
            crop = self._crop_rendered_at(idx)
            if crop is None:
                rows.append(np.zeros(256, dtype=np.float64))
                continue
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
            hist, _ = np.histogram(gray.ravel(), bins=256, range=(0, 255))
            rows.append(hist.astype(np.float64))
            if idx % 20 == 0:
                QtWidgets.QApplication.processEvents()
        if not rows:
            return
        heat = np.array(rows).T                      # (256 niveaux, n frames)
        raw_max = float(heat.max())
        heat_log = np.log1p(heat)
        mx = float(heat_log.max()) if heat_log.max() > 0 else 1.0
        norm = (heat_log / mx * 255).astype(np.uint8)
        norm = np.flipud(norm)                        # ligne 0 = niveau 255 (haut du graphe)
        core = cv2.applyColorMap(norm, cv2.COLORMAP_INFERNO)
        core_h, core_w = core.shape[:2]

        # -- mise en page : axes (X=frame, Y=niveau de gris) + légende colormap --
        ml, mr, mt, mb, legend_w, gap = 46, 60, 14, 34, 18, 10
        W = ml + core_w + gap + legend_w + mr
        H = mt + core_h + mb
        canvas = np.full((H, W, 3), 24, dtype=np.uint8)
        canvas[mt:mt + core_h, ml:ml + core_w] = core

        font = cv2.FONT_HERSHEY_SIMPLEX
        col_txt = (210, 210, 210)
        # axe Y : niveau de gris, 255 en haut -> 0 en bas
        for frac, lbl in ((0.0, "255"), (0.5, "128"), (1.0, "0")):
            y = int(mt + frac * core_h)
            cv2.line(canvas, (ml - 4, y), (ml, y), col_txt, 1, cv2.LINE_AA)
            (tw, th), _ = cv2.getTextSize(lbl, font, 0.35, 1)
            cv2.putText(canvas, lbl, (ml - 8 - tw, y + th // 2), font, 0.35, col_txt, 1, cv2.LINE_AA)
        cv2.putText(canvas, "Gris", (2, mt - 2), font, 0.34, col_txt, 1, cv2.LINE_AA)
        # axe X : numéro de frame, 0 -> n-1
        for frac, val in ((0.0, 0), (0.5, n // 2), (1.0, max(0, n - 1))):
            x = int(ml + frac * core_w)
            cv2.line(canvas, (x, mt + core_h), (x, mt + core_h + 4), col_txt, 1, cv2.LINE_AA)
            lbl = str(val)
            (tw, th), _ = cv2.getTextSize(lbl, font, 0.35, 1)
            cv2.putText(canvas, lbl, (max(0, x - tw // 2), mt + core_h + 16),
                       font, 0.35, col_txt, 1, cv2.LINE_AA)
        cv2.putText(canvas, "Frame", (ml + core_w // 2 - 18, H - 4),
                   font, 0.38, col_txt, 1, cv2.LINE_AA)

        # légende colormap : nombre de pixels (échelle réelle, mapping log)
        lx0 = ml + core_w + gap
        grad = np.linspace(255, 0, core_h).astype(np.uint8).reshape(-1, 1)
        grad_col = cv2.resize(cv2.applyColorMap(grad, cv2.COLORMAP_INFERNO),
                              (legend_w, core_h), interpolation=cv2.INTER_NEAREST)
        canvas[mt:mt + core_h, lx0:lx0 + legend_w] = grad_col
        cv2.rectangle(canvas, (lx0, mt), (lx0 + legend_w, mt + core_h), (90, 90, 90), 1)
        for frac, val in ((0.0, raw_max), (1.0, 0.0)):
            y = int(mt + frac * core_h)
            cv2.putText(canvas, f"{val:.0f}", (lx0 + legend_w + 4, y + 4),
                       font, 0.32, col_txt, 1, cv2.LINE_AA)
        cv2.putText(canvas, "px", (lx0, mt - 4), font, 0.32, col_txt, 1, cv2.LINE_AA)

        cv2.imwrite(path, canvas)
        cur = self._status.text()
        self._status.setText((cur + "\n" if cur else "") + f"Heatmap histogrammes → {path}")


# ----------------------------- fenetre principale -------------------------
