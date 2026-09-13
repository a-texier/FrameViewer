# FrameViewer

FrameViewer is a local desktop application for inspecting image sequences,
videos and raw YUV streams. It provides synchronized multi-view navigation,
contrast and LUT controls, annotations, regions of interest, frame extraction,
conversion tools and a documented Python plugin API.

![FrameViewer](logo_frameviewer.png)

## Run from source

Requirements: Windows or Linux, Python 3.12 and a working C/C++ runtime for
the binary Python wheels.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Linux:

```bash
python3.12 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python main.py
```

Files and folders can be opened from the toolbar, by drag and drop, or as a
positional command-line argument.

## Interactive tutorial

`Tutoriel`, the first toolbar button, starts a 40-step action-driven tour using
the included RGB, thermal, YOLO and multi-view CSV examples. The seed data is
copied to the current user's writable configuration directory before each run.
The orange reminder disappears only after the final step and the tutorial
remains available afterward. Demonstration plugins always start inactive and
their CSV inputs must be supplied explicitly by drag and drop; neither their
activation nor their input paths are persisted.

The `Lier` dialog assigns one starting frame to every visible view. Linked
navigation preserves these offsets during scrubbing, stepping and playback,
and synchronizes zoom and pan. Examples: `[10, 0]` for stacked views and
`[17, 8, 0, 0]` for a 2x2 grid.

## Rebuild the standalone application

The published repository intentionally contains no executable and no vendored
virtual environment. Install the requirements, then run:

```powershell
.\.venv\Scripts\python.exe rebuild_standalone.py
```

The generated application and the two demonstration plugins are written to
`release/`. Use `python rebuild_standalone.py --check` for a fast environment
and source validation without creating an executable.

## Plugins included

- `plugins_demo_showcase`: overview of the public plugin API.
- `plugins_demo_2_multivue_kpts`: synchronized multi-view keypoint example.

Each plugin is loaded from the `plugins/` directory next to the source tree or
next to the standalone executable. See `plugins/PLUGIN_API.md` for the API.

## Repository contents

- `frameviewer/core`: media sources and image processing.
- `frameviewer/workers`: background loading and conversion tasks.
- `frameviewer/ui`: PySide6 interface.
- `frameviewer/plugins`: plugin loader and public API.
- `frameviewer/test_pytest`: themed backend, frontend and integration tests.
- `plugins`: the two demonstration plugins.
- `data_test`: writable tutorial seeds (RGB, IR, annotations and plugin CSVs).
- `rebuild_standalone.py`: reproducible PyInstaller build entry point.

## Tests

```powershell
python -m pip install -r requirements-dev.txt
python run_all_tests.py
python rebuild_standalone.py --check
```
