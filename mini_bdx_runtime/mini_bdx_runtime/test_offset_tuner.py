"""
Off-robot tests for offset_tuner.py (pure stdlib, no hardware).

Run from this directory:  python3 test_offset_tuner.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from offset_tuner import (  # noqa: E402
    MAX_STEP_DT, OFFSET_LIMIT, OFFSET_SLEW_RAD_S, STANCE_SLEW_RAD_S, OffsetTuner,
)

DT = 0.02


def _tuner(**offsets):
    live = {"left_knee": 0.1, "right_knee": -0.2}
    live.update(offsets)
    return OffsetTuner(live), live


def _settle(tuner):
    for _ in range(1000):
        tuner.step(DT)
    assert not tuner.moving


def test_edits_are_refused_until_the_duck_holds_its_stance():
    t, live = _tuner()
    assert t.set_target("left_knee", 0.3) is False
    t.step(DT)
    assert live["left_knee"] == 0.1 and not t.dirty


def test_an_edit_reaches_the_motors_gradually_then_exactly():
    t, live = _tuner()
    t.hold()
    assert t.set_target("left_knee", 0.3) is True
    assert live["left_knee"] == 0.1              # nothing moves until the loop steps
    t.step(DT)
    assert abs(live["left_knee"] - (0.1 + OFFSET_SLEW_RAD_S * DT)) < 1e-12
    assert t.moving
    _settle(t)
    assert live["left_knee"] == 0.3              # arrives exactly, no float residue


def test_tuner_mutates_the_hwi_dict_in_place():
    live = {"left_knee": 0.0}
    t = OffsetTuner(live)
    t.hold()
    t.set_target("left_knee", 0.1)
    _settle(t)
    assert live["left_knee"] == 0.1              # same dict object the HWI reads


def test_a_stalled_tick_cannot_become_a_jump():
    t, live = _tuner()
    t.hold()
    t.set_target("left_knee", 0.5)
    t.step(5.0)                                  # e.g. the 0.2 s battery bus handoff
    assert abs((live["left_knee"] - 0.1) - OFFSET_SLEW_RAD_S * MAX_STEP_DT) < 1e-12


def test_bad_values_are_rejected():
    t, live = _tuner()
    t.hold()
    for bad in (float("nan"), float("inf"), -float("inf"), None, "0.1", True, [0.1]):
        assert t.set_target("left_knee", bad) is False, repr(bad)
    assert t.set_target("not_a_joint", 0.1) is False
    assert not t.dirty and "not_a_joint" not in live


def test_targets_clamp_to_the_limit():
    t, _ = _tuner()
    t.hold()
    t.set_target("left_knee", 9.0)
    t.set_target("right_knee", -9.0)
    snap = t.snapshot()["offsets"]
    assert snap["left_knee"] == OFFSET_LIMIT and snap["right_knee"] == -OFFSET_LIMIT


def test_a_saved_offset_beyond_the_limit_is_not_pulled_in():
    # A duck can legitimately carry a big offset from find_soft_offsets. Opening the
    # page must never move that joint, and it must stay reachable.
    t, live = _tuner(left_knee=0.7)
    t.hold()
    t.step(DT)
    assert live["left_knee"] == 0.7 and not t.dirty
    t.set_target("left_knee", 0.6)
    assert t.snapshot()["offsets"]["left_knee"] == 0.6
    t.set_target("left_knee", 9.0)
    assert t.snapshot()["offsets"]["left_knee"] == 0.7


def test_targets_are_rounded_to_a_tenth_of_a_milliradian():
    t, _ = _tuner()
    t.hold()
    t.set_target("left_knee", 0.123456789)
    assert t.snapshot()["offsets"]["left_knee"] == 0.1235


def test_revert_glides_back_to_the_exact_saved_values():
    original = 0.08399999999999999               # a real value from a tuned duck
    t, live = _tuner(left_knee=original)
    t.hold()
    t.set_target("left_knee", 0.3)
    _settle(t)
    assert t.dirty
    assert t.revert() is True
    assert not t.dirty and live["left_knee"] == 0.3   # still has to travel
    _settle(t)
    assert live["left_knee"] == original


def test_revert_is_refused_unless_holding_because_it_moves_motors():
    t, _ = _tuner()
    t.hold()
    t.set_target("left_knee", 0.3)
    t.release()
    assert t.revert() is False
    assert t.dirty


def test_save_persists_targets_not_a_mid_slew_value():
    t, live = _tuner()
    t.hold()
    t.set_target("left_knee", 0.3)
    t.step(DT)                                   # still far from 0.3
    written = {}
    assert t.save(written.update) is True
    assert written["joints_offsets"]["left_knee"] == 0.3
    assert live["left_knee"] != 0.3
    assert not t.dirty


def test_save_leaves_untouched_joints_byte_identical():
    original = 0.08399999999999999
    live = {"left_knee": original, "right_knee": 0, "extra_key_from_old_config": 0.5}
    t = OffsetTuner(live)
    t.hold()
    t.set_target("right_knee", 0.1)
    written = {}
    t.save(written.update)
    assert written["joints_offsets"]["left_knee"] == original
    assert written["joints_offsets"]["extra_key_from_old_config"] == 0.5


def test_a_failed_save_stays_dirty_and_reports_why():
    t, _ = _tuner()
    t.hold()
    t.set_target("left_knee", 0.3)

    def disk_full(_fields):
        raise OSError("No space left on device")

    assert t.save(disk_full) is False
    snap = t.snapshot()
    assert snap["dirty"] is True and "No space left" in snap["error"]
    assert t.save({}.update) is True
    assert t.snapshot()["error"] == ""


def test_a_corrupt_config_file_is_reported_not_raised():
    t, _ = _tuner()

    def corrupt(_fields):
        raise ValueError("Expecting value: line 1 column 1")

    assert t.save(corrupt) is False


def test_release_keeps_unsaved_targets_so_you_can_walk_on_them():
    t, live = _tuner()
    t.hold()
    t.set_target("left_knee", 0.3)
    t.release()
    assert t.holding is False and t.dirty
    _settle(t)                                   # the walk loop keeps stepping
    assert live["left_knee"] == 0.3


def test_glide_is_bounded_per_tick_and_arrives_exactly():
    t, _ = _tuner()
    pose, goal = [0.0, 1.0, -0.3], [0.5, 1.0, -0.8]
    step = STANCE_SLEW_RAD_S * DT
    t.hold()
    for _ in range(1000):
        new = t.glide(pose, goal, DT)
        assert all(abs(n - p) <= step + 1e-12 for n, p in zip(new, pose))
        pose = new
    assert pose == goal


def test_glide_caps_a_stalled_tick():
    t, _ = _tuner()
    t.hold()
    new = t.glide([0.0], [1.0], 5.0)
    assert abs(new[0] - STANCE_SLEW_RAD_S * MAX_STEP_DT) < 1e-12


def test_moving_covers_the_stance_glide():
    t, _ = _tuner()
    t.hold()
    t.glide([0.0], [1.0], DT)
    assert t.snapshot()["moving"] is True
    t.glide([1.0], [1.0], DT)
    assert t.snapshot()["moving"] is False


def test_release_clears_the_glide_flag():
    t, _ = _tuner()
    t.hold()
    t.glide([0.0], [1.0], DT)
    t.release()
    assert t.snapshot()["moving"] is False


def test_snapshot_reports_state_without_exposing_internals():
    t, _ = _tuner()
    snap = t.snapshot()
    assert snap == {
        "offsets": {"left_knee": 0.1, "right_knee": -0.2},
        "saved": {"left_knee": 0.1, "right_knee": -0.2},
        "limit": OFFSET_LIMIT,
        "holding": False,
        "moving": False,
        "dirty": False,
        "error": "",
    }
    snap["offsets"]["left_knee"] = 99.0
    assert t.snapshot()["offsets"]["left_knee"] == 0.1


def test_limit_and_slews_stay_finite_and_positive():
    for v in (OFFSET_LIMIT, OFFSET_SLEW_RAD_S, STANCE_SLEW_RAD_S, MAX_STEP_DT):
        assert math.isfinite(v) and v > 0


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} offset_tuner tests passed.")
