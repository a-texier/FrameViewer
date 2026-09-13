# FrameViewer pytest matrix

Run from the project root:

```powershell
python -m pip install -r requirements-dev.txt
python run_all_tests.py
```

The suite uses an offscreen Qt platform and an isolated user configuration for
every test. It never writes into the packaged tutorial data.

| Theme | Test module | Layer | Main coverage |
|---|---|---|---|
| Image core | `test_core_io_pipeline.py` | Backend | Unicode I/O, natural ordering, windowing, LUT, filters, fusion and alpha composition |
| Annotations | `test_annotations.py` | Backend | YOLO folder/merged loading, class names, coordinate conversion, splits and export |
| Sources | `test_sources_workers.py` | Backend | Images, videos, raw YUV, virtual fusion, prefetch and conversion workers |
| Plugins | `test_plugins.py` | Backend/API | Manifests, graph evaluation, API contracts, plugin isolation and real demo CSVs |
| Widgets | `test_ui_widgets.py` | Frontend | Histogram controls, link dialog, drag-and-drop zone lifetime and layout widgets |
| Application | `test_main_window.py` | Frontend/integration | Source loading, layouts, linked offsets, common bounds, view context and plugin state |
| Tutorial | `test_tutorial_flow.py` | Frontend/integration | Persistence, blocking steps, real drops, plugin activation and full guided path |
| Public tree | `test_public_contract.py` | Packaging | Required files, demo assets, source compilation and exclusion of generated binaries |

The legacy smoke script remains an internal full-build check. The public test
package intentionally covers only the generic runtime and public data paths.
