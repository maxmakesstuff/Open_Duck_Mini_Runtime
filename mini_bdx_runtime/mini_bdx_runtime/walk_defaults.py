"""
Canonical known-good WALK TUNING defaults — in ONE place so the seeder
(scripts/apply_stability_defaults.py), the Web UI "reset to defaults" button, and
any docs all agree, instead of people hunting the values down.

These are the reference duck's "steady as hell" values. They're hardware-general
(same servos + policy), so they're safe STARTING POINTS on any Open Duck Mini — but
the real balance point still depends on that robot's own IMU trim + joint offsets,
so expect to re-tune (especially action_scale).
"""

WALK_TUNING_DEFAULTS = {
    "action_scale": 0.23,                     # policy residual scale (0.25 code default)
    "phase_frequency_factor_offset": -0.10,   # slower cadence = steadier
    "velocity_clip": True,                     # per-tick slew safety net
    "max_motor_velocity_rad_s": 5.24,
    "stability_governor": {
        "enabled": True,
        "tilt_lo_deg": 10.0, "tilt_hi_deg": 25.0,
        "rate_lo": 2.5, "rate_hi": 7.0,
        "floor": 0.35, "smooth": 0.3,
    },
    # Drive-command ramp: a slammed stick becomes a short ramp (command_shaper.py).
    # Stays off here until it has been walked on a real duck; turn it on in TUNE.
    "command_slew": {"enabled": False, "lin_accel": 0.3, "ang_accel": 4.0,
                     "stop_factor": 3.0},
}

# "Reset" target for the IMU mounting trim: neutral (no trim). The per-duck mount
# tilt is measured with imu_health_check.py, but reset means back to the 0 baseline
# you'd re-measure from if a manual tweak went wrong.
IMU_TRIM_DEFAULTS = {"pitch": 0.0, "roll": 0.0}
