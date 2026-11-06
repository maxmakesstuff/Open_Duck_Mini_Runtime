"""
Live joint-offset tuning for the phone calibration page (webui/offsets.html).

Dial in each joint's soft offset by eye, with the duck standing in front of you,
instead of re-running find_soft_offsets.py joint by joint. The idea and the original
page were contributed by Gene Turnbow (SCIFI.radio).

The tuner belongs to the control loop, not the web server: the loop owns the motors
and the HWI's offsets, so it is the only place that knows what is actually applied.
The server just forwards edits over the ControlBus settings channel and reports this
object's snapshot back through telemetry, so the page can never show a value the
motors did not get.

Motor safety, enforced here rather than in the page:
  * Edits are refused unless the duck is holding its stance (never mid-stride: the
    policy reads dof_pos, which the offset shifts).
  * An edit never reaches the servo as a step. The live offset slews to the target,
    and a stalled tick cannot turn into a jump.
  * Non-finite or non-numeric values are rejected; targets clamp to +/-OFFSET_LIMIT.

Pure stdlib -> unit-tests off-robot (see test_offset_tuner.py).
"""
import math

OFFSET_LIMIT = 0.5        # rad (~29 deg). Largest offset on a tuned reference duck: 0.124
OFFSET_SLEW_RAD_S = 0.5   # how fast a new offset reaches the servo
STANCE_SLEW_RAD_S = 0.5   # how fast "hold stance" glides the pose to init_pos
MAX_STEP_DT = 0.1         # s; caps one tick, e.g. after the ~0.2 s battery bus handoff

_DECIMALS = 4             # 0.1 mrad, well below the STS3215's 1.5 mrad encoder count


def _toward(current, goal, max_step):
    """Move current toward goal by at most max_step, landing on goal exactly."""
    if abs(goal - current) <= max_step:
        return goal
    return current + math.copysign(max_step, goal - current)


class OffsetTuner:
    def __init__(self, live_offsets, limit=OFFSET_LIMIT, slew_rad_s=OFFSET_SLEW_RAD_S):
        self._live = live_offsets      # the HWI's own dict, mutated in place
        self._target = dict(live_offsets)
        self._saved = dict(live_offsets)
        self._limit = limit
        self._slew = slew_rad_s
        self._gliding = False
        self.holding = False
        self.error = ""

    # ------------------------------------------------------ stance hold state
    def hold(self):
        self.holding = True

    def release(self):
        """Back to walking. Unsaved targets stay applied, so you can try them."""
        self.holding = False
        self._gliding = False

    # ------------------------------------------------------------------ edits
    def set_target(self, joint, value):
        """Queue a new offset. Returns False (and changes nothing) if refused."""
        if not self.holding or joint not in self._target:
            return False
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if not math.isfinite(value):
            return False
        # The window always contains the saved value, so a duck that legitimately
        # carries a larger offset is never pulled in just by touching the joint.
        saved = self._saved[joint]
        lo, hi = min(-self._limit, saved), max(self._limit, saved)
        self._target[joint] = round(max(lo, min(hi, float(value))), _DECIMALS)
        return True

    def revert(self):
        """Glide back to the last saved values. Moves motors, so it needs the hold."""
        if not self.holding:
            return False
        self._target = dict(self._saved)
        return True

    # ------------------------------------------------------------ control tick
    def step(self, dt):
        """Slew the live offsets toward their targets. Call once per written tick."""
        max_step = self._slew * min(dt, MAX_STEP_DT)
        for joint, target in self._target.items():
            if self._live[joint] != target:
                self._live[joint] = _toward(self._live[joint], target, max_step)

    def glide(self, pose, goal, dt, slew_rad_s=STANCE_SLEW_RAD_S):
        """One bounded tick of pose -> goal, for gliding into the stance."""
        max_step = slew_rad_s * min(dt, MAX_STEP_DT)
        new = [_toward(float(p), float(g), max_step) for p, g in zip(pose, goal)]
        self._gliding = any(n != float(g) for n, g in zip(new, goal))
        return new

    # ------------------------------------------------------------- persistence
    def save(self, save_fields):
        """Persist the targets through save_fields (duck_config.save_config_fields).
        Never raises: the walk must survive a full disk or a damaged config file, and
        the page reports `error` instead."""
        try:
            save_fields({"joints_offsets": dict(self._target)})
        except (OSError, ValueError) as exc:
            self.error = f"save failed: {exc}"
            return False
        self._saved = dict(self._target)
        self.error = ""
        return True

    # ---------------------------------------------------------------- reporting
    @property
    def moving(self):
        return self._gliding or self._live != self._target

    @property
    def dirty(self):
        return self._target != self._saved

    def snapshot(self):
        """Telemetry view. Fresh dicts: the HTTP thread serialises this off-tick."""
        return {
            "offsets": dict(self._target),
            "saved": dict(self._saved),
            "limit": self._limit,
            "holding": self.holding,
            "moving": self.moving,
            "dirty": self.dirty,
            "error": self.error,
        }
