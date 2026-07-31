# Open Duck Mini — User Guide

This guide covers every way to operate the robot: the two on-robot programs, the full
**Xbox gamepad map** for each, and the **phone Web UI** (including live tuning).

> **The robot has two programs** — **Walk mode** and **Head-puppet mode**. They're
> started separately, and the gamepad is mapped **differently** in each. The two big
> tables below show exactly which button does what, where.

---

## Modes at a glance

| Program | What it does | Start |
|---|---|---|
| **Walk mode** | The robot walks (the AI locomotion policy). Drive, sprint, cadence, record, control the head. | `v2_rl_walk_mujoco.py` |
| **Head-puppet mode** | Legs hold still; you puppet the **head**. Includes **idle recording** and **face tracking** + a live camera view. | `head_puppet.py` |

Both programs also serve a **phone Web UI** (see [section 3](#3-the-phone-web-ui)).

### Choosing a mode with the foot switches (flashed image / auto-start)

On the flashable image the duck auto-starts and lets you pick the mode with the **foot
switches** — no terminal:

- 🦶 **Hold the RIGHT foot switch (~5 s)** → **Walk mode**.
- 🦶 **Hold the LEFT foot switch (~5 s)** → **Head-puppet mode**.

Power on, let it boot, then press and hold the foot for the mode you want for about 5
seconds until it springs to life (it re-checks the switches every ~5 s). If you hold no
foot, it just waits. To switch modes, restart and hold the other foot.

### Starting the robot from source (terminal on the robot)

```bash
cd ~/Open_Duck_Mini_Runtime/scripts

# Walk mode (the model path is required):
python v2_rl_walk_mujoco.py --onnx_model_path <path>/BEST_WALK_ONNX_2.onnx

# Head-puppet mode:
python head_puppet.py
```

On start the robot first moves into its standing pose (let it settle). In walk mode it
starts **paused** by default — press **A** to unpause.

---

## 1. Walk mode

### Driving (left + right stick)
- **Left stick up/down** → forward / backward
- **Left stick left/right** → strafe sideways
- **Right stick left/right** → turn (in place / through a curve)

### Cadence — **D-pad ↑ / ↓**
The **D-pad up/down** changes the **step frequency** (how fast the legs step) in steps
of ±0.05:
- **D-pad ↑** → faster stepping
- **D-pad ↓** → slower stepping (**calmer and more stable**)

> Slower cadence = noticeably steadier. The current value shows as **GAIT** in the Web UI.

### Sprint — **LB (hold)**
**Hold LB** for a faster gait, for as long as you hold it.

### Control the head — **Y**
**Y** toggles **head-control mode** (experimental): the sticks then steer the **head**
(pitch / yaw / roll) instead of driving. Press **Y** again to return to driving.

### Record & play back a run
The robot can record a driven sequence and loop it.

> **Reliable way to start a recording:**
> 1. Press **A** → **pause** the robot.
> 2. **Hold D-pad Left ~3 s** → recording starts (sound *happy1*).
> 3. Press **A** → unpause and **drive**. Everything is recorded.
> 4. **Tap D-pad Left** → **stop** recording (sound *beep2*). Stopping also works **while
>    walking**. The take is saved (`walk_recording.pkl`) and survives a restart.
>
> **Playback:** tap **D-pad Right** → the take loops (sound *beep1*). Stop with **D-pad
> Right** again, or simply **move a stick** (live control resumes instantly).

### Balance fine-tuning (IMU trim) — **RB + D-pad**
To trim balance while walking, **hold RB**; the D-pad then adjusts the tilt correction
(instead of cadence/recording):
- **RB + D-pad ↑ / ↓** → pitch trim (front/back)
- **RB + D-pad ← / →** → roll trim (left/right)
- **RB + Y** → **save** the current trim permanently

> While **RB** is held, cadence (D-pad ↑/↓) and record/playback (D-pad ←/→) are
> **disabled** so nothing double-fires.

### Other buttons
- **A** → pause / resume (the robot also **auto-pauses on a fall** — right it, then **A**).
- **X** → projector / "scanner LED" on/off.
- **B** → random sound.
- **LT / RT** → move the antennas.

### 📋 Gamepad map — Walk mode

| Button / combo | Function |
|---|---|
| Left stick ↑↓ | forward / backward |
| Left stick ←→ | strafe |
| Right stick ←→ | turn |
| **A** | pause / resume (also after a fall) |
| **X** | projector (scanner LED) on/off |
| **B** | random sound |
| **Y** | toggle head-control mode |
| **LB** (hold) | sprint (faster gait) |
| **LT / RT** | antennas |
| **D-pad ↑ / ↓** | cadence faster / slower (±0.05) |
| **D-pad ←** (hold 3 s) | start recording *(pause the robot first)* |
| **D-pad ←** (tap) | stop recording |
| **D-pad →** | start / stop playback |
| **RB + D-pad ↑ / ↓** | IMU trim: pitch |
| **RB + D-pad ← / →** | IMU trim: roll |
| **RB + Y** | save IMU trim |

---

## 2. Head-puppet mode

The legs hold their standing pose — you control only the **head**.

### Head & antennas
- **Left stick ←→** → head **yaw** (turn left/right)
- **Left stick ↑↓** → head **pitch** (up/down)
- **Right stick ←→** → head **roll** (tilt)
- **LT / RT** → antennas
- **B** → random sound
- **X** → projector / scanner LED on/off

### Idle recording (your own motion loop)
- **Hold D-pad Left ~3 s** → recording starts (sound *happy1*). It captures **head
  motion, antennas, sounds, and the projector**. Auto-stops after **60 s**, or **tap
  D-pad Left** to stop (sound *beep2*).
- **D-pad Right** → play the saved loop **forever** (sound *beep1*). Stop with **D-pad
  Right** again, or touch **any stick / trigger / button**.

### Face tracking — **D-pad ↑**
- **D-pad ↑** → face tracking: the head follows the **nearest face** (camera + OpenCV).
  - Hold a face **1.5 s** and the robot **greets** it (antenna wiggle + sound *happy2* +
    a ~5 s scanner "scan"); on **losing** the face it wiggles goodbye.
  - With **no face** in view it replays your **recorded idle loop**.
  - **Stop:** **D-pad ↑** again, or move a **stick**.
- Requires `camera: true` in the config and a connected camera. Without a camera the
  feature is simply disabled (no crash).

### 📋 Gamepad map — Head-puppet mode

| Button | Function |
|---|---|
| Left stick ←→ | head yaw (turn) |
| Left stick ↑↓ | head pitch |
| Right stick ←→ | head roll (tilt) |
| **LT / RT** | antennas |
| **B** | random sound |
| **X** | projector (scanner LED) on/off |
| **D-pad ↑** | face tracking on/off |
| **D-pad ←** (hold 3 s) | start idle recording |
| **D-pad ←** (tap) | stop recording |
| **D-pad →** | play / stop the idle loop |

> Head-puppet mode has **no** pause (A), **no** sprint (LB), and **no** cadence (D-pad
> ↑/↓ is used for tracking here).

---

## 3. The phone Web UI

Both programs also serve a small web app so you can control the robot **from your phone**
— on-screen joysticks and buttons, plus live attitude, battery, temperature, etc.

### Connect (no IP typing needed)
1. On your phone, join the robot's Wi-Fi (**"Openduck"**). The connection **stays up**
   (no more nagging "sign-in" popup).
2. Open **any `http://` address** in the browser — you're auto-forwarded to the control
   page. Or open **`http://10.42.0.1:8080`** directly.
3. Tip: use **"Add to Home Screen"** for one-tap access.

> Only **`http://`** works (not `https://`). The robot has no internet — that's normal.

The app auto-detects whether **Walk** or **Head-puppet** mode is running and shows the
matching controls.

### Readouts (both modes)
Top: artificial horizon (pitch/roll), battery (volts/percent), loop rate (Hz),
temperature, record status, uptime. In walk mode it also shows **GOV** (the stability
governor) when enabled.

### Walk mode in the app
- **DRIVE** (left on-screen joystick) = move, **TURN** (right) = turn
- Buttons: **Y** (head), **X** (scanner), **B** (sound), **A** (pause)
- D-pad: **GAIT+ / GAIT−** (cadence), **REC** (hold = record), **PLAY** (playback)
- **LB** (hold = sprint)
- **IMU-trim card** (pitch/roll ±, **Save**, and **↺** reset) for balance fine-tuning

### Head-puppet mode in the app
- On-screen joysticks steer the **head**
- Buttons: **X** (scanner), **B** (sound)
- D-pad: **TRACK** (face tracking), **REC** (hold = record), **PLAY** (playback)

> The gamepad and the app work **at the same time** — both drive the same robot.

### 🎛️ TUNE — live tuning
Tap **TUNE** (top right) for a panel where **every setting is live** and nothing is
saved until you press **Save**:

- **Walk mode** — sliders for **action scale**, gait offset, velocity clip, max motor
  velocity, and the full stability governor.
- **Head-puppet mode** — a **live camera view** plus **exposure, gain, brightness,
  contrast, saturation, sharpness and white-balance** controls.
- **Both modes** — ear-antenna **free animation** on/off and **ear-sync** on/off.

Two touches that make tuning easy:

- **ⓘ Tap-to-explain** — every control has a small **ⓘ**; tap the name to reveal what it
  does and what lower-vs-higher values change.
- **↺ Reset to defaults** — each section (walk / camera / antennas, and the IMU-trim
  card) has a reset button that restores the known-good defaults with one tap.

---

## Sound feedback

When a speaker is enabled, the robot acknowledges record/playback:

| Event | Sound |
|---|---|
| Recording **started** | `happy1` |
| Recording **stopped** / full | `beep2` |
| Playback **started** | `beep1` |
| Playback **stopped** (manual) | *(no sound)* |
| Fall detected | `beep2` |

---

## Cheat sheet

**Walk mode:** sticks = drive/turn · **A** = pause · **LB** = sprint · **D-pad ↑↓** =
cadence · **D-pad ←** (3 s) = record (pause first) · **D-pad →** = playback · **Y** =
head · **X** = scanner · **B** = sound · **RB + D-pad / Y** = balance trim.

**Head-puppet mode:** sticks = head · **D-pad ↑** = face tracking · **D-pad ←** (3 s) =
idle record · **D-pad →** = playback · **X** = scanner · **B** = sound · **LT/RT** =
antennas.

**Web UI:** join the "Openduck" Wi-Fi → `http://10.42.0.1:8080` (or any http page) →
Add to Home Screen. Tap **TUNE** for live sliders, **ⓘ** hints, and **↺** resets.
