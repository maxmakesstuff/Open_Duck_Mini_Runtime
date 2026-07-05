#!/usr/bin/env bash
#
# duck-pad-reconnect.sh
#
# Belt-and-suspenders watchdog: keep the trusted Xbox controller connected to
# the Pi without ever restarting the robot. Whenever the pad is not connected,
# issue `bluetoothctl connect <MAC>` on a capped exponential backoff. As soon as
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
PAD_MAC="${PAD_MAC:-${1:-}}"          # controller MAC; arg or env
POLL_INTERVAL="${POLL_INTERVAL:-5}"   # seconds between "is it connected?" checks
BACKOFF_MIN="${BACKOFF_MIN:-2}"       # first reconnect backoff (seconds)
BACKOFF_MAX="${BACKOFF_MAX:-30}"      # cap for reconnect backoff (seconds)

log() { printf '%s duck-pad-reconnect: %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"; }

if [[ -z "$PAD_MAC" ]]; then
    log "ERROR: PAD_MAC is not set. Set it in /etc/default/duck-pad-reconnect or pass as arg."
    exit 2
fi
if ! [[ "$PAD_MAC" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; then
    log "ERROR: PAD_MAC='$PAD_MAC' is not a valid MAC (expected AA:BB:CC:DD:EE:FF)."
    exit 2
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

# True if the pad currently reports Connected: yes.
is_connected() {
    bluetoothctl info "$PAD_MAC" 2>/dev/null | grep -q "Connected: yes"
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
log "Watching controller $PAD_MAC (poll ${POLL_INTERVAL}s, backoff ${BACKOFF_MIN}-${BACKOFF_MAX}s)."
ensure_adapter

# Warn (once) if the pad isn't trusted -- auto-reconnect is unreliable without it.
if ! bluetoothctl info "$PAD_MAC" 2>/dev/null | grep -q "Trusted: yes"; then
    log "WARNING: $PAD_MAC is not marked Trusted. Run: bluetoothctl trust $PAD_MAC"
fi

backoff="$BACKOFF_MIN"
while [[ "$RUNNING" -eq 1 ]]; do
    if is_connected; then
        # Connected and happy: reset backoff and poll slowly.
        backoff="$BACKOFF_MIN"
        nap "$POLL_INTERVAL"
        continue
    fi

    ensure_adapter
    log "Pad not connected -> attempting reconnect (next retry backoff ${backoff}s)."
    # A short scan window helps BlueZ notice a pad that just powered on.
    bluetoothctl --timeout 4 scan on >/dev/null 2>&1 || true
    if bluetoothctl connect "$PAD_MAC" >/dev/null 2>&1 && is_connected; then
        log "Reconnected $PAD_MAC."
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
