"""
Standalone tests for head_life (context animations + sight lead).

Run from this directory:  python3 test_head_life.py
"""
import collections
import math
import random

import head_life as hl

DT = 0.02


def _run(h, seconds, t0=0.0, allowed=True, walking=False, turn=0.0, side=0.0):
    """Step the module; returns [(offsets, ears, body_scale, anim, voice)] per tick."""
    out = []
    for i in range(int(round(seconds / DT))):
        o = h.update(t0 + i * DT, DT, allowed=allowed, turn=turn, side=side, walking=walking)
        out.append((o, list(h.ears), h.body_scale, h.anim, h.pop_voice()))
    return out


def _started(out):
    """Names of the animations that started, in order."""
    names, last = [], ""
    for _, _, _, anim, _ in out:
        if anim and anim != last:
            names.append(anim)
        last = anim
    return names


def test_off_by_default_and_silent():
    h = hl.HeadLife()
    assert h.cfg["look"] is False and h.cfg["lead"] is False
    for o, ears, scale, anim, voice in _run(h, 20.0, walking=True, turn=1.0):
        assert o == [0.0, 0.0, 0.0, 0.0] and ears == [0.0, 0.0] and scale == 1.0
        assert anim == "" and voice is None
    assert h.public()["active"] is False


def test_the_library_has_at_least_15_animations_and_every_one_is_sane():
    assert len(hl.ANIMATIONS) >= 15
    kinds = collections.Counter(a["kind"] for a in hl.ANIMATIONS.values())
    assert kinds["look"] >= 5 and kinds["react"] >= 10
    for name, a in hl.ANIMATIONS.items():
        assert a["walk"] > 0, name                        # every one can come up while walking
        last = a["steps"][-1]
        assert last[2:] == (0.0, 0.0, 0.0, 0.0, 0.0), name   # ends looking straight ahead
        for move, (h0, h1), pitch, yaw, roll, el, er in a["steps"]:
            assert 0.08 <= move <= 1.2 and 0.0 <= h0 <= h1 <= 3.0, name
            assert abs(pitch) <= hl.MAX_OFFSET[1] / 1.5 + 0.1 and -0.40 <= pitch <= 0.30, name
            assert abs(yaw) <= 0.70 and abs(roll) <= 0.30, name
            assert -1.0 <= el <= 1.0 and -1.0 <= er <= 1.0, name
        if a["voice"]:
            assert isinstance(a["voice"][0], str) and 0.0 < a["voice"][1] <= 1.0


def test_walking_is_busy_and_uses_the_whole_library():
    h = hl.HeadLife({"look": True}, rng=random.Random(1))
    out = _run(h, 600.0, walking=True)
    names = _started(out)
    assert len(names) >= 150                               # one every few seconds
    assert len(set(names)) >= 15                           # at least 15 different ones
    assert sum(1 for *_, v in out if v) >= 15              # and it talks now and then
    kinds = collections.Counter(hl.ANIMATIONS[n]["kind"] for n in names)
    assert kinds["react"] > kinds["look"] * 0.6            # plenty of reactions


def test_standing_only_looks_around_and_reacts_very_seldom():
    h = hl.HeadLife({"look": True}, rng=random.Random(2))
    out = _run(h, 1800.0, walking=False)                   # half an hour
    names = _started(out)
    kinds = collections.Counter(hl.ANIMATIONS[n]["kind"] for n in names)
    assert kinds["look"] >= 150                            # it does keep looking around
    assert 5 <= kinds["react"] <= 45                       # a reaction every 40 s to several minutes
    assert kinds["react"] < 0.15 * kinds["look"]
    assert sum(1 for *_, v in out if v) <= 12              # nearly silent
    walk = _started(_run(hl.HeadLife({"look": True}, rng=random.Random(2)), 1800.0, walking=True))
    assert len(walk) > 1.7 * len(names)                    # walking is clearly busier


def test_it_looks_like_a_bird_quick_moves_and_still_moments():
    h = hl.HeadLife({"look": True}, rng=random.Random(3))
    out = [o for o, *_ in _run(h, 300.0, walking=True)]
    still = sum(1 for a, b in zip(out, out[1:]) if all(abs(b[i] - a[i]) < 1e-6 for i in (1, 2, 3)))
    assert still > 0.6 * len(out)                          # mostly holding a look, not scanning
    yaw_speed = [abs(b[2] - a[2]) / DT for a, b in zip(out, out[1:])]
    assert max(yaw_speed) > 2.0                            # and the turns are quick (rad/s)


def test_the_head_leans_and_the_neck_is_never_touched():
    h = hl.HeadLife({"look": True}, rng=random.Random(4))
    out = [o for o, *_ in _run(h, 300.0, walking=True)]
    assert max(abs(o[3]) for o in out) > 0.2               # real left/right lean
    assert max(o[1] for o in out) > 0.15 and min(o[1] for o in out) < -0.2   # up and down
    assert all(o[0] == 0.0 for o in out)


def test_motion_is_smooth_and_inside_the_hard_limits_at_any_setting():
    for cfg in ({}, {"speed": 1.6, "look_scale": 1.5, "busy": 2.0}, {"speed": 0.4, "look_scale": 0.2}):
        h = hl.HeadLife(dict({"look": True, "lead": True}, **cfg), rng=random.Random(5))
        out = _run(h, 120.0, walking=True) + _run(h, 20.0, t0=120.0, walking=True, turn=1.0) \
            + _run(h, 20.0, t0=140.0, walking=True, allowed=False)
        offs = [o for o, *_ in out]
        boost = max(1.0, h.cfg["speed"])
        for i in (1, 2, 3):
            assert max(abs(o[i]) for o in offs) <= hl.MAX_OFFSET[i] + 1e-9
            vel = [(b[i] - a[i]) / DT for a, b in zip(offs, offs[1:])]
            assert max(abs(v) for v in vel) <= hl.MAX_RATE[i] * boost + 1e-6
        ears = [e for _, e, *_ in out]
        assert all(-1.0 <= v <= 1.0 for e in ears for v in e)
        assert max(abs(b[k] - a[k]) / DT for a, b in zip(ears, ears[1:]) for k in (0, 1)) <= hl.EAR_RATE + 1e-6


def test_a_move_starts_and_ends_gently():
    h = hl.HeadLife({"look": True}, rng=random.Random(6))
    h.play("glance_far")
    yaw = [o[2] for o, *_ in _run(h, 0.6)]
    vel = [(b - a) / DT for a, b in zip(yaw, yaw[1:])]
    moving = [abs(v) for v in vel if abs(v) > 1e-9]
    assert moving[0] < 0.2 * max(moving)                   # eases in
    assert moving[-1] < 0.2 * max(moving)                  # eases out


def test_ears_move_with_the_head_and_can_be_switched_off():
    h = hl.HeadLife({"look": True}, rng=random.Random(7))
    out = _run(h, 120.0, walking=True)
    ears = [e for _, e, *_ in out]
    assert max(max(e) for e in ears) > 0.7 and min(min(e) for e in ears) < -0.2
    assert any(abs(e[0] - e[1]) > 0.4 for e in ears)       # not always in unison
    # the discovering tilt perks the ear on the side it leans to
    h = hl.HeadLife({"look": True}, rng=random.Random(8))
    h.play("curious_tilt")
    o, ears, *_ = _run(h, 0.6)[-1]
    assert abs(o[3]) > 0.2
    lean_left = o[3] > 0
    assert (ears[0] > ears[1]) == lean_left and max(ears) > 0.6
    h = hl.HeadLife({"look": True, "ears": False}, rng=random.Random(7))
    assert all(e == [0.0, 0.0] for _, e, *_ in _run(h, 60.0, walking=True))


def test_voice_goes_with_some_animations_and_can_be_switched_off():
    h = hl.HeadLife({"look": True}, rng=random.Random(9))
    said = collections.Counter()
    for _ in range(200):
        h.reset()
        h.play("notice")
        said.update(v for *_, v in _run(h, 0.1, walking=True) if v)
    assert set(said) == {"notice"} and 100 < said["notice"] < 180      # about 70 %
    h = hl.HeadLife({"look": True, "voice": False}, rng=random.Random(9))
    assert not any(v for *_, v in _run(h, 300.0, walking=True))
    h = hl.HeadLife({"look": True}, rng=random.Random(9))
    h.play("glance_side")
    assert not any(v for *_, v in _run(h, 1.0))             # a plain glance is silent


def test_animations_mirror_to_both_sides():
    signs = set()
    for seed in range(30):
        h = hl.HeadLife({"look": True}, rng=random.Random(seed))
        h.play("glance_side")
        signs.add(_run(h, 0.5)[-1][0][2] > 0)
    assert signs == {True, False}


def test_play_by_name():
    h = hl.HeadLife({"look": True}, rng=random.Random(1))
    assert h.play("nope") is False
    for name in hl.ANIMATIONS:
        h.reset()
        assert h.play(name) is True
        out = _run(h, 8.0)
        assert out[2][3] == name
        assert any(any(abs(v) > 0.05 for v in o[1:]) or any(abs(v) > 0.3 for v in e)
                   for o, e, *_ in out[:150]), name       # it visibly does something
    h = hl.HeadLife({"look": False}, rng=random.Random(1))
    h.play("notice")
    assert all(o == [0.0] * 4 for o, *_ in _run(h, 3.0))   # not while look is off


def test_starting_to_walk_does_not_wait_out_a_standing_pause():
    h = hl.HeadLife({"look": True, "busy": 0.3}, rng=random.Random(3))   # long standing gaps
    _run(h, 30.0, walking=False)
    out = _run(h, 8.0, t0=30.0, walking=True)
    assert len(_started(out)) >= 1


def test_not_allowed_glides_everything_back_to_zero():
    h = hl.HeadLife({"look": True}, rng=random.Random(1))
    for seed in range(40):                                 # find a moment with the head away
        _run(h, 1.0, t0=seed, walking=True)
        if any(abs(v) > 0.1 for v in h.out):
            break
    before = h.out[:]
    assert any(abs(v) > 0.1 for v in before)
    out = _run(h, 3.0, t0=100.0, walking=True, allowed=False)   # puppeteer takes the head
    first = out[0][0]
    assert all(abs(a - b) <= hl.MAX_RATE[i] * DT + 1e-9 for i, (a, b) in enumerate(zip(first, before)))
    assert all(abs(v) < 1e-3 for v in out[-1][0]) and all(abs(v) < 1e-3 for v in out[-1][1])
    assert h.public()["active"] is False and h.anim == ""
    assert not _started(out[60:])                          # and nothing new starts


def test_switching_off_mid_animation_also_glides_back():
    h = hl.HeadLife({"look": True}, rng=random.Random(5))
    h.play("look_back")
    _run(h, 0.5)
    h.set({"look": False})
    out = _run(h, 3.0, t0=0.5)
    assert all(abs(v) < 1e-3 for v in out[-1][0])


def test_config_is_clamped_and_junk_ignored():
    c = hl.clean_config({"look": 1, "look_scale": float("nan"), "nonsense": 3, "speed": 9,
                         "busy": "x", "ears": 0, "bob": True, "bob_deg": 5})
    assert c == dict(hl.DEFAULTS, look=True, speed=1.6, ears=False)
    assert hl.clean_config({"look_scale": 9, "lead_s": 5, "busy": 0})["look_scale"] == 1.5
    assert hl.clean_config({"lead_s": 5})["lead_s"] == 0.8 and hl.clean_config({"busy": 0})["busy"] == 0.3
    assert hl.clean_config(None) == hl.DEFAULTS and hl.clean_config("x") == hl.DEFAULTS


def test_bad_inputs_never_raise_or_jump():
    h = hl.HeadLife({"look": True, "lead": True}, rng=random.Random(2))
    _run(h, 5.0, walking=True)
    for dt, turn in ((None, 1.0), (float("nan"), float("nan")), (-1.0, "x"), (5.0, None), ("x", 2.0)):
        prev = h.out[:]
        out = h.update(6.0, dt, turn=turn, side=turn, walking=True)
        assert all(v == v for v in out)
        assert all(abs(a - b) <= hl.MAX_RATE[i] * hl.MAX_DT + 1e-9 for i, (a, b) in enumerate(zip(out, prev)))
        assert all(abs(v) <= hl.MAX_OFFSET[i] + 1e-9 for i, v in enumerate(out))


# ------------------------------------------------------------------ sight lead ----
def test_lead_off_changes_nothing():
    h = hl.HeadLife()
    for o, _, scale, *_ in _run(h, 3.0, turn=1.0, side=0.2, walking=True):
        assert o == [0.0, 0.0, 0.0, 0.0] and scale == 1.0


def test_lead_turns_the_head_first_then_lets_the_body_turn():
    h = hl.HeadLife({"lead": True, "lead_s": 0.25})
    _run(h, 1.0, walking=True)                             # walking straight
    out = _run(h, 2.0, t0=1.0, turn=1.0, walking=True)     # full left turn
    yaw = [o[2] for o, *_ in out]; scale = [s for _, _, s, *_ in out]
    assert all(s == 0.0 for s in scale[:12])               # body held for the first 0.25 s
    assert yaw[12] > 0.15                                  # while the head is already on its way
    assert scale[25] == 1.0                                # body fully turning by 0.5 s
    assert all(b >= a - 1e-12 for a, b in zip(scale, scale[1:]))
    assert abs(yaw[-1] - hl.LEAD_YAW_TURN) < 0.01          # head settles looking into the turn
    assert all(o[0] == 0.0 for o, *_ in out)


def test_lead_follows_the_direction_and_sidesteps_too():
    h = hl.HeadLife({"lead": True})
    assert _run(h, 2.0, turn=-1.0)[-1][0][2] < -0.4        # right turn: head right
    h = hl.HeadLife({"lead": True})
    assert 0.25 < _run(h, 2.0, side=0.2)[-1][0][2] < 0.35  # sidestep left: head left
    h = hl.HeadLife({"lead": True})
    assert abs(_run(h, 2.0, turn=0.5)[-1][0][2] - 0.5 * hl.LEAD_YAW_TURN) < 0.01


def test_lead_ignores_small_corrections_and_never_delays_a_stop():
    h = hl.HeadLife({"lead": True})
    out = _run(h, 2.0, turn=0.1)
    assert all(s == 1.0 for _, _, s, *_ in out) and abs(out[-1][0][2]) < 1e-9
    _run(h, 2.0, t0=2.0, turn=1.0)
    out = _run(h, 2.0, t0=4.0, turn=0.0)
    assert all(s == 1.0 for _, _, s, *_ in out) and abs(out[-1][0][2]) < 5e-3


def test_reversing_the_turn_leads_again_and_zero_head_start_means_together():
    h = hl.HeadLife({"lead": True, "lead_s": 0.25})
    _run(h, 2.0, turn=1.0)
    out = _run(h, 2.0, t0=2.0, turn=-1.0)
    assert out[0][2] == 0.0 and out[30][2] == 1.0 and out[-1][0][2] < -0.4
    h = hl.HeadLife({"lead": True, "lead_s": 0.0})
    out = _run(h, 1.0, turn=1.0)
    assert all(s == 1.0 for _, _, s, *_ in out) and out[-1][0][2] > 0.4


def test_lead_takes_the_head_from_the_animations_but_the_ears_go_on():
    h = hl.HeadLife({"look": True, "lead": True}, rng=random.Random(4))
    _run(h, 20.0, walking=True)
    out = _run(h, 60.0, t0=20.0, turn=1.0, walking=True)
    tail = out[100:]
    assert all(abs(o[2] - hl.LEAD_YAW_TURN) < 0.02 for o, *_ in tail)        # yaw is the lead
    assert all(abs(o[1]) < 0.01 and abs(o[3]) < 0.01 for o, *_ in tail)      # no glances on top
    assert max(max(abs(v) for v in e) for _, e, *_ in tail) > 0.5            # the ears stay alive
    back = _run(h, 30.0, t0=80.0, walking=True)
    assert max(abs(o[1]) + abs(o[3]) for o, *_ in back) > 0.1                # the head comes back to life


def test_lead_stops_when_the_puppeteer_has_the_head():
    h = hl.HeadLife({"lead": True})
    _run(h, 2.0, turn=1.0)
    out = _run(h, 2.0, t0=2.0, turn=1.0, allowed=False)
    assert all(s == 1.0 for _, _, s, *_ in out) and abs(out[-1][0][2]) < 5e-3


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
    print(f"OK - {n} head life tests")
