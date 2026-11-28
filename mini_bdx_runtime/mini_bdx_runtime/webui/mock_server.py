#!/usr/bin/env python3
"""Dev mock for the Open Duck Mini web UI.

Stdlib only. Serves ``index.html`` at ``/``, ``offsets.html`` at ``/offsets`` and a
fake ``/api/state`` whose values drift over time so the phone UI can be previewed on
a laptop with no robot attached. The offsets page runs on the real OffsetTuner. POSTs to ``/api/command`` and ``/api/button`` return
``{"ok": true}``, get printed, and nudge the fake state (e.g. tapping X flips
the projector flag) so live-flag highlighting can be exercised too.

    python3 mock_server.py                 # cycles walk <-> head_puppet
    python3 mock_server.py --mode walk     # force a mode
    python3 mock_server.py --port 9000
"""
import argparse
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))
import head_life as _head_life  # noqa: E402  (the real animation engine)
import duck_voice as _duck_voice  # noqa: E402  (the real voice logic)
import json
import math
import os
import struct
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(HERE))  # append: the stdlib still wins any name clash
from offset_tuner import OffsetTuner  # noqa: E402
import fall_reflex  # noqa: E402
import getup as getup_mod  # noqa: E402
import command_shaper  # noqa: E402
import gamepad_pairing  # noqa: E402

INDEX = os.path.join(HERE, "index.html")
OFFSETS_PAGE = os.path.join(HERE, "offsets.html")
PREVIEW_PAGE = os.path.join(HERE, "preview.html")
VOICE_DIR = os.path.join(HERE, "..", "..", "assets", "voice")
START = time.time()

# mutable bits toggled by button POSTs so the UI's flag highlighting is testable
FLAGS = {"projector_on": False, "head_control": False, "sprint": False, "tracking": False}
REC = {"state": "idle", "frames": 0, "seconds": 0.0, "t0": 0.0}
PAUSED = {"v": False}
GAIT = {"v": 0.0}
TRIM = {"pitch": 0.0, "roll": 0.0}   # radians; nudged by /api/trim
TRIM_SAVED = {"pitch": 0.0, "roll": 0.0}
TUNER = OffsetTuner({
    "left_hip_yaw": -0.075, "left_hip_roll": 0.095, "left_hip_pitch": 0.084, "left_knee": 0.124,
    "left_ankle": -0.055, "neck_pitch": 0.104, "head_pitch": -0.104, "head_yaw": -0.117,
    "head_roll": 0.012, "right_hip_yaw": 0.041, "right_hip_roll": -0.027,
    "right_hip_pitch": -0.084, "right_knee": 0.034, "right_ankle": 0.017,
})
STANCE = {"pose": [0.3] * 14, "goal": [0.0] * 14, "last": time.time()}
FORCE_MODE = None  # set from --mode

# live-tunable mocks, mutated by /api/setting so the TUNE panel round-trips
WALK = {"action_scale": 0.23, "action_scale_target": 0.23, "velocity_clip": True,
        "max_motor_velocity_rad_s": 5.24,
        "governor": {"enabled": True, "tilt_lo_deg": 8.0, "tilt_hi_deg": 22.0,
                     "rate_lo": 1.5, "rate_hi": 5.0, "floor": 0.2, "smooth": 0.3}}
CAM = {"ae": True, "awb": True, "exposure": 10000, "gain": 1.0, "brightness": 0.0,
       "contrast": 1.0, "saturation": 1.0, "sharpness": 1.0}
ANT = {"free_anim": True, "sync": False}
HEAD = {"look": False, "look_scale": 1.0, "speed": 1.0, "busy": 1.0, "ears": True, "voice": True,
        "lead": False, "lead_s": 0.25}
HEAD_NOW = {"anim": ""}


class _Preview:
    """The REAL animation engine and voice logic, run in real time for /preview: what
    you see and hear there is what the duck would do with the same settings."""

    def __init__(self):
        self.engine = _head_life.HeadLife(HEAD)
        self.said = []
        self.voice = _duck_voice.DuckVoice(_duck_voice.load_manifest(VOICE_DIR), self.said.append)
        self.t = None
        self.lock = threading.Lock()

    def step(self, walking, turn):
        with self.lock:
            now = time.time()
            if self.t is None or now - self.t > 0.5:
                self.t = now - 0.02
            self.engine.set(HEAD)
            while self.t < now:
                self.t += 0.02
                self.engine.update(self.t, 0.02, walking=walking, turn=turn)
                v = self.engine.pop_voice()
                if v:
                    self.voice.say(v, self.t)
            said, self.said[:] = list(self.said), []
            HEAD_NOW["anim"] = self.engine.anim
            return {"head": self.engine.out, "ears": self.engine.ears, "anim": self.engine.anim,
                    "body": self.engine.body_scale, "said": said, "volume": SOUND["volume"]}

    def play(self, name):
        with self.lock:
            self.engine.play(name)

    def say(self, category):
        with self.lock:
            self.voice.say(category, time.time(), urgent=True)


PREVIEW = _Preview()
SOUND = {"ok": True, "volume": 1.0, "voice": True, "talk": True, "last": "",
         "categories": ["amazed", "babble", "beatbox", "curious", "excited", "happy", "hello",
                        "no", "notice", "sad", "scan", "worried", "yes"]}

# ---- stability suite mocks (policy slots, soft landing, get-up, recorder, health) ----
SLOTS = [
    {"id": "stock", "name": "Stock walk", "kind": "walk",
     "summary": "The policy the walk script was started with (BEST_WALK_ONNX_2.onnx).",
     "license": "",
     "usable": True, "error": "", "defaults": {}},
    {"id": "dk5426-walk", "name": "dk5426 walker", "kind": "walk",
     "summary": "Retrained walk. Tracks stick commands more closely in the authors' "
                "simulation and has run on their real duck.", "license": "Apache-2.0",
     "usable": True, "error": "", "defaults": {"action_scale": 0.25}},
    {"id": "broken-walk", "name": "Half-copied walk", "kind": "walk", "summary": "",
     "license": "", "usable": False, "error": "file missing: half_copied.onnx", "defaults": {}},
    {"id": "dk5426-getup-real2", "name": "dk5426 get-up (real2, 80M)", "kind": "getup",
     "summary": "Stands up from lying face-down. The file shown standing up a real duck "
                "on Discord (2026-11-23).", "license": "Apache-2.0", "usable": True,
     "error": "", "defaults": {}},
    {"id": "dk5426-getup-real", "name": "dk5426 get-up (real, 41M)", "kind": "getup",
     "summary": "Earlier get-up. Stood up 2 of 8 times in the authors' own robot logs.",
     "license": "Apache-2.0", "usable": True, "error": "", "defaults": {}},
]
POLICY = {"active": "stock", "pending": None, "state": "idle", "message": "", "t": 0.0}
REFLEX = fall_reflex.FallReflex()
GETUP = getup_mod.clean_config({})
GETUP_RUN = {"t0": None, "result": None}
SLEW = command_shaper.clean_config({})
REST = {"enabled": False}
BLACKBOX = {"saved": 0, "last_file": "", "take_t0": None}
NOTE = {"text": "", "until": 0.0}
FAKE_LOGS = [
    {"name": "20261124_211502_fall.npz", "bytes": 148_220, "mtime": 0},
    {"name": "20261124_210933_reflex_observed.npz", "bytes": 151_004, "mtime": 0},
    {"name": "20261124_210210_take_dk5426-walk.npz", "bytes": 612_480, "mtime": 0},
    {"name": "20261124_205847_take_stock.npz", "bytes": 598_112, "mtime": 0},
]
FORCE_FALL = {"v": None}   # --fallen: True = always fallen, False = never, None = cycle


def note(text, seconds=5.0):
    NOTE["text"], NOTE["until"] = text, time.time() + seconds


def _suite_state(t, pitch, roll, fallen):
    now = time.time()
    if POLICY["pending"] and now - POLICY["t"] > 0.8:
        POLICY["state"] = "ready"
    if POLICY["pending"] and now - POLICY["t"] > 2.0:
        name = next(x["name"] for x in SLOTS if x["id"] == POLICY["pending"])
        POLICY.update(active=POLICY["pending"], pending=None, state="idle")
        note(f"Now running: {name}")
    tilt = math.hypot(pitch, roll)
    REFLEX.peak_tilt_deg = max(REFLEX.peak_tilt_deg, min(tilt, 19.0))
    REFLEX.peak_predicted_deg = max(REFLEX.peak_predicted_deg, min(tilt * 1.9, 38.0))
    activity = "paused" if PAUSED["v"] else "walk"
    run = None
    if GETUP_RUN["t0"] is not None:
        el = now - GETUP_RUN["t0"]
        if el < 4.0:
            activity, run = "getup", {"phase": "run", "result": "", "elapsed_s": round(el, 1),
                                      "upright": round(min(1.0, el / 4), 2)}
        elif el < 6.0:
            activity, run = "balance", {"phase": "done", "result": "standing",
                                        "elapsed_s": 4.0, "upright": 0.97}
        else:
            GETUP_RUN["t0"], GETUP_RUN["result"] = None, "standing"
            FORCE_FALL["v"] = False
            PAUSED["v"] = False
    elif POLICY["pending"]:
        activity = "switch" if not PAUSED["v"] else activity
    if run is None and GETUP_RUN["result"]:
        run = {"phase": "done", "result": GETUP_RUN["result"], "elapsed_s": 4.0, "upright": 0.97}
    volts = {j: round(7.6 - 0.02 * i + (0.0 if j != "right_knee" else -0.7), 2)
             for i, j in enumerate(TUNER.snapshot()["offsets"])} if PAUSED["v"] else {}
    take = BLACKBOX["take_t0"]
    return {
        "activity": activity, "soft": bool(fallen and REFLEX.mode == "active"),
        "tilt_deg": round(tilt, 1),
        "policy": {"active": POLICY["active"], "pending": POLICY["pending"],
                   "state": POLICY["state"], "message": POLICY["message"], "slots": SLOTS},
        "safety": dict(REFLEX.public(), triggers=2,
                       last_trigger={"tilt": 27.5, "predicted": 61.0, "rate": 134.0, "t": 0}),
        "getup": {"enabled": GETUP["enabled"], "auto": GETUP["auto"], "policy": GETUP["policy"],
                  "ready": GETUP["enabled"], "note": "",
                  "has_reference": GETUP["imu_standing_acc"] is not None, "attempts": 0,
                  "run": run, "config": {k: GETUP[k] for k in getup_mod.LIMITS}},
        "blackbox": {"enabled": True, "taking": take is not None,
                     "take_s": round(now - take, 1) if take else 0.0,
                     "saved": BLACKBOX["saved"], "last_file": BLACKBOX["last_file"],
                     "error": "", "pending": 0},
        "loop": {"late_pct": round(0.6 + 0.5 * math.sin(t * 0.3), 1), "late_total": 14,
                 "worst_ms": 41.0, "read_failures": 0, "ticks": int(t * 50),
                 "imu_repeat_pct": 6.0, "imu_longest_repeat": 2, "imu_glitches": 12},
        "command_slew": dict(SLEW),
        "rest": {"enabled": REST["enabled"],
                 "resting": bool(REST["enabled"] and not PAUSED["v"] and not fallen)},
        "servo_voltages": volts,
    }


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
    fallen = (t % 35) > 32 if FORCE_FALL["v"] is None else FORCE_FALL["v"]
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
        "imu_trim": {"pitch": round(TRIM["pitch"], 4), "roll": round(TRIM["roll"], 4),
                     "saved": {"pitch": round(TRIM_SAVED["pitch"], 4),
                               "roll": round(TRIM_SAVED["roll"], 4)}},
        "joint_offsets": _tick_offsets() if mode == "walk" else None,
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
            "steer_trim": round(WALK.get("steer_trim", 0.0), 3),
            "governor": dict(WALK["governor"]),
        },
        "camera": {"available": True, "controls": dict(CAM)},
        "head_life": dict(HEAD, active=bool(HEAD["look"] or HEAD["lead"]), anim=HEAD_NOW["anim"],
                          animations=sorted(_head_life.ANIMATIONS)),
        "sound": dict(SOUND),
        "antenna_anim": ANT["free_anim"],
        "antenna_sync": ANT["sync"],
        "sounds": ["beep1.wav", "happy1.wav", "sad1.wav", "quack.wav"],
        "uptime_s": round(t, 1),
        "message": NOTE["text"] if time.time() < NOTE["until"] else "",
        **_suite_state(t, pitch, roll, fallen),
    }


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _tick_offsets():
    """Mirrors the walk loop: offsets only move on ticks that would write to the servos."""
    now = time.time()
    dt, STANCE["last"] = now - STANCE["last"], now
    if not PAUSED["v"]:
        TUNER.release()
        STANCE["pose"] = [0.3] * 14       # "mid-stride" again for the next hold
    if not PAUSED["v"] or TUNER.holding:
        TUNER.step(dt)
    if TUNER.holding:
        STANCE["pose"] = TUNER.glide(STANCE["pose"], STANCE["goal"], dt)
    return TUNER.snapshot()


def handle_button(b):
    name, action = b.get("button"), b.get("action")
    if name == "X" and action == "press":
        FLAGS["projector_on"] = not FLAGS["projector_on"]
    elif name == "Y" and action == "press":
        FLAGS["head_control"] = not FLAGS["head_control"]
    elif name == "A" and action == "press":
        PAUSED["v"] = not PAUSED["v"]
        if not PAUSED["v"] and FORCE_FALL["v"]:
            FORCE_FALL["v"] = False       # "stood it up and resumed"
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


def _handle_suite_setting(body):
    """Policy / soft landing / get-up / recorder groups. True if handled."""
    group, key, val, action = (body.get("group"), body.get("key"), body.get("value"),
                               body.get("action"))
    if group == "policy" and key == "active":
        slot = next((x for x in SLOTS if x["id"] == val), None)
        if slot is None or slot["kind"] != "walk" or not slot["usable"]:
            POLICY.update(state="error", message=f"cannot use: {val}")
        elif val != POLICY["active"]:
            POLICY.update(pending=val, state="loading", message="", t=time.time())
        return True
    if group == "policy" and key == "rest_straight":
        REST["enabled"] = val is True or val == "straight"
        note("Rest pose: straight stance when idle." if REST["enabled"]
             else "Rest pose: the policy's own.")
        return True
    if group == "safety":
        if action == "reset":
            REFLEX.cfg = fall_reflex.clean_config({})
        elif action == "save":
            note("Fall reflex settings saved.")
        elif key == "reset_peaks":
            REFLEX.reset_peaks()
        else:
            REFLEX.set(key, val)
        return True
    if group == "getup":
        if action == "reset":
            GETUP.update(getup_mod.clean_config({}))
        elif action == "save":
            note("Get-up settings saved.")
        elif key == "start":
            if not GETUP["enabled"]:
                note("Get-up is switched off (TUNE → Get-up).")
            else:
                GETUP_RUN["t0"] = time.time()
                note("Get-up running (attempt 1). A aborts.", 4.0)
        elif key == "abort":
            GETUP_RUN["t0"], GETUP_RUN["result"] = None, "stopped by the operator"
            note("Get-up stopped: stopped by the operator.")
        elif key == "capture_reference":
            if TUNER.holding:
                GETUP["imu_standing_acc"] = [0.4, -0.3, 9.79]
                note("Standing reference saved.")
            else:
                note("Hold the stance first, then capture.")
        elif key in GETUP:
            keep = GETUP["imu_standing_acc"]
            GETUP.update(getup_mod.clean_config({**GETUP, key: val}))
            GETUP["imu_standing_acc"] = keep
        return True
    if group == "recorder":
        if key == "mark":
            BLACKBOX["saved"] += 1
            BLACKBOX["last_file"] = time.strftime("%Y%m%d_%H%M%S_mark.npz")
            FAKE_LOGS.insert(0, {"name": BLACKBOX["last_file"], "bytes": 150_000, "mtime": 0})
            note("Marked: the last seconds will be saved.")
        elif key == "take":
            if val:
                BLACKBOX["take_t0"] = time.time()
            elif BLACKBOX["take_t0"]:
                BLACKBOX["take_t0"] = None
                BLACKBOX["saved"] += 1
                BLACKBOX["last_file"] = time.strftime("%Y%m%d_%H%M%S_take_") + POLICY["active"] + ".npz"
                FAKE_LOGS.insert(0, {"name": BLACKBOX["last_file"], "bytes": 600_000, "mtime": 0})
                note("Take saved.")
        return True
    if group == "walk" and str(key).startswith("slew_"):
        k = key[len("slew_"):]
        SLEW[k] = bool(val) if k == "enabled" else float(val)
        return True
    return False


def handle_setting(body):
    group = body.get("group")
    if _handle_suite_setting(body):
        return
    if body.get("action") == "save":
        if group == "joint_offsets":
            TUNER.save(lambda fields: print("WOULD WRITE", fields))
        print("SETTING save", group)
        return
    if body.get("action") == "reset":
        if group == "joint_offsets":
            TUNER.revert()
        elif group == "walk":
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
        elif key == "steer_trim":
            WALK["steer_trim"] = max(-0.5, min(0.5, float(val)))
        elif key in WALK:
            WALK[key] = val
    elif group == "camera" and key in CAM:
        CAM[key] = val
    elif group == "head" and key == "play":
        HEAD_NOW["anim"] = str(val)
        PREVIEW.play(str(val))
    elif group == "head" and key in HEAD:
        HEAD[key] = val
    elif group == "sound" and key == "volume":
        SOUND["volume"] = max(0.0, min(1.0, float(val)))
    elif group == "sound" and key == "say":
        SOUND["last"] = str(val)
        PREVIEW.say(str(val))
    elif group == "antenna" and key in ANT:
        ANT[key] = bool(val)
    elif group == "stance" and key == "hold" and val:
        PAUSED["v"] = True
        TUNER.hold()
    elif group == "joint_offsets":
        TUNER.set_target(key, val)
    print("SETTING", group, key, val)


class FakeBluez:
    """A pretend BlueZ behind the real GamepadManager, so the Gamepad tab can be
    clicked through on a laptop: one known pad, and a second one that shows up a few
    seconds into a search. Connecting takes a moment, like the real thing."""

    def __init__(self):
        self.dev = {"AA:BB:CC:DD:EE:01": dict(Name="Xbox Wireless Controller", Paired=True,
                                             Trusted=True, Connected=True, Icon="input-gaming")}
        self.scan_t0 = None

    class _Scan:
        def __init__(self, owner):
            self.owner = owner

        def poll(self):
            o = self.owner
            return None if o.scan_t0 and time.time() - o.scan_t0 < gamepad_pairing.SCAN_SECONDS else 0

        def terminate(self):
            self.owner.scan_t0 = None

        def wait(self, timeout=None):
            return 0

    def spawn_scan(self, seconds):
        self.scan_t0 = time.time()
        return self._Scan(self)

    def pair_session(self, mac):
        print("BLUETOOTH pair session", mac)
        time.sleep(3)
        d = self.dev.get(mac)
        if d is not None:
            d["Paired"] = d["Trusted"] = d["Connected"] = True
        return ""

    def run(self, args, timeout):
        if args[0] == "busctl":
            if self.scan_t0 and time.time() - self.scan_t0 > 3:
                self.dev.setdefault("AA:BB:CC:DD:EE:02", dict(
                    Name="Xbox Wireless Controller", Paired=False, Trusted=False,
                    Connected=False, Icon="input-gaming", RSSI=-51))
                self.dev.setdefault("AA:BB:CC:DD:EE:03", dict(
                    Name="Living room TV", Paired=False, Trusted=False, Connected=False,
                    Icon="video-display", RSSI=-72))
            return 0, gamepad_pairing.fake_managed_objects(self.dev)
        print("BLUETOOTH", " ".join(args[1:]))
        cmd, d = args[1], self.dev.get(args[2]) if len(args) > 2 else None
        if d is None:
            return 0, ""
        time.sleep(1.5)
        if cmd == "connect":
            d["Connected"] = True
        elif cmd == "disconnect":
            d["Connected"] = False
        elif cmd == "remove":
            del self.dev[args[2]]
        return 0, ""


_BLUEZ = FakeBluez()
GAMEPADS = gamepad_pairing.GamepadManager(run=_BLUEZ.run, spawn_scan=_BLUEZ.spawn_scan,
                                          pair_session=_BLUEZ.pair_session, hold_file="")


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
        elif path in ("/offsets", "/offsets.html"):
            with open(OFFSETS_PAGE, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif path == "/preview":
            with open(PREVIEW_PAGE, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif path == "/api/preview":
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            args = dict(kv.split("=", 1) for kv in q.split("&") if "=" in kv)
            try:
                turn = max(-1.0, min(1.0, float(args.get("turn", 0))))
            except ValueError:
                turn = 0.0
            self._send(200, json.dumps(PREVIEW.step(args.get("walking") == "1", turn)))
        elif path.startswith("/voice/"):
            name = os.path.basename(path)
            full = os.path.join(VOICE_DIR, name)
            if name.endswith(".wav") and os.path.isfile(full):
                with open(full, "rb") as f:
                    self._send(200, f.read(), "audio/wav")
            else:
                self._send(404, json.dumps({"error": "not found"}))
        elif path == "/api/state":
            self._send(200, json.dumps(build_state()))
        elif path == "/api/logs":
            self._send(200, json.dumps({"logs": FAKE_LOGS}))
        elif path == "/api/gamepad":
            self._send(200, json.dumps(GAMEPADS.status()))
        elif path.startswith("/api/logs/"):
            self._send(200, b"mock recording", "application/octet-stream")
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
                TRIM_SAVED.update(TRIM)
                print("TRIM save", TRIM)
            else:
                ax = body.get("axis")
                if ax in TRIM:
                    TRIM[ax] = clamp(TRIM[ax] + float(body.get("delta", 0.0)), -0.1, 0.1)
                print("TRIM", TRIM)
        elif path == "/api/setting":
            handle_setting(body)
        elif path == "/api/gamepad":
            ok, error = GAMEPADS.request(str(body.get("action", "")), body.get("mac"))
            self._send(200 if ok else 400, json.dumps({"ok": ok, "error": error}))
            return
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
    ap.add_argument("--fallen", action="store_true",
                    help="start fallen and paused (to preview the get-up / resume buttons)")
    ap.add_argument("--steady", action="store_true", help="never fall by itself")
    args = ap.parse_args()
    FORCE_MODE = args.mode
    if args.fallen:
        FORCE_FALL["v"] = True
        PAUSED["v"] = True
    elif args.steady:
        FORCE_FALL["v"] = False

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
