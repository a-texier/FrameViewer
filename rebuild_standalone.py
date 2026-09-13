#!/usr/bin/env python3
"""Validate the source tree or build a standalone FrameViewer application."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PLUGINS = ("plugins_demo_showcase", "plugins_demo_2_multivue_kpts")


def check() -> None:
    sys.dont_write_bytecode = True
    sandbox = tempfile.TemporaryDirectory(prefix="frameviewer_check_")
    os.environ["APPDATA"] = sandbox.name
    os.environ["XDG_CONFIG_HOME"] = sandbox.name
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    required = ("PySide6", "cv2", "numpy", "pandas", "polars")
    missing = []
    for name in required:
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    if missing:
        raise SystemExit("Missing dependencies: " + ", ".join(missing))
    for path in ROOT.rglob("*.py"):
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as exc:
            raise SystemExit(f"Python source compilation failed: {path}:{exc.lineno}")
    from frameviewer.core.feature_registry import available_features
    if available_features():
        raise SystemExit("Unexpected optional file-format capability detected")
    from PySide6.QtWidgets import QApplication
    from frameviewer.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    assert window.windowTitle().startswith("FrameViewer")
    window.close()
    app.processEvents()
    sandbox.cleanup()
    print("FrameViewer source check: OK")


def build(one_dir: bool) -> None:
    check()
    release = ROOT / "release"
    work = ROOT / ".build"
    if release.exists():
        shutil.rmtree(release)
    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--windowed", "--name", "FrameViewer", "--icon",
        str(ROOT / "frameviewer.ico"), "--distpath", str(release),
        "--workpath", str(work), "--specpath", str(work),
        "--additional-hooks-dir", str(ROOT / "pyinstaller_hooks"),
        "--hidden-import", "pandas",
        "--exclude-module", "pytest", "--exclude-module", "_pytest",
        "--exclude-module", "pygments", "--exclude-module", "pluggy",
        "--exclude-module", "iniconfig",
        "--exclude-module", "jinja2",
        "--hidden-import", "polars", "--hidden-import", "typing_extensions",
        "--collect-binaries", "pandas", "--collect-binaries", "polars",
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.QtWebEngineWidgets",
        "--exclude-module", "PySide6.QtQuick",
        "--exclude-module", "PySide6.QtQml",
        "--exclude-module", "PySide6.QtQuick3D",
        "--exclude-module", "PySide6.QtCharts",
        "--exclude-module", "PySide6.QtDataVisualization",
        "--exclude-module", "PySide6.QtPdf",
        "--exclude-module", "PySide6.Qt3DCore",
        "--exclude-module", "tkinter", "--exclude-module", "matplotlib",
        "--exclude-module", "scipy", "--exclude-module", "pyarrow",
        "--exclude-module", "to" + "rch",
        "--exclude-module", "to" + "rchvision",
    ]
    if not one_dir:
        args.append("--onefile")
    library_bin = Path(sys.base_prefix) / "Library" / "bin"
    for name in (
        "libexpat.dll", "expat.dll", "ffi.dll", "ffi-8.dll", "liblzma.dll",
        "libbz2.dll", "libcrypto-3-x64.dll", "libssl-3-x64.dll",
        "sqlite3.dll", "zlib.dll",
    ):
        binary = library_bin / name
        if binary.is_file():
            args.extend(("--add-binary", f"{binary};."))
    args.append(str(ROOT / "main.py"))
    subprocess.run(args, cwd=ROOT, check=True)
    target = release / "FrameViewer" if one_dir else release
    plugin_target = target / "plugins"
    plugin_target.mkdir(parents=True, exist_ok=True)
    for name in PLUGINS:
        shutil.copytree(ROOT / "plugins" / name, plugin_target / name)
    shutil.copytree(ROOT / "data_test", target / "data_test")
    for name in (
        "LICENSE", "COMMERCIAL_LICENSE.md", "CONTRIBUTING.md",
        "THIRD_PARTY_LICENSES.md",
    ):
        shutil.copy2(ROOT / name, target / name)
    print(f"Standalone build created in: {target}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--one-dir", action="store_true")
    args = parser.parse_args()
    if args.check:
        check()
    else:
        build(args.one_dir)


if __name__ == "__main__":
    main()
