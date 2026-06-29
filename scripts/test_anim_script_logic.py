"""
Off-robot integration test for v2_rl_walk_mujoco_anim.py.

The script imports Raspberry-Pi-only modules (board/pwmio/rustypot/...), so we
inject lightweight fakes for those hardware boundaries into sys.modules, load
the REAL animation_player, then import the script and exercise its REAL decision
methods (no reimplementation). This verifies:
  - the script's top-level imports all resolve (names match animation_player),
  - d-pad direction priority + the "no trigger" case,
  - the walk-stick interrupt deadzone (per axis),
  - animation peripheral routing: direct (non-cross-wired) antenna mapping and
    feature-flag gating (antennas/speaker/projector).

Run:  python3 test_anim_script_logic.py   (from scripts/)
"""
import os
import sys
import types
import importlib.util
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_DIR = os.path.normpath(os.path.join(HERE, "..", "mini_bdx_runtime", "mini_bdx_runtime"))


def _fake_module(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _install_fakes():
    # Parent package placeholder.
    pkg = types.ModuleType("mini_bdx_runtime")
    pkg.__path__ = [PKG_DIR]
    sys.modules["mini_bdx_runtime"] = pkg

    _Dummy = type("Dummy", (), {})

    def make_action_dict(targets, names):  # mirror of the real signature
        return dict(zip(names, targets))

    _fake_module("mini_bdx_runtime.rustypot_position_hwi", HWI=_Dummy)
    _fake_module("mini_bdx_runtime.onnx_infer", OnnxInfer=_Dummy)
    _fake_module("mini_bdx_runtime.raw_imu", Imu=_Dummy)
    _fake_module("mini_bdx_runtime.poly_reference_motion", PolyReferenceMotion=_Dummy)
    _fake_module("mini_bdx_runtime.xbox_controller", XBoxController=_Dummy)
    _fake_module("mini_bdx_runtime.feet_contacts", FeetContacts=_Dummy)
    _fake_module("mini_bdx_runtime.eyes", Eyes=_Dummy)
    _fake_module("mini_bdx_runtime.sounds", Sounds=_Dummy)
    _fake_module("mini_bdx_runtime.antennas", Antennas=_Dummy)
    _fake_module("mini_bdx_runtime.projector", Projector=_Dummy)
    _fake_module(
        "mini_bdx_runtime.rl_utils",
        make_action_dict=make_action_dict,
        LowPassActionFilter=_Dummy,
    )
    _fake_module("mini_bdx_runtime.duck_config", DuckConfig=_Dummy)
    _fake_module(
        "mini_bdx_runtime.scanner_sound",
        ScannerSound=_Dummy,
        PygameScannerBackend=_Dummy,
    )

    # Load the REAL animation_player as mini_bdx_runtime.animation_player.
    spec = importlib.util.spec_from_file_location(
        "mini_bdx_runtime.animation_player",
        os.path.join(PKG_DIR, "animation_player.py"),
    )
    real_ap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real_ap)
    sys.modules["mini_bdx_runtime.animation_player"] = real_ap


_install_fakes()
sys.path.insert(0, HERE)
import v2_rl_walk_mujoco_anim as script  # noqa: E402

RLWalk = script.RLWalk


def _btn(trig=False):
    return SimpleNamespace(triggered=trig, is_pressed=trig)


def _buttons(up=False, down=False, left=False, right=False):
    return SimpleNamespace(
        dpad_up=_btn(up), dpad_down=_btn(down),
        dpad_left=_btn(left), dpad_right=_btn(right),
    )


# --------------------------------------------------------- d-pad detection

def test_import_succeeded():
    # If we got here, every top-level import in the script resolved, including
    # the four names pulled from animation_player.
    assert hasattr(RLWalk, "run")
    assert script.WALK == "walk" and script.ANIMATION == "animation"


def test_dpad_triggered_each_direction():
    for name, kwargs in [("up", {"up": True}), ("down", {"down": True}),
                         ("left", {"left": True}), ("right", {"right": True})]:
        s = SimpleNamespace(buttons=_buttons(**kwargs))
        assert RLWalk._dpad_triggered(s) == name


def test_dpad_triggered_none_when_idle():
    s = SimpleNamespace(buttons=_buttons())
    assert RLWalk._dpad_triggered(s) is None


# --------------------------------------------------------- stick interrupt

def test_walk_stick_inactive_within_deadzone():
    s = SimpleNamespace(last_commands=[0.01, 0.01, 0.04, 0, 0, 0, 0])
    assert RLWalk._walk_stick_active(s) is False


def test_walk_stick_active_on_x():
    s = SimpleNamespace(last_commands=[0.10, 0.0, 0.0, 0, 0, 0, 0])
    assert RLWalk._walk_stick_active(s) is True


def test_walk_stick_active_on_yaw():
    s = SimpleNamespace(last_commands=[0.0, 0.0, 0.5, 0, 0, 0, 0])
    assert RLWalk._walk_stick_active(s) is True


def test_walk_stick_active_negative_values():
    s = SimpleNamespace(last_commands=[-0.10, 0.0, 0.0, 0, 0, 0, 0])
    assert RLWalk._walk_stick_active(s) is True


# ------------------------------------------------ animation peripheral routing

class _AntennaRec:
    def __init__(self):
        self.left = None
        self.right = None

    def set_position_left(self, v):
        self.left = v

    def set_position_right(self, v):
        self.right = v


class _ProjectorRec:
    def __init__(self, on=False):
        self.on = on
        self.switches = 0

    def switch(self):
        self.on = not self.on
        self.switches += 1


class _SoundsRec:
    def __init__(self):
        self.played = []

    def play(self, name):
        self.played.append(name)


def test_animation_antennas_direct_mapping_when_enabled():
    ant = _AntennaRec()
    cfg = SimpleNamespace(antennas=True, speaker=False, projector=False)
    s = SimpleNamespace(duck_config=cfg, antennas=ant)
    RLWalk._apply_animation_peripherals(
        s, {"antennas": [0.3, -0.4], "sounds": [], "lamp": None}
    )
    # animation drives left->left, right->right (NOT cross-wired like triggers)
    assert ant.left == 0.3 and ant.right == -0.4


def test_animation_skips_disabled_features():
    ant = _AntennaRec()
    cfg = SimpleNamespace(antennas=False, speaker=False, projector=False)
    s = SimpleNamespace(duck_config=cfg, antennas=ant)
    RLWalk._apply_animation_peripherals(
        s, {"antennas": [0.3, -0.4], "sounds": ["x.wav"], "lamp": True}
    )
    assert ant.left is None and ant.right is None  # nothing driven


def test_animation_sound_played_when_speaker_enabled():
    snd = _SoundsRec()
    cfg = SimpleNamespace(antennas=False, speaker=True, projector=False)
    s = SimpleNamespace(duck_config=cfg, sounds=snd)
    RLWalk._apply_animation_peripherals(
        s, {"antennas": None, "sounds": ["happy1.wav"], "lamp": None}
    )
    assert snd.played == ["happy1.wav"]


def test_animation_lamp_only_switches_on_state_change():
    proj = _ProjectorRec(on=False)
    cfg = SimpleNamespace(antennas=False, speaker=False, projector=True)
    s = SimpleNamespace(duck_config=cfg, projector=proj)
    # desired True while currently off -> one switch
    RLWalk._apply_animation_peripherals(s, {"antennas": None, "sounds": [], "lamp": True})
    assert proj.on is True and proj.switches == 1
    # lamp None -> no switch
    RLWalk._apply_animation_peripherals(s, {"antennas": None, "sounds": [], "lamp": None})
    assert proj.switches == 1


# ----------------------------------------------------- scanner LED + sound (X)

class _ScannerRec:
    def __init__(self):
        self.started = []
        self.stops = 0

    def start(self, now):
        self.started.append(now)

    def stop(self):
        self.stops += 1


def test_x_turns_led_on_starts_scanner_loop():
    proj = _ProjectorRec(on=False)
    scan = _ScannerRec()
    s = SimpleNamespace(projector=proj, scanner_sound=scan)
    RLWalk._toggle_scanner(s, 5.0)
    assert proj.on is True            # LED switched on
    assert scan.started == [5.0]      # loop started at that time
    assert scan.stops == 0


def test_x_turns_led_off_stops_scanner_loop_immediately():
    proj = _ProjectorRec(on=True)     # currently on
    scan = _ScannerRec()
    s = SimpleNamespace(projector=proj, scanner_sound=scan)
    RLWalk._toggle_scanner(s, 9.0)
    assert proj.on is False           # LED switched off
    assert scan.stops == 1            # loop stopped
    assert scan.started == []


def test_x_toggle_without_speaker_just_switches_led():
    proj = _ProjectorRec(on=False)
    s = SimpleNamespace(projector=proj, scanner_sound=None)  # speaker disabled
    RLWalk._toggle_scanner(s, 1.0)    # must not raise
    assert proj.on is True


def _run():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
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
    sys.exit(1 if _run() else 0)
