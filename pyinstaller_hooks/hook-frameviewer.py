"""Collect FrameViewer runtime modules while keeping tests source-only."""

from PyInstaller.utils.hooks import collect_submodules


hiddenimports = collect_submodules(
    "frameviewer",
    filter=lambda name: not name.startswith("frameviewer.test_pytest"),
)
