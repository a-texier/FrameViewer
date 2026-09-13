# -*- coding: utf-8 -*-
"""YoloConvertDialog : conversion bidirectionnelle .ver <-> dataset YOLO
(Ultralytics), avec split train/val/test configurable (ex. 80/10/10 ou
100/0/0). Recoit l'instance MainWindow (`mw`) en parametre, comme les autres
dialogues d'export -- duck-typing sur mw.source / mw._annotations / mw._raw."""
import os

from PySide6 import QtWidgets
from PySide6.QtCore import Qt

from frameviewer.core.annotation_convert import (class_names_from_annotations,
                                                 export_yolo, yolo_to_ver)


class YoloConvertDialog(QtWidgets.QDialog):

    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw
        self.setWindowTitle("Convertir .ver <-> YOLO")
        self.setMinimumWidth(520)

        vbox = QtWidgets.QVBoxLayout(self)

        self._dir_combo = QtWidgets.QComboBox()
        self._dir_combo.addItem(".ver -> YOLO  (exporter un dataset)", "ver2yolo")
        self._dir_combo.addItem("YOLO -> .ver  (importer)", "yolo2ver")
        self._dir_combo.currentIndexChanged.connect(self._upd_panels)
        vbox.addWidget(self._dir_combo)

        # Calques actuellement charges sur la vue, separes par format d'origine
        # (une meme vue peut avoir a la fois des tracks .ver ET des classes
        # YOLO -- chaque direction ne doit proposer/utiliser QUE le format qui
        # lui correspond, pour que la conversion reste logique).
        all_layers = list(enumerate(getattr(mw, "_layers", []) or []))
        ver_layers = [(i, L) for i, L in all_layers
                      if L["kind"] == "ver" and L.get("fmt", "ver") == "ver"]
        yolo_layers = [(i, L) for i, L in all_layers
                       if L["kind"] == "ver" and L.get("fmt") == "yolo"]

        self._default_class_names = class_names_from_annotations(
            getattr(mw, "_annotations", {}) or {})
        self._class_edit = QtWidgets.QLineEdit(", ".join(self._default_class_names))
        self._class_edit.setToolTip(
            "Noms de classe, dans l'ordre de leur index (0, 1, 2...).\n"
            "Utilisé dans les deux sens : index -> nom (export .ver) et\n"
            "nom -> index (les classes .ver inconnues de cette liste sont\n"
            "ignorées à l'export YOLO).")
        cls_row = QtWidgets.QFormLayout()
        cls_row.addRow("Classes (dans l'ordre) :", self._class_edit)
        vbox.addLayout(cls_row)

        # ---------------- panneau .ver -> YOLO ----------------
        self._ver2yolo_grp = QtWidgets.QGroupBox(".ver -> YOLO")
        g1 = QtWidgets.QVBoxLayout(self._ver2yolo_grp)

        g1.addWidget(QtWidgets.QLabel(
            "Source : calques .ver chargés sur cette vue (décoche ceux à exclure). "
            "« Label id » = id de classe YOLO écrit pour ce track à l'export -- un "
            ".ver a des sous-classes (ex. drone/quadcoptère/mavic) que YOLO n'a pas : "
            "plusieurs tracks peuvent donc partager le même id (0 par défaut partout)."))
        self._ver_tree = QtWidgets.QTreeWidget()
        self._ver_tree.setColumnCount(2)
        self._ver_tree.setHeaderLabels(["Calque .ver", "Label id"])
        self._ver_tree.header().setStretchLastSection(False)
        self._ver_tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self._ver_tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self._ver_tree.setMaximumHeight(140)
        self._track_label_sp = {}   # track_id (key) -> QSpinBox (id de classe voulu a l'export)
        ver_groups = {}
        for _, L in ver_layers:
            ver_groups.setdefault(L.get("file") or L["name"], []).append(L)
        for file, Ls in ver_groups.items():
            parent = QtWidgets.QTreeWidgetItem(
                [f"📄 {file}  ({len(Ls)} track{'s' if len(Ls) > 1 else ''})", ""])
            parent.setFlags(parent.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            parent.setCheckState(0, Qt.Checked)
            self._ver_tree.addTopLevelItem(parent)
            for L in Ls:
                child = QtWidgets.QTreeWidgetItem([f"track {L['key']}", ""])
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Checked)
                child.setData(0, Qt.UserRole, L["key"])
                parent.addChild(child)
                sp = QtWidgets.QSpinBox()
                sp.setRange(0, 999)
                sp.setValue(0)
                sp.setToolTip(
                    "Id de classe YOLO écrit dans les .txt pour les boîtes de ce "
                    "track (0 par défaut). Le nom correspondant à cet id est la "
                    "position N dans le champ Classes ci-dessus.")
                self._ver_tree.setItemWidget(child, 1, sp)
                self._track_label_sp[L["key"]] = sp
            parent.setExpanded(True)
        g1.addWidget(self._ver_tree)
        if not ver_layers:
            self._ver_tree.setEnabled(False)
            warn1 = QtWidgets.QLabel("Aucun calque .ver chargé sur cette vue -- dépose d'abord un/des .ver.")
            warn1.setWordWrap(True)
            warn1.setStyleSheet("color:#e08030;")
            g1.addWidget(warn1)
        if yolo_layers:
            names = ", ".join(f"classe {L.get('cls', L['key'])}" for _, L in yolo_layers)
            note1 = QtWidgets.QLabel(
                f"ℹ {len(yolo_layers)} calque(s) YOLO aussi chargé(s) sur cette vue, ignoré(s) "
                f"ici (déjà au format YOLO) : {names}.")
            note1.setWordWrap(True)
            note1.setStyleSheet("color:#888; font-size:11px;")
            g1.addWidget(note1)

        out_row = QtWidgets.QHBoxLayout()
        self._out_dir_edit = QtWidgets.QLineEdit()
        out_browse = QtWidgets.QPushButton("Parcourir...")
        out_browse.clicked.connect(self._browse_out_dir)
        out_row.addWidget(self._out_dir_edit, 1)
        out_row.addWidget(out_browse)
        g1.addLayout(out_row)

        split_row = QtWidgets.QHBoxLayout()
        split_row.addWidget(QtWidgets.QLabel("Train/Val/Test (%) :"))
        self._sp_train = QtWidgets.QSpinBox()
        self._sp_val = QtWidgets.QSpinBox()
        self._sp_test = QtWidgets.QSpinBox()
        for sp in (self._sp_train, self._sp_val, self._sp_test):
            sp.setRange(0, 100)
            split_row.addWidget(sp)
        self._sp_train.setValue(80)
        self._sp_val.setValue(10)
        self._sp_test.setValue(10)
        g1.addLayout(split_row)

        preset_row = QtWidgets.QHBoxLayout()
        btn_80 = QtWidgets.QPushButton("80/10/10")
        btn_80.clicked.connect(lambda: self._set_split(80, 10, 10))
        btn_100 = QtWidgets.QPushButton("100/0/0 (tout en train)")
        btn_100.clicked.connect(lambda: self._set_split(100, 0, 0))
        preset_row.addWidget(btn_80)
        preset_row.addWidget(btn_100)
        preset_row.addStretch(1)
        g1.addLayout(preset_row)

        self._split_warn = QtWidgets.QLabel("")
        self._split_warn.setStyleSheet("color:#e08030; font-size:11px;")
        g1.addWidget(self._split_warn)
        for sp in (self._sp_train, self._sp_val, self._sp_test):
            sp.valueChanged.connect(self._upd_split_warn)
        self._upd_split_warn()

        self._images_chk = QtWidgets.QCheckBox(
            "Exporter aussi les images (PNG) — nécessaire pour entraîner")
        self._images_chk.setChecked(True)
        g1.addWidget(self._images_chk)

        self._empty_chk = QtWidgets.QCheckBox(
            "Inclure les frames sans annotation (labels vides, fond/négatif)")
        self._empty_chk.setChecked(True)
        g1.addWidget(self._empty_chk)

        self._go_ver2yolo = QtWidgets.QPushButton("Exporter le dataset YOLO...")
        self._go_ver2yolo.clicked.connect(self._do_ver2yolo)
        g1.addWidget(self._go_ver2yolo)
        vbox.addWidget(self._ver2yolo_grp)

        # ---------------- panneau YOLO -> .ver ----------------
        self._yolo2ver_grp = QtWidgets.QGroupBox("YOLO -> .ver")
        g2 = QtWidgets.QVBoxLayout(self._yolo2ver_grp)

        warn = QtWidgets.QLabel(
            "⚠ YOLO ne fournit aucun identifiant d'objet entre frames. Le "
            "track_id écrit dans le .ver est assigné par position dans "
            "chaque frame — ce n'est PAS un vrai suivi temporel.")
        warn.setWordWrap(True)
        warn.setStyleSheet("color:#e08030; font-size:11px;")
        g2.addWidget(warn)

        if ver_layers:
            names = ", ".join(f"track {L['key']}" for _, L in ver_layers)
            note2 = QtWidgets.QLabel(
                f"ℹ {len(ver_layers)} calque(s) .ver aussi chargé(s) sur cette vue, ignoré(s) "
                f"ici (déjà au format .ver) : {names}.")
            note2.setWordWrap(True)
            note2.setStyleSheet("color:#888; font-size:11px;")
            g2.addWidget(note2)

        self._yolo_layers = yolo_layers
        if yolo_layers:
            use_row = QtWidgets.QHBoxLayout()
            use_btn = QtWidgets.QPushButton("Utiliser le calque YOLO déjà chargé sur cette vue")
            use_btn.clicked.connect(self._use_loaded_yolo_layer)
            use_row.addWidget(use_btn)
            use_row.addStretch(1)
            g2.addLayout(use_row)

        src_row = QtWidgets.QHBoxLayout()
        self._yolo_src_edit = QtWidgets.QLineEdit()
        self._yolo_src_edit.setPlaceholderText(
            "Dossier YOLO (un .txt par frame) ou fichier .txt fusionné")
        src_browse = QtWidgets.QPushButton("Parcourir...")
        src_browse.clicked.connect(self._browse_yolo_src)
        src_row.addWidget(self._yolo_src_edit, 1)
        src_row.addWidget(src_browse)
        g2.addLayout(src_row)

        dim_row = QtWidgets.QHBoxLayout()
        dim_row.addWidget(QtWidgets.QLabel("Dimensions image (W x H) :"))
        self._sp_w = QtWidgets.QSpinBox()
        self._sp_h = QtWidgets.QSpinBox()
        for sp in (self._sp_w, self._sp_h):
            sp.setRange(1, 20000)
        src = getattr(mw, "_raw", None)
        if src is not None:
            h0, w0 = src.shape[:2]
            self._sp_w.setValue(int(w0))
            self._sp_h.setValue(int(h0))
        else:
            self._sp_w.setValue(1920)
            self._sp_h.setValue(1080)
        dim_row.addWidget(self._sp_w)
        dim_row.addWidget(QtWidgets.QLabel("x"))
        dim_row.addWidget(self._sp_h)
        dim_row.addStretch(1)
        g2.addLayout(dim_row)
        if src is not None:
            dim_note = QtWidgets.QLabel("Pré-rempli depuis la vue ouverte.")
        else:
            dim_note = QtWidgets.QLabel(
                "Aucune séquence ouverte sur cette vue : ajuste ces valeurs "
                "à la main (dimensions des images sources du dataset YOLO).")
            dim_note.setStyleSheet("color:#e08030;")
        dim_note.setStyleSheet(dim_note.styleSheet() + "font-size:11px;")
        g2.addWidget(dim_note)

        out2_row = QtWidgets.QHBoxLayout()
        self._out_ver_edit = QtWidgets.QLineEdit()
        out2_browse = QtWidgets.QPushButton("Enregistrer sous...")
        out2_browse.clicked.connect(self._browse_out_ver)
        out2_row.addWidget(self._out_ver_edit, 1)
        out2_row.addWidget(out2_browse)
        g2.addLayout(out2_row)

        self._go_yolo2ver = QtWidgets.QPushButton("Importer -> écrire le .ver...")
        self._go_yolo2ver.clicked.connect(self._do_yolo2ver)
        g2.addWidget(self._go_yolo2ver)
        vbox.addWidget(self._yolo2ver_grp)

        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        vbox.addWidget(self._status)

        btn_close = QtWidgets.QPushButton("Fermer")
        btn_close.clicked.connect(self.accept)
        vbox.addWidget(btn_close)

        # Direction par defaut la plus logique compte tenu des calques deja
        # charges : s'il n'y a QUE du YOLO sur la vue, ".ver -> YOLO" n'aurait
        # rien a exporter -- on propose directement l'autre sens.
        if yolo_layers and not ver_layers:
            self._dir_combo.setCurrentIndex(self._dir_combo.findData("yolo2ver"))
        self._upd_panels()

    # ---------- helpers UI ----------
    def _upd_panels(self):
        is_v2y = self._dir_combo.currentData() == "ver2yolo"
        self._ver2yolo_grp.setVisible(is_v2y)
        self._yolo2ver_grp.setVisible(not is_v2y)

    def _set_split(self, t, v, te):
        self._sp_train.setValue(t)
        self._sp_val.setValue(v)
        self._sp_test.setValue(te)

    def _upd_split_warn(self):
        total = self._sp_train.value() + self._sp_val.value() + self._sp_test.value()
        if total == 0:
            self._split_warn.setText("Somme des 3 pourcentages = 0 -- rien ne serait exporté.")
        elif total != 100:
            self._split_warn.setText(
                f"Somme = {total}% (≠ 100%) -- sera normalisée automatiquement.")
        else:
            self._split_warn.setText("")

    def _class_names(self):
        names = [s.strip() for s in self._class_edit.text().split(",") if s.strip()]
        return names or list(self._default_class_names)

    def _browse_out_dir(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Dossier de sortie du dataset YOLO...")
        if d:
            self._out_dir_edit.setText(d)

    def _browse_yolo_src(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Dossier YOLO source (un .txt par frame)...")
        if d:
            self._yolo_src_edit.setText(d)
            return
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "...ou fichier .txt fusionné", "", "YOLO fusionné (*.txt)")
        if p:
            self._yolo_src_edit.setText(p)

    def _browse_out_ver(self):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Enregistrer le .ver sous...", "import.ver", ".ver (*.ver)")
        if p:
            self._out_ver_edit.setText(p)

    def _checked_ver_keys(self):
        """Cles (track_id) des calques .ver coches dans self._ver_tree."""
        keys = set()
        for i in range(self._ver_tree.topLevelItemCount()):
            parent = self._ver_tree.topLevelItem(i)
            for c in range(parent.childCount()):
                child = parent.child(c)
                if child.checkState(0) == Qt.Checked:
                    keys.add(child.data(0, Qt.UserRole))
        return keys

    def _use_loaded_yolo_layer(self):
        """Pre-remplit la source YOLO->.ver avec le chemin d'origine du/des
        calque(s) YOLO deja charges sur cette vue (evite de re-parcourir le
        disque quand le dossier a deja ete depose dans les calques)."""
        paths = {L.get("path") for _, L in self._yolo_layers if L.get("path")}
        if len(paths) == 1:
            self._yolo_src_edit.setText(next(iter(paths)))
            self._status.setText("Source pré-remplie depuis le calque YOLO chargé sur cette vue.")
        elif len(paths) > 1:
            self._status.setText(
                "Plusieurs calques YOLO d'origines différentes sont chargés -- "
                "choisis le dossier/fichier voulu via Parcourir...")
        else:
            self._status.setText(
                "Chemin d'origine du calque YOLO introuvable -- choisis-le via Parcourir...")

    # ---------- actions ----------
    def _do_ver2yolo(self):
        mw = self._mw
        keys = self._checked_ver_keys()
        if not keys:
            self._status.setText("Coche au moins un calque .ver à exporter.")
            return
        raw_annots = getattr(mw, "_annotations", {}) or {}
        annots = {}
        for fr, dets in raw_annots.items():
            keep = []
            for d in dets:
                key = d[5] if len(d) > 5 else 0
                if key not in keys:
                    continue
                # le label id choisi pour ce track (case "Label id") remplace
                # la classe .ver -- YOLO n'a pas de sous-classe, donc plusieurs
                # tracks .ver differents peuvent volontairement partager le
                # meme id de sortie.
                sp = self._track_label_sp.get(key)
                cls = sp.value() if sp is not None else (d[0] if len(d) > 0 else 0)
                keep.append((cls, d[1], d[2], d[3], d[4], key,
                             d[6] if len(d) > 6 else ()))
            if keep:
                annots[fr] = keep
        if not annots:
            self._status.setText("Aucune boîte dans les calques .ver cochés -- rien à exporter.")
            return
        out_dir = self._out_dir_edit.text().strip()
        if not out_dir:
            self._status.setText("Choisis un dossier de sortie.")
            return
        if mw._raw is None:
            self._status.setText("Aucune séquence ouverte sur cette vue.")
            return
        h, w = mw._raw.shape[:2]
        total = self._sp_train.value() + self._sp_val.value() + self._sp_test.value()
        if total <= 0:
            self._status.setText("Le split ne peut pas être 0/0/0.")
            return
        ratios = (self._sp_train.value() / total, self._sp_val.value() / total,
                  self._sp_test.value() / total)

        frame_provider = None
        if self._images_chk.isChecked() and mw.source is not None:
            def frame_provider(idx):
                raw = mw.source.get(idx)
                if raw is None:
                    return None
                return mw.process(mw._apply_rotation(raw))

        try:
            counts = export_yolo(
                annots, list(annots.keys()), w, h, out_dir,
                class_names=self._class_names(), ratios=ratios, seed=0,
                include_empty=self._empty_chk.isChecked(),
                frame_provider=frame_provider)
        except Exception as ex:
            self._status.setText(f"Erreur export YOLO : {ex}")
            return
        self._status.setText(
            f"Dataset YOLO écrit → {out_dir}  "
            f"(train={counts['train']}, val={counts['val']}, test={counts['test']})")

    def _do_yolo2ver(self):
        src = self._yolo_src_edit.text().strip()
        if not src or not os.path.exists(src):
            self._status.setText("Choisis un dossier ou fichier YOLO source valide.")
            return
        out_path = self._out_ver_edit.text().strip()
        if not out_path:
            self._status.setText("Choisis un chemin de sortie .ver.")
            return
        w, h = self._sp_w.value(), self._sp_h.value()
        try:
            n_boxes, n_frames = yolo_to_ver(
                src, out_path, w, h, class_names=self._class_names())
        except Exception as ex:
            self._status.setText(f"Erreur import YOLO : {ex}")
            return
        if n_boxes == 0:
            self._status.setText(
                f"Aucune boîte trouvée dans {src} -- .ver non écrit (ou vide).")
            return
        self._status.setText(
            f".ver écrit → {out_path}  ({n_boxes} boîte(s) sur {n_frames} frame(s))")
