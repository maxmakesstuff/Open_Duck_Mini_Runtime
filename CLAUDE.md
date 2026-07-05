# CLAUDE.md — Operational handoff (Open_Duck_Mini_Runtime)

Instructions for any Claude/LLM instance working in this directory. This is the
**operational playbook + live state**. For the *architecture* (the 50 Hz control
loop, the 101-dim obs, the load-bearing joint order, per-file gotchas) read the
parent **`../CLAUDE.md`** — it is authoritative and still current. This file adds
what that one can't: how to work with the real robots safely, and where things stand.

There is also a machine-local memory at `~/.claude/projects/.../memory/` (auto-loaded
each session) with dated notes — treat it as background, verify anything it names
against the current code before acting.

---

## Current state (last updated 2026-07-04, branch `feature/overnight-suite`)

Two physical robots ("ducks"), identical hardware. The **reference duck** is tuned
and walking well ("steady as hell", walks on carpet). What is LIVE on it:

- **Walk stability** — `~/duck_config.json`: `action_scale: 0.23`,
  `velocity_clip: true`, `stability_governor` enabled (conservative deadbands),
  `phase_frequency_factor_offset: -0.10`. The code levers ship **off by default**;
  they only act because the config enables them. See [[walk-stability-levers]] memory.
- **IMU mounting trim** — `imu_trim` (pitch/roll) live-tuned on this duck; the BNO055
  is mounted **upside down** (`imu_upside_down: true`). The walk balances on **raw
  gyro+accel only**, so IMU bias = a lean = directional falls. See [[imu-stability-fixes]].
- **Captive portal** — phone joins the duck Wi-Fi, stays connected, any `http://`
  URL forwards to the control UI. Two halves: `web_control.py` answers OS
  connectivity probes with "Success" (so iOS won't disconnect), and the AP's dnsmasq
  wildcard resolves everything to the duck. DNS half needs a **reboot** to activate.

Recent commits (`git log --oneline`): stability levers `0cdc069`, config baseline
`acfb147`, captive portal `2754311`+`bd146d9`, German manual `08e2010`, second-duck
deploy `f8c7635`. The pre-session baseline is `37c7638`.

---

## HARD SAFETY RULES (do not violate)

1. **Commit LOCALLY only. NEVER `git push`.** Work on `feature/overnight-suite` (a
   feature branch), never the default branch. Commit only when the user asks.
2. **Never restart NetworkManager or cycle the "Openduck" connection while SSH'd.**
   The duck is **AP-only** (its own Wi-Fi at `wlan0` = `10.42.0.1`); bouncing NM
   drops the AP and locks you *and the user* out. Firewall changes = **nftables only**.
   Config/dnsmasq changes activate on the user's **reboot**, which you never trigger.
3. **`~/duck_config.json` is sacred.** When editing it, the per-joint `joints_offsets`
   and `imu_trim` MUST survive byte-identical — they are this robot's mechanical zero.
   Always write via `duck_config.save_config_fields()` (backup-first) and **verify the
   calibration keys are unchanged afterward** (see `apply_stability_defaults.py` for
   the pattern). Never copy one duck's config onto another.
4. **Motor safety:** any authored/played-back motion must be clamped + slew-limited;
   never assume data is in-bounds. (See the walk loop's velocity clip, the puppet's
   `slew_list`.)
5. Hardware actions are hard to reverse — confirm before deleting/overwriting on the
   robot, and report outcomes honestly (if a test failed or a step was skipped, say so).

---

## Working with the real robot

- **SSH:** `bdxv2@bdxv2.local`, password `ilovemyduck`. Your Mac must be joined to the
  duck's **"Openduck" Wi-Fi AP**. (AP-only, no internet on the duck — that's normal.)
- **Flaky link:** the Openduck AP drops intermittently. If `bdxv2.local` won't resolve
  ("Could not resolve hostname"), the **Mac fell off the AP** — nothing is broken; ask
  the user to reconnect and continue. Don't thrash retries.
- **SSH from zsh:** use a per-command `expect` wrapper that sends the password (zsh does
  NOT word-split an unquoted `$OPTS`, so the ControlMaster-string trick fails here).
  `transfer.command` itself is bash and uses ControlMaster correctly.
- **Background/interactive over SSH is unreliable** through the tooling — foreground
  short commands return cleanly; long-lived/backgrounded ones often swallow output. For
  anything interactive (e.g. reading live gamepad input), have the **user run it** and
  paste the result.
- **venv python on the duck:** `/home/bdxv2/.virtualenvs/open-duck-mini-runtime/bin/python`.
  Repo root on the duck: `/home/bdxv2/Open_Duck_Mini_Runtime`.
- **Package-path shadowing gotcha:** `mini_bdx_runtime` (the installed editable pkg)
  resolves to the **inner** `mini_bdx_runtime/mini_bdx_runtime/`. Running `python -c
  "import mini_bdx_runtime..."` **from the repo root** shadows it with the outer wrapper
  dir (only `__init__.py`) → `ModuleNotFoundError`/wrong path. Run scripts from `scripts/`
  or import from a **neutral cwd** (e.g. `/tmp`). The walk runs from `scripts/`, so it's fine.
- **The duck's clock is wrong** (RTC reads ~2025) — timestamped `.bak` filenames look
  off-date; harmless.

## Deploying code

- **`transfer.command`** (repo root, double-click / `bash transfer.command`) scp's every
  file in its `FILES` list to the duck, backing each up once as `<file>.orig`, and prints
  an ordered **new-duck bring-up checklist**. It deliberately does NOT touch
  `~/duck_config.json`. When you add/rename a runtime file, **add it to `FILES`** (the
  walk loop imports it at runtime — a missing entry = a broken duck).
- The web server is served **inside the walk/head-puppet process** — code changes to it
  (e.g. `web_control.py`) take effect only when the user **restarts the walk**.

## Editing the robot's config safely

`~/duck_config.json` is per-robot (offsets, IMU trim, feature flags), not in git. To
change one field: `duck_config.save_config_fields({...})` writes it backup-first,
preserving all other keys. Then re-read and assert `joints_offsets` + `imu_trim` are
unchanged. Known minor issue: the `.bak` timestamp is second-resolution, so two writes
in the same second collide (the git config-snapshot in `config_snapshots/` is the
canonical "known-good" restore point).

## Off-robot dev & tests

Pure modules (governor, telemetry, duck_config, battery, control_bus, web_control
routing, walk_record, face-tracker math…) unit-test with **no hardware**:
`cd mini_bdx_runtime/mini_bdx_runtime && for t in test_*.py; do python3 "$t"; done`
(15 files, all green as of this writing). The Web UI has a `webui/mock_server.py` you
can run + drive with Playwright for visual checks. Most `import board`-level modules
only import on the Pi — don't try to import the walk loop off-robot; `py_compile` it instead.

---

## Deploying to the SECOND duck

`transfer.command` ships all **code + ops kits** identically. What it cannot ship is the
**per-robot calibration** — that must be measured on each duck.

**The one-command bring-up is `scripts/first_time_setup.py`** — a guided, skippable
terminal wizard (created by Max Schmierer) that runs every step in order: dependency
check + `pip install -e .`, config creation, motor IDs, `configure_all_motors.py`,
`find_soft_offsets.py`, IMU calibration + trim (+ `imu_upside_down`),
`apply_stability_defaults.py`, expression features, Xbox Bluetooth reconnect, captive
portal, and a final verification. It orchestrates the existing scripts (their own
prompts/safety still apply), writes config backup-first, and remembers progress in
`~/.openduck_setup_progress.json`. Prefer it over the manual steps; `transfer.command`'s
printed checklist points to it too.

Never copy duck 1's `duck_config.json` to duck 2 — the offsets/trim are wrong for it.

---

## Known open items / quirks

- **Walk recording won't reliably start via D-pad while actively walking** — documented
  workaround: pause (A), hold **D-pad Left 3 s** to start, unpause and drive, tap D-pad
  Left to stop (stopping works while walking). Root cause not fully confirmed (Xbox
  Series X pad over BT may report a rotated hat / a stick-axis D-pad). `gamepad_probe.py`
  is the diagnostic — but it needs the user to press buttons live.
- The stability governor rarely fires on flat ground (that's intended — it's a
  near-fall safety layer). Tune its deadbands from the **GOV** readout in the Web UI.

## Where to look

- **`../CLAUDE.md`** — base architecture, the 50 Hz loop, joint order, per-file gotchas.
- **`docs/Bedienungsanleitung.md`** — German end-user manual: every mode + full gamepad
  button map per mode. (`.docx` alongside for printing; keep both in sync if you edit.)
- **`ops/captive-portal/`**, **`ops/bluetooth/`** — one-time system setup (sudo + reboot).
- **`config_snapshots/`** — git-tracked known-good `duck_config.json` restore points.
- **git history** — every change this session has a descriptive commit; read it before
  assuming why something is the way it is.
