from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from frameviewer.core.annotation_convert import (
    class_names_from_annotations,
    export_yolo,
    split_frame_ids,
    yolo_to_ver,
    yolo_to_ver_lines,
)
from frameviewer.core.annotation_loader import (
    _yolo_norm_to_pixel,
    is_annotation_path,
    load_annotations,
    scan_dropped_folder,
)


def test_demo_annotation_formats_and_index_bases(data_dir):
    folder = load_annotations(data_dir / "annotations_yolo", 1920, 1080)
    assert len(folder) == 10
    assert folder["frame_05000"][0][6] == ("vehicle",)
    merged = load_annotations(data_dir / "merged_yolo.txt", 1920, 1080)
    assert sorted(merged) == list(range(10))
    tracked = load_annotations(data_dir / "example.ver", 1920, 1080)
    assert sorted(tracked) == [0, 1, 2]
    assert tracked[0][0][5:] == (1, ("vehicle", "moving"))


def test_annotation_path_detection_and_non_recursive_scan(tmp_path):
    image = tmp_path / "frame_2.png"
    image.write_bytes(b"placeholder")
    label = tmp_path / "frame_2.txt"
    label.write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
    track = tmp_path / "track.ver"
    track.write_text("1 1 0 0 1 1\n", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "frame_1.txt").write_text("x", encoding="utf-8")
    images, labels, tracks = scan_dropped_folder(tmp_path)
    assert images == [str(image)]
    assert labels == [str(label)]
    assert tracks == [str(track)]
    assert is_annotation_path(label)
    assert is_annotation_path(track)
    assert is_annotation_path(nested)
    assert not is_annotation_path(tmp_path / "unknown.csv")


@pytest.mark.parametrize(
    ("coords", "expected"),
    [((0.5, 0.5, 0.2, 0.4, 100, 50), (40, 15, 60, 35)),
     ((0.5, 0.5, 0.2, 0.4, 0, 50), (0, 0, 1, 1))],
)
def test_normalized_box_conversion(coords, expected):
    assert _yolo_norm_to_pixel(*coords) == expected


def test_folder_labels_use_stems_and_skip_empty_files(tmp_path):
    (tmp_path / "classes.txt").write_text("vehicle\nperson\n", encoding="utf-8")
    (tmp_path / "camera_100.txt").write_text("1 0.5 0.5 0.2 0.2\n", encoding="utf-8")
    (tmp_path / "camera_500.txt").write_text("", encoding="utf-8")
    loaded = load_annotations(tmp_path, 100, 100)
    assert list(loaded) == ["camera_100"]
    assert loaded["camera_100"][0] == (1, 40, 40, 60, 60, 0, ("person",))


def test_malformed_annotation_rows_are_ignored(tmp_path):
    path = tmp_path / "merged.txt"
    path.write_text(
        "# comment\ninvalid\n0 0 0.5 0.5 0.2 0.2\n1 x 0 0 0 0\n",
        encoding="utf-8",
    )
    loaded = load_annotations(path, 100, 100)
    assert list(loaded) == [0]
    assert len(loaded[0]) == 1


def test_split_is_disjoint_complete_and_seeded():
    ids = list(range(20)) + [5, 5]
    first = split_frame_ids(ids, (0.6, 0.2, 0.2), seed=17)
    second = split_frame_ids(ids, (6, 2, 2), seed=17)
    assert first == second
    sets = [set(first[name]) for name in ("train", "val", "test")]
    assert set.union(*sets) == set(range(20))
    assert all(sets[i].isdisjoint(sets[j]) for i in range(3) for j in range(i + 1, 3))


def test_class_names_prefer_labels_and_fill_gaps():
    annotations = {
        0: [(2, 0, 0, 1, 1, 0, ("vehicle",))],
        1: [(0, 0, 0, 1, 1, 0, ())],
    }
    assert class_names_from_annotations(annotations) == ["class 0", "class 1", "vehicle"]


def test_export_yolo_writes_labels_images_yaml_and_progress(tmp_path):
    annotations = {
        0: [(0, 10, 20, 30, 40, 7, ("vehicle",))],
        2: [(0, 0, 0, 100, 50, 8, ("vehicle",))],
    }
    progress = []

    def frame_provider(index):
        return np.full((50, 100, 3), index * 20, np.uint8)

    counts = export_yolo(
        annotations, [0, 1, 2], 100, 50, tmp_path,
        class_names=["vehicle"], ratios=(1, 0, 0), frame_provider=frame_provider,
        progress_cb=lambda done, total: progress.append((done, total)),
    )
    assert counts == {"train": 3, "val": 0, "test": 0}
    assert (tmp_path / "labels" / "train" / "frame_000001.txt").read_text() == ""
    assert (tmp_path / "images" / "train" / "frame_000002.png").is_file()
    yaml = (tmp_path / "data.yaml").read_text(encoding="utf-8")
    assert "nc: 1" in yaml and "vehicle" in yaml
    assert progress[-1] == (3, 3)


def test_export_rejects_invalid_dimensions(tmp_path):
    with pytest.raises(ValueError, match="Dimensions"):
        export_yolo({}, [0], 0, 10, tmp_path)


def test_yolo_track_export_is_one_based_and_positionally_stable(tmp_path):
    annotations = {
        0: [(0, 1, 2, 3, 4, 99, ("vehicle",)), (0, 5, 6, 7, 8, 98, ("vehicle",))],
        2: [(0, 9, 10, 11, 12, 97, ("vehicle",))],
    }
    lines = yolo_to_ver_lines(annotations, class_names=["vehicle"])
    assert lines[0].split("\t")[0] == "1.000000e+00"
    assert lines[0].split("\t")[6] == "0.000000e+00"
    assert lines[1].split("\t")[6] == "1.000000e+00"
    merged = tmp_path / "labels.txt"
    merged.write_text("0 0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
    output = tmp_path / "converted.ver"
    boxes, frames = yolo_to_ver(merged, output, 100, 100, ["vehicle"])
    assert (boxes, frames) == (1, 1)
    assert output.is_file()
