#!/usr/bin/env python3
"""Dev mock for the Open Duck Mini web UI.

Stdlib only. Serves ``index.html`` at ``/`` and a fake ``/api/state`` whose
values drift over time so the phone UI can be previewed on a laptop with no
robot attached. POSTs to ``/api/command`` and ``/api/button`` return
``{"ok": true}``, get printed, and nudge the fake state (e.g. tapping X flips
the projector flag) so live-flag highlighting can be exercised too.

    python3 mock_server.py                 # cycles walk <-> head_puppet
    python3 mock_server.py --mode walk     # force a mode
    python3 mock_server.py --port 9000
"""
import argparse
import json
import math
import os
import struct
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "index.html")
START = time.time()

# mutable bits toggled by button POSTs so the UI's flag highlighting is testable
FLAGS = {"projector_on": False, "head_control": False, "sprint": False, "tracking": False}
REC = {"state": "idle", "frames": 0, "seconds": 0.0, "t0": 0.0}
PAUSED = {"v": False}
GAIT = {"v": 0.0}
TRIM = {"pitch": 0.0, "roll": 0.0}   # radians; nudged by /api/trim
FORCE_MODE = None  # set from --mode

# live-tunable mocks, mutated by /api/setting so the TUNE panel round-trips
WALK = {"action_scale": 0.23, "action_scale_target": 0.23, "velocity_clip": True,
        "max_motor_velocity_rad_s": 5.24,
        "governor": {"enabled": True, "tilt_lo_deg": 8.0, "tilt_hi_deg": 22.0,
                     "rate_lo": 1.5, "rate_hi": 5.0, "floor": 0.2, "smooth": 0.3}}
CAM = {"ae": True, "awb": True, "exposure": 10000, "gain": 1.0, "brightness": 0.0,
       "contrast": 1.0, "saturation": 1.0, "sharpness": 1.0}
ANT = {"free_anim": True, "sync": False}


def _png(w, h, pix):
    """Minimal RGB PNG encoder (stdlib only) for the fake camera preview. The UI's
    <img> renders by Content-Type, so a PNG stands in fine for the robot's JPEG."""
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # filter type 0
        for x in range(w):
            r, g, b = pix(x, y)
            raw += bytes((r & 255, g & 255, b & 255))

    def chunk(typ, data):
        c = typ + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def _camera_frame():
    """A moving test pattern tinted by the current mock camera controls."""
    t = time.time() - START
    bright = CAM["brightness"]
    gain = CAM["gain"]

    def pix(x, y):
        base = int((x + y) / 240.0 * 255)
        band = int(127 + 127 * math.sin(x * 0.06 + t * 3))
        r = clamp(int((band + bright * 120) * (gain / 2)), 0, 255)
        g = clamp(int((base + bright * 120)), 0, 255)
        b = clamp(int((255 - base)), 0, 255)
        return (r, g, b)

    return _png(120, 120, pix)


def _mode(t):
    if FORCE_MODE:
        return FORCE_MODE
    # slow swap so a previewer sees both panels (~14 s each)
    return "head_puppet" if int(t // 14) % 2 else "walk"


def build_state():
    t = time.time() - START
    mode = _mode(t)

    # battery: slow discharge sawtooth with an occasional charging window
    charging = (int(t // 20) % 3 == 0)
    volt = 8.4 - (t % 40) / 40 * 1.0            # 8.4 -> 7.4
    if charging:
        volt = 7.4 + (t % 20) / 20 * 1.0
    pct = int(clamp((volt - 6.6) / (8.4 - 6.6) * 100, 0, 100))

    # attitude wobble; brief "fall" every ~35 s so the horizon alarm shows
    fallen = (t % 35) > 32
    if fallen:
        pitch, roll = 47.0, 33.0
    else:
        pitch = 9 * math.sin(t * 0.9)
        roll = 14 * math.sin(t * 0.6 + 1)

    # recording auto-finalizes 3 s after DPAD-left down
    if REC["state"] == "recording":
        REC["seconds"] = time.time() - REC["t0"]
        REC["frames"] = int(REC["seconds"] * 50)

    return {
        "mode": mode,
        "paused": PAUSED["v"],
        "battery": {"voltage": round(volt, 2), "percent": pct, "charging": charging},
        "imu": {"pitch": round(pitch, 1), "roll": round(roll, 1)},
        "imu_trim": {"pitch": round(TRIM["pitch"], 4), "roll": round(TRIM["roll"], 4)},
        # governor enabled so the readout is exercised: severity tracks the wobble,
        # scale = 1 - 0.8*severity (floor 0.2), so it dips + warns as the duck tips.
        "governor": (lambda sev: {
            "enabled": True, "severity": round(sev, 3),
            "scale": round(1.0 - 0.8 * sev, 3),
        })(clamp((math.hypot(pitch, roll) - 8) / 14, 0, 1)),
        "loop_hz": round(49.6 + 0.3 * math.sin(t * 3), 1),
        "temp_c": round(38 + 6 * (0.5 + 0.5 * math.sin(t * 0.15)), 1),
        "fallen": fallen,
        "recording": {"state": REC["state"], "frames": REC["frames"], "seconds": round(REC["seconds"], 1)},
        "features": {"antennas": True, "projector": True, "speaker": True, "camera": True},
        "flags": dict(FLAGS),
        "gait_offset": round(GAIT["v"], 2),
        "walk_params": {
            **{k: WALK[k] for k in ("action_scale", "action_scale_target",
                                    "velocity_clip", "max_motor_velocity_rad_s")},
            "gait_offset": round(GAIT["v"], 3),
            "governor": dict(WALK["governor"]),
        },
        "camera": {"available": True, "controls": dict(CAM)},
        "antenna_anim": ANT["free_anim"],
        "antenna_sync": ANT["sync"],
        "sounds": ["beep1.wav", "happy1.wav", "sad1.wav", "quack.wav"],
        "uptime_s": round(t, 1),
        "message": "mock server — no hardware attached",
    }


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def handle_button(b):
    name, action = b.get("button"), b.get("action")
    if name == "X" and action == "press":
        FLAGS["projector_on"] = not FLAGS["projector_on"]
    elif name == "Y" and action == "press":
        FLAGS["head_control"] = not FLAGS["head_control"]
    elif name == "A" and action == "press":
        PAUSED["v"] = not PAUSED["v"]
    elif name == "LB":
        FLAGS["sprint"] = (action == "down")
    elif name == "dpad_up" and action == "press":
        GAIT["v"] = clamp(GAIT["v"] + 0.05, -1, 1)
        FLAGS["tracking"] = not FLAGS["tracking"]  # head-mode: toggles tracking
    elif name == "dpad_down" and action == "press":
        GAIT["v"] = clamp(GAIT["v"] - 0.05, -1, 1)
    elif name == "dpad_left":
        if action == "down":
            REC.update(state="recording", frames=0, seconds=0.0, t0=time.time())
        elif action == "up":
            REC["state"] = "idle"
    elif name == "dpad_right" and action == "press":
        REC["state"] = "idle" if REC["state"] == "playing" else "playing"


def handle_setting(body):
    group = body.get("group")
    if body.get("action") == "save":
        print("SETTING save", group)
        return
    if body.get("action") == "reset":
        if group == "walk":
            WALK.update({"action_scale": 0.23, "action_scale_target": 0.23,
                         "velocity_clip": True, "max_motor_velocity_rad_s": 5.24})
            WALK["governor"] = {"enabled": True, "tilt_lo_deg": 10.0, "tilt_hi_deg": 25.0,
                                "rate_lo": 2.5, "rate_hi": 7.0, "floor": 0.35, "smooth": 0.3}
            GAIT["v"] = -0.10
        elif group == "imu_trim":
            TRIM["pitch"] = 0.0
            TRIM["roll"] = 0.0
        elif group == "camera":
            CAM.update({"ae": True, "awb": True, "exposure": 10000, "gain": 1.0,
                        "brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "sharpness": 1.0})
        elif group == "antenna":
            ANT.update({"free_anim": True, "sync": False})
        print("SETTING reset", group)
        return
    key, val = body.get("key"), body.get("value")
    if group == "walk":
        if key == "gait_offset":
            GAIT["v"] = float(val)
        elif key == "action_scale":
            WALK["action_scale_target"] = WALK["action_scale"] = float(val)
        elif key.startswith("governor_"):
            gk = key[len("governor_"):]
            WALK["governor"][gk] = bool(val) if gk == "enabled" else float(val)
        elif key in WALK:
            WALK[key] = val
    elif group == "camera" and key in CAM:
        CAM[key] = val
    elif group == "antenna" and key in ANT:
        ANT[key] = bool(val)
    print("SETTING", group, key, val)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                with open(INDEX, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(500, "index.html not found next to mock_server.py", "text/plain")
        elif path == "/api/state":
            self._send(200, json.dumps(build_state()))
        elif path == "/api/camera/frame.jpg":
            self._send(200, _camera_frame(), "image/png")
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {"_raw": raw.decode("utf-8", "replace")}
        path = self.path.split("?", 1)[0]
        if path == "/api/button":
            handle_button(body)
            print("BUTTON", body)
        elif path == "/api/trim":
            if body.get("action") == "save":
                print("TRIM save", TRIM)
            else:
                ax = body.get("axis")
                if ax in TRIM:
                    TRIM[ax] = clamp(TRIM[ax] + float(body.get("delta", 0.0)), -0.1, 0.1)
                print("TRIM", TRIM)
        elif path == "/api/setting":
            handle_setting(body)
        elif path == "/api/command":
            if body.get("active"):
                print("COMMAND", {k: body.get(k) for k in
                      ("l_x", "l_y", "r_x", "left_trigger", "right_trigger")})
            else:
                print("COMMAND release")
        else:
            self._send(404, json.dumps({"error": "not found"}))
            return
        self._send(200, json.dumps({"ok": True}))

    def log_message(self, *a):
        pass  # keep the console clean; we print the interesting POSTs ourselves


def main():
    global FORCE_MODE
    ap = argparse.ArgumentParser(description="Mock server for the Open Duck Mini web UI.")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--mode", choices=["walk", "head_puppet"], default=None,
                    help="force a mode instead of cycling")
    args = ap.parse_args()
    FORCE_MODE = args.mode

    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"Open Duck Mini mock UI  ->  http://localhost:{args.port}/")
    print(f"  mode: {args.mode or 'cycling walk <-> head_puppet'}   (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
        srv.shutdown()


if __name__ == "__main__":
    main()
