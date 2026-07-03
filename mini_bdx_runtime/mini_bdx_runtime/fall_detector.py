"""
Fall detection for the walk loop.

Pure-Python, hardware-free (stdlib only) so it can be imported and unit-tested
off-robot. The walk loop feeds it the two foot-switch booleans (and optionally a
gravity/tilt reading) once per control tick; it decides when the robot has
"fallen over and is lying there", which the loop turns into an auto-pause.

Primary signal (matches the user's description): **both foot switches unpressed
continuously for a sustained window**. During a normal gait the robot always has
at least one foot down within a fraction of a second, so a multi-second
both-feet-off stretch reliably means the robot is on its side / back / being
held, not walking. That is exactly the situation where the untouched policy
"kicks its feet looking for footing" — we pause instead.

An accelerometer-tilt corroborator is scaffolded but OFF by default: the BNO055
up-axis and sign depend on the per-robot mounting (`imu_upside_down`) and need
on-robot calibration before we can trust an absolute tilt threshold. Enable it
only once that's verified.
"""

# Continuous "both feet off the ground" time (s) that counts as a fall.
# Long enough to clear any brief double-float in a normal gait, short enough
# that the robot doesn't thrash for long before pausing.
DEFAULT_FALL_AFTER_S = 2.5

# Ignore the first moments after (re)starting detection, while the robot settles
# into init pose and the feet may legitimately be off the ground.
DEFAULT_START_GRACE_S = 2.0

# Optional tilt corroborator (disabled unless a threshold is passed): angle of
# the measured gravity vector away from "straight up", in degrees.
DEFAULT_TILT_FALL_DEG = 60.0


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


class FallDetector:
    def __init__(
        self,
        fall_after_s=DEFAULT_FALL_AFTER_S,
        start_grace_s=DEFAULT_START_GRACE_S,
        tilt_fall_deg=None,          # None disables the tilt corroborator
    ):
        self.fall_after_s = fall_after_s
        self.start_grace_s = start_grace_s
        self.tilt_fall_deg = tilt_fall_deg

        self.fallen = False
        self._off_since = None       # wall time both feet first went off
        self._started_at = None      # when detection (re)started, for the grace
        self._reported = False       # so update() only edge-fires once per fall

    def reset(self, now=None):
        """Clear all state. Call on start and after the user rights + unpauses."""
        self.fallen = False
        self._off_since = None
        self._reported = False
        self._started_at = now

    def _tilt_exceeded(self, tilt_deg):
        return (
            self.tilt_fall_deg is not None
            and tilt_deg is not None
            and tilt_deg >= self.tilt_fall_deg
        )

    def update(self, feet, now, tilt_deg=None):
        """Feed one tick. `feet` is [left_bool, right_bool].

        Returns True ONLY on the tick a fall is first detected (rising edge), so
        the caller can pause + play a sound exactly once. `self.fallen` stays True
        until reset().
        """
        if self._started_at is None:
            self._started_at = now

        # Any foot down clears the "both off" timer immediately.
        both_off = not feet[0] and not feet[1]
        if not both_off:
            self._off_since = None
        elif self._off_since is None:
            self._off_since = now

        # Still within the post-start settle window: never trigger.
        if now - self._started_at < self.start_grace_s:
            return False

        if self.fallen:
            return False  # already flagged; wait for reset()

        off_long_enough = (
            self._off_since is not None
            and now - self._off_since >= self.fall_after_s
        )

        if off_long_enough or self._tilt_exceeded(tilt_deg):
            self.fallen = True
            if not self._reported:
                self._reported = True
                return True
        return False

    def off_duration(self, now):
        """How long both feet have been off (s); 0.0 if a foot is currently down."""
        if self._off_since is None:
            return 0.0
        return max(0.0, now - self._off_since)


def gravity_tilt_deg(accel, up_axis=2, up_sign=1.0):
    """Angle (deg) of the measured acceleration (gravity) vector away from the
    robot's 'up' axis. At rest upright this is ~0; lying on its side ~90.

    accel: 3-vector (m/s^2) from the IMU's accelerometer. up_axis/up_sign encode
    which body axis points up on this robot. Returns None if accel is ~zero.
    Kept here (pure) so the tilt corroborator can be calibrated + tested offline.
    """
    import math

    mag = math.sqrt(sum(a * a for a in accel))
    if mag < 1e-6:
        return None
    cos_up = up_sign * accel[up_axis] / mag
    cos_up = _clamp(cos_up, -1.0, 1.0)
    return math.degrees(math.acos(cos_up))
