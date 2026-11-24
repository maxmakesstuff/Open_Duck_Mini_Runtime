"""
Command shaping: limits how fast the DRIVE commands (forward, sideways, turn) may
change, so a slammed stick becomes a short ramp instead of a step.

Why: the walk policy reacts to a command step with an abrupt change of gait, which
is one of the ways a duck throws itself off balance. vquintin's real-duck runtime
limits the change to 0.3 m/s^2 for the same reason. Stopping is allowed to be faster
than starting, because letting go of the stick should feel immediate.

Head commands (indices 3:7) pass through untouched. Disabled = pure pass-through.
Pure stdlib -> unit-tests off-robot.
"""
import math

DEFAULTS = {"enabled": False, "lin_accel": 0.3, "ang_accel": 4.0, "stop_factor": 3.0}
LIMITS = {"lin_accel": (0.05, 3.0), "ang_accel": (0.5, 30.0), "stop_factor": (1.0, 10.0)}


def clean_config(cfg):
    out = dict(DEFAULTS)
    cfg = cfg if isinstance(cfg, dict) else {}
    if "enabled" in cfg:
        out["enabled"] = bool(cfg["enabled"])
    for key, (lo, hi) in LIMITS.items():
        try:
            v = float(cfg.get(key, out[key]))
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(v):
            out[key] = min(hi, max(lo, v))
    return out


def _approach(current, target, up, down):
    """Move `current` toward `target`. Moving away from zero is limited by `up`,
    moving toward zero by `down` (>= up)."""
    if target == current:
        return current
    toward_zero = abs(target) < abs(current) and (target * current >= 0.0)
    step = down if toward_zero else up
    if current * target < 0.0:
        step = down                   # reversing: first brake at the stopping rate
        target = 0.0 if abs(current) > step else target
    delta = target - current
    if abs(delta) <= step:
        return target
    return current + math.copysign(step, delta)


class CommandShaper:
    def __init__(self, config=None):
        self.cfg = clean_config(config)
        self.state = [0.0, 0.0, 0.0]

    @property
    def enabled(self):
        return self.cfg["enabled"]

    def set(self, key, value):
        if key == "enabled":
            self.cfg["enabled"] = bool(value)
            return True
        if key not in LIMITS:
            return False
        try:
            if not math.isfinite(float(value)):
                return False
        except (TypeError, ValueError, OverflowError):
            return False
        self.cfg = clean_config({**self.cfg, key: value})
        return True

    def reset(self, commands=None):
        """Forget the ramp (after a pause / policy swap). Starts from `commands`'
        drive values, or from standstill."""
        c = commands if commands is not None else (0.0, 0.0, 0.0)
        self.state = [float(c[0]), float(c[1]), float(c[2])]

    def apply(self, commands, dt):
        """Shape commands[0:3] in place and return the list. dt in seconds."""
        if not self.cfg["enabled"]:
            self.state = [float(commands[0]), float(commands[1]), float(commands[2])]
            return commands
        dt = min(0.1, max(0.0, float(dt)))
        rates = (self.cfg["lin_accel"], self.cfg["lin_accel"], self.cfg["ang_accel"])
        for i in range(3):
            up = rates[i] * dt
            self.state[i] = _approach(self.state[i], float(commands[i]), up,
                                      up * self.cfg["stop_factor"])
            commands[i] = self.state[i]
        return commands

    def public(self):
        return dict(self.cfg)
