"""
Antenna "free animation" — a gentle, organic idle motion for the ear antennas
(pure logic, no hardware, so it unit-tests off-robot).

Background: the ears are analog servos driven by PWM. On stock Raspberry Pi OS
the software-timed pwmio PWM jitters, and that accidental jitter read as a cute
"alive" wiggle. Moving to pigpio hardware-timed PWM (antennas.py) removes the
jitter — the ears go dead still. This module puts the life back *on purpose* and,
crucially, makes it switchable so you can instead hand-control the ears precisely.

Policy, evaluated every control tick:
  * Manual input wins.  If the operator is pushing an ear (|trigger| over a small
    deadband) the raw value passes straight through — precise hand control.
  * Free-anim on + idle.  A slow asymmetric sway plus the occasional twitch, eased
    in over RAMP_S after the last manual touch so releasing a trigger never snaps.
  * Free-anim off + idle.  The ears rest at 0.

Deterministic: the sway is a pure function of `now`; twitches are scheduled from
an injected RNG (pass rng=None for sway only). That keeps it reproducible in tests.
"""
import math

DEADZONE = 0.05            # |manual| above this = hand control, passthrough

# idle sway
SWAY_HZ = 0.22            # slow base sway (Hz)
SWAY_AMP = 0.22           # base sway amplitude (of the [-1,1] range)
SWAY_HZ2 = 0.37          # a second, faster component for a less mechanical feel
SWAY_AMP2 = 0.08
R_PHASE = math.pi * 0.6  # right ear lags the left -> asymmetric, organic

# occasional twitch (a quick perk-up on one or both ears)
TWITCH_EVERY = (5.0, 13.0)   # seconds between twitches (uniform)
TWITCH_S = 0.55              # twitch duration
TWITCH_AMP = 0.5
TWITCH_HZ = 2.6

RAMP_S = 0.6              # ease idle in over this long after a manual release


def _clamp(v, lo=-1.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


class AntennaAnimator:
    def __init__(self, enabled=True, rng=None, deadzone=DEADZONE,
                 sway_amp=SWAY_AMP, twitch_every=TWITCH_EVERY):
        self.enabled = bool(enabled)
        self.rng = rng
        self.deadzone = float(deadzone)
        self.sway_amp = float(sway_amp)
        self.twitch_every = twitch_every

        self._last_active_t = None     # last tick with manual input (None = never)
        self._next_twitch = None
        self._twitch_t0 = None
        self._twitch_ears = (0.0, 0.0)  # per-ear twitch gain this window

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)

    def _manual_active(self, ml, mr):
        return abs(ml) > self.deadzone or abs(mr) > self.deadzone

    def _sway(self, now):
        base = math.sin(2 * math.pi * SWAY_HZ * now)
        second = math.sin(2 * math.pi * SWAY_HZ2 * now)
        left = self.sway_amp * base + SWAY_AMP2 * second
        right = self.sway_amp * math.sin(2 * math.pi * SWAY_HZ * now + R_PHASE) \
            + SWAY_AMP2 * math.sin(2 * math.pi * SWAY_HZ2 * now + R_PHASE)
        return left, right

    def _twitch(self, now):
        """Return (left_add, right_add) for the current twitch, scheduling the next
        one. No-op (0,0) when rng is None."""
        if self.rng is None:
            return 0.0, 0.0
        if self._next_twitch is None:
            self._next_twitch = now + self.rng.uniform(*self.twitch_every)
        # start a twitch?
        if self._twitch_t0 is None and now >= self._next_twitch:
            self._twitch_t0 = now
            # perk one ear, or both, at random
            pick = self.rng.random()
            if pick < 0.4:
                self._twitch_ears = (1.0, 0.0)
            elif pick < 0.8:
                self._twitch_ears = (0.0, 1.0)
            else:
                self._twitch_ears = (1.0, 1.0)
            self._next_twitch = now + self.rng.uniform(*self.twitch_every)
        # emit while inside the window
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
        if self._manual_active(ml, mr):
            self._last_active_t = now
            # a manual touch cancels any in-progress twitch cleanly
            self._twitch_t0 = None
            return _clamp(ml), _clamp(mr)

        if not self.enabled:
            return 0.0, 0.0

        ramp = 1.0
        if self._last_active_t is not None:
            ramp = _clamp((now - self._last_active_t) / RAMP_S, 0.0, 1.0)

        sl, sr = self._sway(now)
        tl, tr = self._twitch(now)
        return _clamp(ramp * (sl + tl)), _clamp(ramp * (sr + tr))
