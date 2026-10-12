# -*- coding: utf-8 -*-
"""Dialogues de conversion/extraction sur toute une sequence :
ClipExtractDialog (bouton "Convertir" par vue + extraction IN->OUT),
SplitExtractDialog (plusieurs morceaux d'un coup),
RoiConvertDialog (export de la ROI, fixe ou suivie, sur toute la sequence).

Les dialogues ne sont pas modaux : l'export tourne en tache de fond
(``export_jobs.JobRunner``) a partir d'un etat fige de la vue
(``export_jobs.ExportSnapshot``), on peut donc continuer a travailler dans la
fenetre principale pendant qu'il s'execute. `mw` (MainWindow) est utilise en
duck-typing, aucun import de MainWindow ici."""

import os
import shutil
import subprocess

import cv2
import numpy as np
from PySide6 import QtWidgets

from frameviewer.core.export_formats import (
    ExportSpec,
    convert_frame,
    open_writer,
    stream_copy_possible,
)
from frameviewer.core.feature_registry import operation_for_kind
from frameviewer.core.sources import describe_source
from frameviewer.ui.export_format_panel import ExportFormatPanel
from frameviewer.ui.export_jobs import ExportSnapshot, JobRunner
from frameviewer.ui.i18n import set_ui_pair, set_ui_text, ui_text

write_overlay_sidecar = operation_for_kind("overlay_format", "write")


def _apply_symlink_state(chk, note, panel):
    """Etat de l'option symlink selon le format d'export. Le lien n'a de sens
    qu'en « Copie des fichiers originaux » (entree = sortie, un fichier source
    unique a lier). Sinon on grise ET on BARRE le libelle, avec une explication
    juste en dessous. Masquee quand cette copie n'est pas proposee du tout."""
    available = panel.offers("copy")
    chk.setVisible(available)
    note.setVisible(available)
    is_copy = panel.fmt() == "copy"
    chk.setEnabled(is_copy)
    f = chk.font()
    f.setStrikeOut(not is_copy)
    chk.setFont(f)
    if is_copy:
        note.setText("")
        return
    chk.setChecked(False)
    set_ui_text(
        note,
        "Symlink possible uniquement avec « Copie des fichiers originaux » "
        "(séquence extraite telle quelle, entrée = sortie). Les autres formats "
        "ré-encodent chaque image : il n'existe plus de fichier source unique à "
        "lier, donc aucun lien n'est possible.",
    )


class _BackgroundExportMixin:
    """Dialogue non modal dont l'export tourne en tache de fond.

    Fermer le dialogue pendant un export propose de le laisser finir en
    arriere-plan (le resultat s'affiche alors dans la barre d'etat) ou de
    l'annuler.
    """

    def _init_background_export(self, snapshot, owns_snapshot):
        self.setModal(False)
        self._snap = snapshot
        self._owns_snap = owns_snapshot
        self._runner = JobRunner(self)
        self._runner.finished.connect(self._on_job_finished)

    def is_busy(self):
        return self._runner.busy()

    def cancel_export(self):
        self._runner.cancel()

    def _launch(self, job):
        if self._runner.busy():
            return
        set_ui_text(self._status, "Export en cours...")
        self._set_ui(True)
        self._runner.start(job)

    def _on_job_finished(self, ok, message):
        self._set_ui(False)
        if not ok:
            set_ui_text(self._status, message if message else "Export interrompu.")
        if not self.isVisible():
            lines = [ln for ln in self._status.text().splitlines() if ln.strip()]
            if lines:
                self._mw.statusBar().showMessage(lines[-1], 10000)
            self._release()

    def _release(self):
        if self._owns_snap:
            self._snap.close()
        self.deleteLater()

    def done(self, result):
        if self._runner.busy():
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle(ui_text(self, "Export en cours"))
            box.setText(ui_text(self, "Un export est en cours. Le laisser finir en arrière-plan ?"))
            keep = box.addButton(
                ui_text(self, "Continuer en arrière-plan"), QtWidgets.QMessageBox.AcceptRole
            )
            stop = box.addButton(
                ui_text(self, "Annuler l'export"), QtWidgets.QMessageBox.DestructiveRole
            )
            box.addButton(ui_text(self, "Rester"), QtWidgets.QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is keep:
                self.hide()
                return
            if box.clickedButton() is not stop:
                return
            self._runner.cancel()
        super().done(result)
        self._release()

    # -- ecriture commune ------------------------------------------------------
    def _cut_frame(self, img, idx):
        """Zone exportee de la frame idx (crop ROI) ; surchargee par dialogue."""
        return img

    def _export_frame(self, spec, idx):
        """Frame idx prete a ecrire selon les options choisies, ou None."""
        snap = self._snap
        if spec.renders_view:
            img = snap.viewed(idx)
            return self._cut_frame(img, idx) if img is not None else None
        raw = snap.raw(idx)
        if raw is None:
            return None
        return convert_frame(self._cut_frame(raw, idx), spec)

    def _job_write(self, spec, dest, indices, prefix="frame"):
        """Ecrit les frames ``indices`` (dossier d'images, SPECIALIZED ou MP4)."""
        indices = list(indices)
        self._prog.setMaximum(max(1, len(indices)))
        writer = open_writer(spec, dest, prefix)
        try:
            for i, idx in enumerate(indices):
                frame = self._export_frame(spec, idx)
                if frame is not None:
                    writer.write(i, frame)
                _write_progress(self._prog, i + 1)
                yield
        except GeneratorExit:
            writer.abort()  # export annule : ffmpeg est arrete net
            raise
        finally:
            writer.close()
        if writer.count == 0:
            set_ui_pair(
                self._status,
                "Aucune frame exportée (source illisible ou zone vide).",
                "No frame exported (unreadable source or empty area).",
            )
            return
        set_ui_pair(
            self._status,
            f"{spec.label()} : {writer.count} frame(s) écrite(s) -> {dest}",
            f"{spec.label(english=True)}: {writer.count} frame(s) written -> {dest}",
        )


def _write_progress(prog, value):
    prog.setValue(value)


# --------------------- panneau de selection (ROI) -------------------------


class ClipExtractDialog(_BackgroundExportMixin, QtWidgets.QDialog):
    """Dialogue d'extraction d'un clip (frames IN->OUT sans degradation)."""

    def __init__(self, mw, start, end, has_in_out=True, crop_rect=None, snapshot=None):
        super().__init__(mw)
        self._mw = mw
        self._start = start
        self._end = end
        # crop ROI optionnel (x, y, w, h) en coords image affichee (apres
        # rotation, meme repere que process()) : n'exporte que cette sous-zone.
        self._crop = tuple(crop_rect) if crop_rect else None
        # etat fige de la vue (partage quand un dialogue parent le fournit)
        self._init_background_export(snapshot or ExportSnapshot.from_engine(mw), snapshot is None)
        self._crop_native = self._snap.native_rect(self._crop)
        # has_in_out=False : dialogue ouvert depuis le bouton "Convertir" d'une
        # vue (toute la sequence, start/end ne sont PAS une vraie plage IN/OUT
        # choisie par l'utilisateur) -> pas de case "export calques IN->OUT".
        self._has_in_out = has_in_out
        n = end - start + 1
        self.setWindowTitle(f"Extraire clip  ─  {n} frames")
        self.setMinimumWidth(440)

        vbox = QtWidgets.QVBoxLayout(self)
        src = self._snap.source
        src_name = getattr(src, "name", "?")
        fps_src = float(getattr(src, "fps", mw.fps_spin.value()) or 25.0)
        dur = n / max(fps_src, 0.001)
        info = QtWidgets.QLabel(
            f"<b>{src_name}</b><br>"
            f"Frames {start} → {end}  ({n} frames, {dur:.2f} s @ {fps_src:.1f} fps)"
        )
        info.setWordWrap(True)
        vbox.addWidget(info)
        if self._crop is not None:
            cx, cy, cw, ch = self._crop
            crop_lbl = QtWidgets.QLabel(
                f"Crop ROI actif : {cw}×{ch} px @ ({cx}, {cy}) — "
                "seule cette sous-zone est exportée."
            )
            crop_lbl.setStyleSheet("color:#3fd07a; font-size:11px; font-weight:bold;")
            crop_lbl.setWordWrap(True)
            vbox.addWidget(crop_lbl)
        src_desc = QtWidgets.QLabel(describe_source(self._snap.reader))
        src_desc.setStyleSheet("color:#7fb8ff; font-size:11px;")
        src_desc.setWordWrap(True)
        vbox.addWidget(src_desc)

        # les options proposees dependent du type de la donnee d'entree
        self._panel = ExportFormatPanel(
            self._snap.raw(start),
            fps=fps_src,
            window=(self._snap.lo, self._snap.hi),
            allow_copy=hasattr(src, "paths") and self._crop is None,
            allow_stream_copy=stream_copy_possible(src) and self._crop is None,
            parent=self,
        )
        vbox.addWidget(self._panel)

        self._export_annot_chk = QtWidgets.QCheckBox(
            "Exporter aussi les calques SIDECAR + annotations (IN→OUT)"
        )
        self._export_annot_chk.setChecked(True)
        self._export_annot_chk.setToolTip(
            "Écrit à côté du clip, ré-indexés sur la plage IN→OUT :\n"
            "• les calques SIDECAR (overlays.sidecar)\n"
            "• les annotations .ver / YOLO"
        )
        has_side = bool(self._snap.annotations) or bool(self._snap.overlays)
        if not has_side or not self._has_in_out:
            self._export_annot_chk.setVisible(False)
            if not self._has_in_out:
                # pas de vraie plage IN->OUT ici : desactive aussi la logique
                # d'export (pas seulement l'affichage de la case).
                self._export_annot_chk.setChecked(False)
        vbox.addWidget(self._export_annot_chk)

        # option symlink : uniquement pour une sequence d'images extraite dans
        # son format d'origine (format "copy"), sinon grise + explication rouge.
        self._symlink_chk = QtWidgets.QCheckBox(
            "Créer des liens au lieu de copier (symlink, sans duplication)"
        )
        self._symlink_chk.setToolTip(
            "Pour une séquence d'images extraite dans son format d'origine :\n"
            "crée un lien vers chaque fichier source au lieu de le copier.\n"
            "Essaie symlink, puis lien physique, puis copie en dernier recours."
        )
        vbox.addWidget(self._symlink_chk)
        self._symlink_note = QtWidgets.QLabel("")
        self._symlink_note.setWordWrap(True)
        self._symlink_note.setStyleSheet("color:#e05555; font-size:11px;")
        vbox.addWidget(self._symlink_note)
        self._panel.changed.connect(self._upd_symlink_state)
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
        """Rogne `img` (frame native, rotation appliquee) au crop ROI, si actif."""
        if self._crop_native is None or img is None:
            return img
        H, W = img.shape[:2]
        x, y, w, h = self._crop_native
        x = max(0, min(int(x), W - 1))
        y = max(0, min(int(y), H - 1))
        x2 = min(W, x + int(w))
        y2 = min(H, y + int(h))
        if x2 <= x or y2 <= y:
            return img
        return img[y:y2, x:x2]

    def _cut_frame(self, img, idx):
        return self._apply_crop(img)

    def _upd_symlink_state(self):
        _apply_symlink_state(self._symlink_chk, self._symlink_note, self._panel)

    def _set_ui(self, running):
        self._go.setEnabled(not running)
        self._prog.setVisible(running)

    def _extract(self):
        spec = self._panel.spec()
        start, end = self._start, self._end
        name = f"clip_{start:05d}-{end:05d}"
        if spec.fmt == "mp4":
            dest, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, ui_text(self, "Enregistrer le clip..."), name + ".mp4", "Vidéo MP4 (*.mp4)"
            )
        elif spec.fmt == "specialized":
            dest, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, ui_text(self, "Enregistrer le clip SPECIALIZED..."), name + ".specialized", "SPECIALIZED (*.specialized)"
            )
        else:
            dest = QtWidgets.QFileDialog.getExistingDirectory(
                self, ui_text(self, "Dossier de destination...")
            )
        if not dest:
            return
        work = self._job_for(spec, dest, start, end)
        with_sidecars = self._export_annot_chk.isChecked()

        def job():
            yield from work
            if with_sidecars:
                self._export_sidecars(dest, start, end, spec.is_folder)

        self._launch(job())

    def _job_for(self, spec, dest, start, end):
        """Generateur d'export pour les options choisies (un ``yield`` par frame)."""
        if spec.fmt == "copy":
            return self._job_file_copy(dest, start, end)
        if spec.stream_copy:
            return self._job_stream_copy(dest, start, end, spec)
        return self._job_write(spec, dest, range(start, end + 1))

    def _export_annot_slice(self, dest, start, end, is_folder):
        """Ecrit les annotations IN->OUT dans un fichier .ver ou YOLO .txt.
        Les frame IDs sont re-indices a partir de 0 (0-based dans le clip).
        """
        annots = self._snap.annotations
        if not annots:
            return
        is_ver = os.path.splitext(self._snap.annot_path)[1].lower() == ".ver"

        # Dimensions natives pour la normalisation YOLO (coords des boites)
        w_img, h_img = self._snap.native_dims()

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
                        f"{clip_idx + 1:.6e}",
                        "1.000000e+00",
                        f"{x1:.6e}",
                        f"{y1:.6e}",
                        f"{x2:.6e}",
                        f"{y2:.6e}",
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
                    lines.append(f"{clip_idx} {cls} {cx:.6f} {cy:.6f} {wn:.6f} {hn:.6f}")

        ext_out = ".ver" if is_ver else ".txt"
        if is_folder:
            out_file = os.path.join(dest, f"annotations{ext_out}")
        else:
            out_file = os.path.splitext(dest)[0] + ext_out

        try:
            with open(out_file, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
            cur = self._status.text()
            set_ui_text(self._status, cur + f"\nAnnotations ({len(lines)} det.) → {out_file}")
        except Exception as ex:
            set_ui_text(self._status, self._status.text() + f"\nErreur annot : {ex}")

    def _export_sidecars(self, dest, start, end, is_folder):
        """Exporte, à côté du clip et ré-indexés sur IN→OUT : les calques SIDECAR,
        les annotations .ver/YOLO, PUIS les données déposées sur les plugins
        actifs (CSV/TSV, ré-indexées si une colonne de frame est détectée)."""
        self._export_sidecar_slice(dest, start, end, is_folder)
        self._export_annot_slice(dest, start, end, is_folder)
        self._export_plugin_data_slice(dest, start, end, is_folder)

    # colonnes de frame reconnues dans un CSV/TSV de plugin (minuscules)
    _FRAME_COLS = {
        "frame",
        "frames",
        "numimage",
        "num_image",
        "numimg",
        "frame_id",
        "frameid",
        "frame_index",
        "frameindex",
        "numframe",
        "num_frame",
        "idframe",
        "id_frame",
        "image",
        "img",
        "n",
        "num",
        "numero",
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
        view_key = self._snap.view_key
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
                            path, dest, start, end, is_folder, lp.plugin_id, var
                        )
                    except Exception as ex:
                        set_ui_text(
                            self._status,
                            self._status.text() + f"\nErreur données plugin ({var}) : {ex}",
                        )

    def _slice_one_plugin_file(self, path, dest, start, end, is_folder, pid, var):
        ext = os.path.splitext(path)[1].lower()
        base = os.path.basename(path)
        if is_folder:
            out_file = os.path.join(dest, f"{pid}_{var}_{base}")
        else:
            out_file = os.path.splitext(dest)[0] + f"_{pid}_{var}{ext}"

        if ext not in (".csv", ".tsv", ".txt", ".dat"):
            shutil.copy2(path, out_file)  # binaire/inconnu : copie verbatim
            set_ui_text(
                self._status,
                self._status.text() + f"\nDonnées plugin copiées (non tabulaire) → {out_file}",
            )
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
            set_ui_text(
                self._status,
                self._status.text() + f"\nDonnées plugin ({var}) copiées SANS ré-indexation "
                "(aucune colonne de frame reconnue) → " + out_file,
            )
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
            parts[fcol] = str(int(round(new_v))) if abs(v - round(v)) < 1e-9 else repr(new_v)
            out_lines.append(delim.join(parts))
            kept += 1

        with open(out_file, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(out_lines))
        set_ui_text(
            self._status,
            self._status.text() + f"\nDonnées plugin ({var}) : {kept} ligne(s) ré-indexée(s) "
            f"(base {onebase}) → {out_file}",
        )

    def _export_sidecar_slice(self, dest, start, end, is_folder):
        """Écrit tous les calques SIDECAR de la vue présents entre IN et OUT,
        ré-indexés 0-based dans le clip (overlays.sidecar à côté du clip)."""
        if write_overlay_sidecar is None:
            return
        ov = self._snap.overlays
        if not ov:
            return
        offset = self._snap.sidecar_offset
        sub = {}
        for key, graphs in ov.items():
            f = key - offset  # frame d'affichage du calque
            if start <= f <= end:
                sub[f - start] = graphs  # ré-indexe dans le clip (0-based)
        if not sub:
            return
        out_file = (
            os.path.join(dest, "overlays.sidecar") if is_folder else os.path.splitext(dest)[0] + ".sidecar"
        )
        try:
            write_overlay_sidecar(sub, out_file)
            cur = self._status.text()
            n = sum(len(v) for v in sub.values())
            set_ui_text(
                self._status,
                (cur + "\n" if cur else "")
                + f"Calques SIDECAR ({len(sub)} frames, {n} formes) → {out_file}",
            )
        except Exception as ex:
            set_ui_text(self._status, self._status.text() + f"\nErreur SIDECAR : {ex}")

    def _job_ffmpeg(self, out_path, start, end):
        """Coupe via ffmpeg sans re-compression : renvoie True si ffmpeg a reussi."""
        ff = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
        src = self._snap.source
        if not ff or not hasattr(src, "cap"):
            return False  # ffmpeg ne peut lire que les vraies videos (VideoSource)
        fps = float(getattr(src, "fps", 25.0) or 25.0)
        t0 = start / fps
        td = (end - start + 1) / fps
        cmd = [ff, "-y", "-i", src.path, "-ss", f"{t0:.6f}", "-t", f"{td:.6f}", "-c", "copy"]
        try:
            proc = subprocess.Popen(
                [*cmd, out_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            return False
        try:
            self._prog.setMaximum(0)  # duree inconnue : barre animee
            while proc.poll() is None:
                yield
        finally:
            if proc.poll() is None:
                proc.kill()  # export annule
        if proc.returncode == 0:
            set_ui_text(self._status, f"Extrait sans re-encodage -> {out_path}")
            return True
        return False

    def _job_stream_copy(self, out_path, start, end, spec):
        ok = yield from self._job_ffmpeg(out_path, start, end)
        if not ok:
            # le conteneur refuse la copie : on re-encode en MPEG-4, frames intactes
            fallback = ExportSpec(fmt="mp4", encoder="mp4v", fps=spec.fps)
            yield from self._job_write(fallback, out_path, range(start, end + 1))

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

    def _job_file_copy(self, folder, start, end):
        paths = getattr(self._snap.source, "paths", [])
        use_link = (
            hasattr(self, "_symlink_chk")
            and self._symlink_chk.isChecked()
            and self._symlink_chk.isEnabled()
        )
        self._prog.setMaximum(end - start + 1)
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
            _write_progress(self._prog, i + 1)
            yield
        if use_link:
            set_ui_text(
                self._status,
                f"Liens créés → {folder}  "
                f"(symlink {tally['sym']}, lien physique {tally['hard']}, "
                f"copie {tally['copy']})",
            )
        else:
            set_ui_text(self._status, f"Fichiers copiés → {folder}")


class SplitExtractDialog(_BackgroundExportMixin, QtWidgets.QDialog):
    """Extraction par morceaux : une liste de plages IN→OUT est exportee en une
    seule fois, chaque morceau nomme {base}_{in}_{out}. Reutilise les ecritures
    par clip de ClipExtractDialog (mp4 / SPECIALIZED / PNG / TIFF), crop ROI compris."""

    def __init__(self, mw, segments, crop_rect=None):
        super().__init__(mw)
        self._mw = mw
        # copie triee/nettoyee des morceaux valides
        self._segments = [(int(s), int(e)) for (s, e) in segments if e > s]
        self._crop = tuple(crop_rect) if crop_rect else None
        self._init_background_export(ExportSnapshot.from_engine(mw), True)
        self.setWindowTitle(f"Extraire les morceaux  ─  {len(self._segments)} plage(s)")
        self.setMinimumWidth(460)

        vbox = QtWidgets.QVBoxLayout(self)
        total_f = sum(e - s + 1 for (s, e) in self._segments)
        info = QtWidgets.QLabel(
            f"<b>{getattr(mw.source, 'name', '?')}</b><br>"
            f"{len(self._segments)} morceau(x), {total_f} frames au total."
        )
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

        first = self._segments[0][0] if self._segments else 0
        fps_src = float(getattr(mw.source, "fps", mw.fps_spin.value()) or 25.0)
        self._panel = ExportFormatPanel(
            self._snap.raw(first),
            fps=fps_src,
            window=(self._snap.lo, self._snap.hi),
            allow_copy=hasattr(mw.source, "paths") and self._crop is None,
            allow_stream_copy=stream_copy_possible(mw.source) and self._crop is None,
            parent=self,
        )
        self._panel.setTitle("Format et options (identiques pour tous les morceaux)")
        vbox.addWidget(self._panel)

        # option symlink (comme en extraction simple) : active seulement pour le
        # format "copy" (séquence, même format entrée=sortie).
        self._symlink_chk = QtWidgets.QCheckBox(
            "Créer des liens au lieu de copier (symlink, sans duplication)"
        )
        vbox.addWidget(self._symlink_chk)
        self._symlink_note = QtWidgets.QLabel("")
        self._symlink_note.setWordWrap(True)
        self._symlink_note.setStyleSheet("color:#e05555; font-size:11px;")
        vbox.addWidget(self._symlink_note)
        self._panel.changed.connect(self._upd_symlink_state)
        self._upd_symlink_state()

        self._sidecar_chk = QtWidgets.QCheckBox(
            "Exporter aussi les calques (SIDECAR, .ver) et données plugin par morceau"
        )
        self._sidecar_chk.setToolTip(
            "Écrit à côté de CHAQUE morceau, ré-indexés sur sa plage :\n"
            "• les calques SIDECAR (overlays.sidecar)\n"
            "• les annotations .ver / YOLO\n"
            "• les CSV/TSV déposés sur les plugins actifs (si colonne de frame)"
        )
        has_side = bool(getattr(mw, "_annotations", {})) or bool(getattr(mw, "overlays", {}))
        self._sidecar_chk.setChecked(has_side)
        vbox.addWidget(self._sidecar_chk)

        name_row = QtWidgets.QHBoxLayout()
        name_row.addWidget(QtWidgets.QLabel("Nom commun :"))
        self._name_edit = QtWidgets.QLineEdit(getattr(mw.source, "stem", "clip") or "clip")
        self._name_edit.setToolTip("Chaque morceau est nommé {nom}_{frameIN}_{frameOUT}.")
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
        _apply_symlink_state(self._symlink_chk, self._symlink_note, self._panel)

    def _sanitize(self, name):
        keep = "-_.() "
        return "".join(c for c in name if c.isalnum() or c in keep).strip() or "clip"

    def _set_ui(self, running):
        self._go.setEnabled(not running)
        self._prog.setVisible(running)

    def _extract_all(self):
        if not self._segments:
            set_ui_text(self._status, "Aucun morceau valide à extraire.")
            return
        base = self._sanitize(self._name_edit.text())
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, ui_text(self, "Dossier de destination des morceaux...")
        )
        if not folder:
            return
        self._launch(self._job_all(folder, self._panel.spec(), base, self._sidecar_chk.isChecked()))

    def _job_all(self, folder, spec, base, with_sidecars):
        extensions = {"mp4": ".mp4", "specialized": ".specialized"}
        done = 0
        errors = 0
        for s, e in self._segments:
            tag = f"{base}_{s}_{e}"
            # dialogue-clip cache reutilise pour l'ecriture d'un morceau ; il
            # partage l'etat fige et ses widgets de progression/statut sont
            # routes vers les notres.
            d = ClipExtractDialog(
                self._mw, s, e, has_in_out=False, crop_rect=self._crop, snapshot=self._snap
            )
            d._prog = self._prog
            d._status = self._status
            is_folder = spec.is_folder
            dest = os.path.join(folder, tag + extensions.get(spec.fmt, ""))
            try:
                if is_folder:
                    os.makedirs(dest, exist_ok=True)
                if spec.fmt == "copy":
                    # propage le choix symlink au dialogue-clip cache
                    d._symlink_chk.setEnabled(True)
                    d._symlink_chk.setChecked(self._symlink_chk.isChecked())
                yield from d._job_for(spec, dest, s, e)
                if with_sidecars:
                    d._export_sidecars(dest, s, e, is_folder)
                done += 1
            except Exception as ex:
                errors += 1
                set_ui_text(self._status, f"Erreur sur {tag} : {ex}")
            finally:
                d.deleteLater()
            set_ui_text(
                self._status,
                f"{done}/{len(self._segments)} morceau(x) exporté(s) -> {folder}"
                + (f"  ({errors} erreur(s))" if errors else ""),
            )
            yield


class RoiConvertDialog(_BackgroundExportMixin, QtWidgets.QDialog):
    """Exporte la ROI courante (fixe, ou suivant une boîte .ver si un suivi
    est actif) sur TOUTE la séquence : MP4 / dossier PNG / SPECIALIZED, avec option
    heatmap des histogrammes (X=frame, Y=niveau de gris, couleur=comptage)."""

    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw
        self._init_background_export(ExportSnapshot.from_engine(mw), True)
        self._roi_native = self._snap.native_rect(mw._roi)
        self._track_id = mw._roi_track_id
        self.setWindowTitle("Convertir la ROI")
        self.setMinimumWidth(440)
        vbox = QtWidgets.QVBoxLayout(self)

        tracked = mw._roi_track_id is not None
        x, y, w, h = mw._roi
        mode_txt = (
            f"Suivi de la boîte .ver — track {mw._roi_track_id}\n"
            f"(la zone exportée suit la boîte à chaque frame)"
            if tracked
            else f"ROI fixe — {w}×{h} px @ ({x},{y})\n(même zone exportée à chaque frame)"
        )
        info = QtWidgets.QLabel(mode_txt)
        info.setWordWrap(True)
        vbox.addWidget(info)
        src_desc = QtWidgets.QLabel(describe_source(self._snap.reader))
        src_desc.setStyleSheet("color:#7fb8ff; font-size:11px;")
        src_desc.setWordWrap(True)
        vbox.addWidget(src_desc)

        self._panel = ExportFormatPanel(
            self._snap.raw(0),
            fps=float(getattr(self._snap.source, "fps", 25.0) or 25.0),
            window=(self._snap.lo, self._snap.hi),
            parent=self,
        )
        vbox.addWidget(self._panel)

        pad_row = QtWidgets.QHBoxLayout()
        pad_row.addWidget(QtWidgets.QLabel("Padding autour du crop (px) :"))
        self._pad_spin = QtWidgets.QSpinBox()
        self._pad_spin.setRange(0, 512)
        self._pad_spin.setValue(0)
        self._pad_spin.setToolTip(
            "Marge ajoutée de chaque côté du rectangle (fixe ou suivi .ver),\n"
            "sur toute la séquence. Ex. 2 = 2 px de fond en plus tout autour."
        )
        pad_row.addWidget(self._pad_spin)
        pad_row.addStretch(1)
        vbox.addLayout(pad_row)

        self._heat_chk = QtWidgets.QCheckBox(
            "Exporter aussi la heatmap des histogrammes (PNG, à côté)"
        )
        self._heat_chk.setToolTip(
            "Image X = frame, Y = niveau de gris (0-255), couleur = nombre\n"
            "de pixels : histogramme de la ROI empilé sur toute la séquence."
        )
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

    # -- géométrie de la ROI par frame (pixels natifs, rotation appliquee) --
    def _roi_at(self, idx):
        if self._track_id is not None:
            box = self._snap.track_box(self._track_id, idx)
        else:
            box = self._roi_native
        if box is None:
            return None
        pad = self._pad_spin.value() if hasattr(self, "_pad_spin") else 0
        if pad:
            x, y, w, h = box
            box = (x - pad, y - pad, w + 2 * pad, h + 2 * pad)
        return box

    @staticmethod
    def _cut(img, box):
        H, W = img.shape[:2]
        x, y, w, h = box
        x = max(0, min(int(x), W - 1))
        y = max(0, min(int(y), H - 1))
        x2 = min(W, x + int(w))
        y2 = min(H, y + int(h))
        if x2 <= x or y2 <= y:
            return None
        return img[y:y2, x:x2]

    def _crop_rendered_at(self, idx):
        """Crop « tel qu'affiché » (LUT/contraste) à la frame idx, ou None."""
        box = self._roi_at(idx)
        out = self._snap.rendered(idx) if box is not None else None
        crop = self._cut(out, box) if out is not None else None
        if crop is None:
            return None
        return crop if crop.ndim == 3 else cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)

    def _cut_frame(self, img, idx):
        box = self._roi_at(idx)
        return self._cut(img, box) if box is not None else None

    def _set_ui(self, running):
        self._go.setEnabled(not running)
        self._prog.setVisible(running)

    # -- export --
    def _export(self):
        if self._snap.source is None:
            return
        spec = self._panel.spec()
        n = self._snap.source.count
        if spec.fmt == "mp4":
            dest, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, ui_text(self, "Exporter le crop ROI..."), "roi_crop.mp4", "Vidéo MP4 (*.mp4)"
            )
            base = os.path.splitext(dest)[0]
        elif spec.fmt == "specialized":
            dest, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, ui_text(self, "Exporter le crop ROI (SPECIALIZED)..."), "roi_crop.specialized", "SPECIALIZED (*.specialized)"
            )
            base = os.path.splitext(dest)[0]
        else:
            dest = QtWidgets.QFileDialog.getExistingDirectory(
                self, ui_text(self, "Dossier de destination...")
            )
            base = os.path.join(dest, "roi")
        if not dest:
            return
        work = self._job_write(spec, dest, range(n), prefix="roi")
        heatmap = self._heat_chk.isChecked()

        def job():
            yield from work
            if heatmap:
                yield from self._job_heatmap(base + "_heatmap.png", n)

        self._launch(job())

    def _job_heatmap(self, path, n):
        rows = []
        for idx in range(n):
            crop = self._crop_rendered_at(idx)
            if crop is None:
                rows.append(np.zeros(256, dtype=np.float64))
            else:
                gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
                hist, _ = np.histogram(gray.ravel(), bins=256, range=(0, 255))
                rows.append(hist.astype(np.float64))
            yield
        if rows:
            self._write_heatmap(path, rows, n)

    def _write_heatmap(self, path, rows, n):
        heat = np.array(rows).T  # (256 niveaux, n frames)
        raw_max = float(heat.max())
        heat_log = np.log1p(heat)
        mx = float(heat_log.max()) if heat_log.max() > 0 else 1.0
        norm = (heat_log / mx * 255).astype(np.uint8)
        norm = np.flipud(norm)  # ligne 0 = niveau 255 (haut du graphe)
        core = cv2.applyColorMap(norm, cv2.COLORMAP_INFERNO)
        core_h, core_w = core.shape[:2]

        # -- mise en page : axes (X=frame, Y=niveau de gris) + légende colormap --
        ml, mr, mt, mb, legend_w, gap = 46, 60, 14, 34, 18, 10
        W = ml + core_w + gap + legend_w + mr
        H = mt + core_h + mb
        canvas = np.full((H, W, 3), 24, dtype=np.uint8)
        canvas[mt : mt + core_h, ml : ml + core_w] = core

        font = cv2.FONT_HERSHEY_SIMPLEX
        col_txt = (210, 210, 210)
        # axe Y : niveau de gris, 255 en haut -> 0 en bas
        for frac, lbl in ((0.0, "255"), (0.5, "128"), (1.0, "0")):
            y = int(mt + frac * core_h)
            cv2.line(canvas, (ml - 4, y), (ml, y), col_txt, 1, cv2.LINE_AA)
            (tw, th), _ = cv2.getTextSize(lbl, font, 0.35, 1)
            cv2.putText(
                canvas, lbl, (ml - 8 - tw, y + th // 2), font, 0.35, col_txt, 1, cv2.LINE_AA
            )
        cv2.putText(canvas, "Gris", (2, mt - 2), font, 0.34, col_txt, 1, cv2.LINE_AA)
        # axe X : numéro de frame, 0 -> n-1
        for frac, val in ((0.0, 0), (0.5, n // 2), (1.0, max(0, n - 1))):
            x = int(ml + frac * core_w)
            cv2.line(canvas, (x, mt + core_h), (x, mt + core_h + 4), col_txt, 1, cv2.LINE_AA)
            lbl = str(val)
            (tw, th), _ = cv2.getTextSize(lbl, font, 0.35, 1)
            cv2.putText(
                canvas,
                lbl,
                (max(0, x - tw // 2), mt + core_h + 16),
                font,
                0.35,
                col_txt,
                1,
                cv2.LINE_AA,
            )
        cv2.putText(
            canvas, "Frame", (ml + core_w // 2 - 18, H - 4), font, 0.38, col_txt, 1, cv2.LINE_AA
        )

        # légende colormap : nombre de pixels (échelle réelle, mapping log)
        lx0 = ml + core_w + gap
        grad = np.linspace(255, 0, core_h).astype(np.uint8).reshape(-1, 1)
        grad_col = cv2.resize(
            cv2.applyColorMap(grad, cv2.COLORMAP_INFERNO),
            (legend_w, core_h),
            interpolation=cv2.INTER_NEAREST,
        )
        canvas[mt : mt + core_h, lx0 : lx0 + legend_w] = grad_col
        cv2.rectangle(canvas, (lx0, mt), (lx0 + legend_w, mt + core_h), (90, 90, 90), 1)
        for frac, val in ((0.0, raw_max), (1.0, 0.0)):
            y = int(mt + frac * core_h)
            cv2.putText(
                canvas,
                f"{val:.0f}",
                (lx0 + legend_w + 4, y + 4),
                font,
                0.32,
                col_txt,
                1,
                cv2.LINE_AA,
            )
        cv2.putText(canvas, "px", (lx0, mt - 4), font, 0.32, col_txt, 1, cv2.LINE_AA)

        cv2.imwrite(path, canvas)
        cur = self._status.text()
        set_ui_text(self._status, (cur + "\n" if cur else "") + f"Heatmap histogrammes → {path}")


class Mp4OptionsDialog(QtWidgets.QDialog):
    """Options MP4 d'une composition multi-vues (frames deja rendues)."""

    def __init__(self, parent, fps):
        super().__init__(parent)
        self.setWindowTitle("Options de la vidéo MP4")
        self.setMinimumWidth(420)
        vbox = QtWidgets.QVBoxLayout(self)
        self.panel = ExportFormatPanel(None, fps=fps, formats=("mp4",), parent=self)
        vbox.addWidget(self.panel)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        vbox.addWidget(buttons)
