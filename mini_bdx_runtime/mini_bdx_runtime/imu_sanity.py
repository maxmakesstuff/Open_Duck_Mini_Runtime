"""
Is this IMU sample physically possible?

The BNO055 on the Pi's I2C bus now and then returns one value with a corrupted high
byte: a single axis reads close to the sensor's full scale for exactly one sample
(gyro about +-35 rad/s, accelerometer about +-320 m/s^2), with normal samples right
before and after. Measured on a walking duck (2026-11-26): 37 such samples in 126 s,
one every 3 to 4 seconds. Each went straight into the walk policy, whose inputs
normally stay within a few units, and made it kick the legs for two control steps.

A duck this size turns at up to about 8 rad/s when it falls and reads at most a few
g when a foot lands, so the limits below sit far above anything real and far below
the corrupted values. A rejected sample is simply read again.

Pure and hardware-free, so it unit-tests off-robot.
"""
import math

GYRO_MAX = 25.0     # rad/s per axis (about 1430 deg/s); real motion stays under 10
ACCEL_MAX = 80.0    # m/s^2 per axis (about 8 g); the sensor's own range ends near 40


def plausible(gyro, accel, gyro_max=GYRO_MAX, accel_max=ACCEL_MAX):
    """True if every axis is a finite number inside the possible range."""
    try:
        g = [float(x) for x in gyro]
        a = [float(x) for x in accel]
    except (TypeError, ValueError):
        return False
    if len(g) != 3 or len(a) != 3:
        return False
    for v in g:
        if not math.isfinite(v) or abs(v) > gyro_max:
            return False
    for v in a:
        if not math.isfinite(v) or abs(v) > accel_max:
            return False
    return True
