# Head-Puppet Face-Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a DPAD-UP face-tracking mode to head puppet that turns the head to the nearest face, greets after 1.5 s (ear wiggle + cute sound + 5 s scanner scan), waves goodbye on loss, and falls back to the recorded idle when no face is visible — all on the Pi Zero 2W with the installed picam.

**Architecture:** All testable logic lives in a new pure-import module `mini_bdx_runtime/face_tracker.py` (geometry, proportional servo step, presence hysteresis, greeting timeline) plus a thin `FaceCamera` hardware wrapper (lazy `cv2`/`picamzero`, detection thread). `scripts/head_puppet.py` gains a fourth `TRACKING` state that wires these together, reusing the existing `clamp_head_rad` + `slew_list` safety and the existing `ScannerSound` module for the scan. The walk is untouched.

**Tech Stack:** Python 3, OpenCV (`cv2`, already installed) bundled Haar cascade, `picamzero` (already installed), `numpy`/`math`, the repo's existing `assert`+`__main__` test runner (no pytest).

## Global Constraints

- **The walk is NOT touched. Legs never move.** Only `head_yaw`, `head_roll` (held 0), `head_pitch` are driven; `neck_pitch` stays 0.
- **No new pip dependencies.** Face detection uses the Haar cascade bundled in the already-installed `cv2`. No ONNX model files.
- **`face_tracker.py` must import clean off-robot** — NO top-level `import cv2` / `import picamzero`; those are lazy-imported inside `FaceCamera` only. Pure logic uses `math`/`threading`/`time` only.
- **Every head target goes through `clamp_head_rad(...)` then `slew_list(prev_head, target, MAX_HEAD_DELTA)`** where `MAX_HEAD_DELTA = MAX_HEAD_VELOCITY/CONTROL_HZ = 5.0/60`. This is the motor-safety guarantee — do not bypass it.
- **Non-blocking:** detection runs on its own daemon thread; greeting/scan are per-tick timed state machines. No `sleep` chain may stall the 60 Hz loop (the single per-tick `time.sleep(DT)` already in the loop is the only sleep).
- **Graceful degrade:** missing camera, failed `cv2`/`picamzero` import, or `duck_config.camera == False` → feature disabled, puppet runs normally, never crashes. Per-flag: no `speaker` → no greet sound/scan loop; no `antennas` → no wiggle; no `projector` → no scan LED.
- **Tests:** plain `assert` + a `_run()` that iterates `test_*` globals and `sys.exit(1 if _run() else 0)`. Run with `python3 <file>.py` from the file's own directory. No pytest.
- **Git:** do all work on a branch `feature/head-puppet-face-tracking` cut from `v2`. **Do NOT commit to `v2`.**
- **Reused symbols already in `scripts/head_puppet.py`:** `clamp(v, lo, hi)`, `slew_list(prev, target, max_delta)`, `clamp_head_rad((yaw, roll, pitch))`, `any_active_input(last_commands, buttons, lt, rt)`, `MAX_HEAD_DELTA`, `DT`, `CONTROL_HZ`, the `recording` list of frame dicts `{"head":(yaw,roll,pitch), "ant":(l,r), "sound":name|None, "proj":bool|None}`, `playback_idx`, `prev_head`.
- **Reused module:** `mini_bdx_runtime/scanner_sound.py` → `ScannerSound(backend)` with `.start(now)`, `.update(now)`, `.stop()`, `.is_active`; `PygameScannerBackend(sound_dir)`. Scanner assets dir from `scripts/` is `../mini_bdx_runtime/assets/scanner/`.

---

### Task 0: Branch

- [ ] **Step 1: Create the feature branch off v2**

```bash
cd /Users/maximilianschmierer/Git_Projects/OpenDuckIHK/Open_Duck_Mini_Runtime
git checkout v2
git checkout -b feature/head-puppet-face-tracking
git status
```
Expected: `On branch feature/head-puppet-face-tracking`, working tree clean apart from the already-written spec/plan docs.

---

### Task 1: `face_tracker.py` — geometry + head-source selection (pure)

**Files:**
- Create: `mini_bdx_runtime/mini_bdx_runtime/face_tracker.py`
- Test: `mini_bdx_runtime/mini_bdx_runtime/test_face_tracker.py`

**Interfaces:**
- Produces:
  - `largest_face(faces) -> (x,y,w,h) | None`
  - `face_center(face) -> (cx, cy)`
  - `normalized_error(cx, cy, img_w, img_h) -> (ex, ey)` each in `[-1,1]`
  - `map_error_to_axes(ex, ey, yaw_from=YAW_FROM, yaw_sign=YAW_SIGN, pitch_from=PITCH_FROM, pitch_sign=PITCH_SIGN) -> (err_yaw, err_pitch)`
  - `select_head_source(face_present, has_recording) -> "servo"|"keyframe"|"hold"`
  - module constants block (used by all later tasks and by `head_puppet.py`)

- [ ] **Step 1: Write the failing tests**

Create `mini_bdx_runtime/mini_bdx_runtime/test_face_tracker.py`:

```python
"""
Off-robot tests for face_tracker.py's pure logic.

face_tracker imports NO hardware at module top (cv2/picamzero are lazy-imported
inside FaceCamera), so this imports cleanly on a dev machine.

Run from this directory:  python3 test_face_tracker.py
"""
import os
import sys
import math
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import face_tracker as ft  # noqa: E402

EPS = 1e-9


# ----------------------------------------------------------- geometry
def test_largest_face_picks_max_area():
    faces = [(0, 0, 10, 10), (5, 5, 40, 30), (1, 1, 20, 20)]
    assert ft.largest_face(faces) == (5, 5, 40, 30)


def test_largest_face_empty_is_none():
    assert ft.largest_face([]) is None


def test_face_center():
    assert ft.face_center((10, 20, 40, 60)) == (30.0, 50.0)


def test_normalized_error_center_is_zero():
    ex, ey = ft.normalized_error(160, 120, 320, 240)
    assert abs(ex) < EPS and abs(ey) < EPS


def test_normalized_error_edges_and_sign():
    ex, ey = ft.normalized_error(320, 0, 320, 240)
    assert abs(ex - 1.0) < EPS      # right edge -> +1
    assert abs(ey + 1.0) < EPS      # top edge -> -1


def test_map_error_to_axes_default_signs():
    yaw, pitch = ft.map_error_to_axes(0.5, -0.4)   # defaults: x*-1, y*-1
    assert abs(yaw - (-0.5)) < EPS
    assert abs(pitch - 0.4) < EPS


def test_map_error_to_axes_swap_and_sign():
    yaw, pitch = ft.map_error_to_axes(0.2, 0.7, yaw_from="y", yaw_sign=1.0,
                                      pitch_from="x", pitch_sign=-1.0)
    assert abs(yaw - 0.7) < EPS
    assert abs(pitch - (-0.2)) < EPS


def test_select_head_source():
    assert ft.select_head_source(True, True) == "servo"
    assert ft.select_head_source(True, False) == "servo"
    assert ft.select_head_source(False, True) == "keyframe"
    assert ft.select_head_source(False, False) == "hold"


def _run():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd mini_bdx_runtime/mini_bdx_runtime && python3 test_face_tracker.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'face_tracker'`.

- [ ] **Step 3: Create the module with the minimal implementation**

Create `mini_bdx_runtime/mini_bdx_runtime/face_tracker.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd mini_bdx_runtime/mini_bdx_runtime && python3 test_face_tracker.py`
Expected: PASS for all 8 tests, `8 passed, 0 failed`.

- [ ] **Step 5: Commit**

```bash
git add mini_bdx_runtime/mini_bdx_runtime/face_tracker.py mini_bdx_runtime/mini_bdx_runtime/test_face_tracker.py
git commit -m "feat(face-tracker): pure geometry + head-source selection"
```

---

### Task 2: Proportional servo step

**Files:**
- Modify: `mini_bdx_runtime/mini_bdx_runtime/face_tracker.py` (append after `select_head_source`)
- Test: `mini_bdx_runtime/mini_bdx_runtime/test_face_tracker.py` (add tests above `_run`)

**Interfaces:**
- Consumes: module constants `KP_YAW`, `KP_PITCH`, `DEADZONE`.
- Produces: `servo_step(prev_yaw, prev_pitch, err_yaw, err_pitch, kp_yaw=KP_YAW, kp_pitch=KP_PITCH, deadzone=DEADZONE) -> (yaw, pitch)` — RAW target, caller still clamps + slews.

- [ ] **Step 1: Write the failing tests** — insert these functions immediately above `def _run():` in `test_face_tracker.py`:

```python
# ----------------------------------------------------------- servo step
def test_servo_step_deadzone_no_motion():
    out = ft.servo_step(0.1, -0.2, 0.05, -0.03, kp_yaw=1.0, kp_pitch=1.0, deadzone=0.08)
    assert out == (0.1, -0.2)


def test_servo_step_moves_outside_deadzone():
    out = ft.servo_step(0.0, 0.0, 0.5, -0.5, kp_yaw=0.1, kp_pitch=0.1, deadzone=0.08)
    assert abs(out[0] - 0.05) < EPS and abs(out[1] + 0.05) < EPS


def test_servo_step_mixed_axes():
    # yaw inside deadzone (hold), pitch outside (moves)
    out = ft.servo_step(1.0, 2.0, 0.02, 0.5, kp_yaw=0.1, kp_pitch=0.2, deadzone=0.08)
    assert abs(out[0] - 1.0) < EPS
    assert abs(out[1] - 2.1) < EPS
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `cd mini_bdx_runtime/mini_bdx_runtime && python3 test_face_tracker.py`
Expected: the three `test_servo_step_*` ERROR with `AttributeError: module 'face_tracker' has no attribute 'servo_step'`; the Task-1 tests still PASS.

- [ ] **Step 3: Implement** — append to `face_tracker.py` after `select_head_source`:

```python
def servo_step(prev_yaw, prev_pitch, err_yaw, err_pitch,
               kp_yaw=KP_YAW, kp_pitch=KP_PITCH, deadzone=DEADZONE):
    """Incremental proportional step toward the face (eye-in-hand closed loop:
    the camera is on the head, so nudging toward the face shrinks next tick's
    error). Returns the RAW (yaw, pitch) target; the caller clamps to head limits
    and slew-limits it. No motion while an axis error is within `deadzone`."""
    dyaw = 0.0 if abs(err_yaw) < deadzone else kp_yaw * err_yaw
    dpitch = 0.0 if abs(err_pitch) < deadzone else kp_pitch * err_pitch
    return (prev_yaw + dyaw, prev_pitch + dpitch)
```

- [ ] **Step 4: Run to verify all tests pass**

Run: `cd mini_bdx_runtime/mini_bdx_runtime && python3 test_face_tracker.py`
Expected: `11 passed, 0 failed`.

- [ ] **Step 5: Commit**

```bash
git add mini_bdx_runtime/mini_bdx_runtime/face_tracker.py mini_bdx_runtime/mini_bdx_runtime/test_face_tracker.py
git commit -m "feat(face-tracker): proportional servo step with deadzone"
```

---

### Task 3: `FacePresence` — hysteresis + greet latch

**Files:**
- Modify: `mini_bdx_runtime/mini_bdx_runtime/face_tracker.py` (append after `servo_step`)
- Test: `mini_bdx_runtime/mini_bdx_runtime/test_face_tracker.py` (add tests above `_run`)

**Interfaces:**
- Consumes: `LOST_TIMEOUT_S`, `GREET_AFTER_S`.
- Produces: `class FacePresence(lost_timeout=LOST_TIMEOUT_S, greet_after=GREET_AFTER_S)` with method `update(found: bool, now: float) -> self` and attributes `present`, `just_acquired`, `just_lost`, `greet_ready` (one-shot), `held` (seconds present).

- [ ] **Step 1: Write the failing tests** — insert above `def _run():`:

```python
# ----------------------------------------------------------- presence
def test_presence_acquire_and_greet_once():
    p = ft.FacePresence(lost_timeout=0.4, greet_after=1.5)
    p.update(True, 0.0)
    assert p.present and p.just_acquired and not p.greet_ready
    p.update(True, 1.0)
    assert p.present and not p.greet_ready
    p.update(True, 1.5)
    assert p.greet_ready            # fires exactly once at 1.5 s held
    p.update(True, 2.0)
    assert not p.greet_ready


def test_presence_hysteresis_rides_dropouts():
    p = ft.FacePresence(lost_timeout=0.4, greet_after=1.5)
    p.update(True, 0.0)
    p.update(False, 0.2)            # dropout within timeout -> still present
    assert p.present and not p.just_lost
    p.update(False, 0.5)            # 0.5 s since last detection -> lost
    assert not p.present and p.just_lost


def test_presence_regreets_after_reacquire():
    p = ft.FacePresence(lost_timeout=0.4, greet_after=1.5)
    p.update(True, 0.0)
    p.update(True, 1.5)
    assert p.greet_ready
    p.update(False, 2.0)            # lost (last seen 1.5)
    assert not p.present and p.just_lost
    p.update(True, 3.0)             # reacquire
    assert p.just_acquired
    p.update(True, 4.4)
    assert not p.greet_ready        # held 1.4 < 1.5
    p.update(True, 4.5)
    assert p.greet_ready            # held 1.5 -> regreets
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `cd mini_bdx_runtime/mini_bdx_runtime && python3 test_face_tracker.py`
Expected: `test_presence_*` ERROR with `AttributeError: ... 'FacePresence'`; earlier tests PASS.

- [ ] **Step 3: Implement** — append to `face_tracker.py`:

```python
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
```

- [ ] **Step 4: Run to verify all tests pass**

Run: `cd mini_bdx_runtime/mini_bdx_runtime && python3 test_face_tracker.py`
Expected: `14 passed, 0 failed`.

- [ ] **Step 5: Commit**

```bash
git add mini_bdx_runtime/mini_bdx_runtime/face_tracker.py mini_bdx_runtime/mini_bdx_runtime/test_face_tracker.py
git commit -m "feat(face-tracker): FacePresence hysteresis + one-shot greet latch"
```

---

### Task 4: `GreetSequence` — non-blocking greeting/scan/farewell timeline

**Files:**
- Modify: `mini_bdx_runtime/mini_bdx_runtime/face_tracker.py` (append after `FacePresence`)
- Test: `mini_bdx_runtime/mini_bdx_runtime/test_face_tracker.py` (add tests above `_run`)

**Interfaces:**
- Consumes: `CUTE_SOUND`, `SCAN_S`, `WIGGLE_S`, `WIGGLE_AMP`, `WIGGLE_HZ`; a `presence`-like object exposing `greet_ready` and `just_lost`.
- Produces:
  - `class GreetOutput` with fields `antenna: float|None`, `play_sound: str|None`, `projector: bool|None`, `scanner: bool`.
  - `class GreetSequence(cute_sound=CUTE_SOUND, scan_s=SCAN_S, wiggle_s=WIGGLE_S, wiggle_amp=WIGGLE_AMP, wiggle_hz=WIGGLE_HZ)` with `update(now, presence) -> GreetOutput` and attribute `state ∈ {"IDLE","GREET","SCAN","GREETED","FAREWELL"}`.

- [ ] **Step 1: Write the failing tests** — insert above `def _run():`:

```python
# ----------------------------------------------------------- greet timeline
def _pres(present=False, greet_ready=False, just_lost=False,
          just_acquired=False, held=0.0):
    return SimpleNamespace(present=present, greet_ready=greet_ready,
                           just_lost=just_lost, just_acquired=just_acquired, held=held)


def test_greet_starts_on_greet_ready():
    g = ft.GreetSequence(cute_sound="happy2.wav", wiggle_s=1.0, scan_s=5.0)
    out = g.update(0.0, _pres(present=True, greet_ready=True))
    assert g.state == "GREET"
    assert out.play_sound == "happy2.wav"
    assert out.antenna is not None


def test_greet_to_scan_after_wiggle():
    g = ft.GreetSequence(wiggle_s=1.0, scan_s=5.0)
    g.update(0.0, _pres(present=True, greet_ready=True))
    out = g.update(1.0, _pres(present=True))
    assert g.state == "SCAN"
    assert out.projector is True and out.scanner is True


def test_scan_runs_then_ends():
    g = ft.GreetSequence(wiggle_s=1.0, scan_s=5.0)
    g.update(0.0, _pres(present=True, greet_ready=True))
    g.update(1.0, _pres(present=True))           # enter SCAN at t=1.0
    mid = g.update(3.0, _pres(present=True))
    assert g.state == "SCAN" and mid.projector is True and mid.scanner is True
    end = g.update(6.0, _pres(present=True))     # 5 s after scan start
    assert g.state == "GREETED"
    assert end.projector is False and end.scanner is False


def test_farewell_on_loss_after_greeting():
    g = ft.GreetSequence(wiggle_s=1.0, scan_s=5.0)
    g.update(0.0, _pres(present=True, greet_ready=True))
    g.update(1.0, _pres(present=True))
    g.update(6.0, _pres(present=True))           # GREETED
    out = g.update(7.0, _pres(just_lost=True))   # lost -> abort to FAREWELL
    assert g.state == "FAREWELL"
    assert out.projector is False and out.scanner is False
    out2 = g.update(7.5, _pres())
    assert g.state == "FAREWELL" and out2.antenna is not None
    g.update(8.0, _pres())                       # wiggle_s after farewell start
    assert g.state == "IDLE"


def test_no_farewell_if_never_greeted():
    g = ft.GreetSequence(wiggle_s=1.0, scan_s=5.0)
    out = g.update(0.0, _pres(just_lost=True))
    assert g.state == "IDLE"
    assert out.antenna is None and out.projector is None and out.scanner is False
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `cd mini_bdx_runtime/mini_bdx_runtime && python3 test_face_tracker.py`
Expected: `test_greet_*` / `test_scan_*` / `test_farewell_*` / `test_no_farewell_*` ERROR with `AttributeError: ... 'GreetSequence'`; earlier tests PASS.

- [ ] **Step 3: Implement** — append to `face_tracker.py`:

```python
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
            if now - self._t0 >= self.wiggle_s:
                self._enter("IDLE", now)
        return out
```

- [ ] **Step 4: Run to verify all tests pass**

Run: `cd mini_bdx_runtime/mini_bdx_runtime && python3 test_face_tracker.py`
Expected: `19 passed, 0 failed`.

- [ ] **Step 5: Commit**

```bash
git add mini_bdx_runtime/mini_bdx_runtime/face_tracker.py mini_bdx_runtime/mini_bdx_runtime/test_face_tracker.py
git commit -m "feat(face-tracker): non-blocking greet/scan/farewell timeline"
```

---

### Task 5: `FaceCamera` — picamzero + Haar detection thread

**Files:**
- Modify: `mini_bdx_runtime/mini_bdx_runtime/face_tracker.py` (append after `GreetSequence`)

**Interfaces:**
- Consumes: `largest_face`, `face_center`, the `CAP_*` / `HAAR_*` / `DETECT_FPS` / `DEBUG` constants.
- Produces: `class FaceCamera(cap_w=CAP_W, cap_h=CAP_H, detect_fps=DETECT_FPS)` with:
  - `latest() -> (found: bool, cx: float, cy: float, img_w: int, img_h: int, t: float)` — non-blocking, returns the most recent detection (default `(False, 0.0, 0.0, cap_h, cap_w, 0.0)` before the first frame).
  - `stop()` — stop the thread and release the camera.
  - Construction raises on any hardware/import failure so `head_puppet.py` can catch and disable the feature.

No unit test (needs the picam). Validated by a clean import off-robot + `py_compile`, then on-robot.

- [ ] **Step 1: Implement** — append to `face_tracker.py`:

```python
class FaceCamera:
    """Captures low-res frames from the picam and runs the bundled OpenCV Haar
    face detector on a daemon thread (~DETECT_FPS), publishing only the nearest
    face. Decoupled from the 60 Hz control loop: head_puppet just calls latest().

    Lazy-imports cv2/picamzero so the rest of this module imports off-robot.
    Raises from __init__ on any failure -> head_puppet disables the feature."""

    def __init__(self, cap_w=CAP_W, cap_h=CAP_H, detect_fps=DETECT_FPS):
        import cv2
        from picamzero import Camera

        self._cv2 = cv2
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self._cascade = cv2.CascadeClassifier(cascade_path)
        if self._cascade.empty():
            raise RuntimeError(f"failed to load Haar cascade at {cascade_path}")

        self._cam = Camera()
        self._cap_w = cap_w
        self._cap_h = cap_h
        self._period = 1.0 / float(detect_fps)

        # default published frame size matches the rotated detection frame below
        self._lock = threading.Lock()
        self._latest = (False, 0.0, 0.0, cap_h, cap_w, 0.0)
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self):
        cv2 = self._cv2
        while self._running:
            t = time.time()
            try:
                frame = self._cam.capture_array()
                # downscale first (cheap), then rotate 90 CW so faces are upright
                # for the frontal Haar cascade (matches camera.py's mounting fix).
                small = cv2.resize(frame, (self._cap_w, self._cap_h))
                small = cv2.rotate(small, cv2.ROTATE_90_CLOCKWISE)
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
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
```

- [ ] **Step 2: Verify the module still imports clean off-robot (cv2/picamzero must stay lazy)**

Run: `cd mini_bdx_runtime/mini_bdx_runtime && python3 -c "import face_tracker; print('import OK:', hasattr(face_tracker, 'FaceCamera'))"`
Expected: `import OK: True` with no `ModuleNotFoundError` (proves no top-level hardware import leaked in).

- [ ] **Step 3: Verify it byte-compiles**

Run: `cd mini_bdx_runtime/mini_bdx_runtime && python3 -m py_compile face_tracker.py && echo COMPILE_OK`
Expected: `COMPILE_OK`.

- [ ] **Step 4: Re-run the full pure-logic suite (must still pass)**

Run: `cd mini_bdx_runtime/mini_bdx_runtime && python3 test_face_tracker.py`
Expected: `19 passed, 0 failed`.

- [ ] **Step 5: Commit**

```bash
git add mini_bdx_runtime/mini_bdx_runtime/face_tracker.py
git commit -m "feat(face-tracker): FaceCamera picam+Haar detection thread"
```

---

### Task 6: Wire `TRACKING` into `head_puppet.py`

**Files:**
- Modify: `scripts/head_puppet.py`

**Interfaces:**
- Consumes from `face_tracker`: `FaceCamera`, `FacePresence`, `GreetSequence`, `servo_step`, `normalized_error`, `map_error_to_axes`, `select_head_source`.
- Consumes from `scanner_sound`: `ScannerSound`, `PygameScannerBackend`.
- Consumes existing head_puppet symbols listed in Global Constraints.

Apply each edit exactly. Anchors are unique strings in the current file.

- [ ] **Step 1: Add the imports**

Find:
```python
from mini_bdx_runtime.projector import Projector
```
Replace with:
```python
from mini_bdx_runtime.projector import Projector
from mini_bdx_runtime.scanner_sound import ScannerSound, PygameScannerBackend
from mini_bdx_runtime.face_tracker import (
    FaceCamera, FacePresence, GreetSequence,
    servo_step, normalized_error, map_error_to_axes, select_head_source,
)
```

- [ ] **Step 2: Construct the camera + scanner (optional, graceful)**

Find:
```python
    projector = Projector() if duck_config.projector else None

    hwi = HWI(duck_config)
```
Replace with:
```python
    projector = Projector() if duck_config.projector else None

    # Face tracking (optional). Disabled cleanly if the picam/cv2 isn't there.
    face_cam = None
    if duck_config.camera:
        try:
            face_cam = FaceCamera()
            print("Face tracking available — DPAD-UP to toggle.")
        except Exception as e:  # noqa: BLE001
            print(f"[head_puppet] face tracking unavailable: {e}")
            face_cam = None

    # Scanner-sound lamp-loop for the greeting scan (reuses the X-button feature).
    scanner = None
    if duck_config.speaker:
        try:
            scanner = ScannerSound(
                PygameScannerBackend("../mini_bdx_runtime/assets/scanner/")
            )
        except Exception as e:  # noqa: BLE001
            print(f"[head_puppet] scanner sound unavailable: {e}")
            scanner = None

    hwi = HWI(duck_config)
```

- [ ] **Step 3: Add the tracking state variables**

Find:
```python
    playback_idx = 0
    playback_armed = False
    warned_no_feature = False
```
Replace with:
```python
    playback_idx = 0
    playback_armed = False
    warned_no_feature = False
    presence = FacePresence()
    greet = GreetSequence()
    tracking_armed = False
    last_face = None                   # last real (cx, cy, w, h) while a face is held
```

- [ ] **Step 4: Read the UP button each tick and handle the global toggle**

Find:
```python
            dl = getattr(buttons, "dpad_left", None)
            dr = getattr(buttons, "dpad_right", None)
            feature_on = dl is not None and dr is not None
```
Replace with:
```python
            dl = getattr(buttons, "dpad_left", None)
            dr = getattr(buttons, "dpad_right", None)
            du = getattr(buttons, "dpad_up", None)
            feature_on = dl is not None and dr is not None

            # DPAD-UP toggles face tracking from LIVE or PLAYBACK.
            if du is not None and du.triggered and state in ("LIVE", "PLAYBACK"):
                if face_cam is not None:
                    state = "TRACKING"
                    presence = FacePresence()
                    greet = GreetSequence()
                    playback_idx = 0
                    tracking_armed = False
                    last_face = None
                    print("◉ FACE TRACKING — DPAD-UP or any stick to stop")
                    time.sleep(DT)
                    continue
                else:
                    print("(face tracking unavailable — set duck_config camera "
                          "+ connect the picam)")
```

- [ ] **Step 5: Add the TRACKING block** — insert it right before the PLAYBACK block.

Find:
```python
            # ---- PLAYBACK ----
            if state == "PLAYBACK":
```
Replace with:
```python
            # ---- TRACKING ----
            if state == "TRACKING":
                # exit on UP again, or on any stick/trigger once we've gone idle
                active = any_active_input(
                    last_commands, buttons, left_trigger, right_trigger
                )
                if not active:
                    tracking_armed = True
                up_toggle = du is not None and du.triggered
                if up_toggle or (tracking_armed and active):
                    if scanner is not None:
                        scanner.stop()
                    if projector is not None and projector.on:
                        projector.switch()
                    if antennas is not None:
                        antennas.set_position_left(0)
                        antennas.set_position_right(0)
                    state = "LIVE"
                    print("■ tracking stopped")
                    time.sleep(DT)
                    continue

                found, cx, cy, img_w, img_h, _t = face_cam.latest()
                presence.update(found, now)
                if found:
                    last_face = (cx, cy, img_w, img_h)

                src = select_head_source(presence.present, bool(recording))

                # fetch the next idle keyframe only when we're using it
                keyframe = None
                if src == "keyframe":
                    keyframe = recording[playback_idx]
                    playback_idx = (playback_idx + 1) % len(recording)

                g = greet.update(now, presence)

                # ---- head target ----
                if src == "servo" and last_face is not None:
                    fx, fy, fw, fh = last_face
                    ex, ey = normalized_error(fx, fy, fw, fh)
                    err_yaw, err_pitch = map_error_to_axes(ex, ey)
                    raw_yaw, raw_pitch = servo_step(
                        prev_head[0], prev_head[2], err_yaw, err_pitch
                    )
                    target = list(clamp_head_rad((raw_yaw, 0.0, raw_pitch)))
                elif src == "keyframe":
                    target = list(clamp_head_rad(keyframe["head"]))
                else:  # hold (servo with no face seen yet, or no recording)
                    target = list(clamp_head_rad((prev_head[0], 0.0, prev_head[2])))
                prev_head = slew_list(prev_head, target, MAX_HEAD_DELTA)
                hwi.set_position("head_yaw", prev_head[0])
                hwi.set_position("head_roll", prev_head[1])
                hwi.set_position("head_pitch", prev_head[2])

                # ---- antennas: greet wiggle wins; else the idle keyframe ----
                if g.antenna is not None and antennas is not None:
                    antennas.set_position_left(clamp(g.antenna, -1.0, 1.0))
                    antennas.set_position_right(clamp(g.antenna, -1.0, 1.0))
                elif keyframe is not None and antennas is not None:
                    al, ar = keyframe["ant"]
                    antennas.set_position_left(clamp(al, -1.0, 1.0))
                    antennas.set_position_right(clamp(ar, -1.0, 1.0))

                # ---- sound: greet one-shot; else the idle keyframe's sound ----
                if g.play_sound and sounds is not None:
                    sounds.play(g.play_sound)
                elif keyframe is not None and keyframe["sound"] and sounds is not None:
                    sounds.play(keyframe["sound"])

                # ---- projector: greet/scan wins; else the idle keyframe's state ----
                if g.projector is not None and projector is not None:
                    if g.projector != projector.on:
                        projector.switch()
                elif (keyframe is not None and keyframe["proj"] is not None
                        and projector is not None):
                    if keyframe["proj"] != projector.on:
                        projector.switch()

                # ---- scanner lamp-loop during the scan ----
                if scanner is not None:
                    if g.scanner and not scanner.is_active:
                        scanner.start(now)
                    elif not g.scanner and scanner.is_active:
                        scanner.stop()
                    scanner.update(now)

                time.sleep(DT)
                continue

            # ---- PLAYBACK ----
            if state == "PLAYBACK":
```

- [ ] **Step 6: Clean up the camera + scanner on exit**

Find:
```python
        if projector is not None:
            projector.stop()
        print("head puppet off")
```
Replace with:
```python
        if projector is not None:
            projector.stop()
        if scanner is not None:
            scanner.stop()
        if face_cam is not None:
            face_cam.stop()
        print("head puppet off")
```

- [ ] **Step 7: Verify head_puppet byte-compiles**

Run: `cd /Users/maximilianschmierer/Git_Projects/OpenDuckIHK/Open_Duck_Mini_Runtime && python3 -m py_compile scripts/head_puppet.py && echo COMPILE_OK`
Expected: `COMPILE_OK` (compiles despite the Pi-only imports — `py_compile` doesn't execute them).

- [ ] **Step 8: Verify the existing head_puppet logic tests still pass (no regression in the pure helpers)**

Run: `cd /Users/maximilianschmierer/Git_Projects/OpenDuckIHK/Open_Duck_Mini_Runtime/scripts && python3 test_head_puppet_logic.py`
Expected: `12 passed, 0 failed`.

- [ ] **Step 9: Commit**

```bash
git add scripts/head_puppet.py
git commit -m "feat(head-puppet): DPAD-UP face-tracking mode (TRACKING state)"
```

---

### Task 7: Ship it — `transfer.command` + docstring + full-suite gate

**Files:**
- Modify: `transfer.command`
- Modify: `scripts/head_puppet.py` (docstring only)

- [ ] **Step 1: Add `face_tracker.py` to the transfer list**

In `transfer.command`, find:
```bash
FILES=(
  "scripts/head_puppet.py|scripts"
  "mini_bdx_runtime/mini_bdx_runtime/xbox_controller.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/mini_bdx_runtime/buttons.py|mini_bdx_runtime/mini_bdx_runtime"
)
```
Replace with:
```bash
FILES=(
  "scripts/head_puppet.py|scripts"
  "mini_bdx_runtime/mini_bdx_runtime/xbox_controller.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/mini_bdx_runtime/buttons.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/mini_bdx_runtime/face_tracker.py|mini_bdx_runtime/mini_bdx_runtime"
)
```

- [ ] **Step 2: Add the face-tracker revert line to the on-duck rollback hint**

In `transfer.command`, find:
```bash
echo "      mv mini_bdx_runtime/mini_bdx_runtime/buttons.py.orig mini_bdx_runtime/mini_bdx_runtime/buttons.py'"
```
Replace with:
```bash
echo "      mv mini_bdx_runtime/mini_bdx_runtime/buttons.py.orig mini_bdx_runtime/mini_bdx_runtime/buttons.py && \\"
echo "      rm -f mini_bdx_runtime/mini_bdx_runtime/face_tracker.py'   # new file: just delete to revert"
```

- [ ] **Step 3: Document DPAD-UP in the head_puppet docstring**

In `scripts/head_puppet.py`, find:
```python
  * Press DPAD-RIGHT        -> play the stored sequence, looped forever.
    Press DPAD-RIGHT again, or touch ANY stick / trigger / button, to stop and
    return to live control.
```
Replace with:
```python
  * Press DPAD-RIGHT        -> play the stored sequence, looped forever.
    Press DPAD-RIGHT again, or touch ANY stick / trigger / button, to stop and
    return to live control.
  * Press DPAD-UP           -> face-tracking mode: the head tracks the nearest
    face (picam + OpenCV Haar). After a face is held 1.5 s it greets (ear wiggle
    + happy2.wav, then a 5 s scanner scan); on loss it wiggles goodbye. With no
    face it replays your recorded idle. Press DPAD-UP again, or any stick, to
    stop. Needs duck_config "camera": true; disabled cleanly if the picam/cv2
    isn't present.
```

- [ ] **Step 4: Verify the transfer script parses and head_puppet still compiles**

Run:
```bash
cd /Users/maximilianschmierer/Git_Projects/OpenDuckIHK/Open_Duck_Mini_Runtime
bash -n transfer.command && echo TRANSFER_SYNTAX_OK
python3 -m py_compile scripts/head_puppet.py && echo COMPILE_OK
```
Expected: `TRANSFER_SYNTAX_OK` then `COMPILE_OK`.

- [ ] **Step 5: Full off-robot gate — every test suite green**

Run:
```bash
cd /Users/maximilianschmierer/Git_Projects/OpenDuckIHK/Open_Duck_Mini_Runtime
python3 mini_bdx_runtime/mini_bdx_runtime/test_face_tracker.py && \
( cd scripts && python3 test_head_puppet_logic.py ) && \
echo ALL_SUITES_GREEN
```
Expected: `19 passed, 0 failed`, then `12 passed, 0 failed`, then `ALL_SUITES_GREEN`.

- [ ] **Step 6: Commit**

```bash
git add transfer.command scripts/head_puppet.py
git commit -m "chore(transfer): ship face_tracker.py + document DPAD-UP tracking"
```

---

## On-robot validation (manual, after `transfer.command`)

Not automatable here — do these on the duck with `"camera": true` in `~/duck_config.json`:

1. `cd scripts && python3 head_puppet.py` → expect "Face tracking available — DPAD-UP to toggle."
2. Record a short idle first (hold DPAD-LEFT 3 s, move head, tap DPAD-LEFT).
3. Press **DPAD-UP**. With **no face**, the recorded idle should play. Step into view: head should turn toward you. **If it tracks the wrong way, flip `YAW_SIGN` / `PITCH_SIGN` (or swap `YAW_FROM`/`PITCH_FROM`) in `face_tracker.py`** — the `[face] center=(...)` prints show which axis is which.
4. Hold still ~1.5 s → ear wiggle + `happy2.wav`, then a 5 s scanner scan (LED + lamp loop), then off.
5. Leave the frame → one ear-wiggle goodbye; idle resumes.
6. Press **DPAD-UP** (or nudge a stick) → "tracking stopped", back to live puppet.
7. Sanity: detection FPS / CPU OK? If sluggish, lower `DETECT_FPS` or `CAP_W/CAP_H` in `face_tracker.py`.
8. Confirm the **walk is unaffected** (separate concern — walk files were never touched).
