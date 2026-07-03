# Overnight dev suite — 2026-07-03

Autonomous overnight build. Robot is **off**, so nothing is hardware-tested; every
feature is written to be copy-and-test-ready and degrades safely if a peripheral is
missing. All work is **committed locally only** (no push) on branch
`feature/overnight-suite`, branched from the known-good, hardware-tested tag
`known-good-face-tracking` (face tracking, commit `00b502c`).

Each feature is its own commit so anything can be reverted independently. A morning
testing checklist lives at the bottom.

---

## 0. Safety / git hygiene (done first)
- Tagged the tested state `known-good-face-tracking`.
- New branch `feature/overnight-suite`.
- **Discarded the stale walk attempt** `scripts/v2_rl_walk_mujoco_anim.py` — it layered
  *head idle-animations from animations.json* onto the walk (a different feature). The
  new walk record/playback is built fresh from the pristine `v2_rl_walk_mujoco.py`.

## 1. Xbox controller auto-reconnect (pain point)
Two layers, because both are needed:
- **OS layer** (`ops/bluetooth/`): trust the pad so BlueZ auto-reconnects it the moment
  it powers back on, and disable USB/BT autosuspend so it stops dropping after ~10 min.
- **Runtime layer** (`xbox_controller.py`): the running Python process holds a stale
  `pygame.joystick.Joystick(0)` after a drop. Make the controller reader **resilient**:
  detect the dead device, re-init the joystick subsystem, and re-open device 0 when it
  reappears — no robot restart. While disconnected it emits neutral commands (robot
  holds/stops safely) instead of crashing.

## 2. Web UI (phone) — `web_control.py` + `webui/index.html`
- **Zero new deps**: a tiny stdlib `ThreadingHTTPServer` in a daemon thread. The phone
  connects to the robot's own Wi-Fi/AP. Self-contained single HTML page (inline CSS/JS,
  no CDN) so it works fully offline.
- **Low CPU**: phone polls `GET /api/state` at ~4 Hz (tiny JSON); commands are
  `POST /api/command` (joystick vector) and `POST /api/button` (momentary events). The
  control loop only touches a lock-guarded snapshot once per tick — negligible next to
  ONNX inference.
- **Parallel with the gamepad**: a shared `ControlBus` merges both sources. The web
  virtual stick only overrides the command vector *while the user is touching it*
  (`active` flag); on release it yields back to the gamepad. Button events from either
  source are OR-ed. So you can swap freely mid-session.
- **Telemetry shown**: battery voltage + rough %, heuristic charging indicator, current
  mode (walk / head-puppet), paused state, IMU pitch/roll, control-loop Hz, servo temp
  (if available), fall status, recording/playback status.
- **All xbox controls mapped**, mode-aware: the page renders the walk panel or the
  head-puppet panel depending on `mode` from `/api/state`.

Battery: `HWI.get_present_voltage()` / `get_present_temperature()` added, guarded — if
rustypot 0.1.0 doesn't expose the register the UI shows "n/a" (confirm via
`scripts/probe_battery.py` on hardware). Charging = voltage above the full threshold and
non-falling (heuristic, clearly labelled).

## 3. Walk record & playback (the hard one) — SAFE design
**Record the command stream, replay it through the live policy.** We do NOT record joint
positions. Each tick we store the *intent* — `[lin_vel_x, lin_vel_y, ang_vel, neck_pitch,
head_pitch, head_yaw, head_roll]` plus gait params (freq offset, sprint) and optional
projector/sound events. On playback we feed those commands back into `last_commands`; the
ONNX policy runs live on live IMU/feet data, so **gyro balancing stays fully active** —
the robot re-walks the same path and balances reactively. This directly answers "record
'walk a circle', play it back" and the "must keep balancing" constraint.
- **Stopping mid-play is safe**: we don't freeze joints; we hand control back and ramp the
  commands to zero over ~0.4 s (a normal "stop walking" transition the policy handles),
  so the robot decelerates to a stand instead of lurching.
- **Never trust recorded data**: commands are clamped to the known X/Y/YAW ranges on load
  (motor-safety rule — authored data is untrusted).
- Controls mirror head-puppet: hold DPAD-LEFT 3 s = record (cap 60 s), DPAD-RIGHT =
  play/loop, DPAD-RIGHT again or any stick = stop. Persisted to `walk_recording.pkl`.

## 4. Fall detection → auto-pause (walk)
Pure, testable `FallDetector`. Primary trigger = **both foot switches unpressed for a
sustained window** (~2.5 s) — exactly the user's description, long enough not to fire on a
normal gait's brief flight phases. On fall: auto-`paused = True`, play a sound, print. The
existing pause path freezes the robot so the user can right it and press A/web-resume.
(An accel-tilt corroborator is scaffolded but off by default — the BNO055 up-axis needs
on-robot calibration before we trust it.)

## 5. Scanner sound while the light is on (both modes)
`ScannerSound` already loops the lamp files while active; it was only wired to the
face-greeting scan. Now **couple projector↔scanner** everywhere: when the X button (or web)
toggles the scanner LED on, start the lamp loop; off → stop. Wired in both
`head_puppet.py` (LIVE/PLAYBACK/RECORDING) and `v2_rl_walk_mujoco.py`.

## 6. Communicative tracking (head-puppet) — random sounds + ear wiggles
Pure, testable `TrackingChatter` in `face_tracker.py`: while a face is actively tracked
(present, past the greeting), it schedules a random sound every ~4–9 s and an ear wiggle
every ~6–12 s (jittered, non-overlapping with the greeting). Wired into the TRACKING
state's servo branch. Makes being tracked feel alive.

## 7. BD-1/BD-X voice generator tool (only after everything else) — `tools/duck_voice/`
Off-robot authoring tool. Two paths: (a) **procedural** droid-speech synthesis
(pitch-warbled blips → wav, numpy/scipy only), and (b) **voice conversion** — take a voice
sample and robotize it (ring-mod + pitch/formant shift + bitcrush) into BD-style speech.
Produces new `.wav`s that match the existing set's "little-speech" feel. Ships example
output + README. Not on the robot's critical path.

---

## Morning testing checklist
See `docs/plans/2026-07-03-TESTING-CHECKLIST.md` (deploy order, per-feature on-robot steps,
and the duck_config flags each feature needs).
