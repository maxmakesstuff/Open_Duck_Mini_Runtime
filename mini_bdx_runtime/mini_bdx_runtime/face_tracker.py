"""
On-device face tracking for the head puppet.

Two layers:
  * PURE LOGIC (this is everything except FaceCamera): geometry, the proportional
    servo step, presence hysteresis, and the greeting timeline. Imports nothing
    hardware-specific, so it unit-tests on a dev machine.
  * FaceCamera (added later): a thin picamzero + OpenCV-Haar wrapper that runs a
    detection thread and publishes the nearest face. It lazy-imports cv2/picamzero
    so this module still imports cleanly off-robot.

Nothing here moves a motor. head_puppet.py consumes these and applies the SAME
clamp_head_rad + slew_list safety it already uses for playback.
"""
import math
import threading
import time

# ---- capture / detection (tune on-robot if FPS is low) ----
CAP_W = 320
CAP_H = 240
DETECT_FPS = 30           # picamera2 video streams ~30 FPS; more feedback = smoother servo

# EQUALIZE_HIST is the single most important robustness knob across ROBOTS/ROOMS.
# The ov5647 auto-exposes for the whole scene, so a face that is backlit or in
# dim light comes through DARK and low-contrast -- and Haar frontal detection,
# which keys on local contrast, then misses it almost every frame. Measured
# on-robot on a backlit face: RAW grayscale detected 0/140 frames; a global
# cv2.equalizeHist() (stretch the tone curve before Haar) took the SAME frames to
# 140/140, with 0 false positives on an empty scene. Because it normalizes
# contrast, detection stops depending on each camera unit's metering or the room
# lighting -- i.e. it behaves the same on every duck. Nearly free (~1 ms/frame).
EQUALIZE_HIST = True

# Haar tuned (with EQUALIZE_HIST) for reliable acquisition of a normal ~0.5 m face
# that may be small or slightly off-axis: 1.15/4/32 measured 100% vs 60% for the
# old 1.2/5/40, still 0 false positives. Loosen minSize further only if a
# wider-lens camera makes faces smaller than ~32 px.
HAAR_SCALE_FACTOR = 1.15
HAAR_MIN_NEIGHBORS = 4
HAAR_MIN_SIZE = (32, 32)

# Detection-frame rotation (the picam is mounted on its side). "90_CW" is verified
# to detect faces on this robot. If detection, or the up/down vs left/right axes,
# look wrong, try "90_CCW" / "180" / "none". Resolved to a cv2 code inside
# FaceCamera so this module still imports without cv2.
ROTATION = "90_CW"
_ROTATION_NAMES = {
    "none": None,
    "90_CW": "ROTATE_90_CLOCKWISE",
    "90_CCW": "ROTATE_90_COUNTERCLOCKWISE",
    "180": "ROTATE_180",
}

# ---- image-axis -> head-axis mapping ----
# camera.py rotates the frame 90 deg CW, i.e. the picam is mounted sideways. The
# detector applies the same rotation so faces are upright for Haar; these constants
# then map the (upright) image axes to head yaw/pitch. If the head tracks the WRONG
# way on the first run, flip the sign(s) below (or swap "x"/"y"). DEBUG prints the
# face center so the right fix is obvious.
YAW_FROM = "x"
YAW_SIGN = -1.0
PITCH_FROM = "y"
PITCH_SIGN = -1.0

# ---- servo control ----
KP_YAW = 0.04        # rad toward the face per CAMERA FRAME, per unit error (de-wound servo)
KP_PITCH = 0.04
DEADZONE = 0.08      # normalized; no motion while the face is this near center

# ---- greeting timeline ----
GREET_AFTER_S = 1.5
# Presence hysteresis: how long to keep "tracking" after the last detection. The
# servo HOLDS the head on the face's last position during this window (head_puppet
# only re-advances the servo on a fresh camera frame), so a longer timeout rides
# out the normal gaps -- you turning your head (frontal Haar can't see a profile),
# a couple of dropped frames -- instead of releasing the head to the idle
# animation and losing the lock. 0.4 s was ~a handful of frames at this Pi's
# detect rate and dropped tracking constantly; 1.2 s holds through a head-turn and
# still releases promptly once you actually leave. Independent of the camera unit.
LOST_TIMEOUT_S = 1.2
SCAN_S = 5.0
WIGGLE_S = 1.0
WIGGLE_AMP = 0.6
WIGGLE_HZ = 3.0
CUTE_SOUND = "happy2.wav"

DEBUG = True         # print each detection's face center (handy for axis tuning)

# picamzero -> picamera2 -> libcamera. On Raspberry Pi OS, libcamera is a system
# apt package (a compiled binding) under this dir; a venv built WITHOUT
# --system-site-packages can't import it. FaceCamera appends this to sys.path so
# the import resolves (append => the venv's own packages keep priority; only the
# system-only libcamera/picamera2 fall through). Assumes the venv and system share
# a Python minor version (true for this Pi). Set to "" to disable.
SYSTEM_DIST_PACKAGES = "/usr/lib/python3/dist-packages"


# ----------------------------- pure geometry -----------------------------
def largest_face(faces):
    """Return the largest (= nearest) face (x, y, w, h) from an iterable, or None."""
    best = None
    best_area = 0
    for f in faces:
        area = f[2] * f[3]
        if area > best_area:
            best_area = area
            best = (f[0], f[1], f[2], f[3])
    return best


def face_center(face):
    """Center (cx, cy) of an (x, y, w, h) face box."""
    x, y, w, h = face
    return (x + w / 2.0, y + h / 2.0)


def normalized_error(cx, cy, img_w, img_h):
    """Face-center offset from frame center, each axis in [-1, 1] (0 = centered)."""
    ex = (cx - img_w / 2.0) / (img_w / 2.0)
    ey = (cy - img_h / 2.0) / (img_h / 2.0)
    return (ex, ey)


def map_error_to_axes(ex, ey, yaw_from=YAW_FROM, yaw_sign=YAW_SIGN,
                      pitch_from=PITCH_FROM, pitch_sign=PITCH_SIGN):
    """Map normalized image error to (yaw_error, pitch_error), honoring the
    configured axis source + sign (the picam is mounted rotated)."""
    src = {"x": ex, "y": ey}
    return (yaw_sign * src[yaw_from], pitch_sign * src[pitch_from])


def select_head_source(face_present, has_recording):
    """Where the head target comes from this tick."""
    if face_present:
        return "servo"
    return "keyframe" if has_recording else "hold"


def servo_step(prev_yaw, prev_pitch, err_yaw, err_pitch,
               kp_yaw=KP_YAW, kp_pitch=KP_PITCH, deadzone=DEADZONE):
    """Incremental proportional step toward the face (eye-in-hand closed loop:
    the camera is on the head, so nudging toward the face shrinks next tick's
    error). Returns the RAW (yaw, pitch) target; the caller clamps to head limits
    and slew-limits it. No motion while an axis error is within `deadzone`."""
    dyaw = 0.0 if abs(err_yaw) < deadzone else kp_yaw * err_yaw
    dpitch = 0.0 if abs(err_pitch) < deadzone else kp_pitch * err_pitch
    return (prev_yaw + dyaw, prev_pitch + dpitch)


class FacePresence:
    """Turn raw per-frame detections into a stable presence signal with loss
    hysteresis (rides out dropped frames), plus acquire/lose edges and a one-shot
    `greet_ready` pulse once a face has been held `greet_after` seconds."""

    def __init__(self, lost_timeout=LOST_TIMEOUT_S, greet_after=GREET_AFTER_S):
        self.lost_timeout = lost_timeout
        self.greet_after = greet_after
        self.present = False
        self.just_acquired = False
        self.just_lost = False
        self.greet_ready = False
        self.held = 0.0
        self._last_seen = None
        self._acquired_at = None
        self._greeted = False

    def update(self, found, now):
        self.just_acquired = False
        self.just_lost = False
        self.greet_ready = False
        if found:
            self._last_seen = now

        was_present = self.present
        if found:
            self.present = True
        elif self._last_seen is not None and (now - self._last_seen) <= self.lost_timeout:
            self.present = True       # hysteresis: hold through brief dropouts
        else:
            self.present = False

        if self.present and not was_present:
            self._acquired_at = now
            self._greeted = False
            self.just_acquired = True
        elif not self.present and was_present:
            self._acquired_at = None
            self.just_lost = True

        self.held = (now - self._acquired_at) if self._acquired_at is not None else 0.0

        if (self.present and not self._greeted
                and self._acquired_at is not None and self.held >= self.greet_after):
            self._greeted = True
            self.greet_ready = True
        return self


class GreetOutput:
    """What the greeting timeline wants this tick. None = 'don't touch that
    channel' (the caller's keyframe playback owns it instead)."""
    __slots__ = ("antenna", "play_sound", "projector", "scanner")

    def __init__(self):
        self.antenna = None       # ear-wiggle value in [-1, 1], or None
        self.play_sound = None    # one-shot filename, or None
        self.projector = None     # desired LED state, or None
        self.scanner = False      # run the ScannerSound lamp-loop?


class GreetSequence:
    """Non-blocking greeting, advanced one tick per update():

        IDLE --greet_ready--> GREET (cute sound + ear wiggle)
        GREET --WIGGLE_S--> SCAN (projector LED + scanner lamp-loop)
        SCAN --SCAN_S--> GREETED (just keep tracking)
        {GREET,SCAN,GREETED} --face lost--> FAREWELL (one ear wiggle) --WIGGLE_S--> IDLE

    Losing the face from IDLE (i.e. before we ever greeted) does nothing."""

    def __init__(self, cute_sound=CUTE_SOUND, scan_s=SCAN_S, wiggle_s=WIGGLE_S,
                 wiggle_amp=WIGGLE_AMP, wiggle_hz=WIGGLE_HZ):
        self.state = "IDLE"
        self.cute_sound = cute_sound
        self.scan_s = scan_s
        self.wiggle_s = wiggle_s
        self.wiggle_amp = wiggle_amp
        self.wiggle_hz = wiggle_hz
        self._t0 = 0.0

    def _enter(self, state, now):
        self.state = state
        self._t0 = now

    def _wiggle(self, now):
        phase = (now - self._t0) * self.wiggle_hz * 2.0 * math.pi
        return self.wiggle_amp * math.sin(phase)

    def update(self, now, presence):
        out = GreetOutput()

        # Lose the face after greeting -> abort straight to farewell, kill the scan.
        if presence.just_lost and self.state in ("GREET", "SCAN", "GREETED"):
            self._enter("FAREWELL", now)
            out.projector = False
            out.scanner = False
            return out

        if self.state == "IDLE":
            if presence.greet_ready:
                self._enter("GREET", now)
                out.play_sound = self.cute_sound
                out.antenna = self._wiggle(now)
        elif self.state == "GREET":
            out.antenna = self._wiggle(now)
            if now - self._t0 >= self.wiggle_s:
                self._enter("SCAN", now)
                out.projector = True
                out.scanner = True
        elif self.state == "SCAN":
            out.projector = True
            out.scanner = True
            if now - self._t0 >= self.scan_s:
                self._enter("GREETED", now)
                out.projector = False
                out.scanner = False
        elif self.state == "GREETED":
            pass
        elif self.state == "FAREWELL":
            out.antenna = self._wiggle(now)
            out.projector = False
            out.scanner = False        # explicit: the scan lamp-loop stays off during farewell
            if now - self._t0 >= self.wiggle_s:
                self._enter("IDLE", now)
        return out


# ---- communicative "chatter" while steadily tracking a face ----
# Curated set of short, emotive sounds that read as droid speech (NOT the lamp
# scanner loop). head_puppet intersects this with the sounds actually present.
CHATTER_SOUNDS = ("happy1.wav", "happy2.wav", "happy3.wav", "beep1.wav", "beep2.wav")
CHATTER_SOUND_EVERY = (4.0, 9.0)     # seconds between random sounds (uniform)
CHATTER_WIGGLE_EVERY = (6.0, 12.0)   # seconds between ear wiggles
CHATTER_WIGGLE_S = 0.8
CHATTER_WIGGLE_AMP = 0.5
CHATTER_WIGGLE_HZ = 2.5


class ChatterOutput:
    """What the chatter wants this tick. None = leave that channel to the caller."""
    __slots__ = ("play_sound", "antenna")

    def __init__(self):
        self.play_sound = None    # one-shot filename, or None
        self.antenna = None       # ear-wiggle value in [-1, 1], or None


class TrackingChatter:
    """Makes being tracked feel alive: once we're steadily following a face
    (past the greeting), fire a random sound every few seconds and wiggle the
    ears now and then, on independent jittered schedules.

    Pure + deterministic (inject an rng) so it unit-tests off-robot. It never
    fights the greeting: the caller only drives it during the GREETED phase.
    Going inactive (face lost / tracking stopped) reschedules both timers so it
    doesn't fire the instant a face is re-acquired."""

    def __init__(self, sound_pool=CHATTER_SOUNDS, rng=None,
                 sound_every=CHATTER_SOUND_EVERY, wiggle_every=CHATTER_WIGGLE_EVERY,
                 wiggle_s=CHATTER_WIGGLE_S, wiggle_amp=CHATTER_WIGGLE_AMP,
                 wiggle_hz=CHATTER_WIGGLE_HZ):
        import random as _random
        self.sound_pool = list(sound_pool)
        self.rng = rng if rng is not None else _random.Random()
        self.sound_every = sound_every
        self.wiggle_every = wiggle_every
        self.wiggle_s = wiggle_s
        self.wiggle_amp = wiggle_amp
        self.wiggle_hz = wiggle_hz

        self._active = False
        self._next_sound = None
        self._next_wiggle = None
        self._wiggle_until = 0.0
        self._wiggle_t0 = 0.0

    def _reschedule(self, now):
        self._next_sound = now + self.rng.uniform(*self.sound_every)
        self._next_wiggle = now + self.rng.uniform(*self.wiggle_every)

    def reset(self, now):
        self._active = False
        self._wiggle_until = 0.0
        self._reschedule(now)

    def update(self, now, active):
        """`active` = we are steadily tracking a face this tick. Returns a
        ChatterOutput (play_sound / antenna, either possibly None)."""
        out = ChatterOutput()
        if not active:
            # Re-arm timers from 'now' so re-acquiring a face doesn't instantly fire.
            if self._active or self._next_sound is None:
                self._reschedule(now)
            self._active = False
            self._wiggle_until = 0.0
            return out

        if not self._active:
            self._active = True
            if self._next_sound is None:
                self._reschedule(now)

        # sound
        if self._next_sound is not None and now >= self._next_sound:
            if self.sound_pool:
                out.play_sound = self.rng.choice(self.sound_pool)
            self._next_sound = now + self.rng.uniform(*self.sound_every)

        # start a wiggle window
        if self._next_wiggle is not None and now >= self._next_wiggle:
            self._wiggle_t0 = now
            self._wiggle_until = now + self.wiggle_s
            self._next_wiggle = now + self.rng.uniform(*self.wiggle_every)

        # emit the wiggle value while inside its window
        if now < self._wiggle_until:
            phase = (now - self._wiggle_t0) * self.wiggle_hz * 2.0 * math.pi
            out.antenna = self.wiggle_amp * math.sin(phase)

        return out


class FaceCamera:
    """Captures low-res frames from the picam and runs the bundled OpenCV Haar
    face detector on a daemon thread (~DETECT_FPS), publishing only the nearest
    face. Decoupled from the 60 Hz control loop: head_puppet just calls latest().

    Lazy-imports cv2/picamera2 so the rest of this module imports off-robot.
    Raises from __init__ on any failure -> head_puppet disables the feature."""

    def __init__(self, cap_w=CAP_W, cap_h=CAP_H, detect_fps=DETECT_FPS):
        # libcamera/picamera2 are system apt packages (compiled bindings) the venv
        # can't import on its own; make them importable (see SYSTEM_DIST_PACKAGES).
        import sys
        if SYSTEM_DIST_PACKAGES and SYSTEM_DIST_PACKAGES not in sys.path:
            sys.path.append(SYSTEM_DIST_PACKAGES)
        import cv2
        from picamera2 import Picamera2

        self._cv2 = cv2
        _rot = _ROTATION_NAMES[ROTATION]
        self._rotate_code = None if _rot is None else getattr(cv2, _rot)

        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self._cascade = cv2.CascadeClassifier(cascade_path)
        if self._cascade.empty():
            raise RuntimeError(f"failed to load Haar cascade at {cascade_path}")

        # A low-res VIDEO stream runs ~30 FPS; a still capture (picamzero) is ~2 FPS
        # -- far too slow to servo a head in a closed loop. The stream is already
        # cap_w x cap_h, so no resize is needed before rotate/detect.
        self._cam = Picamera2()
        cfg = self._cam.create_video_configuration(
            main={"size": (cap_w, cap_h), "format": "RGB888"}
        )
        self._cam.configure(cfg)
        self._cam.start()

        self._cap_w = cap_w
        self._cap_h = cap_h
        self._period = 1.0 / float(detect_fps)

        # published frame size = the rotated detection frame (a 90deg rotate swaps w/h)
        if self._rotate_code in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):
            init_w, init_h = cap_h, cap_w
        else:
            init_w, init_h = cap_w, cap_h
        self._lock = threading.Lock()
        self._latest = (False, 0.0, 0.0, init_w, init_h, 0.0)
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self):
        cv2 = self._cv2
        while self._running:
            t = time.time()
            try:
                frame = self._cam.capture_array()          # already cap_w x cap_h
                if self._rotate_code is not None:
                    frame = cv2.rotate(frame, self._rotate_code)
                gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
                # Normalize contrast so a dark/backlit face is still detectable and
                # detection doesn't depend on the camera's exposure (see EQUALIZE_HIST).
                if EQUALIZE_HIST:
                    gray = cv2.equalizeHist(gray)
                h, w = gray.shape[:2]
                faces = self._cascade.detectMultiScale(
                    gray,
                    scaleFactor=HAAR_SCALE_FACTOR,
                    minNeighbors=HAAR_MIN_NEIGHBORS,
                    minSize=HAAR_MIN_SIZE,
                )
                face = largest_face([tuple(f) for f in faces])
                if face is not None:
                    cx, cy = face_center(face)
                    if DEBUG:
                        print(f"[face] center=({cx:.0f},{cy:.0f}) frame=({w}x{h})")
                    with self._lock:
                        self._latest = (True, cx, cy, w, h, t)
                else:
                    with self._lock:
                        self._latest = (False, 0.0, 0.0, w, h, t)
            except Exception as e:  # noqa: BLE001 - never let the thread die
                if DEBUG:
                    print(f"[face] detect error: {type(e).__name__}: {e}")
            dt = self._period - (time.time() - t)
            if dt > 0:
                time.sleep(dt)

    def latest(self):
        with self._lock:
            return self._latest

    def stop(self):
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        try:
            self._cam.stop()
            self._cam.close()
        except Exception:
            pass
