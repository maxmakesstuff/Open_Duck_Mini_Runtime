"""
Early fall detection and a soft landing.

The older fall_detector.py notices that the duck is ALREADY lying down (both foot
switches open for 2.5 s). Until then the walk policy keeps driving the legs at full
stiffness against the floor, which is hard on gears and printed parts. This module
works on the way down instead:

  TiltEstimator   fuses gyro and accelerometer into a tilt angle that stays usable
                  while the body is accelerating (the accelerometer alone is not).
  FallReflex      decides, a few ticks after the point of no return, that the duck
                  will not recover, then walks through tuck -> soft -> down.

The loop turns the phases into motor commands (see RLWalk._reflex_tick):
  "tuck"  stop the policy, fold toward a compact pose at servo speed
  "soft"  drop the servo stiffness so the joints give on impact
  "down"  at rest on the floor: stay soft, paused, until the operator or the get-up
          takes over

Three modes: "off", "observe" (detect and log only; nothing about the motion
changes) and "active". Observe is the way to find thresholds for a given duck: tip
it, look at the recorded lead time, then switch to active.

Pure and hardware-free (stdlib only) so it unit-tests off-robot and scripts/
analyze_falls.py can replay recordings through the very same code.
"""
import math

MODES = ("off", "observe", "active")

DEFAULTS = {
    "mode": "observe",
    # Trigger when the tilt, extrapolated `horizon_s` ahead at the current tipping
    # speed, passes `predict_deg` while the tilt itself is already past
    # `min_tilt_deg`, for `confirm_ticks` ticks in a row ...
    "min_tilt_deg": 20.0,
    "predict_deg": 45.0,
    "horizon_s": 0.25,
    "confirm_ticks": 2,
    # ... or as soon as the tilt passes `hard_deg`, whatever the speed.
    "hard_deg": 40.0,
    # Reflex: fold for `tuck_s`, then go soft. Short on purpose: a small duck is on
    # the floor ~0.15 s after the trigger, and soft must come before the impact.
    "tuck_s": 0.08,
    "soft_kp": 4.0,
    "soft_kp_head": 2.0,
    # "Down" once the gyro has stayed below `rest_rate` rad/s for `rest_s`.
    "rest_rate": 0.35,
    "rest_s": 0.8,
    # Give up waiting for rest after this long and call it down anyway.
    "max_soft_s": 4.0,
}

LIMITS = {
    "min_tilt_deg": (5.0, 60.0), "predict_deg": (15.0, 90.0),
    "horizon_s": (0.0, 0.6), "confirm_ticks": (1, 10), "hard_deg": (15.0, 90.0),
    "tuck_s": (0.0, 0.5), "soft_kp": (0.0, 32.0), "soft_kp_head": (0.0, 32.0),
    "rest_rate": (0.05, 3.0), "rest_s": (0.1, 5.0), "max_soft_s": (0.5, 20.0),
}

G = 9.81


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def clean_config(cfg):
    """DEFAULTS overlaid with the valid parts of `cfg`. Never raises."""
    out = dict(DEFAULTS)
    cfg = cfg if isinstance(cfg, dict) else {}
    if cfg.get("mode") in MODES:
        out["mode"] = cfg["mode"]
    for key, (lo, hi) in LIMITS.items():
        if key not in cfg:
            continue
        try:
            v = float(cfg[key])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(v):
            continue
        v = _clamp(v, lo, hi)
        out[key] = int(round(v)) if key == "confirm_ticks" else v
    return out


def _norm(v):
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


class TiltEstimator:
    """Tracks the direction of "up" in the body frame.

    Between ticks the gyro rotates the estimate (du/dt = -omega x u, exact for a
    world-fixed vector seen from a rotating body); the accelerometer then pulls it
    back slowly, and only while it reads close to 1 g, i.e. while the body is not
    being shoved around. That keeps the angle honest during a fall, when the
    accelerometer reads anything but gravity.

    Needs gyro and accelerometer in one consistent right-handed frame, which the
    BNO055 axis remap and the mounting trim both preserve."""

    def __init__(self, accel_tau=0.5, trust_band=0.2, use_gyro=True):
        # Time constant (s) of the pull toward the accelerometer. Time-based so the
        # estimate settles equally fast at 50 Hz (walking) and 10 Hz (paused).
        self.accel_tau = float(accel_tau)
        self.trust_band = float(trust_band)
        self.use_gyro = bool(use_gyro)
        self.up = None                 # unit vector, body frame
        self.tilt_deg = 0.0
        self.rate_deg_s = 0.0          # d(tilt)/dt, smoothed; + = tipping further

    def reset(self):
        self.up = None
        self.tilt_deg = 0.0
        self.rate_deg_s = 0.0

    def update(self, gyro, accel, dt):
        """Feed one tick (gyro rad/s, accel m/s^2, dt s). Returns the tilt in degrees
        away from the body's z axis."""
        try:
            a = (float(accel[0]), float(accel[1]), float(accel[2]))
            w = (float(gyro[0]), float(gyro[1]), float(gyro[2]))
        except (TypeError, ValueError, IndexError):
            return self.tilt_deg
        if not all(math.isfinite(x) for x in a + w):
            return self.tilt_deg
        dt = _clamp(float(dt), 0.0, 0.1)
        a_mag = _norm(a)

        if self.up is None:
            if a_mag < 1e-3:
                return self.tilt_deg
            self.up = (a[0] / a_mag, a[1] / a_mag, a[2] / a_mag)
        else:
            u = self.up
            if self.use_gyro and dt > 0.0:
                cx = w[1] * u[2] - w[2] * u[1]
                cy = w[2] * u[0] - w[0] * u[2]
                cz = w[0] * u[1] - w[1] * u[0]
                u = (u[0] - cx * dt, u[1] - cy * dt, u[2] - cz * dt)
            if a_mag > 1e-3:
                # Full trust at exactly 1 g, none outside the band.
                off = abs(a_mag - G) / G
                trust = _clamp(1.0 - off / self.trust_band, 0.0, 1.0)
                gain = ((1.0 - math.exp(-dt / self.accel_tau)) * trust
                        if self.use_gyro else 0.25)
                u = (u[0] + gain * (a[0] / a_mag - u[0]),
                     u[1] + gain * (a[1] / a_mag - u[1]),
                     u[2] + gain * (a[2] / a_mag - u[2]))
            n = _norm(u)
            if n > 1e-6:
                self.up = (u[0] / n, u[1] / n, u[2] / n)

        prev = self.tilt_deg
        self.tilt_deg = math.degrees(math.acos(_clamp(self.up[2], -1.0, 1.0)))
        if dt > 0.0:
            raw = (self.tilt_deg - prev) / dt
            self.rate_deg_s += 0.5 * (raw - self.rate_deg_s)
        return self.tilt_deg


class FallReflex:
    """State machine: armed -> tuck -> soft -> down. `phase` is what the loop acts on.

    update() returns an event string on the tick something happens, else None:
      "would_trigger"  observe mode saw a fall (nothing else changes)
      "trigger"        active mode: reflex starts (phase is now "tuck")
      "soft"           tuck over: lower the stiffness now
      "down"           at rest on the floor
    """

    def __init__(self, config=None):
        self.cfg = clean_config(config)
        self.phase = "armed"
        self._confirm = 0
        self._phase_t = None
        self._rest_since = None
        self._observed = False        # observe mode: one event per fall
        self.predicted_deg = 0.0
        # Session peaks while armed, so the operator can see how close ordinary
        # walking comes to the thresholds before trusting them.
        self.peak_tilt_deg = 0.0
        self.peak_predicted_deg = 0.0
        self.triggers = 0
        self.last_trigger = None      # {"tilt":..,"predicted":..,"rate":..,"t":..}

    # -------------------------------------------------------------- config ----
    @property
    def mode(self):
        return self.cfg["mode"]

    def set(self, key, value):
        """Change one setting live. Returns True if accepted. An ongoing reflex
        always finishes; a mode change only affects the next fall."""
        if key == "mode":
            if value not in MODES:
                return False
            self.cfg["mode"] = value
            return True
        if key not in LIMITS:
            return False
        try:
            if not math.isfinite(float(value)):
                return False
        except (TypeError, ValueError):
            return False
        self.cfg = clean_config({**self.cfg, key: value})
        return True

    def reset_peaks(self):
        self.peak_tilt_deg = 0.0
        self.peak_predicted_deg = 0.0

    def rearm(self):
        """Back to armed (after the duck has been stood up again)."""
        self.phase = "armed"
        self._confirm = 0
        self._phase_t = None
        self._rest_since = None
        self._observed = False

    # --------------------------------------------------------------- logic ----
    def _is_falling(self, tilt_deg, rate_deg_s):
        c = self.cfg
        self.predicted_deg = tilt_deg + c["horizon_s"] * max(0.0, rate_deg_s)
        if tilt_deg >= c["hard_deg"]:
            return True
        if tilt_deg >= c["min_tilt_deg"] and self.predicted_deg >= c["predict_deg"]:
            self._confirm += 1
        else:
            self._confirm = 0
        return self._confirm >= c["confirm_ticks"]

    def update(self, tilt_deg, rate_deg_s, gyro_mag, now, enabled=True):
        """One tick. `enabled` False (paused, get-up running, ...) keeps it quiet
        without losing an ongoing reflex."""
        c = self.cfg
        if self.phase == "armed":
            if c["mode"] == "off" or not enabled:
                self._confirm = 0
                return None
            falling = self._is_falling(tilt_deg, rate_deg_s)
            if not falling:
                self.peak_tilt_deg = max(self.peak_tilt_deg, tilt_deg)
                self.peak_predicted_deg = max(self.peak_predicted_deg, self.predicted_deg)
                if tilt_deg < c["min_tilt_deg"] * 0.5:
                    self._observed = False      # upright again: next fall counts anew
                return None
            if self._observed:
                return None
            self.triggers += 1
            self.last_trigger = {"tilt": round(tilt_deg, 1),
                                 "predicted": round(self.predicted_deg, 1),
                                 "rate": round(rate_deg_s, 1), "t": now}
            if c["mode"] == "observe":
                self._observed = True
                return "would_trigger"
            self.phase, self._phase_t = "tuck", now
            if c["tuck_s"] <= 0.0:
                self.phase = "soft"
                self._rest_since = None
                return "trigger_soft"
            return "trigger"

        if self.phase == "tuck":
            if now - self._phase_t >= c["tuck_s"]:
                self.phase, self._phase_t = "soft", now
                self._rest_since = None
                return "soft"
            return None

        if self.phase == "soft":
            if gyro_mag < c["rest_rate"]:
                if self._rest_since is None:
                    self._rest_since = now
                if now - self._rest_since >= c["rest_s"]:
                    self.phase = "down"
                    return "down"
            else:
                self._rest_since = None
            if now - self._phase_t >= c["max_soft_s"]:
                self.phase = "down"
                return "down"
            return None

        return None            # "down": waits for rearm()

    def public(self):
        """Telemetry for the Web UI."""
        c = self.cfg
        return {
            "mode": c["mode"], "phase": self.phase,
            "predicted_deg": round(self.predicted_deg, 1),
            "peak_tilt_deg": round(self.peak_tilt_deg, 1),
            "peak_predicted_deg": round(self.peak_predicted_deg, 1),
            "triggers": self.triggers, "last_trigger": self.last_trigger,
            "config": {k: c[k] for k in LIMITS},
        }


def tuck_pose(init_pos, lower_limits, upper_limits):
    """A compact pose to fold toward during the tuck phase: legs as at stance but
    with the knees fully bent and the hips and ankles following so the feet stay
    flat, head centred. Derived from the stance pose so it stays valid if that
    changes; clamped to the joint limits."""
    pose = [float(v) for v in init_pos]
    # HWI order: left leg 0..4 (yaw, roll, pitch, knee, ankle), head 5..8, right 9..13
    for hip, knee, ankle in ((2, 3, 4), (11, 12, 13)):
        extra = float(upper_limits[knee]) - pose[knee]      # how much more the knee bends
        if extra <= 0.0:
            continue
        pose[knee] += extra
        # A flat foot needs hip and ankle to share the added bend; the hip pitches
        # the opposite way on the two legs, so follow the sign it already has.
        hip_dir = -1.0 if pose[hip] < 0.0 else 1.0
        pose[hip] += hip_dir * extra * 0.5
        pose[ankle] -= extra * 0.5
    for i in range(5, 9):
        pose[i] = 0.0
    return [_clamp(p, float(lo), float(hi))
            for p, lo, hi in zip(pose, lower_limits, upper_limits)]
