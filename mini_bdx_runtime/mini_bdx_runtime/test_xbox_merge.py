"""
Tests for merge_axes() in xbox_controller.py — the gamepad+web stick merge
policy (no pygame / no gamepad needed; fake pygame like test_dpad_decode).

Run from this directory:  python3 test_xbox_merge.py
"""
import sys
import types

sys.modules["pygame"] = types.ModuleType("pygame")
_pkg = types.ModuleType("mini_bdx_runtime")
_pkg.__path__ = []
sys.modules["mini_bdx_runtime"] = _pkg
_btn = types.ModuleType("mini_bdx_runtime.buttons")
_btn.Buttons = type("Buttons", (), {})
sys.modules["mini_bdx_runtime.buttons"] = _btn

from xbox_controller import merge_axes  # noqa: E402


def test_pad_wins_when_web_inactive():
    l_x, l_y, r_x, r_y, lt, rt = merge_axes(
        (0.3, -0.4, 0.1, 0.0), (0.2, 0.0),
        web_active=False, web_axes=(0.9, 0.9, 0.9, 0.9), web_trig=(0.0, 0.0),
    )
    assert (l_x, l_y, r_x) == (0.3, -0.4, 0.1)


def test_web_overrides_axes_when_active():
    l_x, l_y, r_x, r_y, lt, rt = merge_axes(
        (0.3, -0.4, 0.1, 0.0), (0.0, 0.0),
        web_active=True, web_axes=(-0.5, 0.6, -0.2, 0.0), web_trig=(0.0, 0.0),
    )
    assert (l_x, l_y, r_x) == (-0.5, 0.6, -0.2), "web axes take over while active"


def test_triggers_take_the_max_of_both():
    # either source can raise an antenna, even with web sticks inactive
    _, _, _, _, lt, rt = merge_axes(
        (0, 0, 0, 0), (0.1, 0.8),
        web_active=False, web_axes=(0, 0, 0, 0), web_trig=(0.7, 0.2),
    )
    assert lt == 0.7 and rt == 0.8


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} xbox merge tests passed.")
