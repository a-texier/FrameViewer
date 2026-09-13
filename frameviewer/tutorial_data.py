"""Locate and stage the read-only tutorial assets in a writable workspace."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from frameviewer.plugins.loader import user_config_dir


def bundled_data_test_dir() -> Path:
    """Return the first valid data_test directory for source and frozen runs."""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "data_test")
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if bundle_dir:
            candidates.append(Path(bundle_dir) / "data_test")
    candidates.append(Path(__file__).resolve().parents[1] / "data_test")
    for candidate in candidates:
        if (candidate / "traffic_rgb").is_dir() and (candidate / "traffic_ir").is_dir():
            return candidate
    raise FileNotFoundError(
        "Les donnees du tutoriel sont absentes. Le dossier data_test doit etre "
        "place a cote de l'application."
    )


def stage_tutorial_data() -> Path:
    """Create a fresh, writable tutorial workspace and return its path."""
    source = bundled_data_test_dir()
    destination = Path(user_config_dir()) / "tutorial" / "v1" / "workspace"
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    return destination


def is_tutorial_source(source, expected_folder: str) -> bool:
    """Check a loaded source without depending on a source implementation."""
    if source is None:
        return False
    folder = getattr(source, "directory", None) or getattr(source, "_dir", None)
    if not folder:
        paths = getattr(source, "paths", None) or []
        folder = os.path.dirname(paths[0]) if paths else ""
    return Path(folder).name.casefold() == expected_folder.casefold()
