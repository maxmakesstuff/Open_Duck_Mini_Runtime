#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Open Duck Mini — First-Time Setup
=================================================================
Script created by Max Schmierer.

ONE command to bring a brand-new duck fully online. It walks — in the right
order — through every configuration step, from checking that all Python
libraries are installed, through motor IDs / offsets / IMU calibration + trim,
walk tuning, expression features, the Xbox controller and the phone captive
portal, to a final verification that says "this duck is good to go".

  * Every step is SKIPPABLE (Enter = run, s = skip, a = run all remaining, q = quit).
  * Nothing is destructive: config is always written backup-first, and each step
    just orchestrates the existing per-purpose scripts (so their own prompts and
    safety still apply).
  * Progress is remembered (~/.openduck_setup_progress.json) so you can stop and
    resume — already-done steps are shown with a check mark.

Run it ON THE DUCK (from the repo), ideally in the project venv:

    cd ~/Open_Duck_Mini_Runtime
    python scripts/first_time_setup.py
"""
import importlib
import json
import os
import shutil
import subprocess
import sys
import time

# ------------------------------------------------------------------ paths
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS_DIR)
HOME = os.path.expanduser("~")
CONFIG_PATH = os.path.join(HOME, "duck_config.json")
EXAMPLE_CONFIG = os.path.join(REPO_ROOT, "example_config.json")
STATE_PATH = os.path.join(HOME, ".openduck_setup_progress.json")
OPS = os.path.join(REPO_ROOT, "ops")

# ------------------------------------------------------------------ tiny TUI
IS_TTY = sys.stdout.isatty()
W = 64   # box width


def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if IS_TTY else s


def bold(s):   return _c("1", s)
def dim(s):    return _c("2", s)
def red(s):    return _c("31", s)
def green(s):  return _c("32", s)
def yellow(s): return _c("33", s)
def blue(s):   return _c("34", s)
def magenta(s):return _c("35", s)
def cyan(s):   return _c("36", s)


def clear():
    if IS_TTY:
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()


def _visible_len(s):
    # length ignoring ANSI escapes (good enough for our simple codes)
    out, i = 0, 0
    while i < len(s):
        if s[i] == "\033":
            while i < len(s) and s[i] != "m":
                i += 1
        else:
            out += 1
        i += 1
    return out


def boxline(s="", pad=" "):
    inner = W - 2
    vis = _visible_len(s)
    if vis > inner:
        s = s[: inner]
        vis = inner
    return "│" + s + pad * (inner - vis) + "│"


def banner():
    top = "╭" + "─" * (W - 2) + "╮"
    bot = "╰" + "─" * (W - 2) + "╯"
    def center(txt):
        vis = _visible_len(txt)
        left = (W - 2 - vis) // 2
        right = (W - 2 - vis) - left
        return "│" + " " * left + txt + " " * right + "│"
    print(cyan(top))
    print(cyan(center(bold("OPEN DUCK MINI  ·  FIRST-TIME SETUP"))))
    print(cyan(boxline()))
    print(cyan(center(dim("One command  →  one ready-to-run duck"))))
    print(cyan(center(dim("Script created by Max Schmierer"))))
    print(cyan(bot))


ICON = {
    "done":    green("✓"),
    "skipped": yellow("–"),
    "failed":  red("✗"),
    "pending": dim("·"),
}


def render(steps, status, current):
    clear()
    banner()
    print()
    for i, st in enumerate(steps):
        mark = ICON.get(status.get(st["key"], "pending"), dim("·"))
        num = f"{i + 1:>2}."
        title = st["title"]
        if i == current:
            print("  " + cyan("▶") + " " + num + " " + bold(title))
        else:
            faint = status.get(st["key"], "pending") == "pending" and i > current
            line = f"  {mark} {num} {title}"
            print(dim(line) if faint else line)
    print()


def ask(prompt, choices=("", "s", "a", "q"), default=""):
    """Return a lowercased single-key answer restricted to `choices`."""
    while True:
        try:
            raw = input(prompt).strip().lower()
        except EOFError:
            return "q"
        except KeyboardInterrupt:
            return "q"
        if raw == "":
            raw = default
        if raw in choices:
            return raw
        print(yellow(f"  please enter one of: {', '.join(repr(x) for x in choices)}"))


def ask_yes(prompt, default=True):
    d = "Y/n" if default else "y/N"
    while True:
        try:
            raw = input(f"{prompt} [{d}] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return default
        if raw == "":
            return default
        if raw in ("j", "y", "ja", "yes"):
            return True
        if raw in ("n", "nein", "no"):
            return False


def ask_text(prompt, default=""):
    try:
        raw = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return raw or default


# ------------------------------------------------------------------ helpers
def run_child(argv, cwd=SCRIPTS_DIR, sudo=False):
    """Run a child process inheriting the terminal (so its own prompts work).
    Returns (ok, note)."""
    cmd = (["sudo"] if sudo else []) + argv
    print(dim("  $ " + " ".join(cmd)))
    print()
    try:
        rc = subprocess.run(cmd, cwd=cwd).returncode
    except KeyboardInterrupt:
        print(yellow("\n  (step aborted)"))
        return False, "aborted"
    except FileNotFoundError as e:
        return False, f"not found: {e}"
    return (rc == 0), ("ok" if rc == 0 else f"exit code {rc}")


def save_config_field(updates):
    """Write config keys backup-first, preserving everything else. Imports the
    project's own helper (pure, no hardware)."""
    sys.path.insert(0, os.path.join(REPO_ROOT, "mini_bdx_runtime", "mini_bdx_runtime"))
    from duck_config import save_config_fields  # noqa: E402
    return save_config_fields(updates, CONFIG_PATH)


def load_config():
    try:
        return json.load(open(CONFIG_PATH))
    except (FileNotFoundError, ValueError):
        return {}


def load_state():
    try:
        return set(json.load(open(STATE_PATH)).get("done", []))
    except (FileNotFoundError, ValueError):
        return set()


def save_state(done):
    try:
        json.dump({"done": sorted(done), "ts": time.strftime("%Y-%m-%d %H:%M:%S")},
                  open(STATE_PATH, "w"), indent=2)
    except OSError:
        pass


def pause():
    try:
        input(dim("\n  ↵ Enter to continue … "))
    except (EOFError, KeyboardInterrupt):
        pass


# ============================================================= STEP ACTIONS
# Each returns (ok: bool, note: str).

# Required + optional Python deps (import-name, label, required?, pip-hint).
DEPS = [
    ("numpy",           "NumPy",                       True,  "numpy==1.26.4"),
    ("scipy",           "SciPy",                       True,  "scipy==1.15.1"),
    ("onnxruntime",     "ONNX Runtime (policy)",       True,  "onnxruntime==1.18.1"),
    ("rustypot",        "rustypot (servo bus)",        True,  "rustypot==0.1.0"),
    ("pygame",          "pygame (gamepad)",            True,  "pygame==2.6.0"),
    ("board",           "Blinka / board (GPIO)",       True,  "adafruit-blinka"),
    ("digitalio",       "digitalio",                   True,  "adafruit-blinka"),
    ("pwmio",           "pwmio (antennas)",            True,  "adafruit-blinka"),
    ("busio",           "busio (I2C)",                 True,  "adafruit-blinka"),
    ("adafruit_bno055", "BNO055 IMU driver",           True,  "adafruit-circuitpython-bno055==5.4.13"),
    ("pypot",           "pypot (motor provisioning)",  True,  "pypot @ git+…support-feetech-sts3215"),
    ("mini_bdx_runtime","mini_bdx_runtime (this package)", True, "pip install -e ."),
    ("cv2",             "OpenCV (face tracking)",      False, "python3-opencv / opencv-python"),
    ("picamzero",       "picamzero (camera)",          False, "picamzero"),
    ("openai",          "openai (AI chatter)",         False, "openai==1.70.0"),
]


def _check_deps():
    missing_req, missing_opt = [], []
    for mod, label, required, hint in DEPS:
        try:
            importlib.import_module(mod)
            print(f"    {green('✓')} {label}")
        except Exception:  # noqa: BLE001 — any import failure counts as missing
            (missing_req if required else missing_opt).append((mod, label, hint))
            tag = red("MISSING") if required else yellow("optional")
            print(f"    {red('✗') if required else yellow('!')} {label}  ({tag})")
    return missing_req, missing_opt


def step_deps(ctx):
    print(bold("  Checking Python dependencies in the current interpreter:"))
    print(dim(f"    {sys.executable}"))
    if "virtualenv" not in sys.prefix and ".virtualenvs" not in sys.prefix and \
       os.environ.get("VIRTUAL_ENV") is None:
        print(yellow("    ⚠ This does not look like the project venv. Ideally run "
                     "inside the venv."))
    print()
    missing_req, missing_opt = _check_deps()

    if missing_req:
        print()
        print(yellow(f"  {len(missing_req)} required package(s) missing."))
        if ask_yes("  Run 'pip install -e .' in the repo now?", default=True):
            ok, note = run_child([sys.executable, "-m", "pip", "install", "-e", "."],
                                 cwd=REPO_ROOT)
            print()
            print(bold("  Re-checking:"))
            missing_req, missing_opt = _check_deps()
    # Pi 5 hint
    print()
    print(dim("  Pi 5 note: pip uninstall -y RPi.GPIO && pip install lgpio"))
    if missing_opt:
        print(dim("  Optional (only for camera/tracking/AI): "
                  + ", ".join(m[1] for m in missing_opt)))
    if missing_req:
        return False, f"{len(missing_req)} required package(s) still missing"
    return True, "all required packages present"


def step_config(ctx):
    if os.path.exists(CONFIG_PATH):
        print(green("  ~/duck_config.json already exists — keeping it."))
        return True, "already present"
    if not os.path.exists(EXAMPLE_CONFIG):
        return False, "example_config.json not found"
    shutil.copy2(EXAMPLE_CONFIG, CONFIG_PATH)
    print(green(f"  Created: {CONFIG_PATH}  (from example_config.json)"))
    return True, "created from template"


def step_motor_ids(ctx):
    print(bold("  Provision motor IDs"))
    print("  A CLONED duck usually has its IDs already → skip this.")
    print("  A NEW servo (factory ID 1) gets its bus ID here.")
    print(dim("  Connect exactly ONE new servo at a time, then assign its ID.\n"))
    if not ask_yes("  Provision new servos now?", default=False):
        return True, "skipped (IDs already set)"
    any_done = False
    while True:
        mid = ask_text("  Bus ID for the connected servo (empty = done)")
        if not mid:
            break
        ok, note = run_child([sys.executable, "configure_motor.py", "--id", mid])
        any_done = any_done or ok
    return True, ("servos provisioned" if any_done else "no changes")


def step_motor_pid(ctx):
    print("  Applies the baseline PID (P=32,I=0,D=0) to all 14 joints and steps")
    print("  each one to 0.\n")
    return run_child([sys.executable, "configure_all_motors.py"])


def step_offsets(ctx):
    print("  Interactively measure the joint zero offsets. Follow the script's")
    print("  instructions; the values are written directly to ~/duck_config.json.\n")
    return run_child([sys.executable, "find_soft_offsets.py"])


def step_imu_cal(ctx):
    print(bold("  IMU mounting & calibration"))
    cfg = load_config()
    cur = bool(cfg.get("imu_upside_down", False))
    up = ask_yes("  Is the BNO055 IMU mounted UPSIDE DOWN (as in the current CAD)?",
                 default=cur)
    if up != cur:
        save_config_field({"imu_upside_down": up})
        print(green(f"  imu_upside_down = {up} saved."))
    print("\n  Now the calibration (hold the gyro still, ~6 accel orientations).")
    print("  Runs through automatically and saves to ~/.\n")
    return run_child([sys.executable, "calibrate_imu.py"])


def step_imu_trim(ctx):
    print("  The health check reads the gravity axis, gyro bias and residual tilt —")
    print("  keep the duck STILL and LEVEL while it runs. Save the trim at the end (y).\n")
    return run_child([sys.executable, "imu_health_check.py"])


def step_tuning(ctx):
    print("  Applies the proven walk tuning (action_scale, velocity_clip, tilt")
    print("  governor, slower cadence) as starting values — calibration stays")
    print("  untouched. Fine-tune per robot if needed.\n")
    return run_child([sys.executable, "apply_stability_defaults.py"])


def step_features(ctx):
    print(bold("  Enable expression features"))
    cfg = load_config()
    feats = dict(cfg.get("expression_features", {}))
    order = [("eyes", "Eye LEDs"), ("projector", "Projector / scanner LED"),
             ("antennas", "Antennas (PWM servos)"), ("speaker", "Speaker / sounds"),
             ("microphone", "Microphone"), ("camera", "Camera (tracking)")]
    print(dim("  (Enter keeps the current value)\n"))
    for key, label in order:
        feats[key] = ask_yes(f"    Enable {label}?", default=bool(feats.get(key, False)))
    save_config_field({"expression_features": feats})
    on = [label for key, label in order if feats.get(key)]
    print(green(f"\n  Saved. Active: {', '.join(on) if on else '(none)'}"))
    return True, f"{len(on)} feature(s) active"


def step_xbox(ctx):
    print(bold("  Xbox controller: Bluetooth auto-reconnect"))
    print("  Installs the service that automatically reconnects the pad after")
    print("  boot (see ops/bluetooth/README.md, xpadneo recommended).\n")
    setup = os.path.join(OPS, "bluetooth", "setup-bluetooth-reconnect.sh")
    if not os.path.exists(setup):
        return False, "ops/bluetooth/setup-bluetooth-reconnect.sh missing"
    print(dim("  For first-time pairing, beforehand if needed:  bluetoothctl  →  scan on → "
              "pair/connect/trust <MAC>\n"))
    if not ask_yes("  Run setup-bluetooth-reconnect.sh now (sudo)?", default=True):
        return True, "skipped"
    return run_child(["bash", setup], cwd=os.path.join(OPS, "bluetooth"), sudo=True)


def step_captive(ctx):
    print(bold("  Phone captive portal"))
    print("  After joining the duck's Wi-Fi, any http address opens the control")
    print("  page; the phone stays connected. Active after a reboot.\n")
    setup = os.path.join(OPS, "captive-portal", "setup-captive-portal.sh")
    if not os.path.exists(setup):
        return False, "ops/captive-portal/setup-captive-portal.sh missing"
    if not ask_yes("  Run setup-captive-portal.sh now (sudo)?", default=True):
        return True, "skipped"
    ok, note = run_child(["bash", setup], cwd=os.path.join(OPS, "captive-portal"), sudo=True)
    if ok:
        ctx["needs_reboot"] = True
        print(yellow("\n  → For the DNS part, later run once:  sudo reboot"))
    return ok, note


def step_verify(ctx):
    print(bold("  Final verification\n"))
    ok = True
    # 1) code imports
    sys.path.insert(0, os.path.join(REPO_ROOT, "mini_bdx_runtime", "mini_bdx_runtime"))
    for mod, label in [("stability_governor", "Governor"), ("duck_config", "Config"),
                       ("web_control", "Web UI")]:
        try:
            importlib.import_module(mod)
            print(f"    {green('✓')} code loads: {label}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"    {red('✗')} code import {label}: {e}")
    # 2) config completeness
    cfg = load_config()
    offs = cfg.get("joints_offsets", {})
    n_off = len(offs)
    nonzero = sum(1 for v in offs.values() if abs(float(v)) > 1e-9)
    trim = cfg.get("imu_trim", {})
    def status(cond):
        return green("✓") if cond else yellow("!")
    print()
    print(f"    {status(n_off == 14)} joints_offsets: {n_off}/14 joints "
          f"({nonzero} ≠ 0)" + ("" if nonzero else dim("  ← not measured yet?")))
    print(f"    {status(bool(trim))} imu_trim: {trim or '(not set)'}")
    print(f"    {status('action_scale' in cfg)} action_scale: {cfg.get('action_scale', '(default)')}")
    print(f"    {status(True)} imu_upside_down: {cfg.get('imu_upside_down', False)}")
    feats = cfg.get("expression_features", {})
    print(f"    · Features: {', '.join(k for k, v in feats.items() if v) or '(none)'}")
    if nonzero == 0:
        ok = False
    return ok, ("ready to go" if ok else "calibration/imports incomplete")


STEPS = [
    {"key": "deps",      "title": "Check dependencies & installation",    "run": step_deps},
    {"key": "config",    "title": "Create duck_config.json",              "run": step_config},
    {"key": "motor_ids", "title": "Provision motor IDs (new servos)",     "run": step_motor_ids},
    {"key": "motor_pid", "title": "Motors: PID baseline & zero position", "run": step_motor_pid},
    {"key": "offsets",   "title": "Measure joint zero offsets",           "run": step_offsets},
    {"key": "imu_cal",   "title": "IMU mounting & calibration",           "run": step_imu_cal},
    {"key": "imu_trim",  "title": "IMU health check & trim",              "run": step_imu_trim},
    {"key": "tuning",    "title": "Apply walk tuning (stability)",        "run": step_tuning},
    {"key": "features",  "title": "Choose expression features",           "run": step_features},
    {"key": "xbox",      "title": "Xbox controller (Bluetooth reconnect)", "run": step_xbox},
    {"key": "captive",   "title": "Phone captive portal (sudo)",          "run": step_captive},
    {"key": "verify",    "title": "Final verification",                   "run": step_verify},
]


def final_summary(status, ctx):
    clear()
    banner()
    print()
    done = sum(1 for s in STEPS if status.get(s["key"]) == "done")
    skipped = sum(1 for s in STEPS if status.get(s["key"]) == "skipped")
    failed = [s for s in STEPS if status.get(s["key"]) == "failed"]
    for i, st in enumerate(STEPS):
        mark = ICON.get(status.get(st["key"], "pending"), dim("·"))
        print(f"  {mark} {i + 1:>2}. {st['title']}")
    print()
    if not failed and (done + skipped) == len(STEPS):
        print(green(bold("  ✅  This duck is set up and ready to go!")))
    else:
        print(yellow(bold(f"  Done: {done} completed, {skipped} skipped, "
                          f"{len(failed)} failed.")))
        for s in failed:
            print(red(f"     ✗ {s['title']}"))
    if ctx.get("needs_reboot"):
        print(yellow("\n  → Captive-portal DNS activates after:  sudo reboot"))
    print(dim("\n  Start the walk:  cd scripts && python v2_rl_walk_mujoco.py "
              "--onnx_model_path <path>/BEST_WALK_ONNX_2.onnx"))
    print(dim("  Re-run setup (fully or partially):  python scripts/first_time_setup.py"))
    print(dim("\n  — Script created by Max Schmierer 🦆"))


def main():
    done_keys = load_state()
    status = {s["key"]: ("done" if s["key"] in done_keys else "pending") for s in STEPS}
    ctx = {"needs_reboot": False}
    auto = False

    i = 0
    while i < len(STEPS):
        step = STEPS[i]
        render(STEPS, status, i)
        already = status[step["key"]] == "done"
        print("  " + bold(f"Step {i + 1}/{len(STEPS)}: {step['title']}"))
        if already:
            print(green("  (already marked as done)"))
        print()

        if not auto:
            choice = ask(
                "  [↵]=run  s=skip  a=run all  q=quit  › ",
                choices=("", "s", "a", "q"),
                default="s" if already else "",
            )
            if choice == "q":
                if ask_yes("  Really quit the setup?", default=False):
                    break
                continue
            if choice == "s":
                status[step["key"]] = "skipped"
                i += 1
                continue
            if choice == "a":
                auto = True

        print()
        try:
            ok, note = step["run"](ctx)
        except Exception as e:  # noqa: BLE001 — never let one step crash the wizard
            ok, note = False, f"unexpected error: {e}"
        status[step["key"]] = "done" if ok else "failed"
        if ok:
            done_keys.add(step["key"])
            save_state(done_keys)
            print(green(f"\n  ✓ {step['title']} — {note}"))
        else:
            print(red(f"\n  ✗ {step['title']} — {note}"))
            print(dim("    (you can re-run this step later)"))
        pause()
        i += 1

    final_summary(status, ctx)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
