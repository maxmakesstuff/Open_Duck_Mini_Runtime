"""
Pure IMU mounting-trim math (numpy only, no hardware) so it unit-tests off-robot.

The BNO055 axis_remap already handles the COARSE orientation (incl. the upside-down
mount via the sign flags in raw_imu.py). What it can NOT fix is the small residual
tilt from the board not being perfectly square to the torso: that leaves a constant
gravity component on the horizontal axes, which the walk policy reads as a permanent
lean -> a slow directional topple.

`trim_from_gravity` turns one at-rest gravity reading into a (pitch, roll) trim, and
`apply_trim` rotates every subsequent accel/gyro vector by it so the resting gravity
sits straight along the body Z axis. Crucially it PRESERVES the gravity pole (whether
Z reads +9.8 or -9.8), so it is correct for the upside-down mount too.

apply_trim(accel, *trim_from_gravity(accel)) == [0, 0, ~sign(az)*|g|]  (nulls x, y).
Default trim (0, 0) is the identity -> zero behaviour change until a trim is set.
"""
import numpy as np


def _rot_pitch(p):
    """Rotation about Y (pitch)."""
    c, s = np.cos(p), np.sin(p)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def _rot_roll(r):
    """Rotation about X (roll)."""
    c, s = np.cos(r), np.sin(r)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def apply_trim(vec, pitch, roll):
    """Rotate a 3-vector by the trim (pitch about Y, then roll about X). Same rigid
    rotation applies to accel and gyro (they share the sensor->body frame)."""
    v = np.asarray(vec, dtype=float)
    return _rot_roll(roll) @ (_rot_pitch(pitch) @ v)


def trim_from_gravity(accel):
    """(pitch, roll) in radians that, via apply_trim, drive the horizontal gravity
    components to zero while keeping the dominant Z pole. Uses small-angle arctan
    (not atan2) so it does NOT flip an upside-down (-Z) gravity reading.

    Returns (0.0, 0.0) if gravity is not on Z (|az| tiny) -- a remap/mount problem
    the caller should surface rather than silently 'correct'."""
    ax, ay, az = (float(v) for v in accel)
    if abs(az) < 1e-6:
        return (0.0, 0.0)
    pitch = float(np.arctan(-ax / az))
    az2 = -ax * np.sin(pitch) + az * np.cos(pitch)   # z after the pitch rotation
    roll = float(np.arctan(ay / az2)) if abs(az2) > 1e-6 else 0.0
    return (pitch, roll)


def gravity_tilt_deg(accel):
    """The trim expressed in degrees, for human-readable reporting."""
    p, r = trim_from_gravity(accel)
    return (float(np.degrees(p)), float(np.degrees(r)))


def describe_gravity(accel):
    """Which body axis gravity sits on and its sign -- used to confirm the mount /
    upside-down remap is producing a sensible (Z-dominant) gravity vector."""
    a = np.asarray(accel, dtype=float)
    axes = ["x", "y", "z"]
    i = int(np.argmax(np.abs(a)))
    mag = float(np.linalg.norm(a))
    return {
        "dominant_axis": axes[i],
        "sign": "+" if a[i] >= 0 else "-",
        "magnitude": mag,
        "z_dominant": i == 2,
    }
