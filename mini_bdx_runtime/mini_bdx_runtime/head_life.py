"""
Head life: small head motions that make the walking duck look alive.

  * Look around: the head drifts to a new glance direction every few seconds,
    holds, and comes back. Slow and smooth.
  * Head bob: the lowest neck joint nods a few degrees with every step, locked to
    the gait clock.

Both are OFFSETS on the head command the walk policy already receives
(neck_pitch, head_pitch, head_yaw, head_roll). The policy was trained with those
commands drawn at random over neck -0.34..1.1, pitch +-0.78, yaw +-1.5, roll +-0.5
rad, so a head that is not pointing forward is something it knows how to carry. What
it never saw is a head moving IN RHYTHM with the steps: the bob is an experiment and
starts small.

Motion safety: every output is clamped to a small envelope, eased (no steps) and
limited in speed, whatever the configuration says. When switched off or not allowed
(puppeteer has the head, paused, fall, get-up) the offsets glide back to zero.

Pure and hardware-free (no numpy), so it unit-tests off-robot.
"""
import math

DEFAULTS = {
    "look": False,           # look around
    "bob": False,            # head bob with every step
    "bob_deg": 3.0,          # bob strength: +- this many degrees at the neck
    "bob_phase_deg": 0.0,    # where in the step the head is furthest forward
    "look_scale": 1.0,       # 1 = the glance ranges below
}
LIMITS = {"bob_deg": (0.0, 10.0), "bob_phase_deg": (0.0, 360.0), "look_scale": (0.2, 1.5)}

# Glance envelope at look_scale 1 (rad). Well inside the trained ranges.
LOOK_YAW = 0.45
LOOK_PITCH = (-0.18, 0.10)      # head_pitch: a little up, a little down
LOOK_ROLL = 0.08
LOOK_HOLD_S = (1.5, 5.0)        # time until the next glance
LOOK_CENTRE_CHANCE = 0.3        # share of glances that go back to straight ahead
LOOK_TAU = 0.5                  # two of these in a row: soft start, soft stop
BOB_FADE_S = 0.4                # bob fades in/out over this long

# Hard caps on what this module can ever add (rad) and how fast (rad/s):
# neck_pitch, head_pitch, head_yaw, head_roll.
MAX_OFFSET = (0.20, 0.30, 0.70, 0.15)
MAX_RATE = (4.5, 1.0, 1.0, 1.0)      # the bob is quick by nature; the glances are not
MAX_DT = 0.1


def clean_config(cfg):
    """DEFAULTS overlaid with the valid parts of `cfg`. Never raises."""
    out = dict(DEFAULTS)
    cfg = cfg if isinstance(cfg, dict) else {}
    for key in ("look", "bob"):
        if key in cfg:
            out[key] = bool(cfg[key])
    for key, (lo, hi) in LIMITS.items():
        if key not in cfg:
            continue
        try:
            v = float(cfg[key])
        except (TypeError, ValueError):
            continue
        if v == v:
            out[key] = min(hi, max(lo, v))
    return out


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


class HeadLife:
    def __init__(self, cfg=None, rng=None):
        import random
        self.cfg = clean_config(cfg)
        self.rng = rng if rng is not None else random.Random()
        self._target = [0.0, 0.0, 0.0]     # head_pitch, head_yaw, head_roll
        self._s1 = [0.0, 0.0, 0.0]         # first smoothing stage
        self._look = [0.0, 0.0, 0.0]       # second stage = the glance offsets
        self._next_glance = None
        self._bob_env = 0.0
        self.out = [0.0, 0.0, 0.0, 0.0]    # neck_pitch, head_pitch, head_yaw, head_roll

    def reset(self):
        """Forget the current offsets. Only for moments when the head has been put
        somewhere else anyway (after a get-up or a glide back to the stance)."""
        self._target = [0.0, 0.0, 0.0]
        self._s1 = [0.0, 0.0, 0.0]
        self._look = [0.0, 0.0, 0.0]
        self._next_glance = None
        self._bob_env = 0.0
        self.out = [0.0, 0.0, 0.0, 0.0]

    # ------------------------------------------------------------ settings ----
    def set(self, changes):
        merged = dict(self.cfg)
        merged.update(changes if isinstance(changes, dict) else {})
        self.cfg = clean_config(merged)

    def public(self):
        return dict(self.cfg, active=any(abs(v) > 1e-3 for v in self.out))

    # -------------------------------------------------------------- motion ----
    def _pick_glance(self, now):
        s = self.cfg["look_scale"]
        if self.rng.random() < LOOK_CENTRE_CHANCE:
            self._target = [0.0, 0.0, 0.0]
        else:
            self._target = [
                self.rng.uniform(*LOOK_PITCH) * s,
                self.rng.uniform(-LOOK_YAW, LOOK_YAW) * s,
                self.rng.uniform(-LOOK_ROLL, LOOK_ROLL) * s,
            ]
        self._next_glance = now + self.rng.uniform(*LOOK_HOLD_S)

    def update(self, now, dt, gait_phase=0.0, stepping=False, allowed=True):
        """Advance one control tick. Returns the four head offsets (rad):
        [neck_pitch, head_pitch, head_yaw, head_roll].

        gait_phase  the walk's step clock as an angle (rad); one turn = one gait
                    cycle = two steps
        stepping    True while the duck is actually walking (the bob only runs then)
        allowed     False whenever something else owns the head or the duck is not
                    in plain walking: everything glides back to zero
        """
        try:
            dt = float(dt)
        except (TypeError, ValueError):
            dt = 0.0
        dt = _clamp(dt if dt == dt else 0.0, 0.0, MAX_DT)

        # --- look around ---
        if allowed and self.cfg["look"]:
            if self._next_glance is None or now >= self._next_glance:
                self._pick_glance(now)
        else:
            self._target = [0.0, 0.0, 0.0]
            self._next_glance = None
        a = 1.0 - math.exp(-dt / LOOK_TAU) if dt > 0 else 0.0
        for i in range(3):
            self._s1[i] += (self._target[i] - self._s1[i]) * a
            self._look[i] += (self._s1[i] - self._look[i]) * a

        # --- head bob ---
        want = 1.0 if (allowed and self.cfg["bob"] and stepping) else 0.0
        step = dt / BOB_FADE_S
        self._bob_env += _clamp(want - self._bob_env, -step, step)
        try:
            phase = float(gait_phase)
        except (TypeError, ValueError):
            phase = 0.0
        if not phase == phase:
            phase = 0.0
        bob = (self._bob_env * math.radians(self.cfg["bob_deg"])
               * math.sin(2.0 * phase + math.radians(self.cfg["bob_phase_deg"])))

        # --- clamp, then limit the speed of what actually goes out ---
        wanted = [bob, self._look[0], self._look[1], self._look[2]]
        for i in range(4):
            w = _clamp(wanted[i], -MAX_OFFSET[i], MAX_OFFSET[i])
            lim = MAX_RATE[i] * dt
            self.out[i] += _clamp(w - self.out[i], -lim, lim)
        return list(self.out)
