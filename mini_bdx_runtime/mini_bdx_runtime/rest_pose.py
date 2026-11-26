"""
Rest pose: stand in the plain, symmetric stance while nobody is driving.

Every walk policy has its own way of standing at zero command, and it is part of the
network, not of the sensors: fed any accelerometer reading, the stock walk and the
dk5426 walker each return the same crooked standing pose (checked against the ONNX
files, 2026-11-26). One of them stands with the body rolled about 12 degrees. Some
like the look; it also costs the fall detector part of its margin.

With this on, once the sticks have been idle and the duck calm for a moment, the walk
loop fades the policy's amplitude to zero, which leaves the servos at the stance pose
(the same pose the duck holds while paused). The policy keeps running; a touch of the
stick, a push or a growing lean fades it back in at once.

This class only decides WHEN to rest. Pure and hardware-free, so it unit-tests
off-robot.
"""

DEFAULTS = {
    "enabled": False,
    "idle_s": 1.5,          # sticks idle and duck calm this long before resting
    "calm_rate": 0.35,      # rad/s tip speed that still counts as calm
    "wake_rate": 1.2,       # rad/s tip speed that wakes the policy
    "wake_rise_deg": 6.0,   # lean growing this far above its resting low wakes it
}


class RestPose:
    def __init__(self, enabled=False, **overrides):
        self.cfg = dict(DEFAULTS)
        for key, value in overrides.items():
            if key in DEFAULTS and key != "enabled":
                try:
                    self.cfg[key] = float(value)
                except (TypeError, ValueError):
                    pass
        self.enabled = bool(enabled)
        self.reset()

    def reset(self):
        self.resting = False
        self._calm_since = None
        self._low_tilt = None

    def update(self, stick_active, tilt_deg, tip_rate, now, allowed=True):
        """Advance one control tick. Returns True while the duck should rest in the
        stance pose. `allowed` is False whenever something else owns the motion
        (a policy switch, balancing after a get-up)."""
        if not self.enabled or not allowed or stick_active:
            self.reset()
            return False
        try:
            tilt, rate = abs(float(tilt_deg)), abs(float(tip_rate))
        except (TypeError, ValueError):
            self.reset()
            return False
        if not (tilt == tilt and rate == rate):      # NaN: do not rest on bad data
            self.reset()
            return False
        c = self.cfg
        if self.resting:
            self._low_tilt = min(self._low_tilt, tilt)
            if rate > c["wake_rate"] or tilt > self._low_tilt + c["wake_rise_deg"]:
                self.reset()                          # pushed: the policy takes over
                return False
            return True
        if rate > c["calm_rate"]:
            self._calm_since = None
            return False
        if self._calm_since is None:
            self._calm_since = now
        if now - self._calm_since >= c["idle_s"]:
            self.resting = True
            self._low_tilt = tilt
        return self.resting

    def public(self):
        return {"enabled": bool(self.enabled), "resting": bool(self.resting)}
