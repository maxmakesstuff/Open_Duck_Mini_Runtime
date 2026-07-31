"""
Off-robot tests for the walk loop's record/playback + command-source glue.

v2_rl_walk_mujoco imports Pi-only hardware modules, so we fake the hardware
submodules but keep the REAL walk_record / fall_detector / control_bus /
telemetry, then drive the RLWalk helper methods on an instance built with
__new__ (no hardware init). This covers the safety-relevant path where recorded
commands override the live command vector.

Run from scripts/:  python3 test_walk_integration_logic.py
"""
import os
import sys
import types
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
MINI = os.path.normpath(os.path.join(HERE, "..", "mini_bdx_runtime", "mini_bdx_runtime"))
sys.path.insert(0, MINI)   # so the REAL pure modules import flat

# real pure modules we want the walk loop to actually use
import walk_record as _walk_record          # noqa: E402
import fall_detector as _fall_detector       # noqa: E402
import control_bus as _control_bus           # noqa: E402
import telemetry as _telemetry               # noqa: E402
import stability_governor as _stability_governor  # noqa: E402
import antenna_anim as _antenna_anim         # noqa: E402
import walk_defaults as _walk_defaults       # noqa: E402


def _install_fakes():
    pkg = types.ModuleType("mini_bdx_runtime")
    pkg.__path__ = []
    sys.modules["mini_bdx_runtime"] = pkg
    # real modules, exposed under the package name
    for short, mod in [("walk_record", _walk_record), ("fall_detector", _fall_detector),
                       ("control_bus", _control_bus), ("telemetry", _telemetry),
                       ("stability_governor", _stability_governor),
                       ("antenna_anim", _antenna_anim),
                       ("walk_defaults", _walk_defaults)]:
        sys.modules[f"mini_bdx_runtime.{short}"] = mod
    _D = type("Dummy", (), {})
    for name, attrs in [
        ("rustypot_position_hwi", ["HWI"]),
        ("onnx_infer", ["OnnxInfer"]),
        ("raw_imu", ["Imu"]),
        ("poly_reference_motion", ["PolyReferenceMotion"]),
        ("xbox_controller", ["XBoxController"]),
        ("feet_contacts", ["FeetContacts"]),
        ("eyes", ["Eyes"]),
        ("sounds", ["Sounds"]),
        ("antennas", ["Antennas"]),
        ("projector", ["Projector"]),
        ("duck_config", ["DuckConfig"]),
        ("rl_utils", ["make_action_dict", "LowPassActionFilter"]),
        ("web_control", ["WebControlServer"]),
        ("scanner_sound", ["ScannerSound", "PygameScannerBackend"]),
    ]:
        m = types.ModuleType(f"mini_bdx_runtime.{name}")
        for a in attrs:
            setattr(m, a, _D)
        sys.modules[f"mini_bdx_runtime.{name}"] = m


_install_fakes()
sys.path.insert(0, HERE)
import v2_rl_walk_mujoco as W  # noqa: E402
from walk_record import clamp_frame  # noqa: E402


def _btn(is_pressed=False, triggered=False):
    return SimpleNamespace(is_pressed=is_pressed, triggered=triggered)


def _buttons(**kw):
    names = ["A", "B", "X", "Y", "LB", "RB",
             "dpad_up", "dpad_down", "dpad_left", "dpad_right"]
    return SimpleNamespace(**{n: _btn(**kw.get(n, {})) for n in names})


def _mk():
    """A minimal RLWalk with just the fields the helpers touch."""
    w = W.RLWalk.__new__(W.RLWalk)
    w.control_freq = 50
    w.recorder = W.WalkRecorder(50)
    w._record_hold = W._HoldDetector(W.WALK_RECORD_HOLD_S)
    w._record_stop_armed = False
    w.phase_frequency_factor = 1.0
    w.phase_frequency_factor_offset = 0.0
    w.duck_config = SimpleNamespace(speaker=False, projector=False, antennas=False,
                                    camera=False)
    w.buttons = _buttons()
    return w


def test_accel_pitch_roll():
    # gravity straight down z -> ~0 pitch/roll; on side x -> big pitch
    pr = W._accel_pitch_roll([0, 0, 9.81])
    assert abs(pr["pitch"]) < 1.0 and abs(pr["roll"]) < 1.0
    pr2 = W._accel_pitch_roll([9.81, 0, 0.01])
    assert abs(pr2["pitch"]) > 80.0
    assert W._accel_pitch_roll(None) is None


def test_walk_stick_active_deadzones():
    w = _mk()
    assert w._walk_stick_active([0.0, 0.0, 0.0]) is False
    assert w._walk_stick_active([0.02, 0.0, 0.0]) is False   # within lin-x deadzone
    assert w._walk_stick_active([0.10, 0.0, 0.0]) is True
    assert w._walk_stick_active([0.0, 0.0, 0.5]) is True      # turn


def test_record_then_play_overrides_commands():
    w = _mk()
    # start recording, feed a few live command vectors
    w.recorder.start_recording()
    for vx in (0.1, 0.12, 0.14):
        out = w._commands_for_tick([vx, 0, 0, 0, 0, 0, 0], sprint=False, now=0.0)
        assert out[0] == vx, "while recording, live commands pass through"
    w.recorder.stop_recording()
    assert w.recorder.has_recording()

    # play back: the recorded stream overrides the live (zero) command vector
    w.recorder.start_playback()
    seen = []
    for _ in range(4):
        out = w._commands_for_tick([0, 0, 0, 0, 0, 0, 0], sprint=False, now=1.0)
        seen.append(round(out[0], 3))
    assert seen == [0.1, 0.12, 0.14, 0.1], f"playback should loop the record, got {seen}"


def test_playback_stops_when_operator_grabs_stick():
    w = _mk()
    w.recorder.start_recording()
    for _ in range(20):
        w._commands_for_tick([0.15, 0, 1.0, 0, 0, 0, 0], sprint=True, now=0.0)
    w.recorder.stop_recording()
    w.recorder.start_playback()
    w._commands_for_tick([0, 0, 0, 0, 0, 0, 0], sprint=False, now=1.0)  # playing
    # operator pushes forward -> live control resumes IMMEDIATELY with their input
    out = w._commands_for_tick([0.12, 0, 0, 0, 0, 0, 0], sprint=False, now=1.0)
    assert w.recorder.state == "idle", "grabbing the stick must end playback at once"
    assert out[0] == 0.12, "the operator's live command takes over immediately"


def test_dpad_right_starts_and_stops_playback():
    w = _mk()
    # nothing recorded -> DPAD-right is a no-op (stays idle)
    w.buttons = _buttons(dpad_right={"triggered": True})
    w._handle_record_playback([0, 0, 0, 0, 0, 0, 0], now=0.0)
    assert w.recorder.state == "idle"
    # record something, then DPAD-right plays
    w.recorder.frames = [clamp_frame([0.1, 0, 0, 0, 0, 0, 0, 0, 0])]
    w._handle_record_playback([0, 0, 0, 0, 0, 0, 0], now=1.0)
    assert w.recorder.state == "playing"
    # DPAD-right again requests a stop (ramp), then next_frame drains to idle
    w._handle_record_playback([0, 0, 0, 0, 0, 0, 0], now=2.0)
    for _ in range(40):
        if w._commands_for_tick([0, 0, 0, 0, 0, 0, 0], False, 2.0) is None:
            break
        if w.recorder.state == "idle":
            break
    assert w.recorder.state == "idle"


def test_record_hold_3s_starts_recording():
    w = _mk()
    held = _buttons(dpad_left={"is_pressed": True})
    w.buttons = held
    started = False
    t = 0.0
    while t < 4.0:
        w._handle_record_playback([0, 0, 0, 0, 0, 0, 0], now=t)
        if w.recorder.state == "recording":
            started = True
            break
        t += 0.1
    assert started and t >= W.WALK_RECORD_HOLD_S - 0.15


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} walk integration tests passed.")
