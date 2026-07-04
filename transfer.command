#!/bin/bash
#
# Push the overnight dev suite to the duck (head-puppet + WALK + Web UI + all the
# new runtime modules). Existing files are backed up once on the duck as
# <file>.orig before being overwritten (re-running keeps that first backup), so
# you always have a way back ON the duck too; brand-new files are just created
# (revert = delete). The list + revert hint at the end are generated from FILES.
#
# NOTE: for a DIFFERENT duck, edit REMOTE_HOST/REMOTE_USER/REMOTE_ROOT below.
# This does NOT touch ~/duck_config.json (per-robot: offsets + feature flags).
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
echo " Per-robot setup still needed (NOT shipped by this script):"
echo "   • ~/duck_config.json: set expression_features camera/speaker/antennas/"
echo "     projector as desired; web UI is on by default (\"web_ui\": false to"
echo "     disable, \"web_port\": 8080). Battery mapping under \"battery\"."
echo "   • Web UI needs no extra deps (stdlib). Face tracking needs cv2 in the venv."
echo "   • Xbox auto-reconnect: run ops/bluetooth/setup-bluetooth-reconnect.sh on the Pi."
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
