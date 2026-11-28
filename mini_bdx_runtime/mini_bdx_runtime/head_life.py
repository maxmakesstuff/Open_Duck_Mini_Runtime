"""
Head life: context animations that make the duck look alive.

  * Look around: not a slow scan but the way a bird looks: a quick turn, a
    moment of looking, another quick turn. The head also leans left and right, the
    ears move with it, and now and then the duck says something.
    - While WALKING it is busy: a new animation every second or two, drawn from
      the whole library (glances, curious tilts, double takes, nods, sniffing the
      ground, a happy wiggle, ...).
    - While STANDING it only looks around, calmly, and very seldom "notices"
      something and reacts.
  * Sight lead: when the duck is steered into a turn or a sidestep, the head turns
    that way first and the body follows a moment later. It takes the head over
    from the animations for as long as the duck is being steered; the ears go on.

Everything is an OFFSET on the head joints' targets (head_pitch, head_yaw,
head_roll; the neck is left alone) plus two ear values.

HOW the offsets reach the head matters a great deal (simulated 2026-11-28 with both
walk policies in MuJoCo, see CLAUDE.md):
  * If the policy SEES them (in its head command, or just in the head joints'
    positions and targets it observes), it walks worse: with the head turned 23 deg
    the dk5426 walker stops walking forward, the stock walk stops for a head tilted
    up or leaning, both lose most of their turning when the head looks into the
    turn, and with a heavy head the liveliest settings make it fall.
  * If they are HIDDEN from it (`hide`, the default: not in the command, and taken
    out of the head positions and targets it observes), the policy walks, turns and
    balances exactly as with the head straight, at every speed and size setting,
    also with a head 60 % heavier and stiff.
So the walk loop adds the offsets after the policy and subtracts them from the
observation. `hide: false` in the config restores the old behaviour (offsets in the
head command).

Directions (checked against the robot model, 2026-11-28): head_pitch + = up,
head_yaw + = left, head_roll + = leaning left. Ears: + = perked, - = laid back
(assumed; the ear servos' direction is per duck).

Tried and removed (2026-11-27): a head bob, the neck nodding with every step off the
gait clock. On the real duck it visibly upset the walk, and the one recorded walk
with it at 10 degrees ended in a fall after 8 s. The policy never saw a head moving
in rhythm with its steps. Do not bring it back without a policy trained for it.
(The small nods in the animations below are single gestures, not tied to the gait,
and they move the head, not the neck.)

Motion safety: every output is clamped to a small envelope, moves along smooth
curves (no steps) and is limited in speed, whatever the configuration says. When
switched off or not allowed (puppeteer has the head, playback, fall, get-up) the
offsets glide back to zero.

Pure and hardware-free (no numpy), so it unit-tests off-robot.
"""
import math

DEFAULTS = {
    "look": False,           # the context animations ("look around")
    "look_scale": 1.0,       # size of the head motion; 1 = as written below
    "speed": 1.0,            # how quick the moves are; 1 = as written below
    "busy": 1.0,             # how often something happens; 1 = as written below
    "ears": True,            # the ears move with the animations
    "voice": True,           # some animations come with a sound
    "lead": False,           # look into a turn before walking into it
    "lead_s": 0.25,          # how long the head goes first before the body turns
    "hide": True,            # keep the offsets out of what the walk policy observes
}
LIMITS = {"look_scale": (0.2, 1.5), "speed": (0.4, 1.6), "busy": (0.3, 2.0),
          "lead_s": (0.0, 0.8)}
_BOOLS = ("look", "ears", "voice", "lead", "hide")

# ----------------------------------------------------------------- animations ----
# One step = (move_s, (hold_min_s, hold_max_s), pitch, yaw, roll, ear_left, ear_right).
# The head moves to that pose in move_s along a smooth curve, then holds it.
# Angles in rad at look_scale 1; ears in -1..1. An animation written for the LEFT
# is mirrored at random (yaw and roll flip, the ears swap).
# kind "look"  = plain looking around (also used while standing)
# kind "react" = the duck notices or expresses something (walking: often;
#                standing: very seldom)
# voice = (sound category, chance) or None; walk / stand = how likely it is picked.
_Z = (0.0, 0.0, 0.0, 0.0, 0.0)


def _a(kind, steps, walk=1.0, stand=1.0, voice=None, mirror=True):
    return {"kind": kind, "steps": tuple(steps), "walk": float(walk),
            "stand": float(stand), "voice": voice, "mirror": bool(mirror)}


ANIMATIONS = {
    # ---- looking around ----
    "glance_side": _a("look", [
        (0.26, (0.5, 1.3), 0.00, 0.40, 0.06, 0.25, 0.05),
        (0.30, (0.0, 0.3), *_Z)], walk=3.0, stand=3.0),
    "glance_far": _a("look", [
        (0.30, (0.5, 1.1), 0.04, 0.58, 0.12, 0.45, 0.10),
        (0.34, (0.0, 0.3), *_Z)], walk=2.0, stand=1.5),
    "look_up": _a("look", [
        (0.26, (0.6, 1.2), 0.22, 0.05, 0.00, 0.50, 0.50),
        (0.30, (0.0, 0.3), *_Z)], walk=1.5, stand=1.5),
    "look_down": _a("look", [
        (0.28, (0.6, 1.4), -0.30, 0.06, 0.04, -0.20, -0.20),
        (0.32, (0.0, 0.3), *_Z)], walk=1.5, stand=1.5),
    "peek_up_side": _a("look", [
        (0.26, (0.5, 1.2), 0.17, 0.32, 0.10, 0.55, 0.20),
        (0.30, (0.0, 0.3), *_Z)], walk=2.0, stand=2.0),
    "scan_sweep": _a("look", [
        (0.30, (0.15, 0.35), 0.00, 0.45, 0.05, 0.20, 0.00),
        (0.90, (0.15, 0.40), 0.02, -0.45, -0.05, 0.00, 0.20),
        (0.40, (0.0, 0.3), *_Z)], walk=1.0, stand=1.5),
    "two_glances": _a("look", [
        (0.24, (0.35, 0.7), 0.02, 0.36, 0.08, 0.30, 0.00),
        (0.30, (0.4, 0.9), -0.04, -0.30, -0.06, 0.00, 0.30),
        (0.28, (0.0, 0.3), *_Z)], walk=2.0, stand=1.5),
    # ---- noticing, reacting ----
    "curious_tilt": _a("react", [
        (0.22, (0.9, 1.7), 0.05, 0.12, 0.28, 0.75, 0.20),
        (0.30, (0.0, 0.3), *_Z)], walk=3.0, stand=3.0, voice=("curious", 0.45)),
    "notice": _a("react", [
        (0.15, (0.25, 0.40), 0.15, 0.00, 0.00, 0.90, 0.90),
        (0.22, (0.9, 1.5), 0.08, 0.36, 0.18, 0.80, 0.35),
        (0.32, (0.0, 0.3), *_Z)], walk=2.5, stand=3.0, voice=("notice", 0.7)),
    "double_take": _a("react", [
        (0.20, (0.10, 0.20), 0.00, 0.34, 0.04, 0.20, 0.00),
        (0.20, (0.20, 0.35), 0.00, 0.05, 0.00, 0.00, 0.00),
        (0.16, (0.7, 1.2), 0.06, 0.46, 0.12, 0.85, 0.50),
        (0.32, (0.0, 0.3), *_Z)], walk=2.0, stand=2.0, voice=("curious", 0.4)),
    "listen": _a("react", [
        (0.24, (1.1, 2.0), 0.03, -0.06, 0.24, 0.90, -0.30),
        (0.30, (0.0, 0.3), *_Z)], walk=1.5, stand=2.0),
    "ears_perk": _a("react", [
        (0.16, (0.8, 1.4), 0.08, 0.00, 0.00, 0.90, 0.90),
        (0.35, (0.0, 0.3), *_Z)], walk=1.5, stand=1.5, mirror=False),
    "ear_flick": _a("react", [
        (0.10, (0.05, 0.10), 0.00, 0.00, 0.02, 0.90, 0.00),
        (0.12, (0.05, 0.10), 0.00, 0.00, 0.00, -0.25, 0.00),
        (0.14, (0.0, 0.2), *_Z)], walk=2.0, stand=1.0),
    "nod_yes": _a("react", [
        (0.16, (0.03, 0.08), -0.18, 0.00, 0.00, 0.30, 0.30),
        (0.16, (0.03, 0.08), 0.08, 0.00, 0.00, 0.40, 0.40),
        (0.16, (0.03, 0.08), -0.14, 0.00, 0.00, 0.30, 0.30),
        (0.22, (0.0, 0.3), *_Z)], walk=1.0, stand=0.3, voice=("yes", 0.5), mirror=False),
    "shake_no": _a("react", [
        (0.15, (0.02, 0.06), 0.00, 0.20, 0.00, -0.40, -0.40),
        (0.20, (0.02, 0.06), 0.00, -0.20, 0.00, -0.40, -0.40),
        (0.18, (0.02, 0.06), 0.00, 0.14, 0.00, -0.30, -0.30),
        (0.20, (0.0, 0.3), *_Z)], walk=0.7, stand=0.2, voice=("no", 0.5)),
    "happy_wiggle": _a("react", [
        (0.15, (0.02, 0.06), 0.04, 0.00, 0.20, 0.80, -0.50),
        (0.18, (0.02, 0.06), 0.04, 0.00, -0.20, -0.50, 0.80),
        (0.16, (0.02, 0.06), 0.04, 0.00, 0.15, 0.80, -0.50),
        (0.16, (0.02, 0.06), 0.02, 0.00, -0.10, -0.30, 0.60),
        (0.22, (0.0, 0.3), *_Z)], walk=1.5, stand=0.3, voice=("happy", 0.6)),
    "sniff_ground": _a("react", [
        (0.28, (0.20, 0.35), -0.36, 0.12, 0.05, -0.30, -0.30),
        (0.22, (0.15, 0.30), -0.38, -0.12, -0.05, -0.30, -0.10),
        (0.20, (0.25, 0.50), -0.34, 0.08, 0.04, -0.10, -0.30),
        (0.34, (0.0, 0.3), *_Z)], walk=1.5, stand=0.6, voice=("babble", 0.3)),
    "look_back": _a("react", [
        (0.30, (0.7, 1.3), 0.05, 0.64, 0.16, 0.60, 0.10),
        (0.36, (0.0, 0.3), *_Z)], walk=1.5, stand=0.8, voice=("curious", 0.25)),
    "sky_gaze": _a("react", [
        (0.28, (1.1, 1.9), 0.27, 0.10, 0.12, 0.60, 0.60),
        (0.36, (0.0, 0.3), *_Z)], walk=1.0, stand=1.0, voice=("amazed", 0.35)),
    "chatter": _a("react", [
        (0.14, (0.04, 0.10), -0.08, 0.04, 0.03, 0.50, 0.20),
        (0.14, (0.04, 0.10), 0.05, 0.06, 0.05, 0.20, 0.50),
        (0.14, (0.04, 0.10), -0.07, 0.08, 0.03, 0.50, 0.20),
        (0.14, (0.20, 0.40), 0.04, 0.08, 0.06, 0.30, 0.40),
        (0.26, (0.0, 0.3), *_Z)], walk=1.5, stand=0.3, voice=("babble", 0.8)),
    "shy_duck": _a("react", [
        (0.30, (0.9, 1.5), -0.20, 0.10, 0.16, -0.70, -0.70),
        (0.36, (0.0, 0.3), *_Z)], walk=0.5, stand=0.6),
}

# Seconds between animations (divided by `busy`).
GAP_WALK = (0.5, 2.0)
GAP_STAND = (2.5, 6.0)
REACT_STAND = (45.0, 110.0)     # standing: a reaction only this seldom
MIN_MOVE_S = 0.10
RETURN_S = 0.35                 # glide home when switched off / not allowed
VOICE_CHANCE_STAND = 0.5        # standing reactions are quieter still

# Sight lead: head yaw at full turn stick / full sidestep stick (rad), and how much
# stick counts as "being steered" (share of full deflection).
LEAD_YAW_TURN = 0.45
LEAD_YAW_SIDE = 0.30
LEAD_ON = 0.15
LEAD_TAU = 0.10                 # two of these in a row: brisk, but no step
LEAD_BLEND_S = 0.15             # body turn fades in over this long after the lead
TURN_FULL = 1.0                 # rad/s at full turn stick (xbox_controller YAW_RANGE)
SIDE_FULL = 0.2                 # m/s at full sidestep stick (xbox_controller Y_RANGE)

# Hard caps on what this module can ever add (rad) and how fast (rad/s), whatever
# the settings: neck_pitch, head_pitch, head_yaw, head_roll.
MAX_OFFSET = (0.0, 0.45, 0.70, 0.40)
MAX_RATE = (0.0, 4.0, 5.0, 4.0)
EAR_RATE = 12.0                 # ear units per second
MAX_DT = 0.1


def clean_config(cfg):
    """DEFAULTS overlaid with the valid parts of `cfg`. Never raises."""
    out = dict(DEFAULTS)
    cfg = cfg if isinstance(cfg, dict) else {}
    for key in _BOOLS:
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


def _ease(u):
    """Smooth 0..1: no jump in speed or acceleration at either end."""
    u = _clamp(u, 0.0, 1.0)
    return u * u * u * (10.0 + u * (6.0 * u - 15.0))


class HeadLife:
    def __init__(self, cfg=None, rng=None):
        import random
        self.cfg = clean_config(cfg)
        self.rng = rng if rng is not None else random.Random()
        self.reset()

    def reset(self):
        """Forget the current offsets. Only for moments when the head has been put
        somewhere else anyway (after a get-up or a glide back to the stance)."""
        self._pose = [0.0] * 5             # pitch, yaw, roll, ear_l, ear_r (the animation)
        self._from = [0.0] * 5
        self._to = [0.0] * 5
        self._t0 = None                    # start of the current move, or None
        self._move = 0.0
        self._hold = 0.0
        self._hold_until = None
        self._steps = []                   # what is left of the running animation
        self._next_at = None               # when the next animation may start
        self._next_react = None            # standing: when the next reaction may come
        self._walking = None
        self._pending = None               # an animation asked for by name
        self._voice = None
        self.anim = ""                     # name of the running animation ("" between two)
        self._last = ""                    # the one before, so it is not repeated at once
        self._lead_dir = 0                 # -1 / 0 / +1: which way the duck is steered
        self._lead_since = None
        self._lead_w = 0.0                 # 0 = the animations have the head, 1 = the lead
        self._l1 = 0.0
        self._lead_yaw = 0.0
        self.body_scale = 1.0              # share of the turn/sidestep command to let through
        self.ears = [0.0, 0.0]             # ear offsets, left and right
        self.out = [0.0, 0.0, 0.0, 0.0]    # neck_pitch (always 0), head_pitch, head_yaw, head_roll

    # ------------------------------------------------------------ settings ----
    def set(self, changes):
        merged = dict(self.cfg)
        merged.update(changes if isinstance(changes, dict) else {})
        self.cfg = clean_config(merged)

    def public(self):
        return dict(self.cfg, anim=self.anim, animations=sorted(ANIMATIONS),
                    active=any(abs(v) > 1e-3 for v in self.out + self.ears))

    def play(self, name):
        """Ask for one animation by name; it starts on the next tick (if allowed).
        Returns False for an unknown name."""
        if name not in ANIMATIONS:
            return False
        self._pending = name
        return True

    def pop_voice(self):
        """The sound category the running animation wants to be heard with, once."""
        v, self._voice = self._voice, None
        return v

    # ---------------------------------------------------------- animations ----
    def _gap(self, walking):
        lo, hi = GAP_WALK if walking else GAP_STAND
        return self.rng.uniform(lo, hi) / self.cfg["busy"]

    def _pick(self, now, walking):
        """Name of the next animation for this context."""
        react_due = False
        if not walking:
            if self._next_react is None:
                self._next_react = now + self.rng.uniform(*REACT_STAND) / self.cfg["busy"]
            elif now >= self._next_react:
                react_due = True
                self._next_react = now + self.rng.uniform(*REACT_STAND) / self.cfg["busy"]
        names, weights = [], []
        for name, a in ANIMATIONS.items():
            if walking:
                w = a["walk"]
            elif react_due:
                w = a["stand"] if a["kind"] == "react" else 0.0
            else:
                w = a["stand"] if a["kind"] == "look" else 0.0
            if name == self._last:
                w *= 0.15                  # do not do the same thing twice in a row
            if w > 0:
                names.append(name)
                weights.append(w)
        r = self.rng.uniform(0.0, sum(weights))
        for name, w in zip(names, weights):
            r -= w
            if r <= 0:
                return name
        return names[-1]

    def _start(self, name, now, walking):
        a = ANIMATIONS[name]
        sign = -1.0 if (a["mirror"] and self.rng.random() < 0.5) else 1.0
        size, speed = self.cfg["look_scale"], self.cfg["speed"]
        steps = []
        for move, (h0, h1), pitch, yaw, roll, el, er in a["steps"]:
            if sign < 0:
                el, er = er, el
            steps.append((max(MIN_MOVE_S, move / speed), self.rng.uniform(h0, h1),
                          [pitch * size, yaw * size * sign, roll * size * sign, el, er]))
        self._steps = steps
        self.anim = self._last = name
        self._voice = None
        if a["voice"] and self.cfg["voice"]:
            category, chance = a["voice"]
            if not walking:
                chance *= VOICE_CHANCE_STAND
            if self.rng.random() < chance:
                self._voice = category
        self._begin_step(now)

    def _begin_step(self, now):
        move, hold, pose = self._steps.pop(0)
        self._from = list(self._pose)
        self._to = pose
        self._t0, self._move, self._hold = now, move, hold
        self._hold_until = None

    def _go_home(self, now):
        """Drop whatever runs and glide to neutral."""
        self._steps, self._pending, self._voice = [], None, None
        self._next_at = None
        home = [0.0] * 5
        away = any(abs(v) > 1e-4 for v in self._pose)
        if away and (self._t0 is None or self._to != home):
            self._from, self._to = list(self._pose), home
            self._t0, self._move, self._hold = now, RETURN_S, 0.0
            self._hold_until = None
        if not away:
            self.anim = ""

    def _animate(self, now, on, walking):
        if not on:
            self._go_home(now)
        else:
            if walking != self._walking:
                # the context changed: do not sit out a long standing pause
                self._walking = walking
                if self._next_at is not None:
                    self._next_at = min(self._next_at, now + self._gap(walking))
                if walking:
                    self._next_react = None
            idle = self._t0 is None and not self._steps
            if self._pending and (idle or self._hold_until is not None):
                name, self._pending = self._pending, None
                self._start(name, now, walking)
            elif idle:
                if self._next_at is None:
                    self._next_at = now + self._gap(walking) * (0.3 if walking else 0.5)
                elif now >= self._next_at:
                    self._next_at = None
                    self._start(self._pick(now, walking), now, walking)
        # advance the running move / hold
        if self._t0 is not None:
            if self._hold_until is None:
                u = (now - self._t0) / self._move if self._move > 0 else 1.0
                s = _ease(u)
                self._pose = [a + (b - a) * s for a, b in zip(self._from, self._to)]
                if u >= 1.0:
                    self._pose = list(self._to)
                    self._hold_until = now + self._hold
            if self._hold_until is not None and now >= self._hold_until:
                if self._steps:
                    self._begin_step(now)
                else:
                    self._t0 = self._hold_until = None
                    self.anim = ""
                    if on:
                        self._next_at = now + self._gap(walking)

    # ---------------------------------------------------------- sight lead ----
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

    # --------------------------------------------------------------- tick ----
    def update(self, now, dt, allowed=True, turn=0.0, side=0.0, walking=False):
        """Advance one control tick. Returns the four head offsets (rad):
        [neck_pitch, head_pitch, head_yaw, head_roll]. The neck offset is always 0.
        Afterwards:
          `ears`        the two ear offsets (-1..1) that go with the head
          `body_scale`  how much of the turn and sidestep command the walk should let
                        through this tick (below 1 only while the head leads)
          `pop_voice()` a sound category, once, when an animation wants to be heard

        allowed     False whenever something else owns the head or the duck is not
                    in plain walking: everything glides back to zero
        turn, side  the commanded turn rate (rad/s) and sidestep speed (m/s)
        walking     True while the duck is being driven: busy and expressive.
                    False (standing): calm looking around, a reaction only seldom
        """
        try:
            dt = float(dt)
        except (TypeError, ValueError):
            dt = 0.0
        dt = _clamp(dt if dt == dt else 0.0, 0.0, MAX_DT)

        self._lead(now, dt, turn, side, allowed)
        self._animate(now, bool(allowed and self.cfg["look"]), bool(walking))

        # the lead takes the head over from the animations while the duck is steered
        keep = 1.0 - self._lead_w
        p, y, r, el, er = self._pose
        wanted = [0.0, p * keep, y * keep + self._lead_yaw, r * keep]
        boost = max(1.0, self.cfg["speed"])
        for i in range(4):
            w = _clamp(wanted[i], -MAX_OFFSET[i], MAX_OFFSET[i])
            lim = MAX_RATE[i] * boost * dt
            self.out[i] += _clamp(w - self.out[i], -lim, lim)
        want_ears = (el, er) if self.cfg["ears"] else (0.0, 0.0)
        for i in range(2):
            w = _clamp(want_ears[i], -1.0, 1.0)
            self.ears[i] += _clamp(w - self.ears[i], -EAR_RATE * dt, EAR_RATE * dt)
        return list(self.out)
