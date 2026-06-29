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
DETECT_FPS = 10
HAAR_SCALE_FACTOR = 1.2
HAAR_MIN_NEIGHBORS = 5
HAAR_MIN_SIZE = (40, 40)

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
KP_YAW = 0.06        # rad nudged toward the face per tick, per unit normalized error
KP_PITCH = 0.05
DEADZONE = 0.08      # normalized; no motion while the face is this near center

# ---- greeting timeline ----
GREET_AFTER_S = 1.5
LOST_TIMEOUT_S = 0.4
SCAN_S = 5.0
WIGGLE_S = 1.0
WIGGLE_AMP = 0.6
WIGGLE_HZ = 3.0
CUTE_SOUND = "happy2.wav"

DEBUG = True         # print each detection's face center (handy for axis tuning)


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
