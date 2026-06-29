#!/bin/bash
#
# Push the HEAD-PUPPET + face-tracking update to the duck and nothing else.
# Pushes these files (the walk is left untouched):
#   scripts/head_puppet.py                                 (overwrite)
#   mini_bdx_runtime/mini_bdx_runtime/xbox_controller.py   (overwrite)
#   mini_bdx_runtime/mini_bdx_runtime/buttons.py           (overwrite)
#   mini_bdx_runtime/mini_bdx_runtime/face_tracker.py      (new)
#   mini_bdx_runtime/mini_bdx_runtime/scanner_sound.py     (new)
#   mini_bdx_runtime/assets/scanner/lamp{,2,3}.wav         (new)
#
# Before overwriting, the robot's current copy of each file is backed up once
# as <file>.orig (re-running won't clobber that first backup), so you always
# have a way back ON the duck too.
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
  "scripts/head_puppet.py|scripts"
  "mini_bdx_runtime/mini_bdx_runtime/xbox_controller.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/mini_bdx_runtime/buttons.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/mini_bdx_runtime/face_tracker.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/mini_bdx_runtime/scanner_sound.py|mini_bdx_runtime/mini_bdx_runtime"
  "mini_bdx_runtime/assets/scanner/lamp.wav|mini_bdx_runtime/assets/scanner"
  "mini_bdx_runtime/assets/scanner/lamp2.wav|mini_bdx_runtime/assets/scanner"
  "mini_bdx_runtime/assets/scanner/lamp3.wav|mini_bdx_runtime/assets/scanner"
)

echo "================================================="
echo " Head-puppet update  ->  ${REMOTE_USER}@${REMOTE_HOST}"
echo " (the walk script is NOT touched)"
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
  else
    # new file: ensure the remote dir exists; nothing to back up (revert = delete)
    ssh_cmd "mkdir -p '${REMOTE_ROOT}/${remdir}'"
    echo "    new file on the duck (no backup; revert = delete)"
  fi

  scp "${SSH_OPTS[@]}" "${LOCAL_ROOT}/${rel}" "${REMOTE_USER}@${REMOTE_HOST}:${remote}"
  echo "    transferred ✓"
  echo
done

# 4) close the shared connection
ssh "${SSH_OPTS[@]}" -O exit "${REMOTE_USER}@${REMOTE_HOST}" 2>/dev/null || true

echo "================================================="
echo " Done. Head puppet updated; walk untouched."
echo
echo " To revert ON THE DUCK, restore the .orig files, e.g.:"
echo "   ssh ${REMOTE_USER}@${REMOTE_HOST} \\"
echo "     'cd ${REMOTE_ROOT} && \\"
echo "      mv scripts/head_puppet.py.orig scripts/head_puppet.py && \\"
echo "      mv mini_bdx_runtime/mini_bdx_runtime/xbox_controller.py.orig mini_bdx_runtime/mini_bdx_runtime/xbox_controller.py && \\"
echo "      mv mini_bdx_runtime/mini_bdx_runtime/buttons.py.orig mini_bdx_runtime/mini_bdx_runtime/buttons.py && \\"
echo "      rm -f mini_bdx_runtime/mini_bdx_runtime/face_tracker.py && \\"
echo "      rm -f mini_bdx_runtime/mini_bdx_runtime/scanner_sound.py && \\"
echo "      rm -rf mini_bdx_runtime/assets/scanner'   # new files: delete to revert"
echo "================================================="
