"""
Head life inside the real walk loop (fakes for every piece of hardware, borrowed from
test_walk_calibration).

Run from this directory:  python3 test_walk_head_life.py
"""
import os
import shutil
import sys

import numpy as np

import test_walk_calibration as H

walk = H.walk
HEAD = slice(5, 9)                    # head joints in the 14 targets
CMD = slice(6, 13)                    # the 7 commands inside the 101 observation


def head_cmd(duck):
    return np.array(duck.policy.obs[-1][CMD][3:7], dtype=float)


def drive(duck, forward=0.1):
    """Make the fake pad push the stick forward from now on."""
    plain = duck.xbox_controller.get_last_command

    def pushed():
        cmds, buttons, lt, rt = plain()
        cmds = list(cmds)
        cmds[0] = forward
        return cmds, buttons, lt, rt
    duck.xbox_controller.get_last_command = pushed


def test_off_by_default_the_head_command_is_untouched():
    def scenario(duck, post, state):
        yield from H.ticks(60)
        assert not np.any(head_cmd(duck))
        assert state()["head_life"]["look"] is False and state()["head_life"]["active"] is False
    H.run_duck(scenario)


def test_look_around_reaches_the_policy_and_the_servos_smoothly():
    def scenario(duck, post, state):
        yield from H.ticks(5)
        post("/api/setting", {"group": "head", "key": "look", "value": True})
        seen = []
        first = len(duck.hwi.io.writes)
        for _ in range(900):                               # 18 s
            yield
            seen.append(head_cmd(duck))
        seen = np.array(seen)
        assert np.max(np.abs(seen[:, 2])) > 0.1            # the yaw command moves
        assert not np.any(seen[:, 0])                      # the neck does not
        assert np.all(seen >= walk.HEAD_CMD_LO - 1e-9) and np.all(seen <= walk.HEAD_CMD_HI + 1e-9)
        assert np.max(np.abs(np.diff(seen, axis=0))) <= 1.0 * H.DT + 1e-6     # no faster than 1 rad/s
        # what the policy was told is what the head servos were sent (on top of the policy's own head action)
        ids = [duck.hwi.joints[j] for j in list(duck.hwi.joints)[5:9]]
        w = duck.hwi.io.writes[first:]
        yaw = np.array([x[ids[2]] for x in w])
        assert np.ptp(yaw) > 0.1
        assert state()["head_life"]["active"] is True
    H.run_duck(scenario, action=0.0)


def test_puppeteer_taking_the_head_stops_it():
    def scenario(duck, post, state):
        post("/api/setting", {"group": "head", "key": "look", "value": True})
        yield from H.ticks(600)
        assert np.any(np.abs(head_cmd(duck)) > 0.02)
        duck.xbox_controller.head_control_mode = True      # Y pressed
        yield from H.ticks(250)                            # 5 s to glide back
        assert np.max(np.abs(head_cmd(duck))) < 2e-3
        yield from H.ticks(300)
        assert np.max(np.abs(head_cmd(duck))) < 2e-3       # and it stays off
    H.run_duck(scenario, action=0.0)


def test_bob_only_while_driving_and_only_on_the_neck():
    def scenario(duck, post, state):
        post("/api/setting", {"group": "head", "key": "bob", "value": True})
        post("/api/setting", {"group": "head", "key": "bob_deg", "value": 4.0})
        yield from H.ticks(100)
        assert not np.any(head_cmd(duck))                  # standing: no bob
        drive(duck)
        seen = []
        for _ in range(200):
            yield
            seen.append(head_cmd(duck))
        seen = np.array(seen)
        assert abs(np.max(seen[100:, 0]) - np.radians(4.0)) < 0.01
        assert abs(np.min(seen[100:, 0]) + np.radians(4.0)) < 0.01
        assert not np.any(seen[:, 1:])
    H.run_duck(scenario, action=0.0)


def test_settings_save_to_the_config_and_load_again():
    def scenario(duck, post, state):
        post("/api/setting", {"group": "head", "key": "look", "value": True})
        post("/api/setting", {"group": "head", "key": "bob_deg", "value": 99})
        yield from H.ticks(3)
        assert state()["head_life"]["bob_deg"] == 10.0     # clamped
        post("/api/setting", {"group": "head", "action": "save"})
        yield from H.ticks(3)
    H.run_duck(scenario)
    import json
    import time
    for _ in range(50):                                    # the save runs on a thread
        saved = json.load(open(H.CONFIG_PATH))
        if "head_life" in saved:
            break
        time.sleep(0.05)
    assert saved["head_life"]["look"] is True and saved["head_life"]["bob_deg"] == 10.0
    assert saved["joints_offsets"] == H.OFFSETS and saved["some_other_key"] == "keep me"

    def again(duck, post, state):
        yield
        assert state()["head_life"]["look"] is True
    H.run_duck(again, config={"head_life": saved["head_life"]})


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
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
        os.chdir(H.HERE)
        shutil.rmtree(H.SANDBOX, ignore_errors=True)
    print(f"\n{len(tests) - failed} of {len(tests)} walk head-life tests passed.")
    sys.exit(1 if failed else 0)
