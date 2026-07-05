"""
Tilt-based stability governor (pure logic, no hardware -> unit-tests off-robot).

The walk policy balances on raw gyro + accel; when the duck starts to tip, easing
off the DRIVE commands (lin_vel_x/y, ang_vel) gives it room to recover in place
instead of driving itself over. This fires EARLIER than fall_detector, which only
trips once both feet are already off the ground.

Each tick it takes two "how unstable am I" signals:
  * tilt ANGLE  -- how far off-vertical (deg), from the accel gravity vector
  * tilt RATE   -- how fast it's tipping (rad/s), from the non-yaw gyro axes
Each is mapped through a lo->hi deadband ramp to [0,1]; the WORSE of the two is the
severity. severity -> a command scale in [floor, 1.0] (1 = full speed, floor = most
throttled), smoothed with an EMA so it doesn't chatter. The walk multiplies its
drive commands by `scale`.

Disabled by default: update() then always returns 1.0 (a pure pass-through), so the
robot behaves exactly as before until it's explicitly enabled and tuned.
"""


def _ramp(x, lo, hi):
    """0 below lo, 1 at/above hi, linear between. hi<=lo disables the term (->0)."""
    if hi <= lo:
        return 0.0
    if x <= lo:
        return 0.0
    if x >= hi:
        return 1.0
    return (x - lo) / (hi - lo)


class StabilityGovernor:
    def __init__(self, enabled=False, tilt_lo_deg=8.0, tilt_hi_deg=22.0,
                 rate_lo=1.5, rate_hi=5.0, floor=0.2, smooth=0.3):
        self.enabled = bool(enabled)
        self.tilt_lo_deg = float(tilt_lo_deg)
        self.tilt_hi_deg = float(tilt_hi_deg)
        self.rate_lo = float(rate_lo)
        self.rate_hi = float(rate_hi)
        self.floor = float(floor)
        self.smooth = float(smooth)      # EMA weight toward the new target (0..1)
        self.scale = 1.0
        self.severity = 0.0

    def severity_of(self, tilt_deg, tilt_rate):
        """Instantaneous 0..1 severity = worse of the tilt-angle and tilt-rate ramps."""
        return max(_ramp(abs(tilt_deg), self.tilt_lo_deg, self.tilt_hi_deg),
                   _ramp(abs(tilt_rate), self.rate_lo, self.rate_hi))

    def update(self, tilt_deg, tilt_rate):
        """Advance one tick; return the drive-command scale in [floor, 1.0].
        Pure pass-through (1.0) while disabled."""
        if not self.enabled:
            self.severity = 0.0
            self.scale = 1.0
            return 1.0
        sev = self.severity_of(tilt_deg, tilt_rate)
        target = 1.0 - (1.0 - self.floor) * sev
        self.scale += self.smooth * (target - self.scale)   # EMA toward target
        self.severity = sev
        return self.scale


def tilt_angle_deg(pitch_deg, roll_deg):
    """Total tilt off vertical from separate pitch/roll (deg)."""
    return (float(pitch_deg) ** 2 + float(roll_deg) ** 2) ** 0.5


def tilt_rate(gyro_xy):
    """Non-yaw angular speed (rad/s) from the two horizontal gyro axes."""
    gx, gy = float(gyro_xy[0]), float(gyro_xy[1])
    return (gx * gx + gy * gy) ** 0.5


def governor_from_config(cfg):
    """Build a StabilityGovernor from a duck_config `stability_governor` dict
    (all keys optional; missing -> the safe defaults, disabled)."""
    cfg = cfg or {}
    return StabilityGovernor(
        enabled=cfg.get("enabled", False),
        tilt_lo_deg=cfg.get("tilt_lo_deg", 8.0),
        tilt_hi_deg=cfg.get("tilt_hi_deg", 22.0),
        rate_lo=cfg.get("rate_lo", 1.5),
        rate_hi=cfg.get("rate_hi", 5.0),
        floor=cfg.get("floor", 0.2),
        smooth=cfg.get("smooth", 0.3),
    )
