# FrameViewer tutorial data

This folder is the read-only tutorial seed. FrameViewer copies it to the
current user's configuration directory before an interactive tutorial starts.

- `traffic_rgb/`: 10 visible frames.
- `traffic_ir/`: 10 corresponding thermal frames.
- `annotations_yolo/`: one YOLO label file per RGB frame and `classes.txt`.
- `merged_yolo.txt`: the same labels in FrameViewer merged-YOLO form.
- `example.ver`: short tracked annotation example.
- `showcase_boxes.csv`: normalized boxes to drag explicitly into the showcase plugin.
- `multiview_keypoints.csv`: precomputed RGB/thermal correspondences for every
  frame pair from 0 through 9, to drag explicitly into the multiview plugin.

The demonstration plugins intentionally start inactive and remember neither
their activation nor these input paths between application sessions.
