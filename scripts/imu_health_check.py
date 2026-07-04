"""
Read-only IMU health check + mounting-trim measurement. Moves NO motors.

Stand the duck upright in its normal pose on a flat, LEVEL surface (the duck dock
is fine) and hold it still. This reads the IMU for a few seconds and reports:
  * gravity vector + which axis it sits on (confirms the axis remap / upside-down
    mount is producing a sensible Z-dominant gravity),
  * gyro bias at rest (should be ~0; if not, run calibrate_imu.py),
  * the residual mounting tilt (pitch/roll) the walk policy would read as a lean.

It then offers to save that tilt as the `imu_trim` in ~/duck_config.json (backing the
file up first, like the offset tool). The trim defaults to 0 and only changes things
once you save it.
"""
import sys
import time

import numpy as np

from mini_bdx_runtime.raw_imu import Imu
from mini_bdx_runtime.duck_config import DuckConfig, save_config_fields
from mini_bdx_runtime.imu_trim import trim_from_gravity, gravity_tilt_deg, describe_gravity

SECONDS = 4.0
RATE = 50.0
GYRO_BIAS_WARN = 0.05     # |rest gyro| above this suggests gyro needs (re)calibration


def main():
    cfg = DuckConfig(ignore_default=True)
    cur = cfg.imu_trim if isinstance(cfg.imu_trim, dict) else {"pitch": 0.0, "roll": 0.0}

    # Measure RAW (trim=0) so we see the true mounting tilt to suggest.
    imu = Imu(int(RATE), upside_down=cfg.imu_upside_down, pitch_trim=0.0, roll_trim=0.0)
    time.sleep(0.6)  # warmup / let the queue fill

    print(f"\nHold the duck STILL and level... sampling {SECONDS:.0f}s")
    accels, gyros = [], []
    t_end = time.time() + SECONDS
    while time.time() < t_end:
        d = imu.get_data()
        accels.append(np.asarray(d["accelero"], dtype=float))
        gyros.append(np.asarray(d["gyro"], dtype=float))
        time.sleep(1.0 / RATE)

    accels, gyros = np.array(accels), np.array(gyros)
    a_mean, a_std = accels.mean(axis=0), accels.std(axis=0)
    g_mean, g_std = gyros.mean(axis=0), gyros.std(axis=0)

    still = bool(np.all(a_std < 0.15) and np.all(g_std < 0.05))
    grav = describe_gravity(a_mean)
    pitch_deg, roll_deg = gravity_tilt_deg(a_mean)
    p_rad, r_rad = trim_from_gravity(a_mean)

    print("\n================ IMU health ================")
    print(f"stillness           : {'OK' if still else 'MOVING -- results unreliable, hold still'}")
    print(f"accel mean (m/s^2)  : [{a_mean[0]:+.2f} {a_mean[1]:+.2f} {a_mean[2]:+.2f}]  |g|={np.linalg.norm(a_mean):.2f}")
    print(f"gravity axis        : {grav['sign']}{grav['dominant_axis'].upper()}"
          f"  ({'Z-dominant OK' if grav['z_dominant'] else 'NOT on Z -- remap/mount problem!'})")
    print(f"gyro bias (rest)    : [{g_mean[0]:+.3f} {g_mean[1]:+.3f} {g_mean[2]:+.3f}]"
          f"  {'OK' if np.all(np.abs(g_mean) < GYRO_BIAS_WARN) else '<- high, run calibrate_imu.py'}")
    print(f"residual tilt       : pitch={pitch_deg:+.2f} deg   roll={roll_deg:+.2f} deg")
    print(f"current config trim : pitch={float(cur.get('pitch',0.0)):+.4f} rad   roll={float(cur.get('roll',0.0)):+.4f} rad")
    print(f"suggested trim      : pitch={p_rad:+.4f} rad   roll={r_rad:+.4f} rad")
    print("============================================")

    if not grav["z_dominant"]:
        print("\nGravity is not on the Z axis -- the axis_remap/upside_down setting looks")
        print("wrong for this mount. Fix that first; NOT offering a trim (it can't help).")
        return
    if not still:
        print("\nWas moving during the read -- re-run holding the duck still before saving.")
        return

    print("\nNote: roll should be ~0 on a symmetric robot (any roll is mounting tilt).")
    print("Pitch may partly reflect the intended standing stance -- validate by walking;")
    print("if it walks worse, restore the timestamped backup or set imu_trim back to 0.")
    try:
        res = input("Save this trim to ~/duck_config.json? (y/N) ").strip().lower()
    except EOFError:      # non-interactive (e.g. piped) -> never save
        res = "n"
    if res != "y":
        print("Not saving. (You can set \"imu_trim\": {\"pitch\":.., \"roll\":..} by hand.)")
        return
    backup = save_config_fields({"imu_trim": {"pitch": p_rad, "roll": r_rad}})
    if backup:
        print(f"Backed up previous config -> {backup}")
    print("Saved imu_trim. It takes effect next time you start the walk.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted. duck_config.json not modified.")
        sys.exit(0)
