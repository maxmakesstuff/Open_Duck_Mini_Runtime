"""
Tests for decode_dpad() in xbox_controller.py (no pygame / no gamepad needed).

xbox_controller imports pygame at module top, so we inject a fake pygame (and a
fake buttons module) into sys.modules, then import it and test the pure
decode_dpad helper - which covers the hat path and the axes-6/7 fallback used
for recent Xbox Bluetooth controllers.

Run from this directory:  python3 test_dpad_decode.py
"""
import sys
import types

# fake pygame + mini_bdx_runtime.buttons so the import succeeds off-robot
sys.modules["pygame"] = types.ModuleType("pygame")
_pkg = types.ModuleType("mini_bdx_runtime")
_pkg.__path__ = []
sys.modules["mini_bdx_runtime"] = _pkg
_btn_mod = types.ModuleType("mini_bdx_runtime.buttons")
_btn_mod.Buttons = type("Buttons", (), {})
sys.modules["mini_bdx_runtime.buttons"] = _btn_mod

from xbox_controller import decode_dpad  # noqa: E402

AXES6 = [0, 0, 0, 0, 0, 0]            # 6-axis pad (sticks+triggers, no dpad axes)
AXES8 = [0, 0, 0, 0, 0, 0, 0.0, 0.0]  # 8-axis pad (dpad on axes 6/7)


def test_hat_left_right():
    assert decode_dpad((-1, 0), AXES6) == (-1, 0)   # left
    assert decode_dpad((1, 0), AXES6) == (1, 0)      # right


def test_hat_up_down():
    assert decode_dpad((0, 1), AXES6) == (0, 1)      # up
    assert decode_dpad((0, -1), AXES6) == (0, -1)    # down


def test_neutral():
    assert decode_dpad((0, 0), AXES6) == (0, 0)
    assert decode_dpad(None, None) == (0, 0)


def test_axes_fallback_left_right_when_hat_phantom():
    a = list(AXES8)
    a[6] = -1.0
    assert decode_dpad((0, 0), a) == (-1, 0)         # left via axis 6
    a[6] = 1.0
    assert decode_dpad((0, 0), a) == (1, 0)          # right via axis 6


def test_axes_fallback_up_down_with_sign_convention():
    a = list(AXES8)
    a[7] = -1.0
    assert decode_dpad((0, 0), a) == (0, 1)          # axis up (-1) -> dpad up (+1)
    a[7] = 1.0
    assert decode_dpad((0, 0), a) == (0, -1)         # axis down (+1) -> dpad down (-1)


def test_hat_takes_precedence_over_axes():
    a = list(AXES8)
    a[6] = 1.0                                       # axis says right
    assert decode_dpad((-1, 0), a) == (-1, 0)        # but a live hat wins


def test_axes_fallback_ignored_when_too_few_axes():
    a = list(AXES6)                                  # only 6 axes -> no dpad axes
    assert decode_dpad((0, 0), a) == (0, 0)


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
