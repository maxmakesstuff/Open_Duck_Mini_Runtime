"""Off-robot tests for command_shaper.py.  Run:  python3 test_command_shaper.py"""
from command_shaper import CommandShaper, clean_config, DEFAULTS

DT = 0.02


def _run(shaper, target, n):
    out = []
    for _ in range(n):
        c = list(target)
        shaper.apply(c, DT)
        out.append(c)
    return out


def test_disabled_is_a_pure_pass_through():
    s = CommandShaper()
    c = [0.15, -0.1, 1.0, 0.3, 0.2, 0.1, 0.0]
    assert s.apply(list(c), DT) == c


def test_forward_step_becomes_a_ramp_at_the_configured_rate():
    s = CommandShaper({"enabled": True, "lin_accel": 0.3})
    out = _run(s, [0.15, 0, 0, 0, 0, 0, 0], 40)
    steps = [b[0] - a[0] for a, b in zip([[0.0]] + out, out)]
    assert max(steps) <= 0.3 * DT + 1e-12
    assert abs(out[24][0] - 0.15) < 1e-9, "0.15 m/s at 0.3 m/s^2 takes 0.5 s"
    assert out[10][0] < 0.15


def test_stopping_is_faster_than_starting():
    s = CommandShaper({"enabled": True, "lin_accel": 0.3, "stop_factor": 3.0})
    _run(s, [0.15, 0, 0, 0, 0, 0, 0], 40)
    out = _run(s, [0.0] * 7, 20)
    assert abs(out[8][0]) < 1e-9, "stops in a third of the time (0.17 s)"
    assert out[3][0] > 0.0


def test_reversing_brakes_through_zero_without_overshoot():
    s = CommandShaper({"enabled": True})
    _run(s, [0.15, 0, 0, 0, 0, 0, 0], 40)
    out = [c[0] for c in _run(s, [-0.15, 0, 0, 0, 0, 0, 0], 60)]
    assert all(a >= b - 1e-12 for a, b in zip(out, out[1:])), "monotonic"
    assert abs(out[-1] + 0.15) < 1e-9 and min(out) >= -0.15 - 1e-12


def test_head_commands_are_never_touched():
    s = CommandShaper({"enabled": True})
    out = _run(s, [0.15, 0.1, 1.0, 0.5, -0.4, 0.3, 0.2], 3)
    assert all(c[3:] == [0.5, -0.4, 0.3, 0.2] for c in out)


def test_turn_uses_its_own_rate():
    s = CommandShaper({"enabled": True, "ang_accel": 4.0})
    out = _run(s, [0, 0, 1.0, 0, 0, 0, 0], 20)
    assert abs(out[0][2] - 4.0 * DT) < 1e-12 and abs(out[12][2] - 1.0) < 1e-9


def test_reset_forgets_the_ramp():
    s = CommandShaper({"enabled": True})
    _run(s, [0.15, 0, 0, 0, 0, 0, 0], 40)
    s.reset()
    assert _run(s, [0.15, 0, 0, 0, 0, 0, 0], 1)[0][0] <= 0.3 * DT + 1e-12


def test_enabling_mid_drive_does_not_step():
    s = CommandShaper()
    _run(s, [0.15, 0, 0, 0, 0, 0, 0], 5)       # disabled: tracks the live command
    s.set("enabled", True)
    assert abs(_run(s, [0.15, 0, 0, 0, 0, 0, 0], 1)[0][0] - 0.15) < 1e-12


def test_config_is_clamped_and_live_set_validates():
    c = clean_config({"enabled": 1, "lin_accel": 99, "ang_accel": "x", "evil": 2})
    assert c == {"enabled": True, "lin_accel": 3.0, "ang_accel": 4.0, "stop_factor": 3.0}
    assert clean_config(None) == DEFAULTS and DEFAULTS["enabled"] is False
    s = CommandShaper()
    assert s.set("lin_accel", 0.5) and s.cfg["lin_accel"] == 0.5
    assert not s.set("lin_accel", float("nan")) and not s.set("nope", 1)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} command_shaper tests passed.")
