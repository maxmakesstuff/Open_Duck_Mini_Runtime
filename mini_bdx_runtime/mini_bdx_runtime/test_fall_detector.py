"""
Standalone tests for fall_detector (no pytest, pure stdlib -> runs off-robot).

Run from this directory:  python3 test_fall_detector.py
"""
from fall_detector import FallDetector, gravity_tilt_deg


def _run(det, feet_over_time, start=0.0, dt=0.02):
    """Feed feet states at dt spacing; return list of (t, edge, fallen)."""
    out = []
    t = start
    for feet in feet_over_time:
        edge = det.update(feet, t)
        out.append((round(t, 3), edge, det.fallen))
        t += dt
    return out


def test_no_fall_during_normal_gait():
    # A normal gait: feet alternate, never both off for more than a tick or two.
    det = FallDetector(fall_after_s=2.5, start_grace_s=0.0)
    det.reset(0.0)
    pattern = [[True, False], [False, True], [True, True], [False, True]] * 200
    res = _run(det, pattern)
    assert not any(edge for _, edge, _ in res), "should never fire during a gait"
    assert not det.fallen


def test_fall_after_sustained_both_off():
    det = FallDetector(fall_after_s=2.5, start_grace_s=0.0)
    det.reset(0.0)
    # both feet off forever, dt=0.02 -> fall at ~2.5s = ~125 ticks
    res = _run(det, [[False, False]] * 200)
    edges = [t for t, edge, _ in res if edge]
    assert len(edges) == 1, f"exactly one rising edge, got {edges}"
    assert 2.49 <= edges[0] <= 2.53, f"fired at {edges[0]}, expected ~2.5s"
    assert det.fallen


def test_grace_window_suppresses_early():
    det = FallDetector(fall_after_s=0.5, start_grace_s=2.0)
    det.reset(0.0)
    # both off from t=0; without grace it would fire at 0.5s, but grace=2.0s
    res = _run(det, [[False, False]] * 200)
    edges = [t for t, edge, _ in res if edge]
    assert edges and edges[0] >= 2.0, f"grace should delay past 2.0s, got {edges}"


def test_foot_touch_resets_timer():
    det = FallDetector(fall_after_s=1.0, start_grace_s=0.0)
    det.reset(0.0)
    # both off ~0.8s (touch resets), then off again; must not fire early
    seq = [[False, False]] * 40 + [[True, False]] + [[False, False]] * 40
    res = _run(det, seq)
    assert not any(edge for _, edge, _ in res), "a foot touch must reset the timer"


def test_reset_allows_redetection():
    det = FallDetector(fall_after_s=0.5, start_grace_s=0.0)
    det.reset(0.0)
    assert det.update([False, False], 0.0) is False
    assert det.update([False, False], 0.6) is True   # first fall
    assert det.update([False, False], 0.7) is False  # no repeat edge
    det.reset(1.0)                                    # user righted + unpaused
    assert det.fallen is False
    # A fresh sustained both-off window is required after reset (the robot is
    # standing again, so the timer starts over here and fires 0.5s later).
    assert det.update([False, False], 1.7) is False   # timer restarts
    assert det.update([False, False], 2.3) is True    # fires again ~0.5s later


def test_tilt_corroborator_when_enabled():
    det = FallDetector(fall_after_s=99.0, start_grace_s=0.0, tilt_fall_deg=60.0)
    det.reset(0.0)
    # feet irrelevant here; a big tilt trips it even before the (huge) feet window
    assert det.update([True, True], 0.1, tilt_deg=10.0) is False
    assert det.update([True, True], 0.2, tilt_deg=75.0) is True


def test_gravity_tilt_deg_math():
    # up-axis = z (index 2). Upright: gravity along +z -> ~0 deg.
    assert abs(gravity_tilt_deg([0, 0, 9.81], up_axis=2, up_sign=1.0) - 0.0) < 1e-3
    # On its side: gravity along x -> ~90 deg.
    assert abs(gravity_tilt_deg([9.81, 0, 0], up_axis=2, up_sign=1.0) - 90.0) < 1e-3
    # Upside down: gravity along -z -> ~180 deg.
    assert abs(gravity_tilt_deg([0, 0, -9.81], up_axis=2, up_sign=1.0) - 180.0) < 1e-3
    # up_sign flips the reference.
    assert abs(gravity_tilt_deg([0, 0, -9.81], up_axis=2, up_sign=-1.0) - 0.0) < 1e-3
    assert gravity_tilt_deg([0, 0, 0]) is None


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} fall_detector tests passed.")
