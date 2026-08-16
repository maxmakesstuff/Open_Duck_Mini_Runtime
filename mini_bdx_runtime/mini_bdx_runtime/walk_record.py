"""
Whole-body record & playback for the walk loop — the SAFE way.

We do NOT record joint angles and replay them open-loop (that would ignore the
gyro and topple the robot). Instead we record the **command stream** — the same
velocity / turn / head / gait intent the gamepad produces each tick — and on
playback feed it back into the loop. The ONNX policy still runs live on live IMU
and foot data, so **balancing stays fully active**: the robot re-walks the same
path and reacts to disturbances exactly as it does under manual control. This is
the difference between "replay a recording" and "replay an animation".

Consequences that make this safe:
  * Stopping mid-playback is not a freeze — control returns to the operator and
    the commands ramp to zero over a short window, which the policy handles as an
    ordinary "stop walking" transition (decelerate to a stand), not a lurch.
  * Recorded commands are UNTRUSTED input: they are clamped to the known command
    ranges at record time AND again at playback time (motor-safety rule — never
    trust authored data to be in-bounds).

Pure stdlib so it imports and unit-tests off-robot. The loop owns the hardware;
this owns the buffer, the clamp, the playback cursor, and the stop-ramp.

A recorded frame is a plain tuple/list of 9 floats:
    [lin_vel_x, lin_vel_y, ang_vel, neck_pitch, head_pitch, head_yaw, head_roll,
     phase_freq_offset, sprint]
(the 7-element `last_commands` vector + gait frequency offset + sprint 0/1).

Sound presses (the B button) and projector toggles (X, whose LED also drives
the scanner lamp-loop sound) are kept OUT of the float frame: they live in
parallel event lists — `sound_events` as (frame_index, sound_name) pairs and
`proj_events` as (frame_index, state) pairs — so the frame layout, and every
existing recording, stays untouched. The loop logs the exact sound it played /
the projector state that resulted while recording; playback re-plays that
sound and re-applies that state (a set, not a blind toggle, so a different
projector state at playback start can't invert the timeline) when the cursor
serves that frame. Events are untrusted on load: bad shapes, out-of-range
indices, and wrong-typed values are dropped (the player additionally ignores
sound names not present in the robot's sound set).
"""
import pickle

# Command ranges (mirror xbox_controller.py). Anything recorded is clamped to
# these on the way in AND on the way out.
LIN_X_RANGE = (-0.15, 0.15)
LIN_Y_RANGE = (-0.2, 0.2)
ANG_RANGE = (-1.0, 1.0)
NECK_PITCH_RANGE = (-0.34, 1.1)
HEAD_PITCH_RANGE = (-0.78, 0.3)
HEAD_YAW_RANGE = (-0.5, 0.5)
HEAD_ROLL_RANGE = (-0.5, 0.5)
GAIT_OFFSET_RANGE = (-0.5, 0.5)     # phase_frequency_factor_offset, generous but bounded
SPRINT_RANGE = (0.0, 1.0)

_FRAME_RANGES = (
    LIN_X_RANGE, LIN_Y_RANGE, ANG_RANGE,
    NECK_PITCH_RANGE, HEAD_PITCH_RANGE, HEAD_YAW_RANGE, HEAD_ROLL_RANGE,
    GAIT_OFFSET_RANGE, SPRINT_RANGE,
)
FRAME_LEN = len(_FRAME_RANGES)

DEFAULT_MAX_RECORD_S = 120.0
# Time to ramp commands to zero when playback stops / at a non-looping end, so
# the robot decelerates to a stand instead of cutting velocity dead.
DEFAULT_STOP_RAMP_S = 0.4


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def clamp_frame(frame):
    """Clamp a raw 9-float frame to the safe command ranges. Pads/truncates to
    FRAME_LEN so a short/long authored frame can't desync the layout."""
    out = []
    for i, (lo, hi) in enumerate(_FRAME_RANGES):
        v = float(frame[i]) if i < len(frame) else 0.0
        out.append(_clamp(v, lo, hi))
    return out


def commands_to_frame(last_commands, gait_offset, sprint):
    """Build a clamped frame from the loop's live state."""
    lc = list(last_commands) + [0.0] * (7 - len(last_commands))
    return clamp_frame([
        lc[0], lc[1], lc[2], lc[3], lc[4], lc[5], lc[6],
        gait_offset, 1.0 if sprint else 0.0,
    ])


def frame_to_commands(frame):
    """Split a (clamped) frame back into (last_commands[7], gait_offset, sprint_bool)."""
    f = clamp_frame(frame)
    return f[0:7], f[7], f[8] >= 0.5


class WalkRecorder:
    """Records the command stream and plays it back with a safe stop-ramp.

    States: "idle" | "recording" | "playing".
    Frames are captured/emitted once per control tick by the loop.
    """

    def __init__(self, control_hz, max_record_s=DEFAULT_MAX_RECORD_S,
                 stop_ramp_s=DEFAULT_STOP_RAMP_S, loop=True):
        self.control_hz = float(control_hz)
        self.max_frames = int(max_record_s * control_hz)
        self.stop_ramp_frames = max(1, int(stop_ramp_s * control_hz))
        self.loop = loop

        self.state = "idle"
        self.frames = []
        self.sound_events = []     # (frame_index, sound_name) pairs
        self.proj_events = []      # (frame_index, projector_state) pairs
        self._cursor = 0
        self._ramp = None          # (from_frame, ticks_left) while ramping down
        self._pending_sounds = []  # sounds attached to the frame last served
        self._pending_proj = []    # projector states for the frame last served

    # ------------------------------------------------------------- recording
    def start_recording(self):
        self.state = "recording"
        self.frames = []
        self.sound_events = []
        self.proj_events = []
        self._ramp = None

    def record_sound(self, name):
        """Attach a sound to the frame recorded THIS tick (the loop plays the
        sound and records the frame in the same tick, sound first). No-op unless
        recording."""
        if self.state == "recording" and name:
            self.sound_events.append((len(self.frames), str(name)))

    def record_projector(self, state):
        """Attach the projector's (post-toggle) state to the frame recorded THIS
        tick. Also used for the record-start baseline. No-op unless recording."""
        if self.state == "recording":
            self.proj_events.append((len(self.frames), bool(state)))

    def record(self, last_commands, gait_offset, sprint):
        """Append one clamped frame. Returns True while still recording; flips to
        idle (returns False) once the max length is hit."""
        if self.state != "recording":
            return False
        self.frames.append(commands_to_frame(last_commands, gait_offset, sprint))
        if len(self.frames) >= self.max_frames:
            self.state = "idle"
            return False
        return True

    def stop_recording(self):
        if self.state == "recording":
            self.state = "idle"
            # Drop any event whose frame never got recorded (pressed on the
            # stop tick) so no event points past the end of the buffer.
            self.sound_events = [
                (i, n) for i, n in self.sound_events if i < len(self.frames)
            ]
            self.proj_events = [
                (i, s) for i, s in self.proj_events if i < len(self.frames)
            ]

    # -------------------------------------------------------------- playback
    def has_recording(self):
        return len(self.frames) > 0

    def start_playback(self):
        if not self.frames:
            return False
        self.state = "playing"
        self._cursor = 0
        self._ramp = None
        self._pending_sounds = []
        self._pending_proj = []
        return True

    def request_stop(self, from_commands, gait_offset, sprint):
        """Begin a graceful stop from the current live command (decelerate to a
        stand over stop_ramp_s), instead of cutting to zero. Idempotent: calling
        it again while already ramping does NOT restart the ramp (so holding a
        button can't stall the deceleration). No-op unless currently playing."""
        if self.state != "playing" or self._ramp is not None:
            return
        self._ramp = (
            commands_to_frame(from_commands, gait_offset, sprint),
            self.stop_ramp_frames,
        )

    def next_frame(self):
        """Return the frame to apply this tick as (last_commands[7], gait_offset,
        sprint_bool, finished_bool), or None if nothing to play (idle).

        While a stop-ramp is active, the returned commands scale linearly to
        zero; when it completes, playback goes idle and returns finished=True on
        that tick.
        """
        if self.state != "playing":
            return None

        # Graceful stop ramp takes priority over the recorded stream (and
        # serves no recorded frame, so no sound/projector events either).
        self._pending_sounds = []
        self._pending_proj = []
        if self._ramp is not None:
            base, ticks_left = self._ramp
            ticks_left -= 1
            frac = max(0.0, ticks_left / self.stop_ramp_frames)
            ramped = clamp_frame([c * frac for c in base])
            if ticks_left <= 0:
                self.state = "idle"
                self._ramp = None
                lc, off, spr = frame_to_commands(ramped)
                return lc, off, spr, True
            self._ramp = (base, ticks_left)
            lc, off, spr = frame_to_commands(ramped)
            return lc, off, spr, False

        frame = self.frames[self._cursor]
        self._pending_sounds = [
            n for i, n in self.sound_events if i == self._cursor
        ]
        self._pending_proj = [
            s for i, s in self.proj_events if i == self._cursor
        ]
        self._cursor += 1
        finished = False
        if self._cursor >= len(self.frames):
            if self.loop:
                self._cursor = 0
            else:
                # Non-looping end: hand back with a stop-ramp from the last frame.
                self._ramp = (frame, self.stop_ramp_frames)
        lc, off, spr = frame_to_commands(frame)
        return lc, off, spr, finished

    def pop_sounds(self):
        """Sound names attached to the frame served by the last next_frame()
        call. Consumed on read; empty when idle, recording, or ramping."""
        out = self._pending_sounds
        self._pending_sounds = []
        return out

    def pop_projector(self):
        """Projector states attached to the frame served by the last
        next_frame() call (apply in order; last one wins). Consumed on read;
        empty when idle, recording, or ramping."""
        out = self._pending_proj
        self._pending_proj = []
        return out

    @property
    def duration_s(self):
        return len(self.frames) / self.control_hz if self.control_hz else 0.0

    # ----------------------------------------------------------- persistence
    def save(self, path):
        with open(path, "wb") as f:
            pickle.dump({"control_hz": self.control_hz, "frames": self.frames,
                         "sound_events": self.sound_events,
                         "proj_events": self.proj_events}, f)

    def load(self, path):
        """Load frames from disk, re-clamping every frame (untrusted input).
        Sound events are equally untrusted: anything that isn't a well-formed
        (in-range int, string) pair is dropped. Pre-sound pickles simply have
        no sound_events key."""
        with open(path, "rb") as f:
            data = pickle.load(f)
        raw = data.get("frames", []) if isinstance(data, dict) else list(data)
        self.frames = [clamp_frame(fr) for fr in raw]
        self.sound_events = []
        self.proj_events = []
        if isinstance(data, dict):
            for ev in data.get("sound_events", []):
                i, n = self._checked_event(ev)
                if i is not None and isinstance(n, str):
                    self.sound_events.append((i, n))
            for ev in data.get("proj_events", []):
                i, s = self._checked_event(ev)
                if i is not None and isinstance(s, (bool, int)):
                    self.proj_events.append((i, bool(s)))
        self.state = "idle"
        self._cursor = 0
        self._ramp = None
        self._pending_sounds = []
        self._pending_proj = []
        return len(self.frames)

    def _checked_event(self, ev):
        """Unpack an untrusted (frame_index, value) pair. Returns (index, value)
        with index None when the shape or index is invalid; the caller checks
        the value's type."""
        try:
            i, v = ev
        except (TypeError, ValueError):
            return None, None
        if (isinstance(i, int) and not isinstance(i, bool)
                and 0 <= i < len(self.frames)):
            return i, v
        return None, None
