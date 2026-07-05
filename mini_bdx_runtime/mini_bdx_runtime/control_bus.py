"""
Shared control bus: lets the phone Web UI and the Xbox gamepad drive the robot
in parallel, and carries telemetry the other way.

The web server (web_control.py) writes joystick/button intents in from HTTP
handler threads; the input reader (xbox_controller.py) folds them into the SAME
command vector + button edge-detector the gamepad feeds, so both sources share
one code path and you can swap between them freely mid-session. The control loop
publishes a telemetry snapshot the web server serves back to the phone.

Merge policy:
  * Sticks: while the web posts `active=True` (and hasn't gone stale) the web axes
    OVERRIDE the gamepad axes; otherwise the gamepad wins. So releasing the on-
    screen stick (which posts active=False, or simply stops posting) hands control
    straight back to the pad.
  * Triggers: max of the two sources (either can raise an antenna).
  * Buttons: a web "press" becomes a single edge; web "down"/"up" hold across
    ticks (for LB-sprint and the 3 s DPAD-left record hold). Web is OR-ed with the
    gamepad's held state before edge detection, so `.triggered`/`.is_pressed` work
    identically no matter which source acted.

Pure stdlib + thread-safe; no hardware imports, so it unit-tests off-robot.
"""
import threading

BUTTONS = ("A", "B", "X", "Y", "LB", "RB",
           "dpad_up", "dpad_down", "dpad_left", "dpad_right")

# If the phone stops posting commands for this long, its `active` override lapses
# and the gamepad regains the sticks (so a closed browser tab can't wedge input).
COMMAND_STALE_S = 0.5


class ControlBus:
    def __init__(self, stale_s=COMMAND_STALE_S):
        self._lock = threading.Lock()
        self._stale_s = stale_s

        self._active = False
        self._axes = [0.0, 0.0, 0.0, 0.0]     # l_x, l_y, r_x, r_y
        self._trig = [0.0, 0.0]               # left, right
        self._cmd_time = None                 # wall time of the last set_command

        self._held = {b: False for b in BUTTONS}
        self._pending = {b: 0 for b in BUTTONS}   # queued momentary "press" taps
        self._gap = {b: False for b in BUTTONS}   # forced release tick after a tap

        # IMU-trim tuner channel: the web posts +/- nudges (accumulated) and a save
        # request; the walk loop drains them each tick (see consume_trim).
        self._trim_delta = {"pitch": 0.0, "roll": 0.0}
        self._trim_save = False

        self._telemetry = {}

    # ------------------------------------------------------ web -> robot (in)
    def set_command(self, now, active=False, l_x=0.0, l_y=0.0, r_x=0.0, r_y=0.0,
                    left_trigger=0.0, right_trigger=0.0):
        with self._lock:
            self._active = bool(active)
            self._axes = [_c11(l_x), _c11(l_y), _c11(r_x), _c11(r_y)]
            self._trig = [_c01(left_trigger), _c01(right_trigger)]
            self._cmd_time = now

    def push_button(self, button, action="press"):
        """action: 'press' (a tap -> one edge) | 'down' | 'up' (held)."""
        if button not in self._held:
            return False
        with self._lock:
            if action == "press":
                self._pending[button] += 1
            elif action == "down":
                self._held[button] = True
            elif action == "up":
                self._held[button] = False
            else:
                return False
        return True

    def push_trim(self, axis, delta):
        """Queue an IMU-trim nudge from the web (accumulated until consumed)."""
        if axis not in self._trim_delta:
            return False
        with self._lock:
            self._trim_delta[axis] += float(delta)
        return True

    def push_trim_save(self):
        """Queue a 'save the current trim to duck_config' request from the web."""
        with self._lock:
            self._trim_save = True
        return True

    # ------------------------------------------------------ robot <- web (read)
    def consume_trim(self):
        """Return (pitch_delta, roll_delta, save) accumulated since the last call,
        resetting them. The walk loop applies the deltas to the live IMU."""
        with self._lock:
            p = self._trim_delta["pitch"]
            r = self._trim_delta["roll"]
            s = self._trim_save
            self._trim_delta = {"pitch": 0.0, "roll": 0.0}
            self._trim_save = False
            return (p, r, s)

    def stick_override(self, now):
        """Return (active, l_x, l_y, r_x, r_y, left_trigger, right_trigger).
        `active` is False if the web hasn't posted recently (stale) -> gamepad wins."""
        with self._lock:
            active = (
                self._active
                and self._cmd_time is not None
                and (now - self._cmd_time) <= self._stale_s
            )
            return (active, *self._axes, *self._trig)

    def consume_buttons(self):
        """Return {button: pressed_bool} for THIS tick.

        Held buttons stay pressed. A queued tap is emitted as one pressed tick
        FOLLOWED by a forced release tick, so N rapid taps become N separate
        rising edges (two taps consumed back-to-back would otherwise look like one
        long hold = one edge)."""
        with self._lock:
            out = {}
            for b in BUTTONS:
                if self._held[b]:
                    pressed = True
                elif self._gap[b]:
                    self._gap[b] = False       # release tick between taps
                    pressed = False
                elif self._pending[b] > 0:
                    self._pending[b] -= 1
                    self._gap[b] = True
                    pressed = True
                else:
                    pressed = False
                out[b] = pressed
            return out

    # ------------------------------------------------------------- telemetry
    def set_telemetry(self, snapshot):
        with self._lock:
            self._telemetry = dict(snapshot)

    def get_telemetry(self):
        with self._lock:
            return dict(self._telemetry)


def _c11(v):
    v = float(v)
    return -1.0 if v < -1.0 else 1.0 if v > 1.0 else v


def _c01(v):
    v = float(v)
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v
