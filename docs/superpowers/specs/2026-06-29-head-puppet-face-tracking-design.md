# Head-Puppet Face-Tracking Mode — Design

Date: 2026-06-29
Status: approved (pending spec review)
Scope: `scripts/head_puppet.py` and a new `mini_bdx_runtime/face_tracker.py` only.
**The walk script is NOT touched. Legs are never moved.**

## Goal

Add an on-device face-tracking mode to head puppet. Pressing **DPAD-UP** makes the
duck track the nearest face with its head, within the head's safe limits. When it
has seen a face for ≥1.5 s it greets (ear wiggle + cute sound, then a 5 s scanner
scan). When the face leaves, it wiggles its ears goodbye and returns to the
recorded idle. Everything runs on the Raspberry Pi Zero 2W with the installed
picam, reusing the OpenCV (`cv2`) and `picamzero` already on the robot — no new
dependencies, no extra model files.

## Non-goals

- No change to the walk (`v2_rl_walk_mujoco*.py`) or any leg motion.
- No new pip dependencies. Haar cascade ships inside the installed `cv2`.
- No disk persistence of recordings (unchanged from today).
- Not a general "follow me" / body tracker — head yaw + pitch only.

## User-facing behaviour

Controls in head puppet (additions in **bold**):

| Input | Action |
| --- | --- |
| Left stick / right stick | live head puppet (unchanged) |
| Triggers | antennas (unchanged) |
| B | random sound (unchanged) |
| X | projector / scanner LED toggle (unchanged) |
| Hold DPAD-LEFT 3 s | record idle (unchanged) |
| DPAD-RIGHT | play recorded idle, looped (unchanged) |
| **DPAD-UP** | **toggle face-tracking mode** |

In **face-tracking mode**:

- **Face visible** → the head turns to keep the nearest (largest) face centered.
- **Face held ≥1.5 s** (one-shot per acquisition) → **ear wiggle + `happy2.wav`**,
  then a **5 s scanner scan** (projector LED on + the existing `ScannerSound`
  lamp-loop), then projector off.
- **Face lost after greeting** → **one ear wiggle** goodbye; projector/scanner
  forced off; head hands back to the recorded idle.
- **No face** → the head plays the **recorded idle keyframes** (same engine as
  DPAD-RIGHT playback). If no recording exists, the head **holds center** and
  prints a hint to record first.
- **Exit**: press **DPAD-UP again**, or nudge **any stick / trigger / button**
  (same "armed after an idle moment" guard that playback uses, so the entry
  press cannot immediately exit). Exiting returns to LIVE control.

## Architecture

Two files change; one is new.

### New: `mini_bdx_runtime/mini_bdx_runtime/face_tracker.py`

Split into **pure logic** (imports clean off-robot, fully unit-tested) and a
**thin hardware wrapper** (lazy `picamzero`/`cv2` import, not unit-tested).

**Pure logic (no hardware):**

- `largest_face(faces) -> (x,y,w,h) | None` — nearest = max area.
- `face_center(face) -> (cx, cy)`.
- `normalized_error(cx, cy, img_w, img_h) -> (ex, ey)` — each in `[-1, 1]`,
  `0` at frame center.
- `map_error_to_axes(ex, ey, cfg) -> (err_yaw, err_pitch)` — applies the
  image-axis→head-axis mapping and signs (the picam is mounted rotated 90°, so
  this is configurable; see Calibration).
- `servo_step(prev_yaw, prev_pitch, err_yaw, err_pitch, cfg) -> (yaw, pitch)` —
  incremental proportional control with a center **deadzone**. Eye-in-hand
  closed loop: nudge the head toward reducing the error each tick. Output is a
  *raw* target; the caller still applies the existing clamp + slew (below).
- `class FacePresence` — hysteresis + latches over raw detections:
  `update(found, now)`; exposes `.present` (true until `lost_timeout` s without a
  detection, to ride out dropped frames), `.just_acquired`, `.just_lost`,
  `.held_duration`, and a one-shot `.greet_ready` when a face has been held
  ≥`GREET_AFTER_S` (1.5 s).
- `class GreetSequence` — non-blocking timeline driven once per tick by
  `update(now, presence) -> GreetOutput`. States `WAITING → GREET → SCAN →
  GREETED`, plus `FAREWELL` on loss-after-greeting. `GreetOutput` fields:
  - `antenna: float | None` — ear-wiggle value (time-varying sine) during
    GREET/FAREWELL, else `None` (caller owns antennas).
  - `play_sound: str | None` — one-shot `happy2.wav` at GREET entry.
  - `projector: bool | None` — `True` for the 5 s SCAN window, `False` when the
    scan ends or on FAREWELL; `None` = don't touch.
  - `scanner: bool` — whether the `ScannerSound` lamp-loop should be running
    (true during SCAN only).

**Hardware wrapper:**

- `class FaceCamera` — owns a `picamzero` camera, runs a **daemon thread** at
  ~`DETECT_FPS` (10) capturing a low-res grayscale frame and running the Haar
  cascade (`cv2.data.haarcascades + "haarcascade_frontalface_default.xml"`).
  Publishes only the **largest** face plus frame size via a `maxsize=1` queue /
  lock-protected attribute. `latest() -> (found, cx, cy, img_w, img_h, t)` is
  non-blocking. Construction lazily imports `picamzero`/`cv2`; any failure raises
  a clean error the caller catches to disable the feature. `stop()` joins the
  thread and releases the camera.

### Changed: `scripts/head_puppet.py`

Add a fourth state to `LIVE | RECORDING | PLAYBACK`: **`TRACKING`**.

- **Construct** a `FaceCamera` only if `duck_config.camera` is set and the import
  succeeds; otherwise `face_cam = None` and DPAD-UP prints a one-line "camera
  unavailable" notice and does nothing — same graceful-degrade pattern as the
  DPAD record/playback feature. Never crashes the puppet.
- **Enter TRACKING** on `dpad_up.triggered` (from LIVE or PLAYBACK) when
  `face_cam` exists. Seed playback index from any existing recording; if none,
  mark "hold center".
- **Per tick in TRACKING:**
  1. `found, cx, cy, w, h, t = face_cam.latest()`; feed into `FacePresence`.
  2. **Head source:** if `presence.present` → `servo_step(...)` → head tuple
     `(yaw, 0.0, pitch)` (roll held at 0). Else → next **recorded keyframe** head
     (or hold-center if no recording). The keyframe clock **pauses while a face is
     present** and resumes on loss.
  3. **Both head targets** go through the existing `clamp_head_rad()` then
     `slew_list(prev_head, target, MAX_HEAD_DELTA)` — unchanged safety, reused.
  4. `GreetSequence.update(now, presence)` drives antennas / `happy2.wav` /
     projector / `ScannerSound`. **Channel ownership:** face present ⇒ greet/scan
     owns antennas + projector; face absent ⇒ the recorded keyframe playback owns
     antennas + sound + projector (as DPAD-RIGHT does today). They never both
     drive the same channel in the same tick.
  5. **Exit** on `dpad_up.triggered` again, or `any_active_input(...)` once armed.
- **Construct** a `ScannerSound` + `PygameScannerBackend` over
  `../mini_bdx_runtime/assets/scanner/` if `duck_config.speaker`; `.update(now)`
  is called every tick (cheap no-op when inactive). Reuses the existing module
  verbatim.

### Changed: `transfer.command`

Add `mini_bdx_runtime/mini_bdx_runtime/face_tracker.py` to the push list (now
**4 files**). The one-time `.orig` backup + revert instructions already cover any
file in the list. The walk remains untouched.

## Calibration constants (top of `face_tracker.py`)

These are expected to need on-robot tuning (cannot be verified off-robot):

- **Axis mapping** — `YAW_FROM ∈ {"x","y"}`, `YAW_SIGN ∈ {+1,-1}`, `PITCH_FROM`,
  `PITCH_SIGN`. Default guess accounts for the 90° rotation in `camera.py`; the
  detector prints the chosen face center so a wrong-direction track is a
  one-character sign flip.
- **Gains / deadzone** — `KP_YAW`, `KP_PITCH` (incremental rad per unit error),
  `DEADZONE` (normalized, ~0.08).
- **Detection** — `CAP_W`/`CAP_H` (e.g. 320×240), optional detect-downscale,
  `DETECT_FPS` (10), Haar `scaleFactor`/`minNeighbors`/`minSize`.
- **Timing** — `GREET_AFTER_S` (1.5), `SCAN_S` (5.0), `WIGGLE_S` (~1.0),
  `WIGGLE_AMP` (~0.6), `WIGGLE_HZ` (~3), `LOST_TIMEOUT_S` (~0.4).

## Safety

- **Clamp + slew on every head target** (face-driven and keyframe-driven),
  reusing `clamp_head_rad` + `slew_list` — worst-case step stays ≤ `MAX_HEAD_DELTA`
  (`MAX_HEAD_VELOCITY/CONTROL_HZ`). Aligns with the project's motor-safety rule.
- **Legs untouched**; only `head_yaw`, `head_roll(=0)`, `head_pitch` move.
  `neck_pitch` stays 0 as in the puppet today.
- **Non-blocking**: detection on its own thread; greeting/scan are per-tick timed
  state machines — no `sleep` chain stalls the 60 Hz servo loop.
- **Graceful disable**: missing camera, failed `cv2`/`picamzero` import, or
  `duck_config.camera = false` → feature off, puppet runs normally.
- **Per-flag degrade**: no `speaker` → no greet sound / scan loop; no `antennas`
  → no wiggle; no `projector` → no scan LED. Head tracking still works.

## Testing

Pure logic gets a `mini_bdx_runtime/test_face_tracker.py` in the repo's existing
style (plain `assert` + `__main__` `_run()` runner, no pytest):

- `largest_face` picks max area; `None` on empty.
- `normalized_error` is 0 at center, ±1 at edges, correct sign.
- `map_error_to_axes` honors `*_FROM` / `*_SIGN`.
- `servo_step` deadzone yields no motion inside the zone; moves toward the face
  outside it; never exceeds its own step before clamp/slew.
- `FacePresence`: `lost_timeout` hysteresis rides out 1-frame dropouts;
  `greet_ready` fires once at 1.5 s and re-arms only after a loss; `just_acquired`
  / `just_lost` edges.
- `GreetSequence`: WAITING→GREET at 1.5 s (sound fired once); GREET→SCAN with
  projector+scanner on for 5 s then off; loss-after-greeting → FAREWELL wiggle +
  projector/scanner forced off; loss-before-greeting → no farewell.

On-robot validation (manual): `decode`/print face center, confirm track
direction (flip a sign if needed), confirm greeting timing, confirm exit on
UP/stick, confirm walk unaffected (separate robot, walk files untouched).

## Risks / open tuning

- Haar is frontal-only and lighting-sensitive — acceptable for "track until
  gone"; YuNet/UltraFace is a later upgrade if needed.
- Detection FPS on the Zero 2W under the 60 Hz loop is unmeasured; `DETECT_FPS`
  and `CAP_W/H` are the throttles. First on-robot step is a quick FPS/RAM sanity
  check.
- Axis mapping/sign is a guess until tested — designed to be a one-line flip.
