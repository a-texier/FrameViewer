from __future__ import annotations

import tutorial_multiview_test as scenario_tests


def test_tutorial_persistence_spotlight_keyboard_and_resume(qapp):
    scenario_tests.test_tutorial_state(qapp)


def test_tutorial_complete_real_action_path_and_csv_drop_regression(qapp):
    scenario_tests.test_tutorial_action_path(qapp)
