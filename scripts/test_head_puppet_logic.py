"""
Off-robot tests for head_puppet.py's pure record/playback helpers.

head_puppet imports Pi-only hardware modules and (now) keeps hardware setup +
the run loop under `if __name__ == "__main__"`, so importing it with the
hardware boundaries faked just defines the pure helpers we test here:
stick->head mapping, the safety clamp/slew, the 3s hold detector, and the
"any input" playback-stop check.

Run from scripts/:  python3 test_head_puppet_logic.py
"""
import os
import sys
import types
import math
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))


def _install_fakes():
    pkg = types.ModuleType("mini_bdx_runtime")
    pkg.__path__ = []
    sys.modules["mini_bdx_runtime"] = pkg
    _D = type("Dummy", (), {})
    for name, attr in [
        ("rustypot_position_hwi", "HWI"),
        ("duck_config", "DuckConfig"),
        ("xbox_controller", "XBoxController"),
        ("eyes", "Eyes"),
        ("sounds", "Sounds"),
        ("antennas", "Antennas"),
        ("antenna_anim", "AntennaAnimator"),
        ("projector", "Projector"),
    ]:
        m = types.ModuleType(f"mini_bdx_runtime.{name}")
        setattr(m, attr, _D)
        sys.modules[f"mini_bdx_runtime.{name}"] = m
    # head_puppet also imports save_config_fields from duck_config (Web UI saves).
    sys.modules["mini_bdx_runtime.duck_config"].save_config_fields = _D
    _ft = types.ModuleType("mini_bdx_runtime.face_tracker")
    for _n in ("FaceCamera", "FacePresence", "GreetSequence", "TrackingChatter",
               "servo_step", "normalized_error", "map_error_to_axes",
               "select_head_source"):
        setattr(_ft, _n, _D)
    _ft.CHATTER_SOUNDS = ("happy1.wav",)
    sys.modules["mini_bdx_runtime.face_tracker"] = _ft
    # web/telemetry modules are optional in head_puppet (guarded import -> None).
    _ss = types.ModuleType("mini_bdx_runtime.scanner_sound")
    for _n in ("ScannerSound", "PygameScannerBackend"):
        setattr(_ss, _n, _D)
    sys.modules["mini_bdx_runtime.scanner_sound"] = _ss


_install_fakes()
sys.path.insert(0, HERE)
import head_puppet as hp  # noqa: E402

EPS = 1e-4


def approx(a, b, eps=EPS):
    return abs(a - b) <= eps


def _btn(pressed=False):
    return SimpleNamespace(is_pressed=pressed, triggered=False)


def _buttons(**pressed):
    names = ["A", "B", "X", "Y", "LB", "RB",
             "dpad_up", "dpad_down", "dpad_left", "dpad_right"]
    return SimpleNamespace(**{n: _btn(pressed.get(n, False)) for n in names})


# ----------------------------------------------------------- stick -> head

def test_stick_to_head_rad_neutral():
    # neutral sticks -> yaw 0, roll 0, pitch is range-centered at -7.5 deg
    yaw, roll, pitch = hp.stick_to_head_rad([0, 0, 0, 0, 0, 0, 0])
    assert approx(yaw, 0.0)
    assert approx(roll, 0.0)
    assert approx(pitch, math.radians(-7.5))


def test_stick_to_head_rad_full_deflection():
    lc = [0, 0, 0, 0, 0, 0, 0]
    lc[5] = 1.0   # full yaw -> +60 deg
    lc[4] = 1.0   # full pitch -> +45 deg
    lc[6] = 1.0   # full roll -> +20 deg
    yaw, roll, pitch = hp.stick_to_head_rad(lc)
    assert approx(yaw, math.radians(60))
    assert approx(roll, math.radians(20))
    assert approx(pitch, math.radians(45))


# ----------------------------------------------------------- safety helpers

def test_clamp_head_rad_pins_to_limits():
    yaw, roll, pitch = hp.clamp_head_rad((2.0, 1.0, 1.0))
    assert approx(yaw, math.radians(60))    # head_yaw hi 60
    assert approx(roll, math.radians(20))   # head_roll hi 20
    assert approx(pitch, math.radians(45))  # head_pitch hi 45
    yaw, roll, pitch = hp.clamp_head_rad((-2.0, -1.0, -2.0))
    assert approx(yaw, math.radians(-60))
    assert approx(roll, math.radians(-20))
    assert approx(pitch, math.radians(-60))


def test_slew_list_caps_delta():
    out = hp.slew_list([0.0, 0.0, 0.0], [1.0, -1.0, 0.05], 0.08)
    assert approx(out[0], 0.08) and approx(out[1], -0.08) and approx(out[2], 0.05)


# ----------------------------------------------------------- hold detector

def test_hold_detector_fires_once_after_duration():
    h = hp.HoldDetector(3.0)
    assert h.update(True, 0.0) is False
    assert h.update(True, 2.99) is False
    assert h.update(True, 3.0) is True       # crosses 3s
    assert h.update(True, 3.5) is False       # already fired, no re-fire


def test_hold_detector_resets_on_release():
    h = hp.HoldDetector(3.0)
    h.update(True, 0.0)
    h.update(False, 1.0)                       # released -> reset
    assert h.update(True, 1.5) is False        # timer restarts
    assert h.update(True, 4.5) is True         # 3s after restart


def test_hold_detector_require_release_blocks_until_release():
    h = hp.HoldDetector(3.0)
    h.reset_require_release()                   # e.g. after a stop-press
    assert h.update(True, 0.0) is False         # still held from the stop-press
    assert h.update(True, 10.0) is False        # never fires while held
    h.update(False, 10.1)                       # release clears the lock
    assert h.update(True, 10.1) is False
    assert h.update(True, 13.1) is True         # now a fresh 3s hold works


# ----------------------------------------------------------- any input

def test_any_input_false_when_idle():
    assert hp.any_active_input([0, 0, 0, 0, 0, 0, 0], _buttons(), 0.0, 0.0) is False


def test_any_input_true_on_stick():
    lc = [0, 0, 0, 0, 0.0, 0.2, 0.0]   # head_yaw command (index 5) deflected
    assert hp.any_active_input(lc, _buttons(), 0.0, 0.0) is True


def test_any_input_true_on_button():
    assert hp.any_active_input([0, 0, 0, 0, 0, 0, 0], _buttons(X=True), 0.0, 0.0) is True


def test_any_input_true_on_trigger():
    assert hp.any_active_input([0, 0, 0, 0, 0, 0, 0], _buttons(), 0.5, 0.0) is True


def test_any_input_tolerates_missing_dpad_attrs():
    # old controller without dpad_left/right must not crash the check
    b = SimpleNamespace(A=_btn(), B=_btn(), X=_btn(), Y=_btn(),
                        LB=_btn(), RB=_btn(), dpad_up=_btn(), dpad_down=_btn())
    assert hp.any_active_input([0, 0, 0, 0, 0, 0, 0], b, 0.0, 0.0) is False


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
