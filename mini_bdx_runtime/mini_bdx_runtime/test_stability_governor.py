"""
Off-robot tests for the stability governor (pure logic).

Run from this directory:  python3 test_stability_governor.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stability_governor as sg  # noqa: E402


def test_disabled_is_passthrough():
    g = sg.StabilityGovernor(enabled=False)
    for _ in range(5):
        assert g.update(tilt_deg=90, tilt_rate=99) == 1.0     # ignores everything
    assert g.severity == 0.0


def test_below_deadband_full_speed():
    g = sg.StabilityGovernor(enabled=True, tilt_lo_deg=8, tilt_hi_deg=22,
                             rate_lo=1.5, rate_hi=5, smooth=1.0)
    assert g.update(tilt_deg=3, tilt_rate=0.5) == 1.0
    assert g.severity == 0.0


def test_severity_takes_worse_of_angle_and_rate():
    g = sg.StabilityGovernor(enabled=True, tilt_lo_deg=8, tilt_hi_deg=28,
                             rate_lo=1.0, rate_hi=5.0)
    # angle at halfway (18 deg -> 0.5), rate below deadband -> severity 0.5
    assert abs(g.severity_of(18, 0.5) - 0.5) < 1e-9
    # rate dominates (3.0 -> (3-1)/4=0.5) vs tiny angle -> 0.5
    assert abs(g.severity_of(2, 3.0) - 0.5) < 1e-9
    # both high -> saturates at 1
    assert g.severity_of(40, 9) == 1.0


def test_scale_floor_at_full_severity():
    g = sg.StabilityGovernor(enabled=True, tilt_lo_deg=8, tilt_hi_deg=22,
                             floor=0.2, smooth=1.0)
    g.update(tilt_deg=90, tilt_rate=0)        # smooth=1 -> jump straight to target
    assert abs(g.scale - 0.2) < 1e-9


def test_scale_is_smoothed_not_instant():
    g = sg.StabilityGovernor(enabled=True, tilt_lo_deg=8, tilt_hi_deg=22,
                             floor=0.0, smooth=0.5)
    s1 = g.update(tilt_deg=90, tilt_rate=0)   # target 0.0, EMA from 1.0 -> 0.5
    assert abs(s1 - 0.5) < 1e-9
    s2 = g.update(tilt_deg=90, tilt_rate=0)   # -> 0.25
    assert abs(s2 - 0.25) < 1e-9


def test_recovers_back_to_one():
    g = sg.StabilityGovernor(enabled=True, smooth=1.0, floor=0.2)
    g.update(tilt_deg=90, tilt_rate=0)        # throttled
    s = g.update(tilt_deg=0, tilt_rate=0)     # calm again -> back to 1.0
    assert s == 1.0


def test_helpers():
    assert abs(sg.tilt_angle_deg(3, 4) - 5.0) < 1e-9
    assert abs(sg.tilt_rate([3.0, 4.0, 99.0]) - 5.0) < 1e-9   # yaw axis ignored


def test_governor_from_config_defaults_disabled():
    g = sg.governor_from_config(None)
    assert g.enabled is False and g.update(50, 50) == 1.0
    g2 = sg.governor_from_config({"enabled": True, "floor": 0.3, "tilt_lo_deg": 5,
                                  "tilt_hi_deg": 5})  # hi<=lo -> angle term disabled
    assert g2.enabled is True
    assert g2.severity_of(90, 0) == 0.0       # angle ramp disabled by hi<=lo


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
