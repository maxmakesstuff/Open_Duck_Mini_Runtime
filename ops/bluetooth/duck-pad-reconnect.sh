#!/usr/bin/env bash
#
# duck-pad-reconnect.sh
#
# Belt-and-suspenders watchdog: keep the paired gamepads connected to the Pi
# without ever restarting the robot. It watches EVERY paired gamepad, so a pad
# added or forgotten in the Web UI (Gamepad tab) is picked up without touching
# this service. PAD_MAC is optional: a pad named there is watched too, as long
# as it is still paired. While no watched pad is connected, it issues
# `bluetoothctl connect <MAC>` on a capped exponential backoff. As soon as
# the pad is switched back on (it advertises), BlueZ + this loop rejoin it within
# a few seconds.
#
# Meant to run under systemd (see duck-pad-reconnect.service) but also runs
# standalone:  PAD_MAC=AA:BB:CC:DD:EE:FF ./duck-pad-reconnect.sh
#
set -euo pipefail

# ----------------------------------------------------------------------------
# Config (all overridable via environment / EnvironmentFile)
# ----------------------------------------------------------------------------
PAD_MAC="${PAD_MAC:-${1:-}}"          # optional extra controller MAC; arg or env
POLL_INTERVAL="${POLL_INTERVAL:-5}"   # seconds between "is it connected?" checks
BACKOFF_MIN="${BACKOFF_MIN:-2}"       # first reconnect backoff (seconds)
BACKOFF_MAX="${BACKOFF_MAX:-30}"      # cap for reconnect backoff (seconds)
# The Web UI's Gamepad tab touches this file while it searches, pairs or connects.
# While it is fresh we stay off the adapter: BlueZ refuses to search while one of
# our connection attempts is pending.
HOLD_FILE="${HOLD_FILE:-/dev/shm/duck-gamepad-hold}"
HOLD_SECONDS="${HOLD_SECONDS:-60}"

log() { printf '%s duck-pad-reconnect: %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"; }

if [[ -n "$PAD_MAC" ]] && ! [[ "$PAD_MAC" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; then
    log "WARNING: PAD_MAC='$PAD_MAC' is not a valid MAC (expected AA:BB:CC:DD:EE:FF); ignoring it."
    PAD_MAC=""
fi
PAD_MAC="${PAD_MAC^^}"

if ! command -v bluetoothctl >/dev/null 2>&1; then
    log "ERROR: bluetoothctl not found."
    exit 3
fi

# ----------------------------------------------------------------------------
# Clean shutdown
# ----------------------------------------------------------------------------
RUNNING=1
on_term() { log "Received stop signal; exiting."; RUNNING=0; }
trap on_term TERM INT

# Signal-responsive sleep: backgrounding sleep + `wait` lets a trapped SIGTERM
# interrupt it immediately (plain `sleep` would delay clean shutdown by up to N s).
nap() {
    sleep "$1" &
    wait "$!" 2>/dev/null || true
}

# True if the given pad currently reports Connected: yes.
is_connected() {
    bluetoothctl info "$1" 2>/dev/null | grep -q "Connected: yes"
}

# True while the Web UI has asked us to pause (see HOLD_FILE).
held() {
    local t
    t="$(stat -c %Y "$HOLD_FILE" 2>/dev/null)" || return 1
    (( $(date +%s) - t < HOLD_SECONDS ))
}

# Print the MACs to watch, one per line: every paired gamepad, plus PAD_MAC if
# it is still paired. A pad forgotten in the Web UI drops out by itself.
watched_pads() {
    local mac info
    {
        # BlueZ >= 5.65 has `devices Paired`; older ones have `paired-devices`.
        bluetoothctl devices Paired 2>/dev/null || true
        bluetoothctl paired-devices 2>/dev/null || true
    } | awk '$1 == "Device" { print toupper($2) }' | sort -u | while read -r mac; do
        info="$(bluetoothctl info "$mac" 2>/dev/null || true)"
        grep -q "Paired: yes" <<<"$info" || continue
        if [[ "$mac" == "$PAD_MAC" ]] || grep -q "Icon: input-gaming" <<<"$info"; then
            printf '%s\n' "$mac"
        fi
    done
}

# Make sure the adapter is powered and we hold an agent (needed after a
# bluetooth.service restart). Idempotent; cheap to call.
ensure_adapter() {
    bluetoothctl power on      >/dev/null 2>&1 || true
    bluetoothctl agent on      >/dev/null 2>&1 || true
    bluetoothctl default-agent >/dev/null 2>&1 || true
}

# ----------------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------------
log "Watching paired gamepads${PAD_MAC:+ and $PAD_MAC} (poll ${POLL_INTERVAL}s, backoff ${BACKOFF_MIN}-${BACKOFF_MAX}s)."
ensure_adapter

backoff="$BACKOFF_MIN"
while [[ "$RUNNING" -eq 1 ]]; do
    if held; then
        backoff="$BACKOFF_MIN"
        nap 2
        continue
    fi
    mapfile -t pads < <(watched_pads)

    # Nothing paired: nothing to do, and above all no scanning.
    if [[ "${#pads[@]}" -eq 0 ]]; then
        backoff="$BACKOFF_MIN"
        nap "$POLL_INTERVAL"
        continue
    fi

    # One connected pad is enough: reset backoff and poll slowly.
    any=0
    for mac in "${pads[@]}"; do
        if is_connected "$mac"; then any=1; break; fi
    done
    if [[ "$any" -eq 1 ]]; then
        backoff="$BACKOFF_MIN"
        nap "$POLL_INTERVAL"
        continue
    fi

    ensure_adapter
    log "No pad connected -> attempting reconnect of ${pads[*]} (next retry backoff ${backoff}s)."
    # A short scan window helps BlueZ notice a pad that just powered on.
    bluetoothctl --timeout 4 scan on >/dev/null 2>&1 || true
    for mac in "${pads[@]}"; do
        held && break
        if bluetoothctl connect "$mac" >/dev/null 2>&1 && is_connected "$mac"; then
            log "Reconnected $mac."
            any=1
            break
        fi
    done
    if [[ "$any" -eq 1 ]]; then
        backoff="$BACKOFF_MIN"
        continue
    fi

    log "Reconnect attempt failed; sleeping ${backoff}s."
    nap "$backoff"
    # Exponential backoff, capped.
    backoff=$(( backoff * 2 ))
    (( backoff > BACKOFF_MAX )) && backoff="$BACKOFF_MAX"
done

log "Stopped."
exit 0
