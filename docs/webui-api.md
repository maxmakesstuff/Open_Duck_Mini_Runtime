# Duck Web UI — HTTP API contract

The on-robot server (`mini_bdx_runtime/web_control.py`, stdlib `ThreadingHTTPServer`,
default port **8080**) serves the phone UI and a small REST API. The page is a single
self-contained file `mini_bdx_runtime/webui/index.html` (inline CSS+JS, **no external
resources** — must work on the robot's offline Wi-Fi/AP). Design for low CPU: poll state
at ~4 Hz, send commands only on change / at ~10 Hz while a stick is held.

## Endpoints

### `GET /`
Returns `index.html` (text/html).

### `GET /offsets`
Returns `offsets.html`, the joint-offset and IMU-trim calibration page (also self-contained).

### `GET /api/state`  → JSON snapshot (poll ~4 Hz)
```json
{
  "mode": "walk",                     // "walk" | "head_puppet"
  "paused": false,
  "battery": {"voltage": 7.9, "percent": 68, "charging": false},   // any field may be null
  "imu": {"pitch": 1.2, "roll": -0.4},                             // degrees; may be null
  "loop_hz": 49.7,
  "temp_c": 41.0,                     // hottest servo, or null
  "fallen": false,
  "recording": {"state": "idle", "frames": 0, "seconds": 0.0},     // state: idle|recording|playing
  "features": {"antennas": true, "projector": true, "speaker": true, "camera": true},
  "flags": {"projector_on": false, "head_control": false, "sprint": false, "tracking": false},
  "gait_offset": 0.0,                 // walk only
  "sounds": ["beep1.wav", "happy1.wav"],
  "uptime_s": 123.4,
  "message": "",                      // optional transient status line to show the user
  "imu_trim": {"pitch": 0.012, "roll": -0.02,                      // radians, live; null in head puppet
               "saved": {"pitch": 0.01, "roll": -0.02}},           // what duck_config holds
  "joint_offsets": {                  // walk only; null in head puppet
    "offsets": {"left_knee": 0.124},  // accepted targets, all 14 joints, radians
    "saved": {"left_knee": 0.124},    // what duck_config holds
    "limit": 0.5,                     // edit window; widened per joint to include its saved value
    "holding": false,                 // edits are only accepted while true
    "moving": false,                  // the stance glide or an offset slew is still running
    "dirty": false,                   // offsets != saved
    "error": ""                       // why the last save failed, else empty
  }
}
```

### Stability suite: more of `GET /api/state` (walk mode)
```json
{
  "activity": "walk",        // walk|paused|stance|reflex|recover|getup|balance|switch
  "soft": false,             // servo stiffness currently lowered (after a soft landing)
  "tilt_deg": 4.2,           // fused gyro+accel lean, degrees
  "policy": {"active": "stock", "pending": null, "state": "idle", "message": "",
             "slots": [{"id": "stock", "name": "Stock walk", "kind": "walk",
                        "summary": "…", "license": "", "usable": true, "error": "",
                        "defaults": {}}]},        // state: idle|loading|ready|error
  "safety": {"mode": "observe", "phase": "armed",  // phase: armed|tuck|soft|down
             "predicted_deg": 6.1, "peak_tilt_deg": 14.0, "peak_predicted_deg": 31.0,
             "triggers": 0, "last_trigger": null, "config": {"min_tilt_deg": 20.0}},
  "getup": {"enabled": false, "auto": false, "policy": "dk5426-getup-real2",
            "ready": false, "note": "", "has_reference": false, "attempts": 0,
            "run": null, "config": {"blend": 0.5}},
  "blackbox": {"enabled": true, "taking": false, "take_s": 0.0, "saved": 0,
               "last_file": "", "error": "", "pending": 0},
  "loop": {"late_pct": 0.4, "late_total": 3, "worst_ms": 31.0, "read_failures": 0, "ticks": 9000},
  "command_slew": {"enabled": false, "lin_accel": 0.3, "ang_accel": 4.0, "stop_factor": 3.0},
  "servo_voltages": {"left_knee": 7.5}   // last reading taken while paused; {} until then
}
```

Their settings, all over `POST /api/setting` (same "queued, not applied" rule as below):

```json
{"group":"policy","key":"active","value":"dk5426-walk"}   // load, then swap at a standstill
{"group":"safety","key":"mode","value":"active"}          // off | observe | active
{"group":"safety","key":"min_tilt_deg","value":18}        // any key of safety.config
{"group":"safety","key":"reset_peaks","value":true}       // also clears the loop counters
{"group":"safety","action":"save"}                        // -> duck_config "fall_reflex"
{"group":"getup","key":"enabled","value":true}            // also: auto, policy, any config key
{"group":"getup","key":"start","value":true}              // only from a standstill
{"group":"getup","key":"abort","value":true}
{"group":"getup","key":"capture_reference","value":true}  // needs a held stance, duck still
{"group":"getup","action":"save"}                         // -> duck_config "getup"
{"group":"recorder","key":"mark","value":true}            // save the last seconds
{"group":"recorder","key":"take","value":true}            // false stops and saves
{"group":"walk","key":"slew_enabled","value":true}        // also slew_lin_accel, slew_ang_accel
```

`{"group":"safety","action":"reset"}` and `{"group":"getup","action":"reset"}` restore the
defaults live (not saved until Save).

### `GET /api/logs`  → `{"logs":[{"name":"…_fall.npz","bytes":148220,"mtime":…}]}`
Black-box recordings in `~/duck_logs`, newest first. `GET /api/logs/<name>` downloads one.
Only plain `.npz` names inside that folder are served.

### Joint offsets, over `POST /api/setting`
A POST only queues the request for the control loop, so `{"ok":true}` means "received", not
"applied". The loop's answer is the next `joint_offsets` telemetry: a refused edit simply
never appears in `offsets`.

```json
{"group":"stance","key":"hold","value":true}            // pause + glide to the standing pose
{"group":"joint_offsets","key":"left_knee","value":0.13} // needs holding; slews, never steps
{"group":"joint_offsets","action":"reset"}               // needs holding; glide back to saved
{"group":"joint_offsets","action":"save"}                // write joints_offsets, backup first
```

Resuming the walk (button `A`) ends the hold. Unsaved offsets stay active until restart.

Every POST body must be a JSON object of finite numbers: `NaN`, `Infinity` and `1e999` are
a `400` on all routes, because such a value would flow through the observation into the
motor targets.

### `POST /api/command`  → set the web "virtual sticks"
Body (all fields optional; sticks/triggers are **normalized to the same convention as the
Xbox sticks**: left-stick up = +l_y forward, left-stick right = +l_x; the server applies
the identical walk/head mapping the gamepad uses):
```json
{"active": true, "l_x": 0.0, "l_y": 0.4, "r_x": 0.0, "r_y": 0.0,
 "left_trigger": 0.0, "right_trigger": 0.0}
```
- `active`: while `true`, the web stick **overrides** the gamepad command vector; set
  `false` (or stop posting) on release to hand control back to the gamepad. This is what
  lets web + gamepad run in parallel and swap freely.
- Send at ~10 Hz while dragging; send one `{"active": false}` on release.
- Response: `{"ok": true}`.

### `POST /api/button`  → momentary / held button events
```json
{"button": "A", "action": "press"}     // action: "press" (tap) | "down" | "up"
```
- Valid buttons: `A B X Y LB RB dpad_up dpad_down dpad_left dpad_right`.
- `press` = a tap (edge). `down`/`up` = held state, needed for **LB sprint** (hold) and
  **DPAD-LEFT record** (hold 3 s). For a plain tap use `press`.
- Button meaning is **mode-dependent** (see below). The server routes edges into the same
  `Buttons` edge-detector the gamepad feeds, so held/triggered semantics match exactly.
- Response: `{"ok": true}`.

## Button map (what the UI should render per mode)

### Walk mode (`mode == "walk"`)
| Control | UI element | Command |
|---|---|---|
| Left stick | joystick | forward/back (l_y) + strafe (l_x) |
| Right stick X | joystick / slider | turn (r_x) |
| A | button | pause / resume |
| X | button | scanner light on/off (+ scanner sound) |
| B | button | random sound |
| Y | button | toggle head-control mode |
| LB | **hold** button | sprint (faster gait) |
| DPAD up / down | buttons | gait frequency ± 0.05 |
| DPAD left | **hold 3 s** | record whole-body command track |
| DPAD right | button | play / stop recorded track |
| L / R triggers | sliders | antennas |

### Head-puppet mode (`mode == "head_puppet"`)
| Control | UI element | Command |
|---|---|---|
| Left stick | joystick | head yaw (l_x) + head pitch (l_y) |
| Right stick X | joystick / slider | head roll (r_x) |
| B | button | random sound |
| X | button | scanner light on/off (+ scanner sound) |
| DPAD up | button | face-tracking on/off |
| DPAD left | **hold 3 s** | record idle |
| DPAD right | button | play / stop idle |
| L / R triggers | sliders | antennas |

## Design brief for the page
- Mobile-first, single screen, big touch targets, no scrolling needed in either panel.
- Dark theme with a warm, friendly duck accent (amber/orange on charcoal); tasteful, not
  templated. A small battery gauge + charging bolt, a mode pill, a "paused"/"fallen"
  banner. Render the walk panel or head-puppet panel from `mode`.
- No external libraries/fonts. Hand-roll the virtual joysticks with pointer events.
- Show a clear connection indicator (last successful `/api/state`); if polling fails,
  grey out controls and show "reconnecting…".
