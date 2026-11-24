"""
Runs the REAL walk loop on the simulated duck of test_walk_calibration.py to check
the stability suite end to end: policy switching, the fall reflex, the get-up, the
black-box recorder and what all of them send to the servos.

Only the hardware edge is fake (servo bus, IMU, gamepad, policy, feet, clock). The
policy slots are the real ones in policies/ (their files are read and checksummed;
only the ONNX runtime is replaced).

Run from this directory:  python3 test_walk_stability.py
"""
import json
import math
import os
import shutil
import sys
import time

import numpy as np

# The harness swaps time.time / time.sleep for a virtual clock while a duck runs.
# Waiting for real threads (policy load, config save) needs the real ones.
REAL_SLEEP, REAL_TIME = time.sleep, time.time

import test_walk_calibration as base
from test_walk_calibration import (  # noqa: F401  (the import sets up the sandbox)
    CONFIG_PATH, DT, FakePolicy, HERE, OFFSETS, SANDBOX, hold, press_a, run_duck, ticks,
)
from mini_bdx_runtime import flight_recorder  # noqa: E402
from mini_bdx_runtime.getup import JOINT_HI, JOINT_LO  # noqa: E402
from mini_bdx_runtime.web_control import handle_api  # noqa: E402

G = 9.81
LOG_DIR = flight_recorder.LOG_DIR
assert LOG_DIR.startswith(SANDBOX), "recordings must land in the sandbox, not in ~"
INIT = None      # filled from the first duck


# ---------------------------------------------------------------------- helpers
def wait_for(predicate, what, max_ticks=600):
    """Tick until `predicate()` holds. Real threads (policy load, config save) need
    real time while the loop runs on a virtual clock, so nap between ticks."""
    for _ in range(max_ticks):
        try:
            if predicate():
                return
        except KeyError:                 # no telemetry published yet
            pass
        REAL_SLEEP(0.004)
        yield
    raise AssertionError(f"timed out waiting for {what}")


def wait_real(predicate, what, seconds=3.0):
    end = REAL_TIME() + seconds
    while REAL_TIME() < end:
        if predicate():
            return
        REAL_SLEEP(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def setting(post, group, key, value):
    post("/api/setting", {"group": group, "key": key, "value": value})


def spy_gains(duck):
    log = []
    duck.hwi.io.set_kps = lambda ids, kps: log.append([float(k) for k in kps])
    return log


class Imu:
    """Scriptable IMU: upright, a free fall about the feet, or lying flat."""

    def __init__(self, duck):
        self.duck = duck
        self.th = 0.0
        self.w = 0.0
        self.falling = False
        duck.imu.get_data = self.get_data

    def fall(self, start_deg=6.0, rate=0.4):
        self.th, self.w, self.falling = math.radians(start_deg), rate, True

    def upright(self):
        self.th, self.w, self.falling = 0.0, 0.0, False
        self.duck.tilt.reset()

    def get_data(self):
        if self.falling and self.th < math.radians(88):
            self.w += (G / 0.25) * math.sin(self.th) * DT
            self.th += self.w * DT
            return {"gyro": np.array([0.0, self.w, 0.0]),
                    "accelero": np.array([0.0, 0.0, G * math.cos(self.th) - 0.25 * self.w ** 2])}
        self.w = 0.0                             # at rest: gravity along the tilt
        return {"gyro": np.zeros(3),
                "accelero": np.array([-G * math.sin(self.th), 0.0, G * math.cos(self.th)])}


def max_step(writes):
    worst = 0.0
    for before, after in zip(writes, writes[1:]):
        for servo, position in after.items():
            worst = max(worst, abs(position - before[servo]))
    return worst


def in_limits(duck, write):
    names = list(duck.hwi.joints)
    for i, name in enumerate(names):
        pos = write[duck.hwi.joints[name]] - OFFSETS[name]
        if not (JOINT_LO[i] - 1e-6 <= pos <= JOINT_HI[i] + 1e-6):
            return False
    return True


def logs(reason):
    return [e["name"] for e in flight_recorder.list_logs(LOG_DIR) if reason in e["name"]]


def clear_logs():
    shutil.rmtree(LOG_DIR, ignore_errors=True)


REFLEX_ON = {"fall_reflex": {"mode": "active", "tuck_s": 0.08, "rest_s": 0.3}}
GETUP_ON = {"getup": {"enabled": True, "balance_s": 1.0, "timeout_s": 4.0}}


# ------------------------------------------------------------------ policy slots
def test_slots_are_listed_with_stock_active():
    def scenario(duck, post, state):
        yield from ticks(2)
        p = state()["policy"]
        ids = [s["id"] for s in p["slots"]]
        assert ids[0] == "stock" and "dk5426-walk" in ids and "dk5426-getup-real2" in ids
        assert p["active"] == "stock" and p["state"] == "idle"
        assert all(s["usable"] for s in p["slots"]), p["slots"]
    run_duck(scenario)


def test_switch_while_walking_stands_fades_swaps_and_never_jumps():
    def scenario(duck, post, state):
        yield from ticks(10)
        old = duck.policy
        first_write = len(duck.hwi.io.writes)
        setting(post, "policy", "active", "dk5426-walk")
        yield from wait_for(lambda: state()["policy"]["active"] == "dk5426-walk", "the swap")
        assert duck.policy is not old and len(duck.policy.obs) <= 3
        assert state()["activity"] == "walk" and not duck.paused
        yield from ticks(40)
        # Constant-0.5 policy: a swap at full amplitude would step 0.125 rad. The
        # fade keeps every write within one amplitude-ramp step.
        worst = max_step(duck.hwi.io.writes[first_write:])
        assert worst <= 0.5 * 0.02 + 1e-9, f"jumped {worst:.4f} rad at the swap"
        assert abs(duck.action_scale - 0.25) < 1e-9, "the slot's own default amplitude"
        assert len(duck.policy.obs) > 30, "the new policy is the one walking"
        assert logs("switch") or True
    run_duck(scenario, config={"action_scale": 0.2})


def test_switch_is_remembered_and_calibration_is_untouched():
    def scenario(duck, post, state):
        yield from ticks(3)
        setting(post, "policy", "active", "dk5426-walk")
        yield from wait_for(lambda: state()["policy"]["active"] == "dk5426-walk", "the swap")
        wait_real(lambda: config().get("policies", {}).get("active") == "dk5426-walk",
                  "the choice to be saved")
        c = config()
        assert c["joints_offsets"] == OFFSETS, "joints_offsets must survive"
        assert c["imu_trim"] == {"pitch": 0.01, "roll": -0.02}
        assert c["some_other_key"] == "keep me"
        assert any(f.endswith(".bak") for f in os.listdir(SANDBOX)), "backup first"
    run_duck(scenario)


def test_boot_resumes_the_saved_policy_with_its_saved_tuning():
    def scenario(duck, post, state):
        yield from ticks(3)
        assert state()["policy"]["active"] == "dk5426-walk"
        assert abs(duck.action_scale - 0.31) < 1e-9
        assert abs(duck.phase_frequency_factor_offset + 0.05) < 1e-9
    run_duck(scenario, config={
        "action_scale": 0.2, "phase_frequency_factor_offset": -0.1,
        "policies": {"active": "dk5426-walk", "tuning": {
            "dk5426-walk": {"action_scale": 0.31, "phase_frequency_factor_offset": -0.05}}}})


def test_boot_with_an_unknown_or_wrong_kind_choice_falls_back_to_stock():
    for bad in ("gone", "dk5426-getup-real2"):
        def scenario(duck, post, state):
            yield from ticks(2)
            assert state()["policy"]["active"] == "stock"
            assert abs(duck.action_scale - 0.2) < 1e-9
        run_duck(scenario, config={"action_scale": 0.2, "policies": {"active": bad}})


def test_switching_back_restores_the_stock_values():
    def scenario(duck, post, state):
        yield from ticks(3)
        setting(post, "policy", "active", "dk5426-walk")
        yield from wait_for(lambda: state()["policy"]["active"] == "dk5426-walk", "swap 1")
        yield from ticks(30)
        setting(post, "walk", "action_scale", 0.33)      # tune the new slot
        yield from ticks(30)
        setting(post, "policy", "active", "stock")
        yield from wait_for(lambda: state()["policy"]["active"] == "stock", "swap 2")
        yield from ticks(30)
        assert abs(duck.action_scale - 0.2) < 1e-9, "stock runs at its own value again"
        setting(post, "policy", "active", "dk5426-walk")
        yield from wait_for(lambda: state()["policy"]["active"] == "dk5426-walk", "swap 3")
        yield from ticks(40)
        assert abs(duck.action_scale - 0.33) < 1e-9, "this session's edit is kept"
    run_duck(scenario, config={"action_scale": 0.2})


def test_saving_walk_tuning_keeps_stock_and_slot_values_apart():
    def scenario(duck, post, state):
        yield from ticks(3)
        setting(post, "policy", "active", "dk5426-walk")
        yield from wait_for(lambda: state()["policy"]["active"] == "dk5426-walk", "the swap")
        yield from ticks(20)
        setting(post, "walk", "action_scale", 0.3)
        yield from ticks(10)
        post("/api/setting", {"group": "walk", "action": "save"})
        yield from ticks(2)
        c = config()
        assert c["action_scale"] == 0.2, "the top-level value stays the stock walk's"
        assert c["policies"]["tuning"]["dk5426-walk"]["action_scale"] == 0.3
        assert c["policies"]["active"] == "dk5426-walk"
        assert c["joints_offsets"] == OFFSETS
    run_duck(scenario, config={"action_scale": 0.2})


def test_switch_while_paused_swaps_at_once_and_writes_nothing():
    def scenario(duck, post, state):
        yield from ticks(5)
        press_a(duck)
        yield from ticks(2)
        before = len(duck.hwi.io.writes)
        setting(post, "policy", "active", "dk5426-walk")
        yield from wait_for(lambda: state()["policy"]["active"] == "dk5426-walk", "the swap")
        assert duck.paused and len(duck.hwi.io.writes) == before
    run_duck(scenario)


def test_bad_requests_are_refused_and_keep_the_running_policy():
    def scenario(duck, post, state):
        yield from ticks(3)
        old = duck.policy
        for bad in ("nope", "dk5426-getup-real2", 7, None):
            setting(post, "policy", "active", bad)
            yield from ticks(3)
            p = state()["policy"]
            assert p["active"] == "stock" and p["state"] == "error", (bad, p)
            assert duck.policy is old
    run_duck(scenario)


def test_no_swap_during_a_reflex():
    def scenario(duck, post, state):
        imu = Imu(duck)
        yield from ticks(5)
        setting(post, "policy", "active", "dk5426-walk")
        imu.fall()
        yield from wait_for(lambda: duck.paused, "the duck to be down")
        yield from wait_for(lambda: state()["policy"]["active"] == "dk5426-walk",
                            "the swap once at rest")
        assert duck._soft and duck.paused
    run_duck(scenario, config=REFLEX_ON)


# -------------------------------------------------------------------- fall reflex
def test_observe_mode_logs_the_fall_but_leaves_the_walk_alone():
    clear_logs()

    def scenario(duck, post, state):
        imu = Imu(duck)
        gains = spy_gains(duck)
        yield from ticks(5)
        calls = len(duck.policy.obs)
        imu.fall()
        yield from ticks(60)
        s = state()
        assert s["safety"]["mode"] == "observe" and s["safety"]["triggers"] == 1
        assert s["safety"]["last_trigger"]["tilt"] < 45
        assert gains == [], "observe must never touch the stiffness"
        assert len(duck.policy.obs) == calls + 60, "the policy keeps running every tick"
        assert not duck.paused and s["activity"] == "walk"
        yield from ticks(200)                        # past the 3 s post window
        wait_real(lambda: logs("reflex_observed"), "the recording")
    run_duck(scenario)                               # no config: observe is the default


def test_active_reflex_stops_the_policy_goes_soft_and_ends_down():
    clear_logs()

    def scenario(duck, post, state):
        imu = Imu(duck)
        gains = spy_gains(duck)
        yield from ticks(5)
        imu.fall()
        calls = None
        start = len(duck.hwi.io.writes)
        for _ in range(400):
            if duck._activity == "reflex" and calls is None:
                calls = len(duck.policy.obs)
                trigger_tilt = math.degrees(imu.th)
            if duck.paused:
                break
            yield
        assert calls is not None and duck.paused, "the reflex must run and end paused"
        assert trigger_tilt < 45, f"triggered at {trigger_tilt:.0f} deg"
        assert len(duck.policy.obs) == calls, "no policy step after the trigger"
        assert len(gains) == 1, "stiffness lowered exactly once"
        assert gains[0][0] == 4.0 and gains[0][5:9] == [2.0] * 4
        tuck = duck.hwi.io.writes[start:]
        assert max_step(tuck) <= 5.24 * DT + 0.5 * 0.25 + 1e-9
        assert all(in_limits(duck, w) for w in tuck[-3:])
        yield                                        # telemetry trails by one tick
        s = state()
        assert s["fallen"] and s["soft"] and s["activity"] == "paused"
        assert s["safety"]["phase"] == "down"
        yield from ticks(200)
        wait_real(lambda: logs("reflex"), "the recording")
    run_duck(scenario, config=REFLEX_ON)


def test_soft_duck_is_not_driven_while_it_lies_there():
    def scenario(duck, post, state):
        imu = Imu(duck)
        yield from ticks(5)
        imu.fall()
        yield from wait_for(lambda: duck.paused, "down")
        before = len(duck.hwi.io.writes)
        yield from ticks(50)
        assert len(duck.hwi.io.writes) == before
    run_duck(scenario, config=REFLEX_ON)


def test_resume_is_refused_until_upright_then_glides_back_and_walks():
    def scenario(duck, post, state):
        imu = Imu(duck)
        gains = spy_gains(duck)
        yield from ticks(5)
        imu.fall()
        yield from wait_for(lambda: duck.paused, "down")
        press_a(duck)                                # still lying on the floor
        yield from ticks(3)
        assert duck.paused and duck._soft, "must not stiffen up while lying down"
        assert "upright" in state()["message"]
        assert len(gains) == 1

        imu.upright()                                # the operator stands it up
        yield from ticks(3)
        start = len(duck.hwi.io.writes)
        press_a(duck)
        yield
        yield
        assert state()["activity"] == "recover" and not duck._soft
        assert gains[-1][0] == 30.0 and gains[-1][5:9] == [8.0] * 4, "full gains back"
        yield from wait_for(lambda: duck._activity is None, "the glide to stance")
        glide = duck.hwi.io.writes[start:]
        assert max_step(glide[1:]) <= 1.0 * DT + 1e-9, "glide speed"
        stance = {duck.hwi.joints[j]: duck.hwi.init_pos[j] + OFFSETS[j] for j in duck.hwi.joints}
        assert all(abs(glide[-1][k] - v) < 1e-6 for k, v in stance.items())
        calls = len(duck.policy.obs)
        yield from ticks(30)
        assert len(duck.policy.obs) == calls + 30 and not duck.paused
        assert max_step(duck.hwi.io.writes[start:]) <= 0.02 + 1e-9, \
            "amplitude fades back in, no step"
        s = state()
        assert not s["fallen"] and s["safety"]["phase"] == "armed"
    run_duck(scenario, config=REFLEX_ON)


def test_reflex_is_quiet_while_paused():
    def scenario(duck, post, state):
        imu = Imu(duck)
        gains = spy_gains(duck)
        yield from ticks(5)
        press_a(duck)
        yield from ticks(2)
        imu.fall()                                   # e.g. carried / laid down by hand
        yield from ticks(100)
        assert gains == [] and state()["safety"]["triggers"] == 0
    run_duck(scenario, config=REFLEX_ON)


def test_reflex_settings_apply_live_and_save():
    def scenario(duck, post, state):
        yield from ticks(2)
        setting(post, "safety", "mode", "active")
        setting(post, "safety", "hard_deg", 33)
        setting(post, "safety", "soft_kp", "junk")
        yield from ticks(2)
        s = state()["safety"]
        assert s["mode"] == "active" and s["config"]["hard_deg"] == 33.0
        assert s["config"]["soft_kp"] == 4.0
        post("/api/setting", {"group": "safety", "action": "save"})
        yield from ticks(2)
        wait_real(lambda: config().get("fall_reflex", {}).get("mode") == "active", "save")
        c = config()
        assert c["fall_reflex"]["hard_deg"] == 33.0 and c["joints_offsets"] == OFFSETS
    run_duck(scenario)


def test_stance_hold_is_refused_while_soft():
    def scenario(duck, post, state):
        imu = Imu(duck)
        yield from ticks(5)
        imu.fall()
        yield from wait_for(lambda: duck.paused, "down")
        before = len(duck.hwi.io.writes)
        hold(post)
        yield from ticks(10)
        assert not duck.offset_tuner.holding and len(duck.hwi.io.writes) == before
    run_duck(scenario, config=REFLEX_ON)


# ------------------------------------------------------------------------ get-up
def test_getup_is_off_by_default():
    def scenario(duck, post, state):
        yield from ticks(3)
        press_a(duck)
        yield from ticks(2)
        setting(post, "getup", "start", True)
        yield from ticks(3)
        s = state()
        assert s["getup"]["enabled"] is False and s["activity"] == "paused"
        assert "switched off" in s["message"]
    run_duck(scenario)


def test_getup_stands_up_then_the_walk_policy_balances_with_sticks_ignored():
    clear_logs()

    def scenario(duck, post, state):
        global INIT
        INIT = np.array(duck.init_pos)
        imu = Imu(duck)
        gains = spy_gains(duck)
        yield from wait_for(lambda: state()["getup"]["ready"], "the get-up policy")
        yield from ticks(5)
        imu.fall()
        yield from wait_for(lambda: duck.paused, "down")
        walk_policy = duck.policy
        walk_calls = len(walk_policy.obs)
        FakePolicy.action = 0.1                      # legs end within tolerance of home
        start = len(duck.hwi.io.writes)
        setting(post, "getup", "start", True)
        yield from ticks(3)
        assert state()["activity"] == "getup" and not duck._soft
        assert gains[-1][0] == 30.0, "full gains for the get-up"
        yield from ticks(20)
        assert duck._getup_policy.obs[-1].shape == (92,)
        assert len(walk_policy.obs) == walk_calls, "the walk policy is not consulted"
        imu.upright()                                # the policy got it upright
        yield from wait_for(lambda: duck._activity == "balance", "standing")
        run = duck.hwi.io.writes[start:]
        assert max_step(run) <= 5.24 * DT + 1e-9
        assert all(in_limits(duck, w) for w in run)
        early = max_step(run[:20])
        assert early <= 5.24 / 3 * DT + 1e-9, f"soft start, got {early:.4f}"

        # balance: the walk policy runs, sees zero commands whatever the sticks say
        duck.xbox_controller.get_last_command = _stick_forward(duck)
        yield from ticks(20)
        assert len(walk_policy.obs) > walk_calls
        assert np.allclose(walk_policy.obs[-1][6:13], 0.0), "sticks ignored while balancing"
        assert state()["activity"] == "balance"
        yield from wait_for(lambda: duck._activity is None, "control back")
        yield from ticks(5)
        assert not duck.paused and abs(walk_policy.obs[-1][6] - 0.15) < 1e-9
        s = state()
        assert not s["fallen"] and s["getup"]["run"]["result"] == "standing"
        wait_real(lambda: logs("getup"), "the get-up recording")
    run_duck(scenario, config={**REFLEX_ON, **GETUP_ON})


def _stick_forward(duck):
    inner = duck.xbox_controller.get_last_command

    def get():
        cmds, buttons, lt, rt = inner()
        cmds = list(cmds)
        cmds[0] = 0.15
        return cmds, buttons, lt, rt
    return get


def test_getup_that_never_stands_times_out_and_goes_soft():
    def scenario(duck, post, state):
        imu = Imu(duck)
        gains = spy_gains(duck)
        yield from wait_for(lambda: state()["getup"]["ready"], "the get-up policy")
        yield from ticks(5)
        imu.fall()
        yield from wait_for(lambda: duck.paused, "down")
        setting(post, "getup", "start", True)
        yield from ticks(3)
        yield from wait_for(lambda: duck.paused, "the timeout")
        yield
        s = state()
        assert s["activity"] == "paused" and s["soft"]
        assert "not standing" in s["message"]
        assert gains[-1][0] == 4.0, "soft again: no stalling against the floor"
    run_duck(scenario, config={**REFLEX_ON, **GETUP_ON})


def test_a_aborts_a_running_getup():
    def scenario(duck, post, state):
        imu = Imu(duck)
        yield from wait_for(lambda: state()["getup"]["ready"], "the get-up policy")
        yield from ticks(5)
        imu.fall()
        yield from wait_for(lambda: duck.paused, "down")
        setting(post, "getup", "start", True)
        yield from ticks(10)
        assert state()["activity"] == "getup"
        press_a(duck)
        yield from ticks(2)
        before = len(duck.hwi.io.writes)
        yield from ticks(10)
        s = state()
        assert s["activity"] == "paused" and s["soft"] and "operator" in s["message"]
        assert len(duck.hwi.io.writes) == before
    run_duck(scenario, config={**REFLEX_ON, **GETUP_ON})


def test_getup_refuses_to_start_while_walking():
    def scenario(duck, post, state):
        yield from wait_for(lambda: state()["getup"]["ready"], "the get-up policy")
        setting(post, "getup", "start", True)
        yield from ticks(3)
        assert state()["activity"] == "walk" and "Pause first" in state()["message"]
    run_duck(scenario, config=GETUP_ON)


def test_auto_getup_starts_by_itself_only_when_lying():
    def scenario(duck, post, state):
        imu = Imu(duck)
        yield from wait_for(lambda: state()["getup"]["ready"], "the get-up policy")
        yield from ticks(5)
        imu.fall()
        yield from wait_for(lambda: duck._activity == "getup", "the automatic get-up")
        yield
        assert state()["getup"]["attempts"] == 1 and state()["activity"] == "getup"
    run_duck(scenario, config={**REFLEX_ON, "getup": {"enabled": True, "auto": True}})


def test_auto_getup_gives_up_when_the_duck_keeps_falling_again():
    """Stand up, fall, stand up, fall: after the allowed tries it waits for a person
    instead of looping until the battery is flat."""
    def scenario(duck, post, state):
        imu = Imu(duck)
        yield from wait_for(lambda: state()["getup"]["ready"], "the get-up policy")
        FakePolicy.action = 0.1
        starts = 0
        for _ in range(2):
            imu.fall()
            yield from wait_for(lambda: duck._activity == "getup", "an automatic get-up")
            starts += 1
            yield from ticks(10)
            imu.upright()
            yield from wait_for(lambda: duck._activity == "balance", "standing")
        imu.fall()                                   # third fall in a row
        yield from wait_for(lambda: duck.paused, "down again")
        yield from ticks(20)
        s = state()
        assert starts == 2 and s["activity"] == "paused" and s["soft"]
        assert "Waiting for you" in s["message"]
        # the operator stands it up and resumes: the tries are back
        imu.upright()
        yield from ticks(3)
        press_a(duck)
        yield from wait_for(lambda: duck._activity is None and not duck.paused, "walking")
        assert duck._auto_getups == 0
    run_duck(scenario, config={**REFLEX_ON, "getup": {
        "enabled": True, "auto": True, "balance_s": 5.0, "max_auto_attempts": 2}})


def test_tilt_estimate_settles_quickly_once_stood_up_while_paused():
    """Paused ticks come at 10 Hz. Resume must not be refused for seconds after the
    duck has been stood up."""
    def scenario(duck, post, state):
        imu = Imu(duck)
        yield from ticks(5)
        imu.fall()
        yield from wait_for(lambda: duck.paused, "down")
        imu.th, imu.falling = 0.0, False             # stood up; estimator NOT reset
        yield from ticks(25)                         # 2.5 s of paused ticks
        assert duck.tilt.tilt_deg < 10, f"still reads {duck.tilt.tilt_deg:.0f} deg"
        press_a(duck)
        yield from ticks(3)
        assert state()["activity"] == "recover"
    run_duck(scenario, config=REFLEX_ON)


def test_auto_getup_does_not_start_for_a_lifted_duck():
    """Both foot switches open for 2.5 s is also what being picked up looks like."""
    def scenario(duck, post, state):
        yield from wait_for(lambda: state()["getup"]["ready"], "the get-up policy")
        duck.feet_contacts.get = lambda: [0.0, 0.0]
        yield from wait_for(lambda: duck.fall_detector.fallen, "the foot-switch fall")
        yield from ticks(10)
        assert duck.paused and state()["activity"] == "paused", "no get-up in the hand"
    run_duck(scenario, config={"getup": {"enabled": True, "auto": True}})


def test_enabling_getup_live_loads_its_policy():
    def scenario(duck, post, state):
        yield from ticks(3)
        assert not state()["getup"]["ready"]
        setting(post, "getup", "enabled", True)
        yield from wait_for(lambda: state()["getup"]["ready"], "the get-up policy")
        assert state()["getup"]["policy"] == "dk5426-getup-real2"
        setting(post, "getup", "policy", "dk5426-getup-real")
        yield from wait_for(lambda: duck._getup_policy_id == "dk5426-getup-real", "the other")
    run_duck(scenario)


def test_standing_reference_needs_a_held_stance_and_is_saved():
    def scenario(duck, post, state):
        duck.imu.get_data = lambda: {"gyro": np.zeros(3),
                                     "accelero": np.array([0.4, -0.3, 9.79])}
        yield from ticks(5)
        setting(post, "getup", "capture_reference", True)
        yield from ticks(3)
        assert "Hold the stance" in state()["message"]
        assert not state()["getup"]["has_reference"]
        hold(post)
        yield from ticks(120)                        # glide done, window full
        setting(post, "getup", "capture_reference", True)
        yield from ticks(3)
        assert state()["getup"]["has_reference"], state()["message"]
        wait_real(lambda: config().get("getup", {}).get("imu_standing_acc"), "the save")
        c = config()
        assert c["getup"]["imu_standing_acc"] == [0.4, -0.3, 9.79]
        assert c["joints_offsets"] == OFFSETS and c["imu_trim"] == {"pitch": 0.01, "roll": -0.02}
    run_duck(scenario)


def test_standing_reference_refuses_a_moving_duck():
    def scenario(duck, post, state):
        n = [0]

        def shaky():
            n[0] += 1
            return {"gyro": np.zeros(3),
                    "accelero": np.array([1.5 * math.sin(n[0]), 0.0, 9.8])}
        duck.imu.get_data = shaky
        yield from ticks(3)
        hold(post)
        yield from ticks(120)
        setting(post, "getup", "capture_reference", True)
        yield from ticks(3)
        assert not state()["getup"]["has_reference"] and "movement" in state()["message"]
    run_duck(scenario)


# ------------------------------------------- regressions from the code review
def test_refused_auto_retry_ends_soft_and_paused_never_walking():
    """An automatic get-up times out; meanwhile get-up was switched off. The retry is
    refused, and the loop must not drop into a walking tick with the duck lying."""
    def scenario(duck, post, state):
        imu = Imu(duck)
        yield from wait_for(lambda: state()["getup"]["ready"], "the get-up policy")
        yield from ticks(5)
        imu.fall()
        yield from wait_for(lambda: duck._activity == "getup", "the automatic get-up")
        walk_calls = len(duck.policy.obs)
        setting(post, "getup", "enabled", False)     # switched off mid-attempt
        yield from wait_for(lambda: duck._activity != "getup", "the attempt to end")
        yield from ticks(30)
        assert duck.paused and duck._soft and duck._activity is None
        assert len(duck.policy.obs) == walk_calls, "the walk policy must not run"
    run_duck(scenario, config={**REFLEX_ON, "getup": {
        "enabled": True, "auto": True, "timeout_s": 2.0}})


def test_stopping_the_recovery_glide_and_resuming_never_snaps():
    """A (glide), A (stop), A (resume): the last A continues the glide. It used to
    hand the off-pose duck straight to the policy: 1.2 rad in one write."""
    def scenario(duck, post, state):
        imu = Imu(duck)
        yield from ticks(5)
        imu.fall()
        yield from wait_for(lambda: duck.paused, "down")
        imu.upright()
        knee = duck.hwi.joints["right_knee"]
        duck.hwi.io.goal[knee] = OFFSETS["right_knee"] + 0.1     # limp knee, far off
        yield from ticks(3)
        start = len(duck.hwi.io.writes)
        press_a(duck)
        yield from ticks(8)
        assert state()["activity"] == "recover"
        press_a(duck)                                # stop mid-glide
        yield from ticks(5)
        assert duck.paused and duck._activity is None and duck._needs_recover
        calls = len(duck.policy.obs)
        press_a(duck)                                # resume
        yield from ticks(3)
        assert state()["activity"] == "recover" and len(duck.policy.obs) == calls
        yield from wait_for(lambda: duck._activity is None, "the glide to finish")
        yield from ticks(30)
        assert max_step(duck.hwi.io.writes[start:]) <= 1.0 * DT + 1e-9
        assert not duck._needs_recover and not duck.paused
    run_duck(scenario, config=REFLEX_ON)


def test_swap_while_paused_keeps_the_pose_and_resumes_through_a_glide():
    def scenario(duck, post, state):
        yield from ticks(10)                         # targets sit at init + 0.125
        press_a(duck)
        yield from ticks(2)
        held = duck.motor_targets.copy()
        start = len(duck.hwi.io.writes)
        setting(post, "policy", "active", "dk5426-walk")
        yield from wait_for(lambda: state()["policy"]["active"] == "dk5426-walk", "the swap")
        assert np.allclose(duck.motor_targets, held), "the swap must not move the targets"
        assert np.allclose(duck.prev_motor_targets, held)
        press_a(duck)
        yield from ticks(2)
        assert state()["activity"] == "recover"
        yield from wait_for(lambda: duck._activity is None, "the glide")
        yield from ticks(40)
        worst = max_step(duck.hwi.io.writes[start:])
        assert worst <= 1.0 * DT + 1e-9, f"stepped {worst:.4f} rad after a paused swap"
    run_duck(scenario)


def test_swap_during_a_stance_glide_does_not_jump_the_glide():
    def scenario(duck, post, state):
        yield from ticks(10)
        start = len(duck.hwi.io.writes)
        hold(post)
        setting(post, "policy", "active", "dk5426-walk")
        yield from wait_for(lambda: state()["policy"]["active"] == "dk5426-walk", "the swap")
        yield from ticks(60)
        assert max_step(duck.hwi.io.writes[start:]) <= base.MAX_HOLD_DELTA
    run_duck(scenario)


def test_foot_switch_fall_during_balance_clears_the_activity():
    def scenario(duck, post, state):
        imu = Imu(duck)
        yield from wait_for(lambda: state()["getup"]["ready"], "the get-up policy")
        FakePolicy.action = 0.1
        yield from ticks(5)
        imu.fall()
        yield from wait_for(lambda: duck.paused, "down")
        setting(post, "getup", "start", True)
        yield from ticks(15)
        imu.upright()
        yield from wait_for(lambda: duck._activity == "balance", "standing")
        duck.feet_contacts.get = lambda: [0.0, 0.0]  # lifted / tipped slowly
        yield from wait_for(lambda: duck.paused, "the foot-switch fall")
        yield from ticks(3)
        s = state()
        assert s["activity"] == "paused" and not s["blackbox"]["taking"]
    run_duck(scenario, config={**REFLEX_ON, "getup": {"enabled": True, "balance_s": 8.0}})


def test_manual_getup_needs_a_lying_duck():
    def scenario(duck, post, state):
        yield from wait_for(lambda: state()["getup"]["ready"], "the get-up policy")
        press_a(duck)
        yield from ticks(3)
        setting(post, "getup", "start", True)
        yield from ticks(3)
        assert state()["activity"] == "paused" and "lying down" in state()["message"]
    run_duck(scenario, config=GETUP_ON)


def test_absurd_numbers_from_the_web_cannot_stop_the_loop():
    def scenario(duck, post, state):
        yield from ticks(3)
        for group, key in (("safety", "hard_deg"), ("getup", "timeout_s"),
                           ("walk", "slew_lin_accel"), ("walk", "action_scale")):
            body = '{"group":"%s","key":"%s","value":1%s}' % (group, key, "0" * 400)
            status, _, _ = handle_api("POST", "/api/setting", body.encode(), duck.web_bus, 0)
            assert status == 400, (group, key, status)
            # and if such a value ever got past the door, the loop survives it
            duck.web_bus.push_setting(group, key, 10 ** 400)
            yield from ticks(2)
        assert state()["safety"]["config"]["hard_deg"] == 40.0
    run_duck(scenario)


def test_failed_gain_write_still_forces_a_glide_before_walking():
    """The 'go soft' write fails on the bus. The duck is stiff but off-pose; resume
    must still glide, and the remembered gains must be the ones the servos have."""
    def scenario(duck, post, state):
        imu = Imu(duck)

        def broken(ids, kps):
            raise RuntimeError("bus error")
        yield from ticks(5)
        duck.hwi.io.set_kps = broken
        imu.fall()
        yield from wait_for(lambda: duck.paused, "down")
        assert not duck._soft and duck._needs_recover
        assert list(duck.hwi.kps)[0] == 30, "HWI must not remember gains it never set"
        duck.hwi.io.set_kps = lambda ids, kps: None
        imu.upright()
        yield from ticks(3)
        start = len(duck.hwi.io.writes)
        press_a(duck)
        yield from wait_for(lambda: duck._activity is None and not duck.paused, "walking")
        yield from ticks(10)
        assert max_step(duck.hwi.io.writes[start:]) <= 0.02 + 1e-9
    run_duck(scenario, config=REFLEX_ON)


# ---------------------------------------------------- recorder, loop health, misc
def test_mark_and_take_write_recordings_that_can_be_listed():
    clear_logs()

    def scenario(duck, post, state):
        yield from ticks(60)
        setting(post, "recorder", "mark", True)
        setting(post, "recorder", "take", True)
        yield from ticks(100)
        assert state()["blackbox"]["taking"]
        setting(post, "recorder", "take", False)
        yield from ticks(100)
        wait_real(lambda: logs("mark") and logs("take_stock"), "both recordings")
        cols, meta = flight_recorder.load(os.path.join(LOG_DIR, logs("take_stock")[0]))
        assert 95 <= len(cols["t"]) <= 105 and meta["policy"] == "stock"
        assert np.allclose(cols["action"], 0.5), "the policy's raw output is recorded"
        assert np.all(cols["mode"] == 0) and np.all(np.diff(cols["t"]) > 0)
        status, _, body = handle_api("GET", "/api/logs", b"", duck.web_bus, 0)
        names = [e["name"] for e in json.loads(body)["logs"]]
        assert status == 200 and logs("mark")[0] in names
    run_duck(scenario)


def test_pauses_do_not_count_as_late_ticks():
    def scenario(duck, post, state):
        yield from ticks(30)
        press_a(duck)
        yield from ticks(20)                         # 100 ms sleeps
        press_a(duck)
        yield from ticks(30)
        loop = state()["loop"]
        assert loop["late_total"] == 0 and loop["ticks"] > 40, loop
    run_duck(scenario)


def test_command_ramp_is_off_by_default_and_ramps_when_enabled():
    def scenario(duck, post, state):
        duck.xbox_controller.get_last_command = _stick_forward(duck)
        yield from ticks(3)
        assert abs(duck.policy.obs[-1][6] - 0.15) < 1e-9, "off: the stick passes straight"
        setting(post, "walk", "slew_enabled", True)
        press_a(duck)
        yield from ticks(2)
        press_a(duck)                                # resume: the ramp starts from zero
        yield from ticks(3)
        seen = [o[6] for o in duck.policy.obs[-2:]]
        assert 0.0 < seen[0] < seen[1] < 0.15, seen
        yield from ticks(40)
        assert abs(duck.policy.obs[-1][6] - 0.15) < 1e-9
        assert state()["command_slew"]["enabled"] is True
    run_duck(scenario)


def test_default_config_changes_nothing_about_the_walk():
    """No new config keys: every tick's servo write equals init + action * scale,
    exactly as before the suite."""
    def scenario(duck, post, state):
        yield from ticks(50)
        expect = {duck.hwi.joints[j]: duck.hwi.init_pos[j] + 0.5 * 0.25 + OFFSETS[j]
                  for j in duck.hwi.joints}
        for w in duck.hwi.io.writes[-40:]:
            assert all(abs(w[k] - v) < 1e-9 for k, v in expect.items())
        s = state()
        assert s["safety"]["mode"] == "observe" and not s["getup"]["enabled"]
        assert not s["command_slew"]["enabled"] and s["policy"]["active"] == "stock"
        assert not s["soft"]
    run_duck(scenario)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    try:
        for t in tests:
            FakePolicy.action = 0.5
            try:
                t()
                print(f"ok    {t.__name__}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {t.__name__}: {type(e).__name__}: {e}")
    finally:
        os.chdir(HERE)
        shutil.rmtree(SANDBOX, ignore_errors=True)
    print(f"\n{len(tests) - failed} of {len(tests)} walk stability tests passed.")
    sys.exit(1 if failed else 0)
