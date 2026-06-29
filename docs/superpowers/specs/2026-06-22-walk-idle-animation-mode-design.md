# Walk + Idle-Animation Mode — Design

- **Date:** 2026-06-22
- **Status:** Approved (pending written-spec review)
- **Author:** Claude Code (with dev@b-rex.de)
- **Component:** `Open_Duck_Mini_Runtime`

## 1. Goal

Create an **alternate** version of the RL walk script that adds an **idle-animation mode**. The directional pad (up/down/left/right) stops walking and plays one of **four idle animations**, each combining **head motion + antenna ("ear") motion + a sound + the side lamp (projector)**. Critically, mode switching must happen **without the robot ever shutting down or losing balance** — you can walk, stand, trigger an animation, and resume walking seamlessly.

The original `scripts/v2_rl_walk_mujoco.py` is **left untouched**; this is a standalone alternate entrypoint.

## 2. Background (current behavior)

`scripts/v2_rl_walk_mujoco.py` runs one continuous 50 Hz control loop (`RLWalk.run()`):
- Reads the Xbox controller (`last_commands[7]`, buttons, triggers).
- `Y` toggles between *walk* and *walk-with-head-control* (head-control mode zeros locomotion and steers the head).
- dpad up/down currently nudges `phase_frequency_factor_offset` (gait-frequency tuning).
- Each tick: build 101-dim obs → ONNX inference → `motor_targets = init_pos + action*action_scale` → head overlay (`motor_targets[5:9] = last_commands[3:7] + motor_targets[5:9]`) → `hwi.set_position_all`.

Key insight: **the loop already runs the policy every tick and never stops between modes.** When the walk stick is released, `last_commands[0:3] → 0` and the policy already holds the robot standing. "Shutting down" risk comes only from *blocking* animation sequences (e.g. `time.sleep` chains) that would stall servo updates. Therefore animations must be **non-blocking / per-tick stepped**.

The 14 joints (HWI order): left leg (5), **head/neck (indices 5:9 = neck_pitch, head_pitch, head_yaw, head_roll)**, right leg (5). Animations touch **only** the 4 head joints + the 2 antennas + lamp + sound. **Legs are never touched** (policy-only, already trained-safe).

## 3. Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Mode behavior | **Sticky animation mode** — a dpad press enters a held idle mode (legs stand); each dpad press plays an animation; you stay in idle until you leave. |
| Exit | **Push the walk stick** — instantly aborts the current animation and resumes walking ("walk stick wins"). Sole exit mechanism. |
| dpad reassignment | **Drop** the gait-frequency nudge; dpad = the 4 animations. `LB` (hold-to-sprint) is unchanged. |
| Animation source | **Data-driven JSON file** (`scripts/animations.json`); 4 starter animations authored by Claude, fully editable. |
| Motor safety | **Hard, defense-in-depth safety layer** (see §6) — explicit user requirement. |

## 4. Architecture

### 4.1 One loop, one mode flag
Add `self.mode ∈ {WALK, ANIMATION}` (default `WALK`) and an `AnimationPlayer`. The policy runs **every tick in both modes** — legs are always balanced. Only the *source* of certain signals changes:

| Signal | WALK | ANIMATION |
|---|---|---|
| Locomotion cmd (`obs cmd[0:3]`) | from sticks | forced **0** (proven "stand" regime) |
| Legs `motor_targets[0:5],[9:14]` | policy | policy (identical) |
| Head `motor_targets[5:9]` | gamepad overlay | **animation timeline** (overrides policy head) |
| Antennas | triggers | animation timeline |
| Lamp (projector) / Sound | manual `X` / `B` | animation events |

Standing-while-idle == today's "stick released" behavior, so walk↔animate transitions are seamless and balance-safe.

### 4.2 Per-tick state machine (inside `run()`)
1. Read controller (commands, buttons, triggers).
2. **Any dpad `.triggered`** (up/down/left/right) → `mode = ANIMATION`; `player.start(dpad_map[dir], now)`. Works from WALK (enter) or ANIMATION (switch animation). A fresh dpad trigger this tick suppresses the stick-interrupt check (§3) to avoid flicker.
3. Else if `mode == ANIMATION` and **walk stick magnitude > deadzone** → `mode = WALK` (abort animation); the same stick input drives walking this tick.
4. Run policy as today (legs). In ANIMATION, the obs locomotion command is forced to `0` (and head command `0`); the policy's head output is discarded and overridden.
5. Apply the per-mode signal table.
6. **Safety layer (§6)** clamps + slew-limits the head targets every tick, both modes.
7. `make_action_dict` → `hwi.set_position_all` (unchanged).
8. If an animation finishes (non-looping) and no input → head eases back to neutral (via the same slew limiter), robot keeps standing in ANIMATION (sticky) until the next dpad press or stick push.

Unchanged controls: `A` = global pause, `LB` = sprint (walk only), `X`/`B` = manual lamp/sound. **Only** the dpad's old gait-frequency function is removed.

### 4.3 Walk-stick deadzone
`last_commands[0:3]` are scaled velocities (x≤0.15, y≤0.2, yaw≤1.0). Interrupt triggers when any exceeds a small per-axis deadzone, default `WALK_STICK_DEADZONE = {x:0.02, y:0.02, yaw:0.05}` (tunable). This avoids stick drift accidentally exiting idle.

## 5. AnimationPlayer — new module `mini_bdx_runtime/mini_bdx_runtime/animation_player.py`

**Pure Python, no hardware imports** (`json`, plain math only) → importable and testable off-Pi. It owns timeline logic only; the script owns hardware I/O.

### API
- `AnimationPlayer(animations_path)` — load + validate the JSON at construction (fail loud on parse/validation errors).
- `start(name, now)` — begin animation `name` at wall-clock `now`; reset edge-event state.
- `update(now) -> dict` — returns **data only**:
  ```python
  {
    "head":     [neck_pitch, head_pitch, head_yaw, head_roll] | None,  # radians (post load-clamp)
    "antennas": [left, right] | None,                                  # [-1, 1]
    "sounds":   ["happy1.wav", ...],   # events whose t passed since last update, fired once
    "lamp":     True | False | None,   # desired lamp state if it changed this tick
    "finished": bool,
  }
  ```
- `dpad_map -> {"up":name, "down":name, "left":name, "right":name}` (from the JSON).
- `has(name) -> bool`.

### Behavior
- **Head / antenna tracks:** linear interpolation between surrounding keyframes; before the first / after the last keyframe, hold the endpoint value. Omitted channel fields default to `0.0`. **Authoring rule:** every animation's final head keyframe must be neutral (`0,0,0,0`); since the player holds the endpoint after `duration`, an idle robot then rests with a neutral head — this is what "head eases to neutral when finished" (§4.2 step 8) resolves to, with no separate special-case.
- **Sounds / lamp:** discrete **edge-triggered** events — emitted exactly once when `elapsed` crosses their `t`. (Lamp emits its `state` only on change.)
- **Duration** = max `t` across all tracks. `finished` becomes true when `elapsed > duration` (non-looping). If `loop: true`, `elapsed` wraps modulo duration and edge-event state resets each wrap.
- The script applies returned data to hardware, **each channel guarded by its `duck_config` feature flag** — a robot without antennas/speaker/projector simply skips that channel; head motion always applies.

## 6. Safety layer (motor protection) — REQUIRED

Defense-in-depth, enforced in **code** (the JSON is treated as untrusted/editable input). Applies to the 4 head joints + antennas only; legs are policy-controlled and untouched.

1. **Hard position clamps** — code constants, matching the controller's known-safe teleop ranges (radians):
   ```
   HEAD_LIMITS = {
     neck_pitch: [-0.34, 1.1],
     head_pitch: [-0.78, 0.3],
     head_yaw:   [-0.5,  0.5],
     head_roll:  [-0.5,  0.5],
   }
   ```
   Final head targets are clamped to these every tick, in **both** modes (also protects walk-mode head overlay).
2. **Load-time validation** — on load, any head keyframe value outside `HEAD_LIMITS` is **clamped and a warning printed** (with animation name + joint + value). A malformed/over-range JSON edit can never drive a servo into a hard stop. Unknown sound filenames and unknown animation names referenced by `dpad_map` are also warned about at load.
3. **Slew-rate limiter** — head targets change by at most `MAX_HEAD_DELTA = MAX_HEAD_VELOCITY / control_freq` per tick (default `MAX_HEAD_VELOCITY = 4.0 rad/s` → 0.08 rad/tick at 50 Hz; below the leg `max_motor_velocity` of 5.24 rad/s). Applied vs the previous tick's head target, so it smooths keyframe gaps **and** the WALK↔ANIMATION transitions (no sudden lurch on enter/exit). Head joints already run at low kp (8) for compliance; this adds a hard kinematic ceiling on top.
4. **Antenna clamp** — player clamps to `[-1, 1]` (the `Antennas` driver also clamps the resulting duty cycle to `[3277, 6553]`).
5. **Conservative authoring** — the 4 starter animations stay comfortably inside `HEAD_LIMITS`, never at the edges.

All safety constants live at the top of the script as named, tunable constants.

## 7. Data format — `scripts/animations.json`

```jsonc
{
  "dpad": { "up": "nod_yes", "down": "shake_no", "left": "look_around", "right": "happy_dance" },
  "animations": {
    "nod_yes": {
      "loop": false,
      "head": [
        { "t": 0.0 },
        { "t": 0.35, "head_pitch": 0.30 },
        { "t": 0.70, "head_pitch": -0.10 },
        { "t": 1.05, "head_pitch": 0.30 },
        { "t": 1.40, "head_pitch": 0.0 }
      ],
      "antennas": [
        { "t": 0.0, "left": 0.0, "right": 0.0 },
        { "t": 0.35, "left": 0.6, "right": 0.6 },
        { "t": 1.40, "left": 0.0, "right": 0.0 }
      ],
      "sounds": [ { "t": 0.0, "name": "happy1.wav" } ],
      "lamp":   [ { "t": 0.0, "state": true }, { "t": 1.40, "state": false } ]
    }
    // shake_no, look_around, happy_dance ...
  }
}
```

- Head keyframe fields: any subset of `neck_pitch, head_pitch, head_yaw, head_roll` (missing → 0.0).
- Both the **animations** and the **dpad→animation mapping** are data-driven and editable.

### 7.1 Starter animations (all within `HEAD_LIMITS`, tunable)
| dpad | name | head | ears | sound | lamp |
|---|---|---|---|---|---|
| ▲ up | `nod_yes` | double pitch nod | perk up | `happy1.wav` | on→off |
| ▼ down | `shake_no` | yaw shake L/R | droop | `beep2.wav` | — |
| ◄ left | `look_around` | slow yaw pan L↔R + roll tilt | asymmetric wiggle | `motor.wav` | on for duration |
| ► right | `happy_dance` | pitch bob + roll wiggle | fast flaps | `happy2.wav` | blink ×2–3 |

Available wavs: `beep1, beep2, happy1, happy2, happy3, lamp, lamp2, lamp3, motor` (`mini_bdx_runtime/assets/`).

## 8. Controller changes (additive, backward-compatible)

The controller currently reads only `get_hat(0)[1]` (up/down). Left/right is not captured. Changes:

- **`xbox_controller.py`**: also read `left_right = get_hat(0)[0]`; thread it through `get_commands()` → `get_last_command()` and into `self.buttons.update(...)` as `dpad_left = (left_right == -1)`, `dpad_right = (left_right == 1)`. **The public `get_last_command()` return stays the identical 4-tuple** `(last_commands, buttons, left_trigger, right_trigger)` — the new dpad state lives inside `buttons`. No existing caller (`v2_rl_walk_mujoco.py`, `head_puppet.py`, `antennas_controller_test.py`) is affected.
- **`buttons.py`**: add `self.dpad_left = Button()`, `self.dpad_right = Button()`; extend `Buttons.update(...)` with `dpad_left=False, dpad_right=False` (**default args** → existing 8-arg callers keep working).

## 9. Files

**New**
- `scripts/v2_rl_walk_mujoco_anim.py` — alternate entrypoint (copy of v2 + mode state machine + safety layer).
- `mini_bdx_runtime/mini_bdx_runtime/animation_player.py` — `AnimationPlayer` (pure, hardware-free) + a `__main__` self-test.
- `scripts/animations.json` — the 4 starter animations + dpad map.

**Modified (additive)**
- `mini_bdx_runtime/mini_bdx_runtime/xbox_controller.py`
- `mini_bdx_runtime/mini_bdx_runtime/buttons.py`

## 10. Edge cases & error handling

- **Missing/invalid `animations.json`** → fail loudly at startup with a clear message (don't start the robot with broken animations). CLI flag `--animations_path` (default `./animations.json`, i.e. run from `scripts/`).
- **Feature disabled** (antennas/speaker/projector off in `duck_config`) → that channel is skipped; head animation still plays.
- **Sound retrigger spam** → edge-fired once per crossing; respects the existing `Sounds` 0-arg `play(name)` (non-blocking).
- **Lamp state** tracked against `projector.on`; only call `projector.switch()` when desired ≠ current (no method added to `projector.py`).
- **dpad + stick same tick** → dpad wins that tick (suppresses interrupt) to avoid flicker.
- **Animation finished, still idle** → head eases to neutral via slew limiter; stay in ANIMATION until dpad/stick.
- **Shutdown (Ctrl-C)** → existing teardown (antennas/eyes/projector/feet) is preserved; lamp forced off.

## 11. Verification

- **Off-Pi (laptop):** `python -m mini_bdx_runtime.animation_player` self-test loads `animations.json`, validates against `HEAD_LIMITS`, samples each animation timeline at 50 Hz, and prints head/antenna/sound/lamp + asserts every emitted head target is within bounds and within the slew limit. Proves the safety layer and timeline logic without hardware.
- **On-Pi (robot):** run `scripts/v2_rl_walk_mujoco_anim.py` with the ONNX policy; verify walk → each dpad animation → stick-resume, with no fall and no servo slamming. Recommend starting with the robot held/supported for the first run.

## 12. Out of scope (YAGNI)

- No new sounds/assets; reuse existing wavs.
- No easing curves beyond linear interpolation (slew limiter handles smoothness).
- No leg/locomotion animation (legs stay policy-controlled).
- No GUI/animation editor; JSON is hand-edited.
- Camera/microphone untouched.
