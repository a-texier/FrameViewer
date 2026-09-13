from __future__ import annotations

import csv
from pathlib import Path

from frameviewer.tutorial_data import bundled_data_test_dir, stage_tutorial_data


ROOT = Path(__file__).resolve().parents[2]


def test_public_runtime_modules_compile():
    for path in (ROOT / "frameviewer").rglob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_required_demo_assets_are_complete_and_paired(data_dir):
    rgb = sorted((data_dir / "traffic_rgb").glob("*.png"))
    thermal = sorted((data_dir / "traffic_ir").glob("*.png"))
    labels = sorted((data_dir / "annotations_yolo").glob("frame_*.txt"))
    assert len(rgb) == len(thermal) == len(labels) == 10
    assert [path.stem for path in rgb] == [path.stem for path in thermal] == [path.stem for path in labels]
    assert (data_dir / "ATTRIBUTION.md").is_file()
    assert bundled_data_test_dir().resolve() == data_dir.resolve()


def test_multiview_demo_csv_has_real_rows_for_every_frame(data_dir):
    with (data_dir / "multiview_keypoints.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 600
    frames = {int(row["frame_top"]) for row in rows}
    assert frames == set(range(10))
    assert all(0.0 <= float(row["confidence"]) <= 1.0 for row in rows)


def test_showcase_csv_counts_match_label_files(data_dir):
    with (data_dir / "showcase_boxes.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        rows = list(csv.DictReader(stream))
    by_frame = {}
    for row in rows:
        by_frame.setdefault(int(row["frame"]), 0)
        by_frame[int(row["frame"])] += 1
    for index, label_path in enumerate(sorted((data_dir / "annotations_yolo").glob("frame_*.txt"))):
        expected = sum(
            1 for line in label_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        assert by_frame.get(index, 0) == expected


def test_tutorial_staging_is_fresh_writable_and_does_not_modify_seed(isolated_user_config, data_dir):
    seed = data_dir / "traffic_rgb" / "frame_05000.png"
    original = seed.read_bytes()
    first = stage_tutorial_data()
    marker = first / "created_by_test.txt"
    marker.write_text("writable", encoding="utf-8")
    assert marker.is_file()
    second = stage_tutorial_data()
    assert second == first and not marker.exists()
    assert seed.read_bytes() == original


def test_public_test_package_does_not_vendor_generated_runtime_artifacts():
    excluded_dirs = {"node_modules", ".venv", ".venv_build", "release", ".build"}
    excluded_suffixes = {".exe", ".dll", ".pyd", ".so", ".dylib", ".pt", ".pth", ".onnx"}
    test_root = ROOT / "frameviewer" / "test_pytest"
    for path in test_root.rglob("*"):
        relative = path.relative_to(test_root)
        assert not any(part in excluded_dirs for part in relative.parts)
        if path.is_file():
            assert path.suffix.lower() not in excluded_suffixes
