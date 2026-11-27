"""
Head life: small head motions that make the walking duck look alive.

  * Look around: the head drifts to a new glance direction every few seconds,
    holds, and comes back. Slow and smooth.
  * Sight lead: when the duck is steered into a turn or a sidestep, the head turns
    that way first and the body follows a moment later, the way an animal looks
    where it is about to go. It takes the head over from the look-around for as long
    as the duck is being steered.

The glances are OFFSETS on the head command the walk policy already receives
(head_pitch, head_yaw, head_roll; the neck is left alone). The policy was trained
with those commands drawn at random over pitch +-0.78, yaw +-1.5, roll +-0.5 rad, so
a head that is not pointing forward is something it knows how to carry.

Tried and removed (2026-11-27): a head bob, the neck nodding with every step off the
gait clock. On the real duck it visibly upset the walk, and the one recorded walk
with it at 10 degrees ended in a fall after 8 s. The policy never saw a head moving
in rhythm with its steps. Do not bring it back without a policy trained for it.

Motion safety: every output is clamped to a small envelope, eased (no steps) and
limited in speed, whatever the configuration says. When switched off or not allowed
(puppeteer has the head, playback, fall, get-up) the offsets glide back to zero.

Pure and hardware-free (no numpy), so it unit-tests off-robot.
"""
import math

DEFAULTS = {
    "look": False,           # look around
    "look_scale": 1.0,       # 1 = the glance ranges below
    "lead": False,           # look into a turn before walking into it
    "lead_s": 0.25,          # how long the head goes first before the body turns
}
LIMITS = {"look_scale": (0.2, 1.5), "lead_s": (0.0, 0.8)}

# Glance envelope at look_scale 1 (rad). Well inside the trained ranges.
LOOK_YAW = 0.45
LOOK_PITCH = (-0.18, 0.10)      # head_pitch: a little up, a little down
LOOK_ROLL = 0.08
LOOK_HOLD_S = (1.5, 5.0)        # time until the next glance
LOOK_CENTRE_CHANCE = 0.3        # share of glances that go back to straight ahead
LOOK_TAU = 0.5                  # two of these in a row: soft start, soft stop

# Sight lead: head yaw at full turn stick / full sidestep stick (rad), and how much
# stick counts as "being steered" (share of full deflection).
LEAD_YAW_TURN = 0.45
LEAD_YAW_SIDE = 0.30
LEAD_ON = 0.15
LEAD_TAU = 0.10                 # two of these in a row: brisk, but no step
LEAD_BLEND_S = 0.15             # body turn fades in over this long after the lead
TURN_FULL = 1.0                 # rad/s at full turn stick (xbox_controller YAW_RANGE)
SIDE_FULL = 0.2                 # m/s at full sidestep stick (xbox_controller Y_RANGE)

# Hard caps on what this module can ever add (rad) and how fast (rad/s):
# neck_pitch, head_pitch, head_yaw, head_roll.
MAX_OFFSET = (0.0, 0.30, 0.70, 0.15)
MAX_RATE = (0.0, 1.0, 3.0, 1.0)      # yaw is quicker: the lead has to get there first
MAX_DT = 0.1


def clean_config(cfg):
    """DEFAULTS overlaid with the valid parts of `cfg`. Never raises."""
    out = dict(DEFAULTS)
    cfg = cfg if isinstance(cfg, dict) else {}
    for key in ("look", "lead"):
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
        self._lead_dir = 0                 # -1 / 0 / +1: which way the duck is steered
        self._lead_since = None
        self._lead_w = 0.0                 # 0 = look-around has the head, 1 = the lead
        self._l1 = 0.0
        self._lead_yaw = 0.0
        self.body_scale = 1.0              # share of the turn/sidestep command to let through
        self.out = [0.0, 0.0, 0.0, 0.0]    # neck_pitch (always 0), head_pitch, head_yaw, head_roll

    def reset(self):
        """Forget the current offsets. Only for moments when the head has been put
        somewhere else anyway (after a get-up or a glide back to the stance)."""
        self._target = [0.0, 0.0, 0.0]
        self._s1 = [0.0, 0.0, 0.0]
        self._look = [0.0, 0.0, 0.0]
        self._next_glance = None
        self._lead_dir, self._lead_since, self._lead_w = 0, None, 0.0
        self._l1 = self._lead_yaw = 0.0
        self.body_scale = 1.0
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

    def _lead(self, now, dt, turn, side, allowed):
        """Sight lead for this tick: sets the head yaw it wants, how much of the head
        it claims, and `body_scale` (0 while the head goes first, then up to 1)."""
        try:
            t = _clamp(float(turn) / TURN_FULL, -1.0, 1.0)
            s = _clamp(float(side) / SIDE_FULL, -1.0, 1.0)
        except (TypeError, ValueError):
            t = s = 0.0
        if not (t == t and s == s):
            t = s = 0.0
        on = allowed and self.cfg["lead"]
        want = _clamp(LEAD_YAW_TURN * t + LEAD_YAW_SIDE * s, -MAX_OFFSET[2], MAX_OFFSET[2]) if on else 0.0
        steer = t + s * (LEAD_YAW_SIDE / LEAD_YAW_TURN)
        direction = 0 if (not on or abs(steer) < LEAD_ON) else (1 if steer > 0 else -1)
        if direction != self._lead_dir:
            self._lead_dir = direction
            self._lead_since = now if direction else None
        if direction == 0:
            want = 0.0
            self.body_scale = 1.0          # nothing to wait for; a stop is never delayed
        else:
            waited = now - self._lead_since - self.cfg["lead_s"]
            self.body_scale = _clamp(waited / LEAD_BLEND_S, 0.0, 1.0) if LEAD_BLEND_S > 0 else 1.0
            if self.cfg["lead_s"] <= 0.0:
                self.body_scale = 1.0
        a = 1.0 - math.exp(-dt / LEAD_TAU) if dt > 0 else 0.0
        self._l1 += (want - self._l1) * a
        self._lead_yaw += (self._l1 - self._lead_yaw) * a
        step = dt / 0.2
        self._lead_w += _clamp((1.0 if direction else 0.0) - self._lead_w, -step, step)

    def update(self, now, dt, allowed=True, turn=0.0, side=0.0):
        """Advance one control tick. Returns the four head offsets (rad):
        [neck_pitch, head_pitch, head_yaw, head_roll]. The neck offset is always 0.
        Afterwards `body_scale` says how much of the turn and sidestep command the
        walk should let through this tick (below 1 only while the head leads).

        allowed     False whenever something else owns the head or the duck is not
                    in plain walking: everything glides back to zero
        turn, side  the commanded turn rate (rad/s) and sidestep speed (m/s)
        """
        try:
            dt = float(dt)
        except (TypeError, ValueError):
            dt = 0.0
        dt = _clamp(dt if dt == dt else 0.0, 0.0, MAX_DT)

        self._lead(now, dt, turn, side, allowed)

        if allowed and self.cfg["look"] and self._lead_dir == 0:
            if self._next_glance is None or now >= self._next_glance:
                self._pick_glance(now)
        else:
            # off, not allowed, or being steered: no new glances, drift to straight
            self._target = [0.0, 0.0, 0.0]
            self._next_glance = None
        a = 1.0 - math.exp(-dt / LOOK_TAU) if dt > 0 else 0.0
        for i in range(3):
            self._s1[i] += (self._target[i] - self._s1[i]) * a
            self._look[i] += (self._s1[i] - self._look[i]) * a

        # the lead takes the head over from the glances while the duck is steered
        keep = 1.0 - self._lead_w
        wanted = [0.0, self._look[0] * keep, self._look[1] * keep + self._lead_yaw,
                  self._look[2] * keep]
        # clamp, then limit the speed of what actually goes out
        for i in range(4):
            w = _clamp(wanted[i], -MAX_OFFSET[i], MAX_OFFSET[i])
            lim = MAX_RATE[i] * dt
            self.out[i] += _clamp(w - self.out[i], -lim, lim)
        return list(self.out)
