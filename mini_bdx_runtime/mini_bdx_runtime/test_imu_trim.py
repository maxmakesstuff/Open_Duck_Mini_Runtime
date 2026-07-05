"""
Off-robot tests for imu_trim.py (pure numpy) and duck_config.save_config_fields.

Run from this directory:  python3 test_imu_trim.py
"""
import json
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imu_trim as it  # noqa: E402
import duck_config as dc  # noqa: E402

G = 9.81


def test_zero_trim_is_identity():
    v = [1.0, 2.0, 3.0]
    assert np.allclose(it.apply_trim(v, 0.0, 0.0), v)


def test_trim_nulls_horizontal_upright_gravity():
    # gravity mostly +Z with a small forward/side tilt
    accel = [0.6, -0.4, 9.79]
    p, r = it.trim_from_gravity(accel)
    out = it.apply_trim(accel, p, r)
    assert abs(out[0]) < 1e-6 and abs(out[1]) < 1e-6
    assert out[2] > 0                      # pole preserved (+Z)
    assert abs(np.linalg.norm(out) - np.linalg.norm(accel)) < 1e-9


def test_trim_preserves_upside_down_pole():
    # UPSIDE-DOWN mount: gravity reads mostly -Z. Must null x/y WITHOUT flipping.
    accel = [0.5, 0.3, -9.78]
    p, r = it.trim_from_gravity(accel)
    out = it.apply_trim(accel, p, r)
    assert abs(out[0]) < 1e-6 and abs(out[1]) < 1e-6
    assert out[2] < 0                      # still -Z, NOT flipped to +Z
    # trim angles stay small (a few degrees), not ~180 deg
    assert abs(np.degrees(p)) < 10 and abs(np.degrees(r)) < 10


def test_perfect_gravity_needs_no_trim():
    for az in (G, -G):
        p, r = it.trim_from_gravity([0.0, 0.0, az])
        assert abs(p) < 1e-9 and abs(r) < 1e-9


def test_gravity_not_on_z_returns_zero_trim():
    # gravity on X (gross mount error) -> refuse to 'correct', signal via (0,0)
    assert it.trim_from_gravity([9.8, 0.0, 0.0]) == (0.0, 0.0)


def test_describe_gravity_flags_axis_and_sign():
    d = it.describe_gravity([0.1, -0.2, -9.8])
    assert d["dominant_axis"] == "z" and d["sign"] == "-" and d["z_dominant"]
    assert abs(d["magnitude"] - 9.8) < 0.1


def test_apply_trim_to_gyro_small_angle_is_near_identity_at_rest():
    # a stationary gyro ~0 stays ~0 after trim (no bias injected at rest)
    out = it.apply_trim([0.0, 0.0, 0.0], np.radians(3), np.radians(-2))
    assert np.allclose(out, [0.0, 0.0, 0.0])


def test_save_config_fields_preserves_and_backs_up():
    cfg = {"web_ui": True, "joints_offsets": {"a": 1.0}, "imu_upside_down": True}
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "duck_config.json")
        with open(path, "w") as f:
            json.dump(cfg, f, indent=2)
        backup = dc.save_config_fields(
            {"imu_trim": {"pitch": 0.01, "roll": -0.02}}, config_json_path=path
        )
        after = json.load(open(path))
        assert after["imu_trim"] == {"pitch": 0.01, "roll": -0.02}
        assert after["web_ui"] is True
        assert after["joints_offsets"] == {"a": 1.0}          # untouched
        assert after["imu_upside_down"] is True
        assert backup and json.load(open(backup)) == cfg      # backup == pre-write


def test_save_config_fields_no_file_no_backup():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "new.json")
        backup = dc.save_config_fields({"imu_trim": {"pitch": 0.0, "roll": 0.0}},
                                       config_json_path=path)
        assert backup is None
        assert json.load(open(path))["imu_trim"] == {"pitch": 0.0, "roll": 0.0}


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
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
