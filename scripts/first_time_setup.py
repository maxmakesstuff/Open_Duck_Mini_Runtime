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
    print(cyan(center(dim("Ein Befehl  →  ein einsatzbereiter Duck"))))
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
        print(yellow(f"  bitte eingeben: {', '.join(repr(x) for x in choices)}"))


def ask_yes(prompt, default=True):
    d = "J/n" if default else "j/N"
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
        print(yellow("\n  (Schritt abgebrochen)"))
        return False, "abgebrochen"
    except FileNotFoundError as e:
        return False, f"nicht gefunden: {e}"
    return (rc == 0), ("ok" if rc == 0 else f"Exit-Code {rc}")


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
        input(dim("\n  ↵ Enter, um weiterzumachen … "))
    except (EOFError, KeyboardInterrupt):
        pass


# ============================================================= STEP ACTIONS
# Each returns (ok: bool, note: str).

# Required + optional Python deps (import-name, label, required?, pip-hint).
DEPS = [
    ("numpy",           "NumPy",                       True,  "numpy==1.26.4"),
    ("scipy",           "SciPy",                       True,  "scipy==1.15.1"),
    ("onnxruntime",     "ONNX Runtime (Policy)",       True,  "onnxruntime==1.18.1"),
    ("rustypot",        "rustypot (Servo-Bus)",        True,  "rustypot==0.1.0"),
    ("pygame",          "pygame (Gamepad)",            True,  "pygame==2.6.0"),
    ("board",           "Blinka / board (GPIO)",       True,  "adafruit-blinka"),
    ("digitalio",       "digitalio",                   True,  "adafruit-blinka"),
    ("pwmio",           "pwmio (Antennen)",            True,  "adafruit-blinka"),
    ("busio",           "busio (I2C)",                 True,  "adafruit-blinka"),
    ("adafruit_bno055", "BNO055-IMU-Treiber",          True,  "adafruit-circuitpython-bno055==5.4.13"),
    ("pypot",           "pypot (Motor-Provisionierung)",True,  "pypot @ git+…support-feetech-sts3215"),
    ("mini_bdx_runtime","mini_bdx_runtime (dieses Paket)", True, "pip install -e ."),
    ("cv2",             "OpenCV (Gesichts-Tracking)",  False, "python3-opencv / opencv-python"),
    ("picamzero",       "picamzero (Kamera)",          False, "picamzero"),
    ("openai",          "openai (KI-Chatter)",         False, "openai==1.70.0"),
]


def _check_deps():
    missing_req, missing_opt = [], []
    for mod, label, required, hint in DEPS:
        try:
            importlib.import_module(mod)
            print(f"    {green('✓')} {label}")
        except Exception:  # noqa: BLE001 — any import failure counts as missing
            (missing_req if required else missing_opt).append((mod, label, hint))
            tag = red("FEHLT") if required else yellow("optional")
            print(f"    {red('✗') if required else yellow('!')} {label}  ({tag})")
    return missing_req, missing_opt


def step_deps(ctx):
    print(bold("  Prüfe Python-Abhängigkeiten im aktuellen Interpreter:"))
    print(dim(f"    {sys.executable}"))
    if "virtualenv" not in sys.prefix and ".virtualenvs" not in sys.prefix and \
       os.environ.get("VIRTUAL_ENV") is None:
        print(yellow("    ⚠ Sieht nicht nach dem Projekt-venv aus. Idealerweise im "
                     "venv laufen lassen."))
    print()
    missing_req, missing_opt = _check_deps()

    if missing_req:
        print()
        print(yellow(f"  {len(missing_req)} Pflicht-Paket(e) fehlen."))
        if ask_yes("  Jetzt 'pip install -e .' im Repo ausführen?", default=True):
            ok, note = run_child([sys.executable, "-m", "pip", "install", "-e", "."],
                                 cwd=REPO_ROOT)
            print()
            print(bold("  Prüfe erneut:"))
            missing_req, missing_opt = _check_deps()
    # Pi 5 hint
    print()
    print(dim("  Hinweis Pi 5: pip uninstall -y RPi.GPIO && pip install lgpio"))
    if missing_opt:
        print(dim("  Optionale (nur für Kamera/Tracking/KI): "
                  + ", ".join(m[1] for m in missing_opt)))
    if missing_req:
        return False, f"{len(missing_req)} Pflicht-Paket(e) fehlen noch"
    return True, "alle Pflicht-Pakete vorhanden"


def step_config(ctx):
    if os.path.exists(CONFIG_PATH):
        print(green("  ~/duck_config.json existiert bereits — bleibt erhalten."))
        return True, "vorhanden"
    if not os.path.exists(EXAMPLE_CONFIG):
        return False, "example_config.json nicht gefunden"
    shutil.copy2(EXAMPLE_CONFIG, CONFIG_PATH)
    print(green(f"  Angelegt: {CONFIG_PATH}  (aus example_config.json)"))
    return True, "aus Vorlage angelegt"


def step_motor_ids(ctx):
    print(bold("  Motor-IDs provisionieren"))
    print("  Ein GEKLONTER Duck hat die IDs meist schon → überspringen.")
    print("  Ein NEUER Servo (Werks-ID 1) bekommt hier seine Bus-ID.")
    print(dim("  Jeweils genau EINEN neuen Servo anschließen, dann ID vergeben.\n"))
    if not ask_yes("  Neue Servos jetzt provisionieren?", default=False):
        return True, "übersprungen (IDs bereits gesetzt)"
    any_done = False
    while True:
        mid = ask_text("  Bus-ID für den angeschlossenen Servo (leer = fertig)")
        if not mid:
            break
        ok, note = run_child([sys.executable, "configure_motor.py", "--id", mid])
        any_done = any_done or ok
    return True, ("Servos provisioniert" if any_done else "keine Änderung")


def step_motor_pid(ctx):
    print("  Setzt die Baseline-PID (P=32,I=0,D=0) auf alle 14 Gelenke und fährt")
    print("  jedes einzeln auf 0.\n")
    return run_child([sys.executable, "configure_all_motors.py"])


def step_offsets(ctx):
    print("  Interaktives Einmessen der Gelenk-Null-Offsets. Folge den Anweisungen")
    print("  des Skripts; die Werte werden direkt in ~/duck_config.json geschrieben.\n")
    return run_child([sys.executable, "find_soft_offsets.py"])


def step_imu_cal(ctx):
    print(bold("  IMU-Montage & Kalibrierung"))
    cfg = load_config()
    cur = bool(cfg.get("imu_upside_down", False))
    up = ask_yes("  Ist die BNO055-IMU KOPFÜBER montiert (wie im aktuellen CAD)?",
                 default=cur)
    if up != cur:
        save_config_field({"imu_upside_down": up})
        print(green(f"  imu_upside_down = {up} gespeichert."))
    print("\n  Jetzt die Kalibrierung (Gyro still halten, Accel ~6 Lagen). Läuft")
    print("  automatisch durch und speichert nach ~/.\n")
    return run_child([sys.executable, "calibrate_imu.py"])


def step_imu_trim(ctx):
    print("  Health-Check liest Gravitations-Achse, Gyro-Bias und Rest-Neigung —")
    print("  Duck dabei STILL und WAAGERECHT halten. Am Ende den Trim speichern (y).\n")
    return run_child([sys.executable, "imu_health_check.py"])


def step_tuning(ctx):
    print("  Setzt das bewährte Lauf-Tuning (action_scale, velocity_clip, Tilt-")
    print("  Governor, langsamere Kadenz) als Startwerte — Kalibrierung bleibt")
    print("  unangetastet. Pro Roboter ggf. nachjustieren.\n")
    return run_child([sys.executable, "apply_stability_defaults.py"])


def step_features(ctx):
    print(bold("  Ausdrucks-Features aktivieren"))
    cfg = load_config()
    feats = dict(cfg.get("expression_features", {}))
    order = [("eyes", "Augen-LEDs"), ("projector", "Projektor / Scanner-LED"),
             ("antennas", "Antennen (PWM-Servos)"), ("speaker", "Lautsprecher / Sounds"),
             ("microphone", "Mikrofon"), ("camera", "Kamera (Tracking)")]
    print(dim("  (Enter übernimmt den aktuellen Wert)\n"))
    for key, label in order:
        feats[key] = ask_yes(f"    {label} aktivieren?", default=bool(feats.get(key, False)))
    save_config_field({"expression_features": feats})
    on = [label for key, label in order if feats.get(key)]
    print(green(f"\n  Gespeichert. Aktiv: {', '.join(on) if on else '(keine)'}"))
    return True, f"{len(on)} Feature(s) aktiv"


def step_xbox(ctx):
    print(bold("  Xbox-Controller: Bluetooth-Auto-Reconnect"))
    print("  Installiert den Dienst, der den Pad nach dem Booten automatisch")
    print("  wieder verbindet (siehe ops/bluetooth/README.md, xpadneo empfohlen).\n")
    setup = os.path.join(OPS, "bluetooth", "setup-bluetooth-reconnect.sh")
    if not os.path.exists(setup):
        return False, "ops/bluetooth/setup-bluetooth-reconnect.sh fehlt"
    print(dim("  Zum erstmaligen Koppeln vorher ggf.:  bluetoothctl  →  scan on → "
              "pair/connect/trust <MAC>\n"))
    if not ask_yes("  setup-bluetooth-reconnect.sh jetzt ausführen (sudo)?", default=True):
        return True, "übersprungen"
    return run_child(["bash", setup], cwd=os.path.join(OPS, "bluetooth"), sudo=True)


def step_captive(ctx):
    print(bold("  Handy Captive-Portal"))
    print("  Nach dem Verbinden mit dem Duck-WLAN öffnet jede http-Adresse die")
    print("  Steuerseite; das Handy bleibt verbunden. Aktiv nach einem Reboot.\n")
    setup = os.path.join(OPS, "captive-portal", "setup-captive-portal.sh")
    if not os.path.exists(setup):
        return False, "ops/captive-portal/setup-captive-portal.sh fehlt"
    if not ask_yes("  setup-captive-portal.sh jetzt ausführen (sudo)?", default=True):
        return True, "übersprungen"
    ok, note = run_child(["bash", setup], cwd=os.path.join(OPS, "captive-portal"), sudo=True)
    if ok:
        ctx["needs_reboot"] = True
        print(yellow("\n  → Für den DNS-Teil später einmal:  sudo reboot"))
    return ok, note


def step_verify(ctx):
    print(bold("  Abschluss-Verifikation\n"))
    ok = True
    # 1) code imports
    sys.path.insert(0, os.path.join(REPO_ROOT, "mini_bdx_runtime", "mini_bdx_runtime"))
    for mod, label in [("stability_governor", "Governor"), ("duck_config", "Config"),
                       ("web_control", "Web-UI")]:
        try:
            importlib.import_module(mod)
            print(f"    {green('✓')} Code lädt: {label}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"    {red('✗')} Code-Import {label}: {e}")
    # 2) config completeness
    cfg = load_config()
    offs = cfg.get("joints_offsets", {})
    n_off = len(offs)
    nonzero = sum(1 for v in offs.values() if abs(float(v)) > 1e-9)
    trim = cfg.get("imu_trim", {})
    def status(cond):
        return green("✓") if cond else yellow("!")
    print()
    print(f"    {status(n_off == 14)} joints_offsets: {n_off}/14 Gelenke "
          f"({nonzero} ≠ 0)" + ("" if nonzero else dim("  ← noch nicht eingemessen?")))
    print(f"    {status(bool(trim))} imu_trim: {trim or '(nicht gesetzt)'}")
    print(f"    {status('action_scale' in cfg)} action_scale: {cfg.get('action_scale', '(default)')}")
    print(f"    {status(True)} imu_upside_down: {cfg.get('imu_upside_down', False)}")
    feats = cfg.get("expression_features", {})
    print(f"    · Features: {', '.join(k for k, v in feats.items() if v) or '(keine)'}")
    if nonzero == 0:
        ok = False
    return ok, ("startklar" if ok else "Kalibrierung/Imports unvollständig")


STEPS = [
    {"key": "deps",      "title": "Abhängigkeiten & Installation prüfen", "run": step_deps},
    {"key": "config",    "title": "duck_config.json anlegen",            "run": step_config},
    {"key": "motor_ids", "title": "Motor-IDs provisionieren (neue Servos)", "run": step_motor_ids},
    {"key": "motor_pid", "title": "Motoren: PID-Baseline & Nullstellung", "run": step_motor_pid},
    {"key": "offsets",   "title": "Gelenk-Null-Offsets einmessen",       "run": step_offsets},
    {"key": "imu_cal",   "title": "IMU-Montage & Kalibrierung",          "run": step_imu_cal},
    {"key": "imu_trim",  "title": "IMU Health-Check & Trim",             "run": step_imu_trim},
    {"key": "tuning",    "title": "Lauf-Tuning (Stabilität) setzen",     "run": step_tuning},
    {"key": "features",  "title": "Ausdrucks-Features wählen",           "run": step_features},
    {"key": "xbox",      "title": "Xbox-Controller (Bluetooth-Reconnect)", "run": step_xbox},
    {"key": "captive",   "title": "Handy Captive-Portal (sudo)",         "run": step_captive},
    {"key": "verify",    "title": "Abschluss-Verifikation",             "run": step_verify},
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
        print(green(bold("  ✅  Dieser Duck ist eingerichtet und startklar!")))
    else:
        print(yellow(bold(f"  Fertig: {done} erledigt, {skipped} übersprungen, "
                          f"{len(failed)} fehlgeschlagen.")))
        for s in failed:
            print(red(f"     ✗ {s['title']}"))
    if ctx.get("needs_reboot"):
        print(yellow("\n  → Captive-Portal-DNS aktiviert sich nach:  sudo reboot"))
    print(dim("\n  Walk starten:  cd scripts && python v2_rl_walk_mujoco.py "
              "--onnx_model_path <pfad>/BEST_WALK_ONNX_2.onnx"))
    print(dim("  Setup erneut/teilweise:  python scripts/first_time_setup.py"))
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
        print("  " + bold(f"Schritt {i + 1}/{len(STEPS)}: {step['title']}"))
        if already:
            print(green("  (bereits als erledigt markiert)"))
        print()

        if not auto:
            choice = ask(
                "  [↵]=ausführen  s=überspringen  a=alle ausführen  q=beenden  › ",
                choices=("", "s", "a", "q"),
                default="s" if already else "",
            )
            if choice == "q":
                if ask_yes("  Setup wirklich beenden?", default=False):
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
            ok, note = False, f"unerwarteter Fehler: {e}"
        status[step["key"]] = "done" if ok else "failed"
        if ok:
            done_keys.add(step["key"])
            save_state(done_keys)
            print(green(f"\n  ✓ {step['title']} — {note}"))
        else:
            print(red(f"\n  ✗ {step['title']} — {note}"))
            print(dim("    (du kannst diesen Schritt später erneut ausführen)"))
        pause()
        i += 1

    final_summary(status, ctx)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
