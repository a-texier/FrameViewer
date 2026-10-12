# -*- coding: utf-8 -*-
"""Theme sombre / clair applique a toute l'interface.

L'interface a ete dessinee en sombre et beaucoup de widgets portent leurs
couleurs en dur (feuilles de style locales, dessin QPainter). Plutot que de
dupliquer chaque couleur, le theme clair est obtenu en convertissant chaque
couleur selon son ROLE :

- gris (fonds, bordures, textes) : luminosite inversee -> fond sombre devient
  clair, texte clair devient fonce ;
- fond colore « accent » (onglet selectionne, bouton coche) : conserve, ainsi
  que le texte pose dessus (deja contraste) ;
- fond colore tres sombre (teinte de surface) : devient une teinte claire ;
- texte / trait colore trop clair (bleu, orange, vert sur fond sombre) : fonce
  pour rester lisible sur fond clair.

``ThemeController`` applique la conversion a toutes les feuilles de style
locales, y compris celles posees apres coup (evenement StyleChange).
Les dessins faits a la main passent par ``ui_color`` ; ce qui est dessine SUR
l'image (ROI, marqueurs, calques) garde ses couleurs.
"""

import colorsys
import re

from PySide6 import QtCore, QtGui, QtWidgets

from frameviewer.ui.theme import THEMES

_current = "dark"

_HEX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9a-fA-F])")
_RGB = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(,\s*[\d.]+\s*)?\)")
_BLOCK = re.compile(r"([^{}]*)\{([^{}]*)\}")
_STYLE_ATTR = re.compile(r'style="([^"]*)"')

# chroma (max - min des canaux, 0..1) sous laquelle une couleur est un gris ;
# la saturation HLS, elle, gonfle pres du blanc et du noir
_GRAY_CHROMA = 0.10
# luminosite max d'un texte colore sur fond clair
_FG_MAX_L = 0.40


def current():
    return _current


def is_light():
    return _current == "light"


# --- conversion d'une couleur ---------------------------------------------------


def _to_light(rgb, role):
    r, g, b = (c / 255.0 for c in rgb)
    h, lum, sat = colorsys.rgb_to_hls(r, g, b)
    if max(r, g, b) - min(r, g, b) < _GRAY_CHROMA:
        lum = min(0.97, max(0.12, 1.0 - lum))
    elif role == "bg":
        if lum < 0.22:
            # surface teintee tres sombre -> teinte claire de meme famille
            lum, sat = 1.0 - lum * 0.6, min(sat, 0.45)
        else:
            return rgb  # vrai fond d'accent : conserve
    elif lum > _FG_MAX_L:
        lum = _FG_MAX_L
    r, g, b = colorsys.hls_to_rgb(h, lum, sat)
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))


def convert_rgb(rgb, role="fg"):
    """(r, g, b) pour le theme courant (identite en sombre)."""
    return _to_light(rgb, role) if is_light() else tuple(rgb)


def ui_color(r, g, b, a=255, role="bg"):
    """QColor d'interface (fond, grille, texte de graphe) adaptee au theme."""
    rr, gg, bb = convert_rgb((r, g, b), role)
    return QtGui.QColor(rr, gg, bb, a)


def _hex_rgb(text):
    text = text.lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def _convert_value(value, role):
    def hex_sub(m):
        r, g, b = _to_light(_hex_rgb(m.group(0)), role)
        return f"#{r:02x}{g:02x}{b:02x}"

    def rgb_sub(m):
        r, g, b = _to_light((int(m.group(1)), int(m.group(2)), int(m.group(3))), role)
        alpha = m.group(4)
        return f"rgba({r},{g},{b}{alpha})" if alpha else f"rgb({r},{g},{b})"

    return _RGB.sub(rgb_sub, _HEX.sub(hex_sub, value))


def _role(prop):
    prop = prop.strip().lower()
    if "background" in prop or prop in ("gridline-color",):
        return "bg"
    if prop.startswith("border") or prop.startswith("outline"):
        return "border"
    return "fg"


def _is_accent_background(decls):
    for part in decls.split(";"):
        prop, sep, value = part.partition(":")
        if not sep or _role(prop) != "bg":
            continue
        m = _HEX.search(value) or _RGB.search(value)
        if m is None:
            continue
        rgb = (
            _hex_rgb(m.group(0))
            if m.re is _HEX
            else (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        )
        chans = [c / 255.0 for c in rgb]
        lum = colorsys.rgb_to_hls(*chans)[1]
        if max(chans) - min(chans) >= _GRAY_CHROMA and lum >= 0.22:
            return True
    return False


def _convert_decls(decls):
    keep_text = _is_accent_background(decls)
    out = []
    for part in decls.split(";"):
        prop, sep, value = part.partition(":")
        if not sep:
            out.append(part)
            continue
        role = _role(prop)
        if role == "fg" and keep_text:
            out.append(part)  # texte sur fond d'accent conserve : deja lisible
            continue
        out.append(f"{prop}:{_convert_value(value, role)}")
    return ";".join(out)


def light_qss(sheet):
    """Feuille de style sombre -> equivalent clair (bloc par bloc)."""
    if "{" not in sheet:
        return _convert_decls(sheet)
    return _BLOCK.sub(lambda m: f"{m.group(1)}{{{_convert_decls(m.group(2))}}}", sheet)


def themed_qss(sheet):
    return light_qss(sheet) if is_light() and sheet else sheet


def themed_html(html):
    """Couleurs des attributs style="..." d'un texte HTML (aide, console)."""
    if not is_light() or not html:
        return html
    return _STYLE_ATTR.sub(lambda m: f'style="{_convert_decls(m.group(1))}"', html)


# --- application a l'interface -----------------------------------------------------

_SOURCE = "_fv_qss_source"
_APPLIED = "_fv_qss_applied"


class ThemeController(QtCore.QObject):
    """Convertit les feuilles de style locales au theme courant, y compris
    celles posees apres le changement de theme."""

    def __init__(self, app):
        super().__init__(app)
        self._app = app
        self._busy = False
        app.installEventFilter(self)

    def apply(self, name):
        global _current
        _current = name if name in THEMES else "dark"
        self._app.setStyleSheet(THEMES[_current])
        for w in self._app.allWidgets():
            self._restyle(w, force=True)
            QtWidgets.QWidget.update(w)  # les vues item surchargent update(index)

    def eventFilter(self, obj, ev):
        if (
            not self._busy
            and ev.type() == QtCore.QEvent.StyleChange
            and isinstance(obj, QtWidgets.QWidget)
        ):
            self._restyle(obj)
        return False

    def _restyle(self, w, force=False):
        sheet = w.styleSheet()
        applied = w.property(_APPLIED)
        if sheet and sheet != applied:
            w.setProperty(_SOURCE, sheet)  # feuille posee par le code
        source = w.property(_SOURCE)
        if not source or (not force and sheet == applied):
            return
        target = themed_qss(source)
        w.setProperty(_APPLIED, target)
        if target != sheet:
            self._busy = True
            try:
                w.setStyleSheet(target)
            finally:
                self._busy = False


_controller = None


def apply_theme(name):
    """Applique le theme ``name`` ("dark" | "light") a toute l'application."""
    global _controller
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    if _controller is None or _controller.parent() is not app:
        _controller = ThemeController(app)
    _controller.apply(name)
