# -*- coding: utf-8 -*-
"""Console plugins : fenetre non-modale qui affiche le journal des plugins
(messages api.log(), print() captures, tracebacks, comptes rendus de test) et
donne de quoi debugger vite depuis l'app :
  - Tester le plugin selectionne sur la frame courante (sans l'activer) ;
  - Diagnostic environnement (ou numpy/pandas/cv2 sont reellement charges) ;
  - Exporter la frame courante + la commande VS Code pour un debug pas-a-pas.

Le journal vit sur le loader (self._mw._plugin_loader) ; un timer relit ses
lignes pour l'affichage -- c'est le seul timer ici, sans rapport avec le
rechargement des plugins."""
import html
import os

from PySide6 import QtCore, QtGui, QtWidgets

# couleur stable par plugin (le nom -> une teinte de la palette).
_PALETTE = ["#5aafff", "#3ad07a", "#ffb454", "#c58aff", "#ff8aa0",
            "#8ad0c0", "#e6c84f", "#7fb5ff", "#f6a1d0", "#9ad06a"]
_LEVEL_COLOR = {"info": "#cdd6e0", "ok": "#3ad07a", "warn": "#ffb454", "error": "#ff6b6b"}
_HELP_BUTTON_SS = (
    "QToolButton{min-width:28px;min-height:28px;border:1px solid #5aafff;"
    "border-radius:4px;background:#1769aa;color:white;font-weight:bold;}"
    "QToolButton:hover{background:#2185d0;border-color:#8ac7ff;}"
    "QToolButton:pressed{background:#125789;}")


def _pid_color(pid):
    if not pid:
        return "#9aa4ad"
    h = 0
    for c in pid:
        h = (h * 31 + ord(c)) & 0xffffffff
    return _PALETTE[h % len(_PALETTE)]


def _record_html(r):
    """Une ligne coloree : heure [plugin] [Lnn] - message. Plugin colore (stable
    par nom), message colore selon le niveau (warn/error en gras)."""
    lvl = r.get("level", "info")
    txt_c = _LEVEL_COLOR.get(lvl, "#cdd6e0")
    weight = "font-weight:bold;" if lvl in ("warn", "error") else ""
    ts = f"<span style='color:#7c8794'>{r['ts']}</span>"
    tag = ""
    if r.get("pid"):
        tag = (f" <span style='color:{_pid_color(r['pid'])};font-weight:bold'>"
               f"[{html.escape(str(r['pid']))}]</span>")
    ln = ""
    if r.get("lineno"):
        loc = f"{html.escape(str(r['src']))}:{r['lineno']}" if r.get("src") else f"L{r['lineno']}"
        ln = f" <span style='color:#6b7580'>[{loc}]</span>"
    body = html.escape(str(r.get("text", ""))).replace("\n", "<br>&nbsp;&nbsp;&nbsp;")
    return f"{ts}{tag}{ln} <span style='color:{txt_c};{weight}'>- {body}</span>"


class PluginConsoleDialog(QtWidgets.QDialog):

    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw
        self.setWindowTitle("Console plugins")
        self.resize(780, 520)
        self.setModal(False)

        v = QtWidgets.QVBoxLayout(self)
        info = QtWidgets.QLabel(
            "Journal des plugins : api.log(), print(), erreurs et tests. Pour un "
            "vrai debug pas-a-pas (breakpoints), exporte la frame et lance le "
            "runner sous VS Code (bouton ci-dessous).")
        info.setWordWrap(True)
        v.addWidget(info)

        self._view = QtWidgets.QTextEdit()
        self._view.setReadOnly(True)
        self._view.setStyleSheet("QTextEdit{background:#141414;border:1px solid #333;"
                                 "font-family:Consolas,monospace;font-size:12px;}")
        self._view.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        v.addWidget(self._view, 1)

        row = QtWidgets.QHBoxLayout()
        b_test = QtWidgets.QPushButton("Tester sur la frame courante")
        b_test.setToolTip("Execute les hooks de rendu du plugin selectionne sur "
                          "la frame courante et affiche le resultat/erreurs.")
        b_test.clicked.connect(self._test)
        row.addWidget(b_test)
        b_env = QtWidgets.QPushButton("Diagnostic environnement")
        b_env.setToolTip("Montre d'ou numpy/pandas/cv2 sont reellement charges "
                         "(bundle vs env externe) et les site-packages actifs.")
        b_env.clicked.connect(self._env)
        row.addWidget(b_env)
        b_exp = QtWidgets.QPushButton("Exporter frame + commande VS Code")
        b_exp.setToolTip("Ecrit la frame courante sur disque et donne la commande "
                         "prete a coller pour debugger le plugin dans VS Code.")
        b_exp.clicked.connect(lambda: self._mw._export_current_frame_for_debug())
        row.addWidget(b_exp)
        b_help = QtWidgets.QToolButton()
        b_help.setText("?")
        b_help.setStyleSheet(_HELP_BUTTON_SS)
        b_help.setToolTip("Comprendre les bibliothèques des plugins et les "
                          "méthodes de debug dans FrameViewer ou VS Code.")
        b_help.clicked.connect(self._help)
        row.addWidget(b_help)
        row.addStretch(1)
        self._auto = QtWidgets.QCheckBox("Défilement auto")
        self._auto.setChecked(True)
        row.addWidget(self._auto)
        b_clear = QtWidgets.QPushButton("Effacer")
        b_clear.clicked.connect(self._clear)
        row.addWidget(b_clear)
        v.addLayout(row)

        self._last = 0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(600)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()

    # le timer ne tourne que quand la console est ouverte : fermee, aucun cout
    # (le journal, lui, continue d'etre alimente mais borne cote loader).
    def showEvent(self, e):
        super().showEvent(e)
        if not self._timer.isActive():
            self._timer.start()
        self._refresh()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    def _loader(self):
        return self._mw._plugin_loader

    def _selected_lp(self):
        panel = getattr(self._mw, "_plugins_panel", None)
        return panel.selected_lp() if panel is not None else None

    def _refresh(self):
        recs = self._loader().log_records()
        n = len(recs)
        if n < self._last:            # journal efface -> on repart de zero
            self._view.clear()
            self._last = 0
        if n == self._last:
            return
        cur = self._view.textCursor()
        cur.movePosition(QtGui.QTextCursor.End)
        self._view.setTextCursor(cur)
        for r in recs[self._last:]:   # n'ajoute QUE les nouvelles lignes
            self._view.insertHtml(_record_html(r) + "<br>")
        self._last = n
        if self._auto.isChecked():
            sb = self._view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _clear(self):
        self._loader().clear_log()
        self._refresh()

    def _env(self):
        from frameviewer.plugins.loader import environment_report
        self._loader().log("=== Diagnostic environnement ===\n" + environment_report())
        self._refresh()

    def _help(self):
        """Guide court : choix du mode d'import puis debug app ou VS Code."""
        lp = self._selected_lp()
        plugin = os.path.basename(lp.folder) if lp is not None else None
        cmd_pick = "python -m frameviewer.plugins.debug_runner"
        cmd = cmd_pick + (f" --plugin {plugin}" if plugin else "")
        selected = html.escape(plugin or "<nom_du_plugin>")
        command = html.escape(cmd)
        guide = f"""
        <style>
          body {{ color:#d7dde5; font-family:'Segoe UI',sans-serif; font-size:13px; }}
          h1 {{ color:#73bfff; font-size:20px; margin:0 0 10px 0; }}
          h2 {{ color:#9bcfff; font-size:16px; margin:18px 0 7px 0; }}
          h3 {{ color:#ffffff; font-size:14px; margin:11px 0 4px 0; }}
          p {{ margin:4px 0 8px 0; }} li {{ margin:4px 0; }}
          code {{ color:#a9e5ff; background:#202830; }}
          .solution {{ border-left:3px solid #2185d0; padding-left:10px; margin:9px 0; }}
          .warn {{ color:#ffcc7a; }} .ok {{ color:#74dc9a; }}
        </style>
        <h1>Plugins : fonctionnement et debug</h1>
        <p>Un plugin reçoit la frame courante et ses fichiers d'entrée, puis renvoie
        des overlays, une image de panneau ou des données. Le choix important est
        l'endroit où ses bibliothèques Python s'exécutent.</p>

        <h2>1. Choisir comment charger une bibliothèque</h2>
        <div class="solution"><h3>Solution A — bibliothèque incluse dans l'exe</h3>
        <p><span class="ok">Le plus simple et le plus rapide.</span> L'exe actuel
        contient notamment <b>pandas</b> et <b>polars</b>. Le plugin importe donc
        directement la bibliothèque :</p>
        <p><code>import pandas as pd</code> &nbsp; ou &nbsp; <code>import polars as pl</code></p>
        <p>Exemple : <code>plugins_demo_complet_local</code>.</p></div>

        <div class="solution"><h3>Solution B — même processus avec external_import</h3>
        <p>Pour une bibliothèque absente de l'exe, utilise un <code>site-packages</code>
        provenant d'un Python de même version que FrameViewer, ici <b>Python 3.12</b>.</p>
        <p><code>pd = api.external_import("pandas", r"C:/.../Lib/site-packages")</code></p>
        <p>Rapide, mais les extensions compilées et leurs DLL doivent être compatibles
        avec l'interpréteur et les bibliothèques déjà chargées par l'exe. Exemple :
        <code>plugins_demo_complet_external_import</code>.</p></div>

        <div class="solution"><h3>Solution C — processus isolé avec run_external</h3>
        <p>Pour une bibliothèque complexe, ses DLL, une autre version de Python ou un
        environnement incompatible, lance son propre <code>python.exe</code> et un script
        séparé. Le plugin récupère seulement une petite sortie structurée, typiquement
        du JSON, jamais le CSV complet à chaque frame.</p>
        <p>Exemple : <code>plugins_demo_complet_run_external</code> et son
        <code>external_reader.py</code>. C'est le mode le plus isolé, avec un coût de
        démarrage du sous-processus à chaque appel.</p></div>

        <p class="warn"><b>Attention :</b> Actualiser recharge le code du plugin, mais
        ne redémarre pas Python. Il n'annule ni un chemin ajouté à <code>sys.path</code>,
        ni un module présent dans <code>sys.modules</code>, ni une DLL chargée. Après un
        changement d'environnement ou un import compilé raté, redémarre FrameViewer.</p>

        <h2>2. Debug rapide dans FrameViewer</h2>
        <ul>
          <li>Sélectionne une carte plugin, puis ouvre <b>Console</b>.</li>
          <li>Utilise <b>Tester sur la frame courante</b> pour exécuter ses hooks sans
          devoir l'activer.</li>
          <li>Journalise avec <code>api.log("message")</code>. Les niveaux
          <code>info</code>, <code>ok</code>, <code>warn</code> et <code>error</code>
          ont chacun une couleur.</li>
          <li><b>Diagnostic environnement</b> affiche la version et l'origine réelles
          de numpy, pandas, polars et cv2.</li>
          <li><b>Actualiser</b> reprend les modifications de <code>plugin.py</code>, avec
          la limite <code>sys.path/sys.modules</code> expliquée ci-dessus.</li>
        </ul>

        <h2>3. Debug profond dans VS Code</h2>
        <ul>
          <li>Place-toi sur la frame utile, sélectionne <code>{selected}</code>, puis
          clique <b>Exporter frame + commande VS Code</b>. La frame devient
          <code>&lt;plugin&gt;/debug/frameNNNN.npy</code>.</li>
          <li>Ouvre dans VS Code le dossier source qui contient <code>frameviewer/</code>.</li>
          <li>Dans son terminal, lance : <code>{command}</code>. Ajoute si nécessaire
          <code>--npy chemin.npy --frame-index N --input nom=chemin.csv</code>.</li>
          <li>Avec <code>breakpoint()</code> : <code>n</code> avance, <code>s</code> entre,
          <code>p variable</code> inspecte, <code>c</code> continue.</li>
          <li>Ou pose un breakpoint dans la marge, appuie sur <b>F5</b> et choisis
          <b>Debug plugin (frame courante)</b> dans <code>.vscode/launch.json</code>.</li>
        </ul>
        <p>Les rendus de contrôle sont écrits dans
        <code>&lt;plugin&gt;/debug/out/</code> : <code>overlay.png</code>,
        <code>composited.png</code> et, si disponible, <code>panel.png</code>.</p>
        """

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Debug pas-a-pas d'un plugin")
        v = QtWidgets.QVBoxLayout(dlg)
        view = QtWidgets.QTextBrowser()
        view.setReadOnly(True)
        view.setHtml(guide)
        view.setStyleSheet("QTextBrowser{background:#14181d;border:1px solid #36414c;padding:10px;}")
        v.addWidget(view, 1)
        r = QtWidgets.QHBoxLayout()
        b_copy = QtWidgets.QPushButton("Copier la commande")
        b_copy.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(cmd))
        r.addWidget(b_copy)
        r.addStretch(1)
        b_ok = QtWidgets.QPushButton("Fermer")
        b_ok.clicked.connect(dlg.accept)
        r.addWidget(b_ok)
        v.addLayout(r)
        dlg.resize(780, 680)
        dlg.exec()

    def _test(self):
        lp = self._selected_lp()
        if lp is None:
            self._loader().log("Test : aucun plugin selectionne dans l'onglet Plugins.")
            self._refresh()
            return
        idx = self._mw.cur if self._mw.source is not None else 0
        raw = getattr(self._mw, "_raw", None)
        if raw is not None:
            h, w = raw.shape[:2]
            size = (int(w), int(h))
        else:
            size = (640, 480)
        self._loader().log(self._loader().run_plugin_test(lp.plugin_id, idx, size))
        if getattr(self._mw, "_raw", None) is not None:
            self._mw._display()
        self._refresh()
