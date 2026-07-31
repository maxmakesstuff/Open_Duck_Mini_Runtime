"""
Antenna "free animation" — a gentle, organic idle motion for the ear antennas
(pure logic, no hardware, so it unit-tests off-robot).

Background: the ears are analog servos driven by PWM. On stock Raspberry Pi OS the
software-timed PWM jitters, and that accidental jitter read as a cute "alive"
wiggle. Moving to pigpio hardware-timed PWM (antennas.py) removes the jitter — the
ears go dead still. This module puts the life back *on purpose*, switchably.

Motion model (evaluated every control tick):
  * Manual input wins.  If the operator is pushing an ear (|trigger| over a small
    deadband) the raw value passes straight through — precise hand control.
  * Free-anim on + idle.  Each ear does a smooth RANDOM-WAYPOINT wander: it eases
    toward a fresh random target picked at random intervals, so the motion never
    settles into a visible repeating cycle. An occasional twitch adds a "perk".
    Eased in over RAMP_S after the last manual touch so releasing never snaps.
  * Free-anim off + idle.  The ears rest at 0.

Sync:
  * sync=False (default): the two ears wander INDEPENDENTLY (uncorrelated — they
    drift apart and together, the lively "each ear has a mind of its own" look).
  * sync=True: both ears follow ONE shared motion so they move in unison.

Deterministic: inject an RNG (rng=Random(seed)) and the whole thing is
reproducible, so it unit-tests. rng=None (real use) seeds from the system RNG.
"""
import math

DEADZONE = 0.05            # |manual| above this = hand control, passthrough

AMP = 0.5                 # wander target amplitude (of the [-1,1] range)
WAYPOINT_EVERY = (0.7, 2.6)   # seconds between new random wander targets (uniform)
EASE_TAU = 0.45           # smoothing time constant (s); larger = lazier, softer

# occasional twitch (a quick perk on one or both ears), on a random schedule
TWITCH_EVERY = (6.0, 15.0)
TWITCH_S = 0.5
TWITCH_AMP = 0.45
TWITCH_HZ = 2.6

RAMP_S = 0.6              # ease idle in over this long after a manual release
MAX_DT = 0.1             # clamp the per-tick dt so a stall can't jump the ease


def _clamp(v, lo=-1.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


class _Wander:
    """One smooth random-walk channel: eases toward a fresh random target picked at
    random intervals -> organic, non-repeating motion. Deterministic given an rng."""

    def __init__(self, rng, amp=AMP, waypoint_every=WAYPOINT_EVERY, tau=EASE_TAU):
        self.rng = rng
        self.amp = float(amp)
        self.waypoint_every = waypoint_every
        self.tau = float(tau)
        self.val = 0.0
        self.target = 0.0
        self._next = None

    def _new_target(self):
        return self.rng.uniform(-self.amp, self.amp) if self.rng is not None else 0.0

    def _interval(self):
        return self.rng.uniform(*self.waypoint_every) if self.rng is not None else 1.5

    def step(self, now, dt):
        if self._next is None:              # first call: seed a target + schedule
            self.target = self._new_target()
            self._next = now + self._interval()
        if now >= self._next:
            self.target = self._new_target()
            self._next = now + self._interval()
        if dt > 0:
            a = 1.0 - math.exp(-dt / self.tau)   # dt-aware exponential ease
            self.val += (self.target - self.val) * a
        return self.val


class AntennaAnimator:
    def __init__(self, enabled=True, rng=None, sync=False, deadzone=DEADZONE,
                 amp=AMP, twitch_every=TWITCH_EVERY):
        import random as _random
        self.enabled = bool(enabled)
        self.sync = bool(sync)
        self.deadzone = float(deadzone)
        self.rng = rng if rng is not None else _random.Random()
        self.twitch_every = twitch_every
        self._wander_l = _Wander(self.rng, amp=amp)
        self._wander_r = _Wander(self.rng, amp=amp)

        self._last_active_t = None     # last tick with manual input (None = never)
        self._last_now = None          # for dt
        self._next_twitch = None
        self._twitch_t0 = None
        self._twitch_ears = (0.0, 0.0)

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)

    def set_sync(self, sync):
        self.sync = bool(sync)

    def _manual_active(self, ml, mr):
        return abs(ml) > self.deadzone or abs(mr) > self.deadzone

    def _twitch(self, now):
        """(left_add, right_add) for the current twitch, scheduling the next. rng=None
        -> no twitch. Returns per-ear so a twitch can perk one ear or both."""
        if self.rng is None:
            return 0.0, 0.0
        if self._next_twitch is None:
            self._next_twitch = now + self.rng.uniform(*self.twitch_every)
        if self._twitch_t0 is None and now >= self._next_twitch:
            self._twitch_t0 = now
            pick = self.rng.random()
            self._twitch_ears = (1.0, 0.0) if pick < 0.4 else \
                (0.0, 1.0) if pick < 0.8 else (1.0, 1.0)
            self._next_twitch = now + self.rng.uniform(*self.twitch_every)
        if self._twitch_t0 is not None:
            dt = now - self._twitch_t0
            if dt >= TWITCH_S:
                self._twitch_t0 = None
                return 0.0, 0.0
            env = math.sin(math.pi * dt / TWITCH_S)          # 0->1->0 over the window
            wig = math.sin(2 * math.pi * TWITCH_HZ * dt)
            gl, gr = self._twitch_ears
            return gl * TWITCH_AMP * env * wig, gr * TWITCH_AMP * env * wig
        return 0.0, 0.0

    def update(self, now, manual_left=0.0, manual_right=0.0):
        """Return (left, right) ear targets in [-1,1] for this tick."""
        ml, mr = float(manual_left), float(manual_right)
        dt = 0.0 if self._last_now is None else _clamp(now - self._last_now, 0.0, MAX_DT)
        self._last_now = now

        if self._manual_active(ml, mr):
            self._last_active_t = now
            self._twitch_t0 = None        # a manual touch cancels any in-progress twitch
            return _clamp(ml), _clamp(mr)

        if not self.enabled:
            return 0.0, 0.0

        ramp = 1.0
        if self._last_active_t is not None:
            ramp = _clamp((now - self._last_active_t) / RAMP_S, 0.0, 1.0)

        tl, tr = self._twitch(now)
        wl = self._wander_l.step(now, dt)
        wr = self._wander_r.step(now, dt)   # always advance both (no jump when sync flips)
        if self.sync:
            # both ears share ONE motion -> they move in unison.
            left = right = wl + tl
        else:
            left = wl + tl
            right = wr + tr
        return _clamp(ramp * left), _clamp(ramp * right)
