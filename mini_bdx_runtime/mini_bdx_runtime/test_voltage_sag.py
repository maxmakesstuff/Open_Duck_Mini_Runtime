"""Off-robot tests for scripts/voltage_sag_test.py (its maths and a full run on a
fake servo bus).  Run:  python3 test_voltage_sag.py"""
import importlib.util
import math
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "voltage_sag_test", os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts",
                                     "voltage_sag_test.py"))
vs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vs)


class FakeIO:
    """Knees at the stance; the right knee's supply sags when the knees move."""
    unit = "deg"
    stance = True
    instances = []

    def __init__(self, *_, **__):
        k = 78.0 if self.unit == "deg" else 1.37
        if not self.stance:
            k = 0.0
        self.pos = {i: 0.0 for i in vs.JOINTS.values()}
        self.pos[23], self.pos[13] = k, k
        self.start = dict(self.pos)
        self.goals = []
        self.closed = False
        FakeIO.instances.append(self)

    def get_present_position(self, ids):
        return [self.pos[i] for i in ids]

    def set_goal_position(self, goal):
        self.goals.append(dict(goal))
        self.pos.update(goal)

    def get_present_voltage(self, ids):
        moved = abs(self.pos[13] - self.start[13]) * (1.0 if self.unit == "deg" else 57.3)
        return [(76 - (12 if i == 13 else 2) * min(1.0, moved / 3.0)) for i in ids]

    def close(self):
        self.closed = True


def _run(argv, unit="deg", stance=True, answer="y"):
    FakeIO.unit, FakeIO.stance, FakeIO.instances = unit, stance, []
    sys.modules["pypot"] = types.ModuleType("pypot")
    sys.modules["pypot.feetech"] = types.ModuleType("pypot.feetech")
    sys.modules["pypot.feetech"].FeetechSTS3215IO = FakeIO
    lines = []
    vs.print = lambda *a: lines.append(" ".join(str(x) for x in a))
    vs.input = lambda *_: answer
    try:
        code = vs.main(argv)
    finally:
        del vs.print, vs.input
    return code, "\n".join(lines), FakeIO.instances[0]


def test_units_are_detected_from_the_stance_knee():
    assert vs.detect_units([78.4, 79.0]) == 1.0
    assert abs(vs.detect_units([1.37, 1.38]) - 57.2958) < 1e-3
    assert vs.detect_units([-1.37, 1.38]) is not None
    for bad in ([0.0, 0.0], [3.0, 3.0], [78.0, 0.2], [300.0, 300.0]):
        assert vs.detect_units(bad) is None, bad


def test_bob_starts_at_rest_and_stays_inside_the_amplitude():
    assert vs.bob_offset(0.0, 1.2, 6.0) == 0.0
    vals = [vs.bob_offset(i * 0.01, 1.2, 6.0) for i in range(2000)]
    assert max(abs(v) for v in vals) <= 6.0 and max(vals) > 5.5
    assert abs(vals[1] - vals[0]) < 0.01, "fades in"


def test_step_limit():
    assert vs.limit_step(0.0, 5.0, 0.6) == 0.6
    assert vs.limit_step(0.0, -5.0, 0.6) == -0.6
    assert vs.limit_step(1.0, 1.2, 0.6) == 1.2


def test_full_run_moves_only_the_knees_gently_and_returns_them():
    code, text, io = _run(["--cycles", "2", "--period", "0.6"])
    assert code == 0 and io.closed
    moved = {i for g in io.goals for i in g}
    assert moved == {23, 13}, f"only the knees may move, got {moved}"
    for knee in (23, 13):
        seq = [g[knee] for g in io.goals]
        assert max(abs(v - 78.0) for v in seq) <= 6.0 + 1e-6
        assert max(abs(b - a) for a, b in zip([78.0] + seq, seq)) <= vs.MAX_STEP_DEG + 1e-6
        assert abs(seq[-1] - 78.0) < 1e-6, "ends where it started"
    assert "right_knee -1.2 V" in text, text
    assert "left_knee" in text and "At rest" in text


def test_radian_libraries_get_radian_sized_moves():
    code, text, io = _run(["--cycles", "1", "--period", "0.6"], unit="rad")
    assert code == 0
    seq = [g[13] for g in io.goals]
    assert max(abs(v - 1.37) for v in seq) <= math.radians(6.0) + 1e-6
    assert max(abs(v - 1.37) for v in seq) > math.radians(2.0)


def test_refuses_to_move_when_the_duck_is_not_in_its_stance():
    code, text, io = _run(["--cycles", "1"], stance=False)
    assert code == 1 and io.goals == [] and "Nothing moved" in text and io.closed


def test_saying_no_moves_nothing():
    code, text, io = _run(["--cycles", "1"], answer="n")
    assert code == 0 and io.goals == [] and io.closed


def test_amplitude_is_capped():
    code, text, io = _run(["--cycles", "1", "--period", "0.6", "--amplitude", "90", "--yes"])
    assert max(abs(g[13] - 78.0) for g in io.goals) <= vs.MAX_AMPLITUDE_DEG + 1e-6


def test_verdicts():
    rest = {n: 7.6 for n in vs.JOINTS}
    even = {n: 7.3 for n in vs.JOINTS}
    assert "Even supply" in vs.summarise(rest, even)[1][0]
    weak = dict(even, right_knee=5.2, right_ankle=5.4)
    text = " ".join(vs.summarise(rest, weak)[1])
    assert "right_knee" in text and "right_ankle" in text and "Lowest reading 5.2" in text
    flat = {n: 6.4 for n in vs.JOINTS}
    assert "battery" in " ".join(vs.summarise(rest, flat)[1])
    assert vs.summarise({}, {})[1] == ["No voltages read."]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} voltage_sag tests passed.")
