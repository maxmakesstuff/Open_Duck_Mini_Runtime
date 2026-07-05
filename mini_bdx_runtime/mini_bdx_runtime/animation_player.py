"""
Idle-animation playback for the Open Duck Mini.

Pure-Python, hardware-free (stdlib only) so it can be imported and unit-tested
off-robot. It owns *timeline logic only*: given a wall-clock time it returns the
head/antenna targets and any sound/lamp events that should fire this tick. The
caller (the walk script) applies these to the hardware, gated by the robot's
expression-feature flags.

Safety: head joint limits and the slew-rate helper live here (in code, not in
the editable JSON). Animation files are treated as untrusted input - any head
keyframe outside HEAD_LIMITS or antenna value outside [-1, 1] is clamped at load
time and recorded in `self.warnings`.
"""
import json

# Head joints in motor_targets[5:9] order.
HEAD_JOINT_ORDER = ["neck_pitch", "head_pitch", "head_yaw", "head_roll"]

# Hard, safe position limits (radians) matching the controller's teleop ranges.
HEAD_LIMITS = {
    "neck_pitch": (-0.34, 1.1),
    "head_pitch": (-0.78, 0.3),
    "head_yaw": (-0.5, 0.5),
    "head_roll": (-0.5, 0.5),
}

# Conservative head speed cap; below the leg max_motor_velocity of 5.24 rad/s.
DEFAULT_MAX_HEAD_VELOCITY = 4.0  # rad/s

ANTENNA_MIN = -1.0
ANTENNA_MAX = 1.0


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def clamp_head_targets(targets):
    """Clamp a 4-list of head targets (HEAD_JOINT_ORDER) to HEAD_LIMITS."""
    out = []
    for i, joint in enumerate(HEAD_JOINT_ORDER):
        lo, hi = HEAD_LIMITS[joint]
        out.append(clamp(targets[i], lo, hi))
    return out


def slew_limit(prev, target, max_delta):
    """Limit each element of `target` to within +/- max_delta of `prev`."""
    out = []
    for p, t in zip(prev, target):
        out.append(p + clamp(t - p, -max_delta, max_delta))
    return out


def _lerp(a, b, frac):
    return a + (b - a) * frac


def _interp(track, t):
    """Linear-interpolate a sorted [(t, [values...]), ...] track at time t.

    Holds the endpoint value before the first / after the last keyframe.
    Returns a fresh list, or None if the track is empty.
    """
    if not track:
        return None
    if t <= track[0][0]:
        return list(track[0][1])
    if t >= track[-1][0]:
        return list(track[-1][1])
    for i in range(len(track) - 1):
        t0, v0 = track[i]
        t1, v1 = track[i + 1]
        if t0 <= t <= t1:
            if t1 == t0:
                return list(v0)
            frac = (t - t0) / (t1 - t0)
            return [_lerp(a, b, frac) for a, b in zip(v0, v1)]
    return list(track[-1][1])  # unreachable, defensive


class AnimationPlayer:
    def __init__(self, animations_path, known_sounds=None):
        with open(animations_path) as f:
            data = json.load(f)
        self._build(data, known_sounds)

    @classmethod
    def from_dict(cls, data, known_sounds=None):
        obj = cls.__new__(cls)
        obj._build(data, known_sounds)
        return obj

    # ------------------------------------------------------------------ build
    def _build(self, data, known_sounds):
        self.warnings = []
        self.known_sounds = known_sounds
        self._anims = {}

        for name, spec in data.get("animations", {}).items():
            head = self._parse_head(name, spec.get("head", []))
            antennas = self._parse_antennas(name, spec.get("antennas", []))
            sounds = self._parse_sounds(name, spec.get("sounds", []))
            lamp = self._parse_lamp(spec.get("lamp", []))
            duration = 0.0
            for track in (head, antennas, sounds, lamp):
                if track:
                    duration = max(duration, track[-1][0])
            self._anims[name] = {
                "loop": bool(spec.get("loop", False)),
                "head": head,
                "antennas": antennas,
                "sounds": sounds,
                "lamp": lamp,
                "duration": duration,
            }

        self.dpad_map = dict(data.get("dpad", {}))
        for direction, target in self.dpad_map.items():
            if target not in self._anims:
                self.warnings.append(
                    f"dpad '{direction}' maps to unknown animation '{target}'"
                )

        # playback state
        self.current = None
        self.start_time = 0.0
        self._fired_sounds = set()
        self._lamp_state = None
        self._cycle = 0

    def _parse_head(self, name, raw):
        track = []
        for kf in raw:
            t = float(kf["t"])
            vals = []
            for joint in HEAD_JOINT_ORDER:
                v = float(kf.get(joint, 0.0))
                lo, hi = HEAD_LIMITS[joint]
                c = clamp(v, lo, hi)
                if c != v:
                    self.warnings.append(
                        f"[{name}] {joint}={v} at t={t} out of [{lo}, {hi}]; "
                        f"clamped to {c}"
                    )
                vals.append(c)
            track.append((t, vals))
        track.sort(key=lambda kv: kv[0])
        return track

    def _parse_antennas(self, name, raw):
        track = []
        for kf in raw:
            t = float(kf["t"])
            vals = []
            for side in ("left", "right"):
                v = float(kf.get(side, 0.0))
                c = clamp(v, ANTENNA_MIN, ANTENNA_MAX)
                if c != v:
                    self.warnings.append(
                        f"[{name}] antenna {side}={v} at t={t} out of "
                        f"[{ANTENNA_MIN}, {ANTENNA_MAX}]; clamped to {c}"
                    )
                vals.append(c)
            track.append((t, vals))
        track.sort(key=lambda kv: kv[0])
        return track

    def _parse_sounds(self, name, raw):
        track = []
        for ev in raw:
            sound = ev["name"]
            if self.known_sounds is not None and sound not in self.known_sounds:
                self.warnings.append(
                    f"[{name}] references unknown sound '{sound}'"
                )
            track.append((float(ev["t"]), sound))
        track.sort(key=lambda kv: kv[0])
        return track

    def _parse_lamp(self, raw):
        track = [(float(ev["t"]), bool(ev["state"])) for ev in raw]
        track.sort(key=lambda kv: kv[0])
        return track

    # ----------------------------------------------------------------- query
    def has(self, name):
        return name in self._anims

    def animation_names(self):
        return list(self._anims.keys())

    def duration(self, name):
        return self._anims[name]["duration"]

    # -------------------------------------------------------------- playback
    def start(self, name, now):
        if name not in self._anims:
            raise ValueError(f"unknown animation '{name}'")
        self.current = name
        self.start_time = now
        self._fired_sounds = set()
        self._lamp_state = None
        self._cycle = 0

    def update(self, now):
        if self.current is None:
            return {"head": None, "antennas": None, "sounds": [],
                    "lamp": None, "finished": True}

        anim = self._anims[self.current]
        dur = anim["duration"]
        elapsed = max(0.0, now - self.start_time)

        if anim["loop"] and dur > 0:
            cycle = int(elapsed // dur)
            if cycle != self._cycle:
                self._fired_sounds = set()
                self._lamp_state = None
                self._cycle = cycle
            t_eval = elapsed - cycle * dur
            event_t = t_eval
            finished = False
        else:
            t_eval = min(elapsed, dur) if dur > 0 else 0.0
            event_t = elapsed
            finished = elapsed >= dur

        head = _interp(anim["head"], t_eval)
        antennas = _interp(anim["antennas"], t_eval)

        sounds = []
        for idx, (t, sound) in enumerate(anim["sounds"]):
            if t <= event_t and idx not in self._fired_sounds:
                self._fired_sounds.add(idx)
                sounds.append(sound)

        lamp = None
        desired = None
        for t, state in anim["lamp"]:
            if t <= event_t:
                desired = state
            else:
                break
        if desired is not None and desired != self._lamp_state:
            self._lamp_state = desired
            lamp = desired

        return {"head": head, "antennas": antennas, "sounds": sounds,
                "lamp": lamp, "finished": finished}


if __name__ == "__main__":
    # Off-robot self-test: load the shipped animations and sample each timeline,
    # asserting head targets stay within HEAD_LIMITS and the slew rate.
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.normpath(os.path.join(here, "..", "..", "scripts", "animations.json"))
    known = {
        "beep1.wav", "beep2.wav", "happy1.wav", "happy2.wav", "happy3.wav",
        "motor.wav",
    }  # lamp*.wav live in assets/scanner/ (scanner loop), not the rotation
    player = AnimationPlayer(path, known_sounds=known)
    if player.warnings:
        print("WARNINGS:")
        for w in player.warnings:
            print("  -", w)
    else:
        print("No load warnings.")

    control_freq = 50.0
    dt = 1.0 / control_freq
    max_delta = DEFAULT_MAX_HEAD_VELOCITY / control_freq
    print(f"dpad map: {player.dpad_map}")
    for name in player.animation_names():
        player.start(name, 0.0)
        dur = player.duration(name)
        prev = None
        sounds, lamps, t = [], [], 0.0
        worst_step = 0.0
        while t <= dur + 0.5:
            r = player.update(t)
            sounds += r["sounds"]
            if r["lamp"] is not None:
                lamps.append((round(t, 2), r["lamp"]))
            if r["head"] is not None and prev is not None:
                worst_step = max(worst_step, max(abs(a - b) for a, b in zip(r["head"], prev)))
            prev = r["head"]
            t += dt
        print(f"\n{name}: dur={dur:.2f}s  sounds={sounds}  lamp={lamps}  "
              f"worst_head_step={worst_step:.4f} (limit {max_delta:.4f})")
