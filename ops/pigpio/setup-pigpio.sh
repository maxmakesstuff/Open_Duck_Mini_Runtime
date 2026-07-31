#!/usr/bin/env bash
#
# setup-pigpio.sh
#
# Idempotent installer for the ear-antenna JITTER FIX (Open Duck Mini).
#
# The ears are analog servos. Driven by CircuitPython pwmio (software-timed PWM)
# their pulse edges jitter whenever the Pi is busy, so the ears twitch constantly.
# pigpio generates the PWM from a HARDWARE timer, so the pulse width is exact and
# the ears sit still unless commanded. antennas.py prefers pigpio automatically and
# falls back to pwmio if pigpiod isn't running — so installing this makes the fix
# active with no code change.
#
# I2S SAFETY: this duck's max98357A speaker uses I2S (the PCM clock). pigpio
# defaults to that SAME PCM clock, which would corrupt the audio and can DAMAGE the
# speaker. We therefore run pigpiod with '-t 0' (the PWM clock) via a systemd
# override so servo PWM and speaker audio never fight. (Credit: antenna fix by
# Brad3D; I2S coexistence by Elpidiovaldez5.)
#
# Run ON THE PI with sudo:
#     sudo ./setup-pigpio.sh
#
# Safe to re-run: every step is guarded and re-applying it is a no-op.
#
set -euo pipefail

GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; BOLD=$'\033[1m'; RST=$'\033[0m'
log()  { printf '%s[*]%s %s\n' "$GREEN"  "$RST" "$*"; }
warn() { printf '%s[!]%s %s\n' "$YELLOW" "$RST" "$*"; }
err()  { printf '%s[x]%s %s\n' "$RED"    "$RST" "$*" >&2; }
step() { printf '\n%s==>%s %s%s%s\n' "$BOLD" "$RST" "$BOLD" "$*" "$RST"; }

write_if_changed() {   # $1 path, $2 content ; returns 0 if it wrote
    local path="$1" content="$2"
    if [[ -f "$path" ]] && printf '%s' "$content" | cmp -s - "$path"; then
        return 1
    fi
    mkdir -p "$(dirname "$path")"
    printf '%s' "$content" > "$path"
    return 0
}

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    err "This script must be run as root (use: sudo $0)."
    exit 1
fi

CHANGES=()
record() { CHANGES+=("$1"); }

# ----------------------------------------------------------------------------
# 1. Install pigpio (daemon + python client)
# ----------------------------------------------------------------------------
step "1/4  Install pigpio (pigpiod + python3-pigpio)"
if command -v pigpiod >/dev/null 2>&1 && python3 -c 'import pigpio' >/dev/null 2>&1; then
    log "pigpiod and python3-pigpio already present."
else
    log "Installing via apt-get..."
    apt-get update -y
    apt-get install -y pigpio python3-pigpio
    record "apt: pigpio python3-pigpio"
fi

# ----------------------------------------------------------------------------
# 2. Install the -t 0 override so pigpio uses the PWM clock (NOT I2S's PCM clock)
# ----------------------------------------------------------------------------
step "2/4  Configure pigpiod for the PWM clock (I2S-safe: -t 0)"
OVERRIDE_DIR=/etc/systemd/system/pigpiod.service.d
OVERRIDE=$OVERRIDE_DIR/override.conf
read -r -d '' OVERRIDE_CONTENT <<'EOF' || true
# Installed by ops/pigpio/setup-pigpio.sh (Open Duck Mini).
# Run pigpiod on the PWM clock peripheral (-t 0) instead of its default PCM clock.
# The max98357A speaker uses I2S/PCM; leaving pigpio on PCM corrupts the audio and
# can DAMAGE the speaker. -t 0 keeps servo PWM and speaker audio apart.
[Service]
ExecStart=
ExecStart=/usr/bin/pigpiod -l -t 0
EOF

if write_if_changed "$OVERRIDE" "$OVERRIDE_CONTENT"; then
    log "Wrote $OVERRIDE (pigpiod -t 0)"
    record "systemd override: pigpiod -t 0"
    systemctl daemon-reload
else
    log "$OVERRIDE already up to date."
fi

# ----------------------------------------------------------------------------
# 3. Enable + (re)start pigpiod on boot
# ----------------------------------------------------------------------------
step "3/4  Enable pigpiod on boot"
systemctl enable pigpiod >/dev/null 2>&1 || warn "enable pigpiod reported an error."
# restart (not just start) so a freshly written override takes effect immediately.
systemctl restart pigpiod || warn "Could not (re)start pigpiod."
sleep 1
record "service: pigpiod enabled + started"

# ----------------------------------------------------------------------------
# 4. Verify
# ----------------------------------------------------------------------------
step "4/4  Verify"
ok=1
if systemctl is-active --quiet pigpiod; then
    log "pigpiod is active."
else
    err "pigpiod is NOT active — check: systemctl status pigpiod"; ok=0
fi
# Confirm the running command actually carries -t 0.
if systemctl show pigpiod -p ExecStart 2>/dev/null | grep -q -- '-t 0'; then
    log "Running with -t 0 (PWM clock; I2S-safe)."
else
    warn "Could not confirm '-t 0' in the running ExecStart — check: systemctl cat pigpiod"
fi
# Confirm the python client can connect (this is what antennas.py uses).
if python3 - <<'PY' 2>/dev/null; then
import pigpio, sys
pi = pigpio.pi()
sys.exit(0 if pi.connected else 1)
PY
    log "python3 pigpio client connected to the daemon."
else
    warn "python3 pigpio client could NOT connect. antennas.py will fall back to pwmio."
    ok=0
fi

echo
if [[ "${#CHANGES[@]}" -eq 0 ]]; then
    log "No changes were needed — everything was already configured."
else
    log "Applied changes:"; for c in "${CHANGES[@]}"; do printf '      - %s\n' "$c"; done
fi
echo
if [[ "$ok" -eq 1 ]]; then
    log "${BOLD}Antenna jitter fix is active.${RST} Restart the walk / head puppet;"
    log "antennas.py will report: [antennas] pigpio hardware PWM (jitter-free)."
else
    warn "Something needs attention above; the robot still runs (pwmio fallback)."
fi
