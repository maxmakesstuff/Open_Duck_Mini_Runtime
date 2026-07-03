# Morning testing checklist — overnight dev suite (2026-07-03)

Branch `feature/overnight-suite`. Nothing here was hardware-tested (robot was off).
Everything degrades safely if a peripheral/flag is missing. Safe copy of the last
tested state: tag `known-good-face-tracking`.

Test in this order (safest → riskiest). Keep a hand on the robot for anything that
moves legs.

## 0. Deploy
1. Double-click `transfer.command` (edit `REMOTE_HOST/USER/ROOT` first if it's a
   different duck). It ships the walk script, web UI, all new modules, and the
   bluetooth kit; existing files are backed up as `<file>.orig` on the duck.
2. On the duck, update `~/duck_config.json` from the new `example_config.json`:
   - `"web_ui": true` (default) + optional `"web_port": 8080`, `"battery": {...}`.
   - `expression_features`: turn on `speaker`, `antennas`, `projector`, `camera`
     as the robot has them.
3. Face tracking needs `cv2` in the venv (already there on the current duck).

## 1. Web UI  (lowest risk — no motion)
- Start either mode (head puppet or walk). The console prints
  `[web] control UI at http://<ip>:8080`.
- On your phone (same Wi-Fi/AP as the duck) open that URL, or
  `http://<robot>.local:8080`.
- Expect: mode pill (WALK/HEAD), green LINK dot, battery gauge, LOOP Hz ticking,
  attitude horizon moving with the robot, two joysticks + all buttons.
- **Battery number**: if it shows `n/a`, run `cd scripts && python3 probe_battery.py`
  and follow its output (it tells you if rustypot exposes the register and whether
  the ×0.1 scale is right — one-line fix in `HWI.VOLTAGE_SCALE` if not).
- **Parallel control**: drive from the phone AND the gamepad; releasing the phone
  stick hands control straight back to the pad. Buttons work from either.
- IMU pitch/roll on the horizon is best-effort (accel-derived) — if the axes look
  swapped/inverted it's cosmetic; tune `_accel_pitch_roll` in the walk script.

## 2. Xbox auto-reconnect
- Runtime side (no restart): while head-puppet/walk is running, power the pad off,
  wait, power it on → it should re-attach within a few seconds (the log prints
  `gamepad disconnected` then re-loads it). The web UI keeps working throughout.
- OS side (the real fix for the 10-min drop): run
  `sudo bash ops/bluetooth/setup-bluetooth-reconnect.sh <PAD_MAC>` and follow
  `ops/bluetooth/README.md` (a reboot makes the autosuspend/ERTM changes stick).
  Then verify >10 min stable + auto-reconnect per the README.

## 3. Scanner sound (both modes) — audio only
- Press **X** (scanner LED). Expect the lamp loop to start and keep looping while
  the LED is on; press X again → light off, sound stops. Works in head puppet and
  walk. Needs `speaker` + the `assets/scanner/lamp*.wav` (shipped).

## 4. Head-puppet tracking chatter — head only
- Enter face tracking (DPAD-UP / TRACK). After the greeting, while it's steadily
  following you, expect a random droid sound every few seconds and an occasional
  ear wiggle. Priority: greeting > chatter > recorded-idle. Stop with DPAD-UP.

## 5. Head-puppet record/playback (unchanged feature; sanity only)
- Hold DPAD-LEFT 3 s to record, DPAD-RIGHT to loop, DPAD-RIGHT/stick to stop.
  Confirm the web `REC` state reflects idle/recording/playing.

## 6. Fall detection → auto-pause (walk)  ⚠ moves legs
- Start the walk. Let it stand, then gently lay the duck on its side (or lift both
  feet clear) for ~2.5 s. Expect: it prints `FALL DETECTED — auto-paused`, plays a
  sound, and freezes (stops kicking). Right it, press **A** (or web Resume) → it
  re-arms and continues.
- If it false-triggers during normal walking, raise `DEFAULT_FALL_AFTER_S` in
  `mini_bdx_runtime/fall_detector.py`. If it's too slow to notice a fall, lower it.

## 7. Walk record & playback  ⚠⚠ moves the whole robot — most caution
- Have the duck walking under manual control first (confirm balance is normal).
- Hold **DPAD-LEFT 3 s** → `WALK RECORDING`. Drive a simple path (e.g. forward,
  turn), then tap DPAD-LEFT to stop (or 120 s cap). It saves `walk_recording.pkl`.
- Press **DPAD-RIGHT** → it replays the *commands* through the live policy, so it
  balances the whole time. It loops. **Grab a stick** → live control resumes
  instantly. **DPAD-RIGHT** again → it ramps to a stand.
- The recording persists across restarts (loaded on boot; DPAD-RIGHT to replay).
- Safety notes: recorded commands are clamped to the normal command ranges; the
  policy + gyro are always live, so it should never open-loop topple. If a
  playback ever looks unstable, grab a stick (instant live control) or press A.
  Start with SHORT, GENTLE recordings on a soft surface.

## Rollback
- Per-file: restore `<file>.orig` on the duck (transfer.command prints the exact
  commands at the end).
- Whole suite: `git checkout known-good-face-tracking` and redeploy, or just set
  `"web_ui": false` to drop the web layer while keeping everything else.

## Off-robot test suite (already green on the dev machine)
`mini_bdx_runtime/mini_bdx_runtime/`: `python3 test_fall_detector.py`,
`test_walk_record.py`, `test_tracking_chatter.py`, `test_control_bus.py`,
`test_battery.py`, `test_web_control.py`, `test_telemetry.py`, `test_xbox_merge.py`.
`scripts/`: `test_walk_integration_logic.py`, `test_head_puppet_logic.py`.
