"""
Off-robot tests for face_tracker.py's pure logic.

face_tracker imports NO hardware at module top (cv2/picamzero are lazy-imported
inside FaceCamera), so this imports cleanly on a dev machine.

Run from this directory:  python3 test_face_tracker.py
"""
import os
import sys
import math
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import face_tracker as ft  # noqa: E402

EPS = 1e-9


# ----------------------------------------------------------- geometry
def test_largest_face_picks_max_area():
    faces = [(0, 0, 10, 10), (5, 5, 40, 30), (1, 1, 20, 20)]
    assert ft.largest_face(faces) == (5, 5, 40, 30)


def test_largest_face_empty_is_none():
    assert ft.largest_face([]) is None


def test_face_center():
    assert ft.face_center((10, 20, 40, 60)) == (30.0, 50.0)


def test_normalized_error_center_is_zero():
    ex, ey = ft.normalized_error(160, 120, 320, 240)
    assert abs(ex) < EPS and abs(ey) < EPS


def test_normalized_error_edges_and_sign():
    ex, ey = ft.normalized_error(320, 0, 320, 240)
    assert abs(ex - 1.0) < EPS      # right edge -> +1
    assert abs(ey + 1.0) < EPS      # top edge -> -1


def test_map_error_to_axes_default_signs():
    yaw, pitch = ft.map_error_to_axes(0.5, -0.4)   # defaults: x*-1, y*-1
    assert abs(yaw - (-0.5)) < EPS
    assert abs(pitch - 0.4) < EPS


def test_map_error_to_axes_swap_and_sign():
    yaw, pitch = ft.map_error_to_axes(0.2, 0.7, yaw_from="y", yaw_sign=1.0,
                                      pitch_from="x", pitch_sign=-1.0)
    assert abs(yaw - 0.7) < EPS
    assert abs(pitch - (-0.2)) < EPS


def test_select_head_source():
    assert ft.select_head_source(True, True) == "servo"
    assert ft.select_head_source(True, False) == "servo"
    assert ft.select_head_source(False, True) == "keyframe"
    assert ft.select_head_source(False, False) == "hold"


# ----------------------------------------------------------- servo step
def test_servo_step_deadzone_no_motion():
    out = ft.servo_step(0.1, -0.2, 0.05, -0.03, kp_yaw=1.0, kp_pitch=1.0, deadzone=0.08)
    assert out == (0.1, -0.2)


def test_servo_step_moves_outside_deadzone():
    out = ft.servo_step(0.0, 0.0, 0.5, -0.5, kp_yaw=0.1, kp_pitch=0.1, deadzone=0.08)
    assert abs(out[0] - 0.05) < EPS and abs(out[1] + 0.05) < EPS


def test_servo_step_mixed_axes():
    # yaw inside deadzone (hold), pitch outside (moves)
    out = ft.servo_step(1.0, 2.0, 0.02, 0.5, kp_yaw=0.1, kp_pitch=0.2, deadzone=0.08)
    assert abs(out[0] - 1.0) < EPS
    assert abs(out[1] - 2.1) < EPS


# ----------------------------------------------------------- presence
def test_presence_acquire_and_greet_once():
    p = ft.FacePresence(lost_timeout=0.4, greet_after=1.5)
    p.update(True, 0.0)
    assert p.present and p.just_acquired and not p.greet_ready
    p.update(True, 1.0)
    assert p.present and not p.greet_ready
    p.update(True, 1.5)
    assert p.greet_ready            # fires exactly once at 1.5 s held
    p.update(True, 2.0)
    assert not p.greet_ready


def test_presence_hysteresis_rides_dropouts():
    p = ft.FacePresence(lost_timeout=0.4, greet_after=1.5)
    p.update(True, 0.0)
    p.update(False, 0.2)            # dropout within timeout -> still present
    assert p.present and not p.just_lost
    p.update(False, 0.5)            # 0.5 s since last detection -> lost
    assert not p.present and p.just_lost


def test_presence_regreets_after_reacquire():
    p = ft.FacePresence(lost_timeout=0.4, greet_after=1.5)
    p.update(True, 0.0)
    p.update(True, 1.5)
    assert p.greet_ready
    p.update(False, 2.0)            # lost (last seen 1.5)
    assert not p.present and p.just_lost
    p.update(True, 3.0)             # reacquire
    assert p.just_acquired
    p.update(True, 4.4)
    assert not p.greet_ready        # held 1.4 < 1.5
    p.update(True, 4.5)
    assert p.greet_ready            # held 1.5 -> regreets


def _run():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
