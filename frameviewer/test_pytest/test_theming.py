"""Conversion sombre -> clair des couleurs d'interface."""

from __future__ import annotations

import colorsys

from frameviewer.ui import theming


def _lum(hex_color):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return colorsys.rgb_to_hls(r, g, b)[1]


def test_dark_surfaces_and_light_text_swap_in_light_theme():
    out = theming.light_qss("background:#141418; color:#cdd6e0; border:1px solid #3a3a42;")
    bg = out.split("background:")[1][:7]
    fg = out.split("color:")[1][:7]
    assert _lum(bg) > 0.85 and _lum(fg) < 0.25


def test_accent_background_keeps_its_text():
    sheet = "QTabBar::tab:selected{background:#2d4f70;color:#fff;}"
    assert theming.light_qss(sheet) == sheet


def test_light_accent_text_is_darkened_for_contrast():
    out = theming.light_qss("QLabel{color:#5aafff;}")
    assert _lum(out.split("color:")[1][:7]) <= 0.41


def test_html_style_attributes_follow_the_theme(monkeypatch):
    monkeypatch.setattr(theming, "_current", "light")
    html = '<td style="color:#dddde3;">x</td>'
    assert "#dddde3" not in theming.themed_html(html)
    monkeypatch.setattr(theming, "_current", "dark")
    assert theming.themed_html(html) == html


def test_ui_color_is_identity_in_dark(monkeypatch):
    monkeypatch.setattr(theming, "_current", "dark")
    assert theming.ui_color(20, 20, 22).getRgb()[:3] == (20, 20, 22)
    monkeypatch.setattr(theming, "_current", "light")
    assert theming.ui_color(20, 20, 22).lightness() > 200
