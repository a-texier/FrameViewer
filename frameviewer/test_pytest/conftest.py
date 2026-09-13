from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from PySide6 import QtCore, QtWidgets


@pytest.fixture(scope="session")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setApplicationName("FrameViewerTests")
    yield app
    for widget in app.topLevelWidgets():
        widget.close()
    app.processEvents()


@pytest.fixture(scope="session")
def app(qapp):
    return qapp


@pytest.fixture
def data_dir():
    return ROOT / "data_test"


@pytest.fixture(autouse=True)
def isolated_user_config(tmp_path, monkeypatch, qapp):
    config = tmp_path / "user_config"
    config.mkdir()
    monkeypatch.setenv("APPDATA", str(config))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    yield config
    for widget in qapp.topLevelWidgets():
        widget.close()
    qapp.processEvents()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    qapp.processEvents()
