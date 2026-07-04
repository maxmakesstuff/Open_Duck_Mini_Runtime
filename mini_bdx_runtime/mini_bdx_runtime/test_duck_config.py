"""
Off-robot tests for DuckConfig parsing + save_config_fields (pure JSON, no hardware).

Run from this directory:  python3 test_duck_config.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from duck_config import DuckConfig, save_config_fields  # noqa: E402


def _cfg(d):
    """Write dict `d` to a temp json and load it as a DuckConfig."""
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(path, "w") as f:
        json.dump(d, f)
    return DuckConfig(config_json_path=path, ignore_default=True), path


def test_stability_levers_default_to_todays_behaviour():
    c, path = _cfg({})
    try:
        assert c.action_scale is None            # -> walk keeps CLI/0.25 default
        assert c.velocity_clip is False          # slew clamp off
        assert abs(c.max_motor_velocity_rad_s - 5.24) < 1e-9
        assert c.stability_governor == {}        # -> governor_from_config disabled
    finally:
        os.remove(path)


def test_stability_levers_parse_explicit_values():
    c, path = _cfg({
        "action_scale": 0.20,
        "velocity_clip": True,
        "max_motor_velocity_rad_s": 3.0,
        "stability_governor": {"enabled": True, "floor": 0.3},
    })
    try:
        assert abs(c.action_scale - 0.20) < 1e-9
        assert c.velocity_clip is True
        assert abs(c.max_motor_velocity_rad_s - 3.0) < 1e-9
        assert c.stability_governor["enabled"] is True
        assert abs(c.stability_governor["floor"] - 0.3) < 1e-9
    finally:
        os.remove(path)


def test_save_config_fields_preserves_joint_offsets():
    """The critical safety property: writing a new field must NEVER disturb the
    per-joint offsets (they encode the robot's mechanical zero)."""
    offs = {"left_hip_yaw": 0.11, "right_ankle": -0.07, "neck_pitch": 0.02}
    c, path = _cfg({"joints_offsets": offs, "imu_upside_down": True})
    try:
        backup = save_config_fields({"action_scale": 0.20}, config_json_path=path)
        assert backup is not None and os.path.exists(backup)
        reloaded = DuckConfig(config_json_path=path, ignore_default=True)
        assert reloaded.joints_offset == offs          # offsets untouched
        assert reloaded.imu_upside_down is True         # other fields untouched
        assert abs(reloaded.action_scale - 0.20) < 1e-9  # new field written
        os.remove(backup)
    finally:
        os.remove(path)


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
