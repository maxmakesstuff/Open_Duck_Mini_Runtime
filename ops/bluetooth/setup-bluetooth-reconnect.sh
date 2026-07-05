#!/usr/bin/env bash
#
# setup-bluetooth-reconnect.sh
#
# Idempotent OS-level fix kit for an Xbox wireless controller that drops off a
# Raspberry Pi (Raspberry Pi OS / BlueZ) after ~10 minutes and won't re-pair
# without a reboot.
#
# It addresses four well-known root causes:
#   1. btusb (USB Bluetooth radio) USB autosuspend powering the radio down.
#   2. BlueZ not auto-reconnecting a trusted device after it powers back on.
#   3. Xbox pads' ERTM quirk on Linux (needs bluetooth.disable_ertm=1).
#   4. (documented, not forced) Wi-Fi/BT 2.4 GHz coexistence interference.
#
# Run ON THE PI with sudo:
#     sudo ./setup-bluetooth-reconnect.sh [CONTROLLER_MAC]
#
# CONTROLLER_MAC is optional; if omitted the script asks interactively (and can
# scan for you). Format: AA:BB:CC:DD:EE:FF.
#
# Safe to re-run: every mutating step is guarded and re-applying it is a no-op.
#
set -euo pipefail

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; BOLD=$'\033[1m'; RST=$'\033[0m'
log()   { printf '%s[*]%s %s\n'  "$GREEN"  "$RST" "$*"; }
warn()  { printf '%s[!]%s %s\n'  "$YELLOW" "$RST" "$*"; }
err()   { printf '%s[x]%s %s\n'  "$RED"    "$RST" "$*" >&2; }
step()  { printf '\n%s==>%s %s%s%s\n' "$BOLD" "$RST" "$BOLD" "$*" "$RST"; }

# Track whether anything we changed needs a reboot to take effect.
REBOOT_NEEDED=0
CHANGES=()
record_change() { CHANGES+=("$1"); }

# Write $2 to file $1 only if the content differs. Returns 0 if it wrote.
write_if_changed() {
    local path="$1" content="$2"
    if [[ -f "$path" ]] && printf '%s' "$content" | cmp -s - "$path"; then
        return 1   # unchanged
    fi
    printf '%s' "$content" > "$path"
    return 0
}

require_root() {
    if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
        err "This script must be run as root (use: sudo $0 ...)."
        exit 1
    fi
}

valid_mac() {
    [[ "$1" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]
}

# ----------------------------------------------------------------------------
# 0. Preconditions
# ----------------------------------------------------------------------------
require_root

if ! command -v bluetoothctl >/dev/null 2>&1; then
    err "bluetoothctl not found. Install BlueZ first: sudo apt-get install -y bluez"
    exit 1
fi

MAC="${1:-}"

# ----------------------------------------------------------------------------
# 1. Disable btusb USB autosuspend (root cause #1)
#    - Persistent module option: btusb.enable_autosuspend=0
#    - Fallback udev rule: force power/control=on for the Bluetooth USB device.
# ----------------------------------------------------------------------------
step "1/6  Disable btusb autosuspend"

MODPROBE_BTUSB=/etc/modprobe.d/duck-btusb-noautosuspend.conf
read -r -d '' MODPROBE_BTUSB_CONTENT <<'EOF' || true
# Installed by setup-bluetooth-reconnect.sh (Open Duck Mini).
# Stop the kernel from USB-autosuspending the Bluetooth radio, which is the
# classic cause of an Xbox pad dropping after a few idle minutes.
options btusb enable_autosuspend=0
EOF

if write_if_changed "$MODPROBE_BTUSB" "$MODPROBE_BTUSB_CONTENT"; then
    log "Wrote $MODPROBE_BTUSB (options btusb enable_autosuspend=0)"
    record_change "modprobe: btusb.enable_autosuspend=0"
    # This only takes effect when the btusb module is (re)loaded -> reboot.
    REBOOT_NEEDED=1
else
    log "$MODPROBE_BTUSB already up to date."
fi

# Fallback udev rule: for any USB device with bDeviceClass 0xe0 (Wireless
# controller -> Bluetooth) OR the common CSR/onboard IDs, force runtime PM off.
# This helps immediately (no reboot) once udev reloads, even before the module
# option kicks in, and also covers onboard radios exposed over USB.
UDEV_RULE=/etc/udev/rules.d/81-duck-bt-power.rules
read -r -d '' UDEV_RULE_CONTENT <<'EOF' || true
# Installed by setup-bluetooth-reconnect.sh (Open Duck Mini).
# Force USB runtime power management OFF for Bluetooth radios so the pad's link
# is never suspended. Matches the Bluetooth device class (bDeviceClass e0) and
# a few very common dongle vendor IDs. Harmless if none match.
ACTION=="add", SUBSYSTEM=="usb", ATTR{bDeviceClass}=="e0", TEST=="power/control", ATTR{power/control}="on"
# Cambridge Silicon Radio (CSR) dongles are extremely common on the Pi:
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="0a12", TEST=="power/control", ATTR{power/control}="on"
# Broadcom / Cypress (onboard Pi radio families):
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="0a5c", TEST=="power/control", ATTR{power/control}="on"
EOF

if write_if_changed "$UDEV_RULE" "$UDEV_RULE_CONTENT"; then
    log "Wrote $UDEV_RULE (udev fallback: power/control=on)"
    record_change "udev: force USB power/control=on for BT radios"
    if command -v udevadm >/dev/null 2>&1; then
        udevadm control --reload-rules && udevadm trigger --subsystem-match=usb || \
            warn "udevadm reload/trigger failed; rule applies on next boot."
        log "Reloaded udev rules and triggered USB re-evaluation."
    fi
else
    log "$UDEV_RULE already up to date."
fi

# Best-effort: apply power/control=on to any live BT USB device right now so the
# fix helps in the current session without waiting for a reboot.
applied_live=0
for dev in /sys/bus/usb/devices/*/power/control; do
    [[ -w "$dev" ]] || continue
    devdir="$(dirname "$(dirname "$dev")")"
    cls="$(cat "$devdir/bDeviceClass" 2>/dev/null || echo '')"
    vid="$(cat "$devdir/idVendor"     2>/dev/null || echo '')"
    if [[ "$cls" == "e0" || "$vid" == "0a12" || "$vid" == "0a5c" ]]; then
        if [[ "$(cat "$dev" 2>/dev/null)" != "on" ]]; then
            echo on > "$dev" 2>/dev/null && { applied_live=1; log "Set power/control=on for $devdir"; }
        fi
    fi
done
[[ "$applied_live" -eq 0 ]] && log "No live BT USB device needed a runtime-PM change (or none matched)."

# ----------------------------------------------------------------------------
# 2. Disable ERTM for Xbox pads (root cause #3)
#    Preferred: kernel module param via /etc/modprobe.d (no cmdline edit).
#    Also print the cmdline alternative in case the platform ignores the module
#    option (bluetooth is often built-in on some kernels).
# ----------------------------------------------------------------------------
step "2/6  Disable Bluetooth ERTM (Xbox controller quirk)"

MODPROBE_ERTM=/etc/modprobe.d/duck-bluetooth-disable-ertm.conf
read -r -d '' MODPROBE_ERTM_CONTENT <<'EOF' || true
# Installed by setup-bluetooth-reconnect.sh (Open Duck Mini).
# Xbox controllers frequently fail to (re)connect on Linux with Enhanced
# Retransmission Mode enabled. Disable it globally for the bluetooth module.
options bluetooth disable_ertm=1
EOF

if write_if_changed "$MODPROBE_ERTM" "$MODPROBE_ERTM_CONTENT"; then
    log "Wrote $MODPROBE_ERTM (options bluetooth disable_ertm=1)"
    record_change "modprobe: bluetooth.disable_ertm=1"
    REBOOT_NEEDED=1
else
    log "$MODPROBE_ERTM already up to date."
fi

# Try to flip it live too (works only if the param is writable this boot).
ERTM_PARAM=/sys/module/bluetooth/parameters/disable_ertm
if [[ -w "$ERTM_PARAM" ]]; then
    if [[ "$(cat "$ERTM_PARAM")" != "Y" && "$(cat "$ERTM_PARAM")" != "1" ]]; then
        echo Y > "$ERTM_PARAM" 2>/dev/null && log "Set disable_ertm live (=Y) for this session." || \
            warn "Could not set $ERTM_PARAM live; will apply on reboot."
    else
        log "ERTM already disabled live."
    fi
else
    warn "$ERTM_PARAM not writable now."
    warn "If ERTM is still enabled after reboot, the bluetooth stack is built-in;"
    warn "add this to the kernel command line instead (single line, space-separated):"
    warn "    bluetooth.disable_ertm=1"
    warn "  Raspberry Pi OS cmdline file: /boot/firmware/cmdline.txt  (older: /boot/cmdline.txt)"
    warn "  Append it to the END of the existing single line, then reboot."
fi

# ----------------------------------------------------------------------------
# 3. BlueZ policy: AutoEnable + reconnect attempts/intervals (root cause #2)
# ----------------------------------------------------------------------------
step "3/6  Configure BlueZ auto-reconnect policy (/etc/bluetooth/main.conf)"

MAIN_CONF=/etc/bluetooth/main.conf
DROPIN_DIR=/etc/bluetooth/main.conf.d
DROPIN=/etc/bluetooth/main.conf.d/duck-reconnect.conf

read -r -d '' MAINCONF_SNIPPET <<'EOF' || true
# Installed by setup-bluetooth-reconnect.sh (Open Duck Mini).
[General]
# Retry connecting to a known device this many times before giving up.
ReconnectAttempts=7
# Backoff (seconds) between those attempts. BlueZ repeats the last value.
ReconnectIntervals=1,2,4,8,16,32,64

[Policy]
# Power the adapter on automatically at boot and auto-connect trusted devices.
AutoEnable=true
EOF

# BlueZ >= 5.x reads drop-ins from main.conf.d on most Raspberry Pi OS builds.
# Use the drop-in when the directory exists; otherwise append a guarded block
# to main.conf (never edit existing keys, just add our clearly marked block).
if [[ -d "$DROPIN_DIR" ]] || mkdir -p "$DROPIN_DIR" 2>/dev/null; then
    if write_if_changed "$DROPIN" "$MAINCONF_SNIPPET"; then
        log "Wrote $DROPIN"
        record_change "BlueZ: AutoEnable=true + ReconnectAttempts/Intervals"
    else
        log "$DROPIN already up to date."
    fi
else
    # Fallback: manage a marked block inside main.conf itself.
    MARK_BEGIN="# >>> duck-pad-reconnect BEGIN >>>"
    MARK_END="# <<< duck-pad-reconnect END <<<"
    BLOCK="${MARK_BEGIN}
${MAINCONF_SNIPPET}
${MARK_END}"
    if [[ -f "$MAIN_CONF" ]] && grep -qF "$MARK_BEGIN" "$MAIN_CONF"; then
        # Replace existing marked block.
        tmp="$(mktemp)"
        awk -v b="$MARK_BEGIN" -v e="$MARK_END" '
            $0==b {skip=1}
            skip==0 {print}
            $0==e {skip=0}
        ' "$MAIN_CONF" > "$tmp"
        printf '\n%s\n' "$BLOCK" >> "$tmp"
        if ! cmp -s "$tmp" "$MAIN_CONF"; then
            cp -a "$MAIN_CONF" "${MAIN_CONF}.duckbak.$(date +%s)"
            mv "$tmp" "$MAIN_CONF"
            log "Updated marked block in $MAIN_CONF (backup kept)."
            record_change "BlueZ: AutoEnable + reconnect (main.conf block)"
        else
            rm -f "$tmp"; log "$MAIN_CONF marked block already up to date."
        fi
    else
        [[ -f "$MAIN_CONF" ]] && cp -a "$MAIN_CONF" "${MAIN_CONF}.duckbak.$(date +%s)"
        printf '\n%s\n' "$BLOCK" >> "$MAIN_CONF"
        log "Appended marked block to $MAIN_CONF (backup kept)."
        record_change "BlueZ: AutoEnable + reconnect (main.conf block)"
    fi
fi

# Restart bluetooth so main.conf changes load. Safe/idempotent.
if command -v systemctl >/dev/null 2>&1; then
    systemctl restart bluetooth 2>/dev/null || warn "Could not restart bluetooth.service (continuing)."
    # Give the adapter a moment to come up before we talk to it.
    sleep 2
    log "Restarted bluetooth.service."
fi

# ----------------------------------------------------------------------------
# 4. Make sure the adapter is powered and pairable, then pair+trust the pad.
# ----------------------------------------------------------------------------
step "4/6  Pair and TRUST the controller"

# Bring the controller MAC in if not given.
if [[ -z "$MAC" ]]; then
    warn "No controller MAC given on the command line."
    read -r -p "Enter Xbox controller MAC (AA:BB:CC:DD:EE:FF), or press Enter to scan: " MAC || true
    if [[ -z "$MAC" ]]; then
        log "Scanning for 15s. Put the controller in pairing mode (hold the pair button until the light flashes fast)."
        bluetoothctl --timeout 15 scan on || true
        echo
        log "Known/seen devices:"
        bluetoothctl devices || true
        read -r -p "Now enter the controller MAC from the list above: " MAC || true
    fi
fi

if [[ -z "$MAC" ]] || ! valid_mac "$MAC"; then
    warn "No valid MAC provided; skipping pair/trust."
    warn "Re-run later with: sudo $0 AA:BB:CC:DD:EE:FF"
else
    MAC="${MAC^^}"   # normalize to uppercase
    log "Target controller: $MAC"

    # Ensure adapter is up. These are all idempotent.
    bluetoothctl power on            >/dev/null 2>&1 || true
    bluetoothctl agent on            >/dev/null 2>&1 || true
    bluetoothctl default-agent       >/dev/null 2>&1 || true

    already_paired=0
    if bluetoothctl info "$MAC" 2>/dev/null | grep -q "Paired: yes"; then
        already_paired=1
        log "Controller already paired."
    fi

    if [[ "$already_paired" -eq 0 ]]; then
        log "Pairing... put the pad in pairing mode now (fast-flashing Xbox light)."
        bluetoothctl --timeout 20 scan on >/dev/null 2>&1 || true
        bluetoothctl pair "$MAC"    || warn "pair reported an error (may already be paired)."
        bluetoothctl connect "$MAC" || warn "connect reported an error (will be retried by the service)."
        record_change "Paired controller $MAC"
    fi

    # TRUST is the important bit for auto-reconnect after the pad powers off/on.
    if bluetoothctl info "$MAC" 2>/dev/null | grep -q "Trusted: yes"; then
        log "Controller already trusted."
    else
        bluetoothctl trust "$MAC" && { log "Marked $MAC as TRUSTED."; record_change "Trusted controller $MAC"; } \
            || warn "Could not mark trusted; re-run once the pad is visible."
    fi

    # Persist the MAC for the watchdog service (root cause #2 belt-and-suspenders).
    ENV_FILE=/etc/default/duck-pad-reconnect
    read -r -d '' ENV_CONTENT <<EOF2 || true
# Installed by setup-bluetooth-reconnect.sh (Open Duck Mini).
# MAC of the trusted Xbox controller the watchdog service reconnects.
PAD_MAC=$MAC
EOF2
    if write_if_changed "$ENV_FILE" "$ENV_CONTENT"; then
        log "Wrote $ENV_FILE (PAD_MAC=$MAC) for the watchdog service."
        record_change "Env: PAD_MAC=$MAC for watchdog"
    else
        log "$ENV_FILE already up to date."
    fi
fi

# ----------------------------------------------------------------------------
# 5. Wi-Fi / BT 2.4 GHz coexistence note (root cause #4) -- documented, not forced
# ----------------------------------------------------------------------------
step "5/6  Wi-Fi/BT coexistence (informational)"
cat <<'EOF'
    The Pi's onboard Wi-Fi and Bluetooth share one 2.4 GHz antenna. Heavy 2.4 GHz
    Wi-Fi traffic can starve the BT link and cause drops that look like this bug.
    Mitigations (apply only if drops persist after the above):
      - Move the AP/router to 5 GHz for the Pi, or pin the Pi's Wi-Fi to 5 GHz.
      - If the robot runs headless over Ethernet/AP-less, consider disabling
        onboard Wi-Fi entirely:  add 'dtoverlay=disable-wifi' to /boot/firmware/config.txt
      - Keep the controller within a few meters; avoid USB3 devices next to the
        radio (USB3 emits 2.4 GHz noise).
    This script does NOT change your Wi-Fi setup automatically.
EOF

# ----------------------------------------------------------------------------
# 6. Summary
# ----------------------------------------------------------------------------
step "6/6  Summary"
if [[ "${#CHANGES[@]}" -eq 0 ]]; then
    log "No changes were needed -- everything was already configured."
else
    log "Applied changes:"
    for c in "${CHANGES[@]}"; do printf '      - %s\n' "$c"; done
fi

echo
if [[ "$REBOOT_NEEDED" -eq 1 ]]; then
    warn "${BOLD}A REBOOT IS RECOMMENDED${RST} so the modprobe options (btusb autosuspend,"
    warn "bluetooth ERTM) load cleanly. Run:  sudo reboot"
else
    log "No reboot required; changes are active now."
fi

echo
log "Next: install the watchdog service (belt-and-suspenders auto-reconnect):"
cat <<'EOF'
    sudo install -m 0755 duck-pad-reconnect.sh /usr/local/bin/duck-pad-reconnect.sh
    sudo install -m 0644 duck-pad-reconnect.service /etc/systemd/system/duck-pad-reconnect.service
    sudo systemctl daemon-reload
    sudo systemctl enable --now duck-pad-reconnect.service
    systemctl status duck-pad-reconnect.service
EOF
log "Done."
