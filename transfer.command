#!/bin/bash
#
# Push ALL runtime code to a duck: walk + head-puppet + Web UI + every runtime
# module, plus the ops kits (captive-portal, Xbox auto-reconnect). Existing files
# are backed up once on the duck as <file>.orig before being overwritten (re-running
# keeps that first backup), so you always have a way back ON the duck too; brand-new
# files are just created (revert = delete). List + revert hint are built from FILES.
#
# This is also the SECOND-DUCK deploy: it copies all code identically; the per-robot
# CALIBRATION + system setup that it can't ship is printed as an ordered checklist at
# the end (motor offsets, IMU trim, walk tuning, captive portal, reboot).
#
# NOTE: for a DIFFERENT hostname/user, edit REMOTE_HOST/REMOTE_USER/REMOTE_ROOT below
# (an identical clone needs no edit — just join that duck's Wi-Fi and run this).
# This deliberately does NOT touch ~/duck_config.json (per-robot: offsets, IMU trim,
# feature flags) — use scripts/apply_stability_defaults.py to seed the walk tuning.
#
# Just double-click this file (it lives in the repo root).

set -euo pipefail

# Keep the Terminal window open so you can read the output / any errors.
trap 'echo; read -r -p "Press Return to close. "' EXIT

# This script lives in the repo root; resolve it regardless of where it's run.
LOCAL_ROOT="$(cd "$(dirname "$0")" && pwd)"

REMOTE_USER="bdxv2"
REMOTE_HOST="bdxv2.local"
REMOTE_ROOT="/home/bdxv2/Open_Duck_Mini_Runtime"

# Reuse ONE ssh connection for every step -> a single password prompt.
CTRL="/tmp/transfer-${REMOTE_USER}-${REMOTE_HOST}.sock"
SSH_OPTS=(-o "ControlMaster=auto" -o "ControlPath=${CTRL}" -o "ControlPersist=120" -o "ConnectTimeout=10")
ssh_cmd() { ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "$@"; }

# "local path (relative to repo)  |  remote directory (relative to REMOTE_ROOT)"
FILES=(
  # --- scripts (entrypoints) ---
  "scripts/head_puppet.py|scripts"
  "scripts/v2_rl_walk_mujoco.py|scripts"
  "scripts/probe_battery.py|scripts"
  "scripts/find_soft_offsets.py|scripts"
  "scripts/calibrate_imu.py|scripts"
  "scripts/imu_health_check.py|scripts"
  "scripts/apply_stability_defaults.py|scripts"
  "scripts/gamepad_probe.py|scripts"
  "scripts/first_time_setup.py|scripts"
  # --- runtime library (mini_bdx_runtime package) ---
  "mini_bdx_runtime/mini_bdx_runtime/xbox_controller.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/mini_bdx_runtime/buttons.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/mini_bdx_runtime/face_tracker.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/mini_bdx_runtime/scanner_sound.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/mini_bdx_runtime/control_bus.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/mini_bdx_runtime/web_control.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/mini_bdx_runtime/telemetry.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/mini_bdx_runtime/battery.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/mini_bdx_runtime/fall_detector.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/mini_bdx_runtime/walk_record.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/mini_bdx_runtime/duck_config.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/mini_bdx_runtime/rustypot_position_hwi.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/mini_bdx_runtime/raw_imu.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/mini_bdx_runtime/imu_trim.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/mini_bdx_runtime/stability_governor.py|mini_bdx_runtime/mini_bdx_runtime"
  # --- Web UI page (served by web_control.py) ---
  "mini_bdx_runtime/mini_bdx_runtime/webui/index.html|mini_bdx_runtime/mini_bdx_runtime/webui"
  "mini_bdx_runtime/mini_bdx_runtime/webui/mock_server.py|mini_bdx_runtime/mini_bdx_runtime/webui"
  # --- scanner sounds ---
  "mini_bdx_runtime/assets/scanner/lamp.wav|mini_bdx_runtime/assets/scanner"
  "mini_bdx_runtime/assets/scanner/lamp2.wav|mini_bdx_runtime/assets/scanner"
  "mini_bdx_runtime/assets/scanner/lamp3.wav|mini_bdx_runtime/assets/scanner"
  # --- Xbox Bluetooth auto-reconnect kit (run on the Pi; see ops/bluetooth/README.md) ---
  "ops/bluetooth/README.md|ops/bluetooth"
  "ops/bluetooth/setup-bluetooth-reconnect.sh|ops/bluetooth"
  "ops/bluetooth/duck-pad-reconnect.sh|ops/bluetooth"
  "ops/bluetooth/duck-pad-reconnect.service|ops/bluetooth"
  "ops/bluetooth/install-xpadneo.md|ops/bluetooth"
  # --- Captive portal (phone joins the duck Wi-Fi -> control UI; run setup on the Pi) ---
  "ops/captive-portal/README.md|ops/captive-portal"
  "ops/captive-portal/setup-captive-portal.sh|ops/captive-portal"
  "ops/captive-portal/duck-captive.nft|ops/captive-portal"
  "ops/captive-portal/90-duck-captive|ops/captive-portal"
  "ops/captive-portal/dnsmasq-captive.conf|ops/captive-portal"
)

echo "================================================="
echo " Overnight dev suite  ->  ${REMOTE_USER}@${REMOTE_HOST}"
echo " (head-puppet + walk + web UI + new modules)"
echo "================================================="
echo

# 1) make sure every local file is present before we touch the robot
for entry in "${FILES[@]}"; do
  rel="${entry%%|*}"
  if [ ! -f "${LOCAL_ROOT}/${rel}" ]; then
    echo "ERROR: local file missing: ${LOCAL_ROOT}/${rel}"
    exit 1
  fi
done

# 2) open + verify the connection (this is where you type the password, once)
echo "Connecting to ${REMOTE_HOST} ..."
if HN="$(ssh_cmd hostname 2>/dev/null)"; then
  echo "  connected to: ${HN}"
else
  echo "ERROR: cannot SSH to ${REMOTE_USER}@${REMOTE_HOST}"
  echo "       (is the duck on and reachable? try: ping ${REMOTE_HOST})"
  exit 1
fi
echo

# 3) per file: confirm it exists on the duck, back it up once, then overwrite
NEW_FILES=()
MODIFIED_FILES=()
for entry in "${FILES[@]}"; do
  rel="${entry%%|*}"
  remdir="${entry##*|}"
  base="$(basename "${rel}")"
  remote="${REMOTE_ROOT}/${remdir}/${base}"

  echo "• ${base}"
  echo "    -> ${remote}"

  if ssh_cmd "test -f '${remote}'"; then
    # existing file: back up the robot's ORIGINAL once (cp -n keeps the first .orig)
    ssh_cmd "cp -n '${remote}' '${remote}.orig'" \
      && echo "    backed up robot original -> ${base}.orig (or kept existing)"
    MODIFIED_FILES+=("${remdir}/${base}")
  else
    # new file: ensure the remote dir exists; nothing to back up (revert = delete)
    ssh_cmd "mkdir -p '${REMOTE_ROOT}/${remdir}'"
    echo "    new file on the duck (no backup; revert = delete)"
    NEW_FILES+=("${remdir}/${base}")
  fi

  scp "${SSH_OPTS[@]}" "${LOCAL_ROOT}/${rel}" "${REMOTE_USER}@${REMOTE_HOST}:${remote}"
  echo "    transferred ✓"
  echo
done

# 4) close the shared connection
ssh "${SSH_OPTS[@]}" -O exit "${REMOTE_USER}@${REMOTE_HOST}" 2>/dev/null || true

echo "================================================="
echo " Done. ${#MODIFIED_FILES[@]} updated, ${#NEW_FILES[@]} new."
echo
cat <<'EOF'
 New / second-duck bring-up — per-robot (the code above is now identical to the ref;
 each robot still needs its OWN calibration, which is NOT auto-shipped).

 >>> EASIEST: one guided command does everything below, every step skippable:
       cd ~/Open_Duck_Mini_Runtime && python scripts/first_time_setup.py

 Or do it by hand, in THIS order:

   1. First time only: install the package in the venv:
        cd ~/Open_Duck_Mini_Runtime && pip install -e .
        (Pi 5 also: pip uninstall -y RPi.GPIO && pip install lgpio)
   2. Motors (skip if IDs already set): python scripts/configure_all_motors.py
        (a brand-new servo first: python scripts/configure_motor.py --id <n>)
   3. Zero offsets:  python scripts/find_soft_offsets.py    (writes joints_offsets)
   4. IMU:           python scripts/calibrate_imu.py
                     python scripts/imu_health_check.py      (writes imu_trim; set
                     "imu_upside_down": true if the BNO055 is mounted inverted)
   5. Walk tuning (known-good starting points -- RE-TUNE per robot):
                     python scripts/apply_stability_defaults.py
   6. Features: edit ~/duck_config.json "expression_features"
                (camera/speaker/antennas/projector). Web UI on by default
                ("web_ui": false to disable, "web_port": 8080). Battery under
                "battery". Face tracking needs cv2 in the venv.
   7. Phone captive portal (join Wi-Fi -> control UI, stays connected):
                     sudo bash ops/captive-portal/setup-captive-portal.sh
                     sudo reboot         # activates the DNS half
   8. Xbox auto-reconnect (optional):
                     bash ops/bluetooth/setup-bluetooth-reconnect.sh

 NOTE: for a DIFFERENT hostname/user, edit REMOTE_* at the top. If the second duck
 is an identical clone (user bdxv2, host bdxv2.local), just connect your Mac to
 ITS Wi-Fi and re-run this script -- it targets whichever duck you're joined to.
EOF
echo
echo " To REVERT on the duck:"
if [ "${#MODIFIED_FILES[@]}" -gt 0 ]; then
  echo "   restore overwritten files from their .orig backups:"
  for f in "${MODIFIED_FILES[@]}"; do
    echo "     mv '${REMOTE_ROOT}/${f}.orig' '${REMOTE_ROOT}/${f}'"
  done
fi
if [ "${#NEW_FILES[@]}" -gt 0 ]; then
  echo "   delete the new files:"
  for f in "${NEW_FILES[@]}"; do
    echo "     rm -f '${REMOTE_ROOT}/${f}'"
  done
fi
echo "================================================="
