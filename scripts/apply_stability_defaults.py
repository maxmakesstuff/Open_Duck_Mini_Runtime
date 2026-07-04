#!/usr/bin/env python3
"""Seed the known-good WALK TUNING into ~/duck_config.json (for a second robot).

These are the values that made the reference duck "steady as hell". They are
hardware-general (same servos, same policy), so they're safe STARTING POINTS on
another Open Duck Mini — but the balance point depends on that robot's own IMU
trim + joint offsets, so calibrate first and expect to re-tune (esp. action_scale).

What it does NOT touch: joints_offsets, imu_trim, imu_upside_down, expression_features
— all per-robot. Written backup-first (a timestamped .bak), other keys preserved.

Run on the robot:  python scripts/apply_stability_defaults.py
"""
import copy
import json
import os

# import the installed package (works run as scripts/apply_stability_defaults.py)
from mini_bdx_runtime.duck_config import save_config_fields

CFG = os.path.expanduser("~/duck_config.json")

# The reference duck's known-good walk tuning (see docs / commit history).
WALK_TUNING = {
    "action_scale": 0.23,                 # policy residual scale (0.25 default)
    "phase_frequency_factor_offset": -0.10,   # slower cadence = steadier
    "velocity_clip": True,                 # per-tick slew safety net
    "max_motor_velocity_rad_s": 5.24,
    "stability_governor": {
        "enabled": True,
        "tilt_lo_deg": 10.0, "tilt_hi_deg": 25.0,
        "rate_lo": 2.5, "rate_hi": 7.0,
        "floor": 0.35, "smooth": 0.3,
    },
}

# Per-robot calibration that MUST NOT be overwritten by this seeding.
CALIBRATION_KEYS = ("joints_offsets", "imu_trim", "imu_upside_down",
                    "expression_features")


def main():
    try:
        before = json.load(open(CFG))
    except FileNotFoundError:
        before = {}
    guarded = {k: copy.deepcopy(before.get(k)) for k in CALIBRATION_KEYS}

    # Warn (don't block) if this duck looks uncalibrated — the tuning is harmless
    # but it won't stand well until offsets + IMU trim are measured on THIS robot.
    if not before.get("joints_offsets"):
        print("⚠ No joints_offsets found — calibrate this duck first "
              "(configure_all_motors.py, find_soft_offsets.py). Seeding anyway.")
    if not before.get("imu_trim"):
        print("⚠ No imu_trim found — run calibrate_imu.py + imu_health_check.py "
              "for THIS robot before trusting balance.")

    backup = save_config_fields(WALK_TUNING, CFG)

    after = json.load(open(CFG))
    changed = [k for k in CALIBRATION_KEYS if after.get(k) != guarded[k]]
    print(f"\nbackup written : {backup}")
    print("applied walk tuning:")
    for k, v in WALK_TUNING.items():
        print(f"  {k} = {v}")
    if changed:
        print(f"\n✗ ERROR: calibration keys changed unexpectedly: {changed}")
        return 1
    print("\n✓ calibration untouched (joints_offsets / imu_trim / imu_upside_down / "
          "expression_features preserved).")
    print("Now walk-test and re-tune per robot (start with action_scale, watch the "
          "GOV readout for the governor).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
