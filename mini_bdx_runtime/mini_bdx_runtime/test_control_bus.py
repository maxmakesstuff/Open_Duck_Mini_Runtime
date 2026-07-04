"""
Standalone tests for ControlBus (no pytest; pure stdlib -> runs off-robot).

Run from this directory:  python3 test_control_bus.py
"""
from control_bus import ControlBus, BUTTONS


def test_stick_override_active_then_stale():
    bus = ControlBus(stale_s=0.5)
    # no command yet -> not active
    assert bus.stick_override(now=0.0)[0] is False
    bus.set_command(now=1.0, active=True, l_x=0.5, l_y=-0.3, r_x=0.2,
                    left_trigger=0.4, right_trigger=0.0)
    active, l_x, l_y, r_x, r_y, lt, rt = bus.stick_override(now=1.1)
    assert active is True
    assert (l_x, l_y, r_x) == (0.5, -0.3, 0.2)
    assert lt == 0.4
    # goes stale after 0.5s of silence -> gamepad regains control
    assert bus.stick_override(now=1.7)[0] is False


def test_set_command_active_false_yields_gamepad():
    bus = ControlBus()
    bus.set_command(now=1.0, active=False, l_x=0.9)
    assert bus.stick_override(now=1.0)[0] is False


def test_axes_and_triggers_clamped():
    bus = ControlBus()
    bus.set_command(now=1.0, active=True, l_x=5.0, l_y=-9.0, r_x=2.0,
                    left_trigger=9.0, right_trigger=-3.0)
    active, l_x, l_y, r_x, r_y, lt, rt = bus.stick_override(now=1.0)
    assert (l_x, l_y, r_x) == (1.0, -1.0, 1.0)
    assert lt == 1.0 and rt == 0.0


def test_button_press_is_single_edge():
    bus = ControlBus()
    bus.push_button("A", "press")
    first = bus.consume_buttons()
    second = bus.consume_buttons()
    assert first["A"] is True, "tap registers pressed on the first consume"
    assert second["A"] is False, "and is gone next tick -> exactly one edge"


def test_button_down_up_holds():
    bus = ControlBus()
    bus.push_button("LB", "down")
    assert bus.consume_buttons()["LB"] is True
    assert bus.consume_buttons()["LB"] is True   # still held across ticks
    bus.push_button("LB", "up")
    assert bus.consume_buttons()["LB"] is False


def test_two_quick_taps_yield_two_edges():
    bus = ControlBus()
    bus.push_button("B", "press")
    bus.push_button("B", "press")
    seq = [bus.consume_buttons()["B"] for _ in range(4)]
    assert seq == [True, False, True, False], f"two taps -> two edges, got {seq}"


def test_unknown_button_ignored():
    bus = ControlBus()
    assert bus.push_button("NOPE", "press") is False
    for b in BUTTONS:
        assert bus.consume_buttons()[b] is False


def test_trim_accumulates_and_consume_resets():
    bus = ControlBus()
    bus.push_trim("pitch", 0.002)
    bus.push_trim("pitch", 0.002)
    bus.push_trim("roll", -0.001)
    p, r, save = bus.consume_trim()
    assert abs(p - 0.004) < 1e-9 and abs(r - (-0.001)) < 1e-9 and save is False
    # consuming again yields nothing (reset)
    assert bus.consume_trim() == (0.0, 0.0, False)


def test_trim_save_flag_one_shot():
    bus = ControlBus()
    bus.push_trim_save()
    assert bus.consume_trim() == (0.0, 0.0, True)
    assert bus.consume_trim() == (0.0, 0.0, False)


def test_trim_unknown_axis_ignored():
    bus = ControlBus()
    assert bus.push_trim("yaw", 0.01) is False
    assert bus.consume_trim() == (0.0, 0.0, False)


def test_telemetry_roundtrip_is_copied():
    bus = ControlBus()
    snap = {"mode": "walk", "paused": False}
    bus.set_telemetry(snap)
    got = bus.get_telemetry()
    assert got == snap
    got["mode"] = "mutated"
    assert bus.get_telemetry()["mode"] == "walk", "telemetry must be copied, not aliased"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} control_bus tests passed.")
