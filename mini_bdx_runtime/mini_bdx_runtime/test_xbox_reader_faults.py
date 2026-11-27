"""
The gamepad reader must survive anything pygame throws at it.

Background: on 2026-11-27 a pad that connected after head puppet had started made
pygame.event.get() raise SystemError (pygame 2.6.0, pad registered twice by the
kernel). Only pygame.error was caught, the reader thread died, and neither the pad
nor the Web UI stick did anything until a restart. In walk mode the same fault
would have left the duck walking on its last stick position.

Run from this directory:  python3 test_xbox_reader_faults.py
"""
import sys
import types

import numpy as np


class PgError(Exception):
    pass


class FakeJoystick:
    def __init__(self, world):
        self.w = world

    def init(self):
        pass

    def get_name(self):
        return "Fake Pad"

    def get_numaxes(self):
        return 6

    def get_numbuttons(self):
        return 16

    def get_numhats(self):
        return 1

    def get_axis(self, i):
        if self.w.axis_error is not None:
            raise self.w.axis_error
        return self.w.axes[i]

    def get_hat(self, i):
        return (0, 0)

    def get_button(self, i):
        return False


class World:
    def __init__(self):
        self.count = 0
        self.events = []          # list of events, or an exception to raise once
        self.axis_error = None
        self.axes = [0.0, 0.0, 0.0, 0.0, -1.0, -1.0]


W = World()


def _event_get():
    ev, W.events = W.events, []
    if isinstance(ev, BaseException):
        raise ev
    return ev


pg = types.ModuleType("pygame")
pg.error = PgError
pg.JOYDEVICEADDED, pg.JOYDEVICEREMOVED, pg.JOYBUTTONDOWN, pg.JOYBUTTONUP = 1, 2, 3, 4
pg.init = lambda: None
pg.event = types.SimpleNamespace(get=_event_get, pump=lambda: None)
pg.joystick = types.SimpleNamespace(
    init=lambda: None, get_count=lambda: W.count, Joystick=lambda i: FakeJoystick(W))
sys.modules["pygame"] = pg
_pkg = types.ModuleType("mini_bdx_runtime")
_pkg.__path__ = []
sys.modules["mini_bdx_runtime"] = _pkg
_btn = types.ModuleType("mini_bdx_runtime.buttons")
_btn.Buttons = type("Buttons", (), {"update": lambda self, *a: None})
sys.modules["mini_bdx_runtime.buttons"] = _btn

import xbox_controller as xc  # noqa: E402


class NoThread:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass


xc.Thread = NoThread          # the tests step the reader by hand


def make(count=1, head=False):
    global W
    W.count, W.events, W.axis_error = count, [], None
    W.axes = [0.0, 0.0, 0.0, 0.0, -1.0, -1.0]
    return xc.XBoxController(20, only_head_control=head)


def test_neutral_command_shape_matches_get_commands():
    c = make()
    assert len(xc.neutral_command()) == len(c.get_commands()) == 13
    cmd = xc.neutral_command()
    assert not np.any(cmd[0]) and not any(cmd[1:7]) and cmd[7] == cmd[8] == 0.0


def test_system_error_from_event_get_drops_pad_and_recovers():
    c = make()
    W.axes[1] = -1.0                                   # stick pushed forward
    assert c.get_commands()[0][0] != 0.0
    W.events = SystemError("<built-in function get> returned a result with an exception set")
    cmd = c.get_commands()                             # must not raise
    assert c.p1 is None and not np.any(cmd[0])         # neutral while the pad is gone
    assert c.fault_count == 1
    c._last_open_attempt = 0.0                         # re-probe interval elapsed
    c.get_commands()
    assert c.p1 is not None                            # picked up again without restart
    assert c.get_commands()[0][0] != 0.0


def test_late_pad_with_double_registration_is_picked_up():
    c = make(count=0)
    assert c.p1 is None
    W.count = 1
    W.events = SystemError("KeyError: 0")
    c.get_commands()
    c._last_open_attempt = 0.0
    c.get_commands()
    assert c.p1 is not None


def test_any_error_while_reading_axes_gives_neutral():
    c = make()
    W.axis_error = RuntimeError("boom")
    cmd = c.get_commands()
    assert c.p1 is None and not np.any(cmd[0])


def test_worker_survives_a_failing_get_commands():
    c = make()
    calls = {"n": 0}

    def bad():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("anything")
        raise KeyboardInterrupt            # ends the endless loop for the test

    c.get_commands = bad
    try:
        c.commands_worker()
    except KeyboardInterrupt:
        pass
    got = c.cmd_queue.get(False)
    assert not np.any(got[0]) and got[7] == got[8] == 0.0
    c.last_commands = [0.15, 0, 0, 0, 0, 0, 0]         # the loop's stale command...
    c.cmd_queue.put(got)
    assert not np.any(c.get_last_command()[0])         # ...is replaced by neutral


def test_dead_zone_swallows_a_resting_offset_and_keeps_full_range():
    assert xc.apply_deadzone(0.0) == 0.0
    assert xc.apply_deadzone(0.06) == 0.0 and xc.apply_deadzone(-0.099) == 0.0
    assert abs(xc.apply_deadzone(1.0) - 1.0) < 1e-9 and abs(xc.apply_deadzone(-1.0) + 1.0) < 1e-9
    assert 0.0 < xc.apply_deadzone(0.12) < 0.05                 # continuous at the edge
    assert xc.apply_deadzone(float("nan")) == 0.0 and xc.apply_deadzone(3.0) == 1.0
    c = make()
    W.axes[0], W.axes[1], W.axes[2] = -0.03, 0.04, -0.017      # sticks resting off-centre
    cmd = c.get_commands()[0]
    assert not np.any(cmd[:3]), cmd                              # exactly idle: rest pose can engage
    W.axes[1] = -1.0
    assert abs(c.get_commands()[0][0] - 0.15) < 1e-6             # full stick is still full speed


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
    print(f"OK - {n} tests")
