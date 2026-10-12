"""Exports non bloquants : etat fige, dialogue non modal, annulation."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6 import QtTest

from frameviewer.core.export_formats import ExportSpec
from frameviewer.core.sources import ImageSequenceSource
from frameviewer.ui.export_jobs import ExportSnapshot, JobRunner, run_sync


def _sequence(folder, count, value_step):
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for index in range(count):
        path = folder / f"f_{index:02d}.png"
        cv2.imwrite(str(path), np.full((12, 16), index * value_step, np.uint8))
        paths.append(str(path))
    return ImageSequenceSource(paths, label=folder.name, directory=str(folder))


def _window():
    from frameviewer.ui.main_window import MainWindow

    return MainWindow()


def test_snapshot_keeps_the_view_state_of_launch_time(qapp, tmp_path):
    window = _window()
    try:
        window.set_source(_sequence(tmp_path / "a", 3, 50))
        window.hist.set_window(0, 255, emit=False)
        snap = ExportSnapshot.from_engine(window)
        assert snap.reader is not window.source  # lecteur propre a l'export
        window.set_source(_sequence(tmp_path / "b", 3, 10))  # on change de fichier
        window.hist.set_window(0, 10, emit=False)  # et de contraste
        assert int(snap.raw(2)[0, 0]) == 100
        assert int(snap.rendered(2)[0, 0, 0]) == 100
        snap.close()
    finally:
        window.close()


def test_snapshot_converts_display_rect_to_native_pixels(qapp, tmp_path):
    window = _window()
    try:
        window.set_source(_sequence(tmp_path / "a", 2, 50))
        window._fetch_scale = 0.5
        snap = ExportSnapshot.from_engine(window)
        assert snap.native_rect((2, 3, 4, 5)) == (4, 6, 8, 10)
        snap.close()
    finally:
        window.close()


def test_export_dialog_is_not_modal_and_writes_in_background(qapp, tmp_path):
    from frameviewer.ui.export_dialogs import ClipExtractDialog

    window = _window()
    try:
        window.set_source(_sequence(tmp_path / "a", 4, 40))
        dialog = ClipExtractDialog(window, 0, 3, has_in_out=False)
        window._show_export_dialog(dialog)
        assert not dialog.isModal()
        out = tmp_path / "png"
        out.mkdir()
        done = QtTest.QSignalSpy(dialog._runner.finished)
        dialog._launch(dialog._job_write(ExportSpec(fmt="png"), str(out), range(4)))
        assert dialog.is_busy()
        window.show_index(1)  # la fenetre principale reste utilisable
        assert done.wait(10000)
        assert len(list(out.glob("*.png"))) == 4
    finally:
        window.close()


def test_cancelled_job_runs_its_cleanup():
    closed = []

    def job():
        try:
            while True:
                yield
        finally:
            closed.append(True)

    runner = JobRunner()
    gen = job()
    next(gen)  # export demarre (fichier ouvert) avant l'annulation
    runner.start(gen)
    assert runner.busy()
    runner.cancel()
    assert not runner.busy() and closed == [True]


def test_run_sync_drives_a_generator_to_the_end():
    seen = []

    def job():
        for i in range(3):
            seen.append(i)
            yield

    run_sync(job())
    assert seen == [0, 1, 2]


def test_multiview_composition_uses_frozen_views(qapp, tmp_path):
    window = _window()
    try:
        window.show()
        window.set_source(_sequence(tmp_path / "a", 3, 40))
        window._set_split("h2")
        sat = window._split_canvas.satellites()[0]
        sat.load(_sequence(tmp_path / "b", 3, 80), 0, 255, None, None, 0, (0, 255), {})
        plan = window._composition_plan()
        window.set_source(_sequence(tmp_path / "c", 3, 1))  # change pendant l'export
        frame = window._compose_plan_frame(plan, 2)
        assert frame.shape == (12, 32, 3)
        assert int(frame[0, 0, 0]) == 80 and int(frame[0, 20, 0]) == 160
        out = tmp_path / "mv.mp4"

        class _Progress:
            def setValue(self, _v):
                pass

        spec = ExportSpec(fmt="mp4", fps=10.0)
        run_sync(window._job_compose(plan, str(out), spec, 3, _Progress()))
        window._close_plan(plan)
        assert out.stat().st_size > 0
    finally:
        window.close()
