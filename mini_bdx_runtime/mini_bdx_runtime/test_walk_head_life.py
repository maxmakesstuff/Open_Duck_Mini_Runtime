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
    """The head command the policy was shown (neck, pitch, yaw, roll)."""
    return np.array(duck.policy.obs[-1][CMD][3:7], dtype=float)


def head_sent(duck):
    """Where the head servos were just sent, relative to the stance (neck, pitch, yaw,
    roll). With a policy output of 0 this is: head command + animation offset."""
    names = list(duck.hwi.joints)[5:9]
    w = duck.hwi.io.writes[-1]
    return np.array([w[duck.hwi.joints[n]] - H.OFFSETS[n] - duck.hwi.init_pos[n] for n in names])


def head_seen(duck):
    """What the policy observes of the head: (joint positions - stance, motor targets - stance)."""
    obs = np.array(duck.policy.obs[-1], dtype=float)
    init = np.array(list(duck.hwi.init_pos.values()))[5:9]
    return obs[13 + 5:13 + 9], obs[-4 - 14 + 5:-4 - 14 + 9] - init


def test_off_by_default_the_head_command_is_untouched():
    def scenario(duck, post, state):
        yield from H.ticks(60)
        assert not np.any(head_cmd(duck))
        assert state()["head_life"]["look"] is False and state()["head_life"]["active"] is False
    H.run_duck(scenario)


def test_animations_move_the_head_servos_but_are_hidden_from_the_policy():
    def scenario(duck, post, state):
        import random
        duck.head_life.rng = random.Random(5)              # the same run every time
        yield from H.ticks(5)
        post("/api/setting", {"group": "head", "key": "look", "value": True})
        sent, told, seen_pos, seen_tgt = [], [], [], []
        for _ in range(900):                               # 18 s
            yield
            sent.append(head_sent(duck)); told.append(head_cmd(duck))
            a, b = head_seen(duck); seen_pos.append(a); seen_tgt.append(b)
        sent, told, seen_pos, seen_tgt = map(np.array, (sent, told, seen_pos, seen_tgt))
        assert np.max(np.abs(sent[:, 2])) > 0.1            # the head really turns
        assert not np.any(sent[:, 0])                      # the neck does not
        assert np.all(sent >= walk.HEAD_CMD_LO - 1e-9) and np.all(sent <= walk.HEAD_CMD_HI + 1e-9)
        assert np.max(np.abs(np.diff(sent, axis=0))) <= 5.0 * H.DT + 1e-6      # inside the speed cap
        assert not np.any(told)                            # the policy's head command stays zero
        assert np.max(np.abs(seen_tgt)) < 1e-9             # its "motor targets" show no offset
        # joint positions: during a move the (instant) fake servo and our lag estimate
        # differ for a moment; once a look is held nothing of it is visible
        held = [k for k in range(60, len(sent)) if np.max(np.abs(sent[k] - sent[k - 30])) < 1e-9
                and np.max(np.abs(sent[k])) > 0.1]
        assert held and all(np.max(np.abs(seen_pos[k])) < 0.02 for k in held)   # a held look: invisible
        assert state()["head_life"]["look"] is True and state()["head_life"]["hide"] is True
    H.run_duck(scenario, action=0.0)


def test_with_hide_off_the_policy_is_told_as_before():
    def scenario(duck, post, state):
        post("/api/setting", {"group": "head", "key": "look", "value": True})
        yield from H.ticks(50)
        post("/api/setting", {"group": "head", "key": "play", "value": "look_back"})
        yield from H.ticks(25)
        assert np.max(np.abs(head_cmd(duck))) > 0.2        # in the command
        assert np.allclose(head_sent(duck), head_cmd(duck), atol=1e-6)   # and nothing added on top
    H.run_duck(scenario, action=0.0, config={"head_life": {"hide": False}})


def test_puppeteer_taking_the_head_stops_it():
    def scenario(duck, post, state):
        post("/api/setting", {"group": "head", "key": "look", "value": True})
        yield from H.ticks(50)
        post("/api/setting", {"group": "head", "key": "play", "value": "look_back"})
        yield from H.ticks(25)                             # half a second in: head turned away
        assert np.any(np.abs(head_sent(duck)) > 0.2)
        duck.xbox_controller.head_control_mode = True      # Y pressed
        yield from H.ticks(250)                            # 5 s to glide back
        assert np.max(np.abs(head_sent(duck))) < 2e-3
        yield from H.ticks(300)
        assert np.max(np.abs(head_sent(duck))) < 2e-3      # and it stays off
    H.run_duck(scenario, action=0.0)


class FakeSounds:
    """Stands in for sounds.Sounds: remembers what was asked for."""
    last = None

    def __init__(self, volume=1.0, sound_directory="./", **_):
        self.volume, self.ok, self.sounds = volume, True, {"beep2.wav": object()}
        self.said, self.played = [], []
        FakeSounds.last = self

    def say(self, category, now=None, urgent=False):
        self.said.append((category, bool(urgent)))
        return f"{category}_1.wav"

    def play(self, name):
        self.played.append(name)
        return True

    def play_random(self, now=None):
        self.played.append("random")
        return "curious_2.wav"

    def set_volume(self, v):
        self.volume = float(v)

    def public(self):
        return {"ok": True, "volume": self.volume, "voice": True, "talk": True,
                "categories": ["curious"], "last": ""}


class FakeAntennas:
    last = None

    def __init__(self, *_, **__):
        self.left, self.right = [], []
        FakeAntennas.last = self

    def set_position_left(self, v):
        self.left.append(float(v))

    def set_position_right(self, v):
        self.right.append(float(v))

    def stop(self):
        pass


FEATURES = {"expression_features": {"speaker": True, "antennas": True}}


def with_body(fn):
    """Run a test with a (fake) speaker and (fake) ears fitted."""
    def wrapped():
        keep = walk.Sounds, walk.Antennas
        walk.Sounds, walk.Antennas = FakeSounds, FakeAntennas
        try:
            fn()
        finally:
            walk.Sounds, walk.Antennas = keep
    wrapped.__name__ = fn.__name__
    return wrapped


@with_body
def test_walking_is_busier_than_standing_and_the_ears_and_voice_join_in():
    counts = {}

    def scenario(duck, post, state):
        import random
        duck.head_life.rng = random.Random(11)             # the same run every time
        post("/api/setting", {"group": "head", "key": "look", "value": True})
        post("/api/setting", {"group": "antenna", "key": "free_anim", "value": False})
        for phase in ("stand", "walk"):
            if phase == "walk":
                steer(duck, forward=0.1)
            names, last = [], ""
            for _ in range(3000):                           # one minute
                yield
                anim = duck.head_life.anim
                if anim and anim != last:
                    names.append(anim)
                last = anim
            counts[phase] = names
        ears = FakeAntennas.last
        assert max(ears.left) > 0.6 and max(ears.right) > 0.6      # the ears perk with the head
        assert min(ears.left + ears.right) >= -1.0 and max(ears.left + ears.right) <= 1.0
        said = [c for c, urgent in FakeSounds.last.said if not urgent]
        assert len(said) >= 3                               # it said things while walking
        assert state()["head_life"]["animations"] and state()["sound"]["voice"] is True
    H.run_duck(scenario, action=0.0, config=FEATURES)
    assert len(counts["walk"]) > 1.4 * len(counts["stand"]) and len(counts["walk"]) >= 12
    import head_life
    assert all(head_life.ANIMATIONS[n]["kind"] == "look" for n in counts["stand"])


@with_body
def test_hand_control_of_the_ears_wins_over_the_animation():
    def scenario(duck, post, state):
        post("/api/setting", {"group": "head", "key": "look", "value": True})
        post("/api/setting", {"group": "head", "key": "play", "value": "ears_perk"})
        plain = duck.xbox_controller.get_last_command

        def squeezed():
            cmds, buttons, lt, rt = plain()
            return cmds, buttons, 0.8, 0.0                 # left trigger held
        duck.xbox_controller.get_last_command = squeezed
        yield from H.ticks(40)
        ears = FakeAntennas.last
        assert ears.right[-1] == 0.8 and ears.left[-1] == 0.0      # exactly the triggers (cross-wired)
    H.run_duck(scenario, action=0.0, config=FEATURES)


@with_body
def test_play_by_name_volume_and_say_from_the_app():
    def scenario(duck, post, state):
        yield from H.ticks(3)
        assert ("hello", True) in FakeSounds.last.said      # greets at the start
        post("/api/setting", {"group": "head", "key": "look", "value": True})
        post("/api/setting", {"group": "head", "key": "play", "value": "happy_wiggle"})
        yield from H.ticks(10)
        assert duck.head_life.anim == "happy_wiggle" and state()["head_life"]["anim"] == "happy_wiggle"
        post("/api/setting", {"group": "sound", "key": "volume", "value": 0.4})
        post("/api/setting", {"group": "sound", "key": "say", "value": "curious"})
        yield from H.ticks(3)
        assert FakeSounds.last.volume == 0.4 and ("curious", True) in FakeSounds.last.said
        assert state()["sound"]["volume"] == 0.4
        post("/api/setting", {"group": "sound", "action": "save"})
        yield from H.ticks(3)
    H.run_duck(scenario, action=0.0, config=FEATURES)
    import json
    import time
    for _ in range(50):
        saved = json.load(open(H.CONFIG_PATH))
        if "sound_volume" in saved:
            break
        time.sleep(0.05)
    assert saved["sound_volume"] == 0.4 and saved["joints_offsets"] == H.OFFSETS


def test_no_speaker_no_ears_still_animates_the_head():
    def scenario(duck, post, state):
        post("/api/setting", {"group": "head", "key": "look", "value": True})
        steer(duck, forward=0.1)
        moved = 0.0
        for _ in range(600):
            yield
            moved = max(moved, float(np.max(np.abs(head_sent(duck)))))
        assert moved > 0.2 and state()["sound"] is None
    H.run_duck(scenario, action=0.0)


def steer(duck, turn=0.0, forward=0.0):
    """Make the fake pad hold a turn (and forward) command from now on."""
    plain = duck.xbox_controller.get_last_command

    def pushed():
        cmds, buttons, lt, rt = plain()
        cmds = list(cmds)
        cmds[0], cmds[2] = forward, turn
        return cmds, buttons, lt, rt
    duck.xbox_controller.get_last_command = pushed


def test_sight_lead_head_first_then_the_turn_reaches_the_policy():
    def scenario(duck, post, state):
        post("/api/setting", {"group": "head", "key": "lead", "value": True})
        yield from H.ticks(20)
        steer(duck, turn=0.8, forward=0.1)
        seen, yaw = [], []
        for _ in range(100):
            yield
            seen.append(np.array(duck.policy.obs[-1][CMD], dtype=float)); yaw.append(head_sent(duck)[2])
        seen, yaw = np.array(seen)[2:], np.array(yaw)[2:]  # the pad's new command needs a tick to arrive
        assert np.all(seen[:, 0] == 0.1)                   # forward speed is never held back
        assert np.all(seen[:10, 2] == 0.0)                 # the turn waits ...
        assert yaw[10] > 0.1                               # ... while the head is already turning
        assert abs(seen[-1, 2] - 0.8) < 1e-9               # then the full turn goes through
        assert abs(yaw[-1] - 0.8 * 0.45) < 0.01            # head looks into the turn
        assert np.all(np.diff(seen[:, 2]) >= -1e-12)
        assert not np.any(seen[:, 3:7])                    # and the policy is not told about the head
    H.run_duck(scenario, action=0.0)


def test_sight_lead_off_leaves_the_commands_alone():
    def scenario(duck, post, state):
        yield from H.ticks(20)
        steer(duck, turn=0.8, forward=0.1)
        yield from H.ticks(2)                              # the pad's new command needs a tick to arrive
        for _ in range(40):
            yield
            c = np.array(duck.policy.obs[-1][CMD], dtype=float)
            assert c[2] == 0.8 and not np.any(c[3:7])
    H.run_duck(scenario, action=0.0)


def test_settings_save_to_the_config_and_load_again():
    def scenario(duck, post, state):
        post("/api/setting", {"group": "head", "key": "look", "value": True})
        post("/api/setting", {"group": "head", "key": "look_scale", "value": 99})
        yield from H.ticks(3)
        assert state()["head_life"]["look_scale"] == 1.5   # clamped
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
    import head_life
    assert saved["head_life"] == dict(head_life.DEFAULTS, look=True, look_scale=1.5)
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
