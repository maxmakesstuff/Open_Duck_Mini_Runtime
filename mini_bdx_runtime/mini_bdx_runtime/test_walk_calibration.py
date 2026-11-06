"""
Runs the REAL walk loop (scripts/v2_rl_walk_mujoco.py, RLWalk.run) on a simulated duck
to check the joint-offset page end to end: HTTP route -> ControlBus -> loop -> HWI -> bus.

Only the hardware edge is fake (servo bus, IMU, gamepad, policy, feet, clock); the HWI,
config, routing, telemetry and the loop itself are the production code. A real
~/duck_config.json is never touched: HOME points at a temp dir before anything imports.

Run from this directory:  python3 test_walk_calibration.py
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time as real_time
import types

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SANDBOX = tempfile.mkdtemp(prefix="duck-walk-test-")
os.environ["HOME"] = SANDBOX
os.chdir(SANDBOX)
sys.path.insert(0, os.path.dirname(HERE))   # so `mini_bdx_runtime.*` resolves


# ------------------------------------------------------------- simulated hardware
class FakeBus:
    """Stands in for rustypot.feetech. A servo sits exactly on its last goal."""

    def __init__(self, *_):
        self.goal = {}
        self.writes = []                    # one {id: position} per write

    def write_goal_position(self, ids, positions):
        self.goal.update(zip(ids, positions))
        self.writes.append(dict(zip(ids, positions)))

    def read_present_position(self, ids):
        return [self.goal[i] for i in ids]

    def read_present_velocity(self, ids):
        return [0.0 for _ in ids]

    def set_kps(self, *_):
        pass

    def set_kds(self, *_):
        pass

    def disable_torque(self, *_):
        pass


class FakeImu:
    """Accelerometer drifts every read, so a frozen horizon is detectable."""

    def __init__(self, *_, pitch_trim=0.0, roll_trim=0.0, **__):
        self.pitch_trim, self.roll_trim = pitch_trim, roll_trim
        self.reads = 0

    def get_data(self):
        self.reads += 1
        return {"gyro": np.zeros(3),
                "accelero": np.array([0.01 * self.reads, 0.0, 9.81])}


class FakePolicy:
    action = 0.5                            # a constant, visibly non-stance output

    def __init__(self, *_, **__):
        self.obs = []

    def infer(self, obs):
        assert np.isfinite(obs).all(), "non-finite observation reached the policy"
        self.obs.append(obs)
        return np.full(14, float(FakePolicy.action))


class FakeFeet:
    def get(self):
        return [1.0, 1.0]

    def stop(self):
        pass


class _Button:
    def __init__(self):
        self.triggered = False
        self.is_pressed = False


class FakePad:
    """Each control tick asks the pad for input, so this is where a test scenario
    (a generator; one `yield` = one tick) advances. Exhausted -> stop the loop."""

    scenario = None

    def __init__(self, *_, **__):
        self.script = None

    def get_last_command(self):
        buttons = types.SimpleNamespace(
            **{n: _Button() for n in ("A", "B", "X", "Y", "LB", "RB", "dpad_up",
                                      "dpad_down", "dpad_left", "dpad_right")})
        self.buttons = buttons
        try:
            next(self.script)
        except StopIteration:
            raise KeyboardInterrupt from None
        return [0.0] * 7, buttons, 0.0, 0.0


def _module(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod


_module("rustypot", feetech=FakeBus)
_module("mini_bdx_runtime.onnx_infer", OnnxInfer=FakePolicy)
_module("mini_bdx_runtime.raw_imu", Imu=FakeImu)
_module("mini_bdx_runtime.xbox_controller", XBoxController=FakePad)
_module("mini_bdx_runtime.feet_contacts", FeetContacts=FakeFeet)
_module("mini_bdx_runtime.poly_reference_motion",
        PolyReferenceMotion=lambda *_: types.SimpleNamespace(nb_steps_in_period=100))
for _name, _cls in (("eyes", "Eyes"), ("sounds", "Sounds"),
                    ("antennas", "Antennas"), ("projector", "Projector")):
    _module(f"mini_bdx_runtime.{_name}", **{_cls: object})

_spec = importlib.util.spec_from_file_location(
    "v2_rl_walk_mujoco",
    os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts", "v2_rl_walk_mujoco.py"))
walk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(walk)

from mini_bdx_runtime.web_control import handle_api  # noqa: E402


# ------------------------------------------------------------------- test harness
OFFSETS = {
    "left_hip_yaw": -0.075, "left_hip_roll": 0.095, "left_hip_pitch": 0.08399999999999999,
    "left_knee": 0.124, "left_ankle": -0.055, "neck_pitch": 0.104, "head_pitch": -0.104,
    "head_yaw": -0.117, "head_roll": 0.012, "right_hip_yaw": 0.041, "right_hip_roll": -0.027,
    "right_hip_pitch": -0.084, "right_knee": 0.034, "right_ankle": 0.017,
}
CONFIG_PATH = os.path.join(SANDBOX, "duck_config.json")
DT = 1 / 50
# Per-write motion the hold may cause on one joint: stance glide + offset slew.
MAX_HOLD_DELTA = 0.5 * DT + 0.5 * DT + 1e-9


class Clock:
    """Virtual time, so the 4 s of bring-up sleeps and the loop run instantly."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def run_duck(scenario, action=0.5, config=None):
    """Build a fresh duck, run `scenario(duck, post, state)`, return the duck."""
    with open(CONFIG_PATH, "w") as f:
        json.dump({"joints_offsets": OFFSETS, "imu_trim": {"pitch": 0.01, "roll": -0.02},
                   "imu_upside_down": True, "web_port": 0, "battery_servo_read": False,
                   "some_other_key": "keep me", **(config or {})}, f)
    for stale in os.listdir(SANDBOX):
        if stale.endswith(".bak"):
            os.unlink(os.path.join(SANDBOX, stale))

    FakePolicy.action = action
    clock = Clock()
    walk.time.time, walk.time.sleep = clock.time, clock.sleep
    try:
        duck = walk.RLWalk("fake.onnx", commands=True)

        def post(path, body):
            status, _, resp = handle_api("POST", path, json.dumps(body).encode(),
                                         duck.web_bus, clock.now)
            assert status == 200, f"{path} {body} -> {status} {resp!r}"

        def state():
            status, _, resp = handle_api("GET", "/api/state", b"", duck.web_bus, clock.now)
            assert status == 200
            return json.loads(resp)

        duck.xbox_controller.script = scenario(duck, post, state)
        duck.run()
    finally:
        walk.time.time, walk.time.sleep = real_time.time, real_time.sleep
    return duck


def press_a(duck):
    duck.xbox_controller.buttons.A.triggered = True


def edit(post, joint, value):
    post("/api/setting", {"group": "joint_offsets", "key": joint, "value": value})


def hold(post):
    post("/api/setting", {"group": "stance", "key": "hold", "value": True})


def ticks(n):
    for _ in range(n):
        yield


def expected_stance(duck, offsets):
    return {duck.hwi.joints[j]: duck.hwi.init_pos[j] + offsets[j] for j in duck.hwi.joints}


def assert_bounded(writes, limit=MAX_HOLD_DELTA):
    for before, after in zip(writes, writes[1:]):
        for servo, position in after.items():
            delta = abs(position - before[servo])
            assert delta <= limit, f"servo {servo} jumped {delta:.4f} rad in one write"


# --------------------------------------------------------------------------- tests
def test_edit_while_walking_changes_nothing():
    def scenario(duck, post, state):
        yield from ticks(3)
        edit(post, "left_knee", 0.4)
        yield from ticks(5)
        s = state()["joint_offsets"]
        assert s["holding"] is False and s["dirty"] is False
        assert s["offsets"]["left_knee"] == OFFSETS["left_knee"]
        assert duck.hwi.joints_offsets["left_knee"] == OFFSETS["left_knee"]
    run_duck(scenario)


def test_plain_pause_writes_nothing_and_refuses_edits():
    def scenario(duck, post, state):
        yield from ticks(3)
        press_a(duck)
        yield
        yield
        before = len(duck.hwi.io.writes)
        edit(post, "left_knee", 0.4)
        yield from ticks(5)
        assert len(duck.hwi.io.writes) == before, "a plain pause must not move the duck"
        assert state()["joint_offsets"]["offsets"]["left_knee"] == OFFSETS["left_knee"]
    run_duck(scenario)


def test_hold_stance_stops_the_policy_and_glides_without_a_jump():
    def scenario(duck, post, state):
        yield from ticks(5)                       # mid-stride: targets are far from init
        assert duck.hwi.io.goal != expected_stance(duck, OFFSETS)
        calls_before = len(duck.policy.obs)
        hold(post)
        start = len(duck.hwi.io.writes) - 1       # the last goal before the hold
        yield
        yield                                     # telemetry publishes before the write
        s = state()
        assert s["paused"] is True and s["joint_offsets"]["holding"] is True
        assert s["joint_offsets"]["moving"] is True
        yield from ticks(120)
        assert_bounded(duck.hwi.io.writes[start:])
        assert len(duck.policy.obs) == calls_before, "policy must not run during the hold"
        final = duck.hwi.io.goal
        for servo, position in expected_stance(duck, OFFSETS).items():
            assert abs(final[servo] - position) < 1e-9
        assert state()["joint_offsets"]["moving"] is False
    run_duck(scenario)


def test_offset_edit_reaches_the_servo_gradually_and_exactly():
    def scenario(duck, post, state):
        yield
        hold(post)
        yield from ticks(120)
        start = len(duck.hwi.io.writes) - 1
        edit(post, "left_knee", 0.5)
        yield
        s = state()["joint_offsets"]
        assert s["offsets"]["left_knee"] == 0.5 and s["dirty"] is True and s["moving"] is True
        yield from ticks(60)
        assert_bounded(duck.hwi.io.writes[start:], limit=0.5 * DT + 1e-9)
        servo = duck.hwi.joints["left_knee"]
        assert abs(duck.hwi.io.goal[servo] - (duck.hwi.init_pos["left_knee"] + 0.5)) < 1e-9
        # The other 13 joints never moved.
        for other, position in expected_stance(duck, OFFSETS).items():
            if other != servo:
                assert abs(duck.hwi.io.goal[other] - position) < 1e-9
        # Read-back stays consistent: the policy still sees the stance, not the offset.
        present = duck.hwi.get_present_positions()
        assert abs(present[3] - duck.hwi.init_pos["left_knee"]) < 1e-3
    run_duck(scenario)


def test_nan_cannot_reach_the_servos():
    def scenario(duck, post, state):
        yield
        hold(post)
        yield from ticks(3)
        duck.web_bus.push_setting("joint_offsets", "left_knee", float("nan"))
        yield from ticks(3)
        assert np.isfinite(list(duck.hwi.joints_offsets.values())).all()
        assert state()["joint_offsets"]["dirty"] is False
    run_duck(scenario)


def test_horizon_stays_live_while_paused():
    def scenario(duck, post, state):
        yield from ticks(2)
        press_a(duck)
        yield
        yield
        first = state()["imu"]["pitch"]
        yield from ticks(10)
        assert state()["imu"]["pitch"] != first, "IMU readout froze during pause"
    run_duck(scenario)


def test_save_writes_only_the_offsets_and_keeps_a_backup():
    def scenario(duck, post, state):
        yield
        hold(post)
        yield from ticks(5)
        edit(post, "right_knee", 0.2)
        yield
        post("/api/setting", {"group": "joint_offsets", "action": "save"})
        yield
        s = state()["joint_offsets"]
        assert s["dirty"] is False and s["error"] == ""
        assert s["saved"]["right_knee"] == 0.2
    run_duck(scenario)
    with open(CONFIG_PATH) as f:
        saved = json.load(f)
    assert saved["joints_offsets"] == {**OFFSETS, "right_knee": 0.2}
    assert saved["imu_trim"] == {"pitch": 0.01, "roll": -0.02}
    assert saved["some_other_key"] == "keep me"
    assert any(f.endswith(".bak") for f in os.listdir(SANDBOX)), "no backup was taken"


def test_a_failed_save_keeps_the_walk_alive_and_says_why():
    def scenario(duck, post, state):
        yield
        hold(post)
        yield from ticks(3)
        edit(post, "right_knee", 0.2)
        yield
        with open(CONFIG_PATH, "w") as f:
            f.write("{ this is no longer json")
        post("/api/setting", {"group": "joint_offsets", "action": "save"})
        yield from ticks(3)
        s = state()["joint_offsets"]
        assert s["dirty"] is True and "save failed" in s["error"]
    run_duck(scenario)                            # reaching here = the loop survived
    with open(CONFIG_PATH) as f:
        assert f.read() == "{ this is no longer json"   # damaged file left for repair


def test_revert_glides_back_to_saved():
    def scenario(duck, post, state):
        yield
        hold(post)
        yield from ticks(120)
        edit(post, "left_knee", 0.5)
        yield from ticks(60)
        start = len(duck.hwi.io.writes) - 1
        post("/api/setting", {"group": "joint_offsets", "action": "reset"})
        yield from ticks(60)
        assert_bounded(duck.hwi.io.writes[start:], limit=0.5 * DT + 1e-9)
        assert state()["joint_offsets"]["dirty"] is False
        assert duck.hwi.joints_offsets["left_knee"] == OFFSETS["left_knee"]
    run_duck(scenario)


def test_resume_ends_the_hold_and_walks_on_the_unsaved_offset():
    def scenario(duck, post, state):
        yield from ticks(5)
        hold(post)
        yield from ticks(120)
        edit(post, "left_knee", 0.2)
        yield from ticks(30)
        policy_calls = len(duck.policy.obs)
        press_a(duck)
        yield
        yield
        s = state()
        assert s["paused"] is False and s["joint_offsets"]["holding"] is False
        assert s["joint_offsets"]["dirty"] is True
        assert len(duck.policy.obs) > policy_calls, "policy did not take over"
        assert duck.hwi.joints_offsets["left_knee"] == 0.2
        # The first obs after the hold starts clean: no stale mid-stride action history.
        # obs layout: gyro3 accel3 cmd7 pos14 vel14 | last14 last214 last314 | ...
        resumed_obs = duck.policy.obs[policy_calls]
        assert (resumed_obs[41:83] == 0).all(), "stale action history after the hold"
    run_duck(scenario)


def test_resuming_mid_slew_still_finishes_gradually():
    def scenario(duck, post, state):
        yield
        hold(post)
        yield from ticks(120)
        edit(post, "left_knee", 0.5)
        yield from ticks(5)
        start = len(duck.hwi.io.writes) - 1
        press_a(duck)
        yield from ticks(60)
        assert_bounded(duck.hwi.io.writes[start:], limit=0.5 * DT + 1e-9)
        assert duck.hwi.joints_offsets["left_knee"] == 0.5
    run_duck(scenario, action=0.0)


def test_a_new_pause_mid_slew_freezes_the_offset_until_a_writer_returns():
    # With no writes going out, advancing the live offset would bank a jump for the
    # next write. It has to wait.
    def scenario(duck, post, state):
        yield
        hold(post)
        yield from ticks(120)
        edit(post, "left_knee", 0.5)
        yield from ticks(3)
        press_a(duck)                             # resume
        yield
        press_a(duck)                             # plain pause, slew unfinished
        yield
        yield
        frozen = duck.hwi.joints_offsets["left_knee"]
        writes = len(duck.hwi.io.writes)
        assert frozen != 0.5
        yield from ticks(30)
        assert duck.hwi.joints_offsets["left_knee"] == frozen
        assert len(duck.hwi.io.writes) == writes
    run_duck(scenario, action=0.0)


def test_imu_trim_reports_live_and_saved_separately():
    def scenario(duck, post, state):
        yield
        post("/api/trim", {"axis": "pitch", "delta": 0.002})
        yield
        trim = state()["imu_trim"]
        assert abs(trim["pitch"] - 0.012) < 1e-9
        assert trim["saved"] == {"pitch": 0.01, "roll": -0.02}
        post("/api/trim", {"action": "save"})
        yield
        trim = state()["imu_trim"]
        assert abs(trim["saved"]["pitch"] - 0.012) < 1e-9
    run_duck(scenario)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    try:
        for t in tests:
            try:
                t()
                print(f"ok    {t.__name__}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {t.__name__}: {type(e).__name__}: {e}")
    finally:
        os.chdir(HERE)
        shutil.rmtree(SANDBOX, ignore_errors=True)
    print(f"\n{len(tests) - failed} of {len(tests)} walk calibration tests passed.")
    sys.exit(1 if failed else 0)
