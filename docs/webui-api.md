# Duck Web UI — HTTP API contract

The on-robot server (`mini_bdx_runtime/web_control.py`, stdlib `ThreadingHTTPServer`,
default port **8080**) serves the phone UI and a small REST API. The page is a single
self-contained file `mini_bdx_runtime/webui/index.html` (inline CSS+JS, **no external
resources** — must work on the robot's offline Wi-Fi/AP). Design for low CPU: poll state
at ~4 Hz, send commands only on change / at ~10 Hz while a stick is held.

## Endpoints

### `GET /`
Returns `index.html` (text/html).

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
  "message": ""                       // optional transient status line to show the user
}
```

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
