#!/usr/bin/env python3
"""Run the complete public FrameViewer verification chain."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def run(*args: str) -> None:
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    print("\n$", " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, env=env, check=True)


def main() -> None:
    run(sys.executable, "-m", "pytest", "frameviewer/test_pytest", "-ra")
    run(sys.executable, "tutorial_multiview_test.py")
    checker = ROOT / "rebuild_standalone.py"
    if checker.is_file():
        run(sys.executable, str(checker), "--check")
    print("\nFRAMEVIEWER TEST CHAIN OK")


if __name__ == "__main__":
    main()
