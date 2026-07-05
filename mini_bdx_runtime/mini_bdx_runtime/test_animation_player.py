"""
Standalone test suite for animation_player (no pytest dependency).

Run from this directory:
    python3 test_animation_player.py

The module under test is pure stdlib, so this runs off-robot (no Adafruit/
hardware imports). The final test also validates the *real* scripts/animations.json
against the motor safety bounds.
"""
import os
import json
import tempfile

from animation_player import (
    AnimationPlayer,
    clamp,
    clamp_head_targets,
    slew_limit,
    HEAD_JOINT_ORDER,
    HEAD_LIMITS,
)

EPS = 1e-6
HERE = os.path.dirname(os.path.abspath(__file__))
REAL_ANIMATIONS_JSON = os.path.normpath(
    os.path.join(HERE, "..", "..", "scripts", "animations.json")
)


def approx(a, b, eps=EPS):
    return abs(a - b) <= eps


def approx_list(a, b, eps=EPS):
    return len(a) == len(b) and all(approx(x, y, eps) for x, y in zip(a, b))


def _basic_data():
    return {
        "dpad": {
            "up": "nod",
            "down": "nod",
            "left": "nod",
            "right": "nod",
        },
        "animations": {
            "nod": {
                "loop": False,
                "head": [
                    {"t": 0.0},
                    {"t": 1.0, "head_pitch": 0.2},
                ],
                "antennas": [
                    {"t": 0.0, "left": 0.0, "right": 0.0},
                    {"t": 1.0, "left": 0.5, "right": -0.5},
                ],
                "sounds": [{"t": 0.0, "name": "a.wav"}, {"t": 0.5, "name": "b.wav"}],
                "lamp": [{"t": 0.0, "state": True}, {"t": 0.5, "state": False}],
            }
        },
    }


# ---------------------------------------------------------------- pure helpers

def test_clamp():
    assert approx(clamp(5.0, -1.0, 1.0), 1.0)
    assert approx(clamp(-5.0, -1.0, 1.0), -1.0)
    assert approx(clamp(0.3, -1.0, 1.0), 0.3)


def test_clamp_head_targets_pins_each_joint_to_its_limit():
    # order: neck_pitch, head_pitch, head_yaw, head_roll
    out = clamp_head_targets([2.0, -2.0, 1.0, -1.0])
    assert approx_list(out, [1.1, -0.78, 0.5, -0.5]), out


def test_clamp_head_targets_passes_in_range_values():
    out = clamp_head_targets([0.1, -0.1, 0.2, -0.2])
    assert approx_list(out, [0.1, -0.1, 0.2, -0.2]), out


def test_slew_limit_caps_per_element_delta():
    out = slew_limit([0.0, 0.0, 0.0, 0.0], [1.0, -1.0, 0.0, 0.05], 0.08)
    assert approx_list(out, [0.08, -0.08, 0.0, 0.05]), out


def test_slew_limit_passes_small_deltas():
    out = slew_limit([0.10, 0.0, 0.0, 0.0], [0.13, 0.0, 0.0, 0.0], 0.08)
    assert approx_list(out, [0.13, 0.0, 0.0, 0.0]), out


# -------------------------------------------------------------- loading / map

def test_load_from_dict_basic():
    p = AnimationPlayer.from_dict(_basic_data())
    assert p.has("nod")
    assert not p.has("missing")
    assert p.dpad_map["up"] == "nod"
    assert p.dpad_map["right"] == "nod"
    assert p.warnings == [], p.warnings


def test_file_load_roundtrip():
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(_basic_data(), f)
        path = f.name
    try:
        p = AnimationPlayer(path)
        assert p.has("nod")
    finally:
        os.unlink(path)


def test_start_unknown_animation_raises():
    p = AnimationPlayer.from_dict(_basic_data())
    raised = False
    try:
        p.start("does_not_exist", 0.0)
    except ValueError:
        raised = True
    assert raised


# ----------------------------------------------------------- interpolation

def test_head_interpolation_midpoint_and_hold():
    p = AnimationPlayer.from_dict(_basic_data())
    p.start("nod", 100.0)
    # before/at start -> first keyframe (all zero)
    out = p.update(100.0)
    assert approx_list(out["head"], [0.0, 0.0, 0.0, 0.0]), out["head"]
    # midpoint -> half of head_pitch target (0.2 -> 0.1)
    out = p.update(100.5)
    assert approx(out["head"][1], 0.1), out["head"]
    # past end -> hold last keyframe (0.2)
    out = p.update(102.0)
    assert approx(out["head"][1], 0.2), out["head"]


def test_antenna_interpolation():
    p = AnimationPlayer.from_dict(_basic_data())
    p.start("nod", 0.0)
    out = p.update(0.5)  # halfway: left 0->0.5 => 0.25, right 0->-0.5 => -0.25
    assert approx_list(out["antennas"], [0.25, -0.25]), out["antennas"]


# ----------------------------------------------------------- discrete events

def test_sound_events_fire_exactly_once():
    p = AnimationPlayer.from_dict(_basic_data())
    p.start("nod", 0.0)
    fired = []
    for t in [0.0, 0.1, 0.2, 0.5, 0.6, 1.0, 1.5]:
        fired.extend(p.update(t)["sounds"])
    assert fired.count("a.wav") == 1, fired
    assert fired.count("b.wav") == 1, fired
    # a.wav (t=0) fires before b.wav (t=0.5)
    assert fired.index("a.wav") < fired.index("b.wav"), fired


def test_lamp_emits_only_on_change():
    p = AnimationPlayer.from_dict(_basic_data())
    p.start("nod", 0.0)
    assert p.update(0.0)["lamp"] is True       # turns on
    assert p.update(0.2)["lamp"] is None        # unchanged
    assert p.update(0.5)["lamp"] is False       # turns off
    assert p.update(0.8)["lamp"] is None        # unchanged


def test_finished_flag():
    p = AnimationPlayer.from_dict(_basic_data())
    p.start("nod", 0.0)
    assert p.update(0.5)["finished"] is False
    assert p.update(1.0)["finished"] is True
    assert p.update(1.5)["finished"] is True


def test_loop_rewinds_and_refires_events():
    data = _basic_data()
    data["animations"]["nod"]["loop"] = True
    p = AnimationPlayer.from_dict(data)
    p.start("nod", 0.0)
    first = []
    for t in [0.0, 0.5]:
        first.extend(p.update(t)["sounds"])
    assert first.count("a.wav") == 1 and first.count("b.wav") == 1, first
    # wrap past duration 1.0 -> events should be available to fire again
    second = []
    for t in [1.0, 1.5]:
        second.extend(p.update(t)["sounds"])
    assert second.count("a.wav") == 1, second
    assert second.count("b.wav") == 1, second
    # never "finished" while looping
    assert p.update(3.3)["finished"] is False


# --------------------------------------------------------- safety at load time

def test_out_of_range_head_keyframe_is_clamped_and_warned():
    data = _basic_data()
    data["animations"]["nod"]["head"] = [
        {"t": 0.0},
        {"t": 1.0, "head_pitch": 5.0, "head_yaw": -9.0},
    ]
    p = AnimationPlayer.from_dict(data)
    assert any("head_pitch" in w for w in p.warnings), p.warnings
    p.start("nod", 0.0)
    out = p.update(1.0)
    # clamped to head_pitch hi 0.3 and head_yaw lo -0.5
    assert approx(out["head"][1], 0.3), out["head"]
    assert approx(out["head"][2], -0.5), out["head"]


def test_out_of_range_antenna_is_clamped_and_warned():
    data = _basic_data()
    data["animations"]["nod"]["antennas"] = [{"t": 0.0, "left": 3.0, "right": -3.0}]
    p = AnimationPlayer.from_dict(data)
    assert any("antenna" in w.lower() for w in p.warnings), p.warnings
    p.start("nod", 0.0)
    out = p.update(0.0)
    assert approx_list(out["antennas"], [1.0, -1.0]), out["antennas"]


def test_unknown_dpad_target_warns():
    data = _basic_data()
    data["dpad"]["up"] = "ghost"
    p = AnimationPlayer.from_dict(data)
    assert any("ghost" in w for w in p.warnings), p.warnings


def test_unknown_sound_warns_when_known_sounds_provided():
    data = _basic_data()
    p = AnimationPlayer.from_dict(data, known_sounds={"a.wav"})  # b.wav unknown
    assert any("b.wav" in w for w in p.warnings), p.warnings


# --------------------------------------------- ACCEPTANCE: real animations.json

def test_real_animations_json_is_within_safety_bounds():
    """The shipped animations must never command a head target out of HEAD_LIMITS,
    and (sampled at 50 Hz) never exceed the slew rate. This is the safety gate."""
    if not os.path.exists(REAL_ANIMATIONS_JSON):
        raise AssertionError(
            f"animations.json not found at {REAL_ANIMATIONS_JSON} "
            "(author it before shipping)"
        )
    known = {
        "beep1.wav", "beep2.wav", "happy1.wav", "happy2.wav", "happy3.wav",
        "motor.wav",
    }  # lamp*.wav moved to assets/scanner/ (not in rotation)
    p = AnimationPlayer(REAL_ANIMATIONS_JSON, known_sounds=known)
    # Authored content must already be in-bounds -> zero load warnings.
    assert p.warnings == [], f"animations.json has issues: {p.warnings}"

    control_freq = 50.0
    dt = 1.0 / control_freq
    max_delta = 4.0 / control_freq  # MAX_HEAD_VELOCITY / control_freq
    for name in p.animation_names():
        p.start(name, 0.0)
        duration = p.duration(name)
        prev = None
        t = 0.0
        while t <= duration + 0.5:  # include the post-finish hold
            head = p.update(t)["head"]
            if head is not None:
                for i, joint in enumerate(HEAD_JOINT_ORDER):
                    lo, hi = HEAD_LIMITS[joint]
                    assert lo - EPS <= head[i] <= hi + EPS, (
                        f"{name}:{joint}={head[i]} out of [{lo},{hi}]"
                    )
                if prev is not None:
                    for i in range(4):
                        assert abs(head[i] - prev[i]) <= max_delta + EPS, (
                            f"{name} head[{i}] step {abs(head[i]-prev[i]):.4f} "
                            f"> slew {max_delta:.4f}"
                        )
                prev = head
            t += dt


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    import sys

    sys.exit(1 if _run() else 0)
