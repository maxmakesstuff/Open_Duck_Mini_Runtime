# Xbox controller Bluetooth persistence fix (Raspberry Pi OS / BlueZ)

**Problem:** the Xbox wireless controller drops off the Pi (`bdxv2@bdxv2.local`)
after ~10 minutes and won't re-pair until the whole robot is restarted. During a
50 Hz control loop that means an unplanned stop.

This kit fixes the **OS / Bluetooth layer** so the link stays up and the pad
auto-reconnects within seconds of being switched on — **no robot restart**.
(The Python-runtime side — re-opening the pygame joystick without a restart — is
handled separately.)

## Root causes this kit addresses

| # | Cause | Fix here |
|---|-------|----------|
| 1 | **USB autosuspend** on the Bluetooth radio (`btusb`) powers the radio down when idle → pad drops after a few minutes. | `btusb.enable_autosuspend=0` module option + udev `power/control=on` fallback. |
| 2 | **BlueZ doesn't auto-reconnect** a trusted device after it powers back on. | `main.conf` `[Policy] AutoEnable=true`, `ReconnectAttempts`/`ReconnectIntervals`, controller marked **Trusted**, plus a watchdog service. |
| 3 | **Xbox ERTM quirk** — Xbox pads often fail to (re)connect on Linux with Enhanced Retransmission Mode on. | `bluetooth.disable_ertm=1` (module option; kernel-cmdline fallback documented). |
| 4 | **Wi-Fi/BT 2.4 GHz coexistence** — shared antenna; heavy 2.4 GHz Wi-Fi starves the BT link. | Documented mitigations (not forced). |
| — | Long-term robustness | Optional **xpadneo** driver — see `install-xpadneo.md`. |

## Files in this directory

- `setup-bluetooth-reconnect.sh` — idempotent installer for causes 1–3 + pair/trust (run with `sudo`).
- `duck-pad-reconnect.sh` — watchdog that reconnects the pad on a backoff.
- `duck-pad-reconnect.service` — systemd unit that runs the watchdog.
- `install-xpadneo.md` — optional better driver (dkms; needs headers + reboot).
- `README.md` — this runbook.

---

## Step 0 — Diagnosis (run these first, understand the baseline)

SSH in: `ssh bdxv2@bdxv2.local`. First get the controller's MAC:

```bash
bluetoothctl devices          # lists known devices -> note the Xbox one's MAC
# or, if never paired, put pad in pairing mode (hold pair button, light flashes fast):
bluetoothctl --timeout 15 scan on
bluetoothctl devices
```

Then check each root cause. **Why** is noted per command:

```bash
# (2) Is the pad trusted + currently connected? Look for "Trusted: yes" / "Connected: yes".
bluetoothctl info AA:BB:CC:DD:EE:FF

# (1) Is the BT radio being autosuspended? These reveal the current state:
cat /sys/module/btusb/parameters/enable_autosuspend      # want: N (0). Y means it will suspend.
for f in /sys/bus/usb/devices/*/power/control; do echo "$f = $(cat "$f")"; done
#   ^ any BT USB device showing "auto" can be suspended; we want "on".
lsusb                                                    # identify the BT dongle vendor:id

# (3) Is ERTM disabled? Y/1 = good for Xbox pads.
cat /sys/module/bluetooth/parameters/disable_ertm

# Kernel/BlueZ log: look for disconnects, "btusb", supervision timeouts, ERTM errors.
dmesg | grep -i -E 'blue|btusb|hci|ertm' | tail -n 40
journalctl -u bluetooth -b --no-pager | tail -n 60

# Watch it drop in real time while you use the pad (leave running for >10 min):
journalctl -u bluetooth -f
```

**Typical smoking gun for this bug:** `enable_autosuspend = Y`, the BT USB
device's `power/control = auto`, and a disconnect line in `dmesg` a few minutes
after the last input. That is cause #1.

---

## Step 1 — Apply the OS fixes (idempotent installer)

Copy this folder to the Pi (or use `mac_transfer.command` / `windows_transfer.ps1`), then:

```bash
cd ~/Open_Duck_Mini_Runtime/ops/bluetooth
sudo bash setup-bluetooth-reconnect.sh AA:BB:CC:DD:EE:FF
```

If you omit the MAC it will ask (and can scan for you). The script:

1. Writes `/etc/modprobe.d/duck-btusb-noautosuspend.conf` → `options btusb enable_autosuspend=0` (cause 1).
2. Writes `/etc/udev/rules.d/81-duck-bt-power.rules` → forces `power/control=on` for BT USB radios (cause 1 fallback; applied live via `udevadm` too).
3. Writes `/etc/modprobe.d/duck-bluetooth-disable-ertm.conf` → `options bluetooth disable_ertm=1` (cause 3), and tries to flip it live.
4. Writes BlueZ policy to `/etc/bluetooth/main.conf.d/duck-reconnect.conf` (or a marked block in `main.conf`): `AutoEnable=true`, `ReconnectAttempts=7`, `ReconnectIntervals=1,2,4,8,16,32,64` (cause 2), and restarts `bluetooth.service`.
5. **Pairs and TRUSTS** the controller, and records its MAC to `/etc/default/duck-pad-reconnect` for the watchdog.
6. Prints exactly what changed and whether a reboot is needed.

It is safe to re-run — every step is guarded and only writes when content differs.

### Which steps need a reboot?

- The **modprobe options** (btusb autosuspend, bluetooth ERTM) take effect when
  the module reloads, i.e. **on reboot**. The script flips both **live** where
  the kernel allows, but a reboot guarantees a clean state:

  ```bash
  sudo reboot
  ```

- The **udev rule**, **BlueZ `main.conf`** changes, and **pair/trust** apply
  immediately (no reboot).
- If, after reboot, `cat /sys/module/bluetooth/parameters/disable_ertm` still
  shows `N`, the bluetooth stack is built into the kernel and ignores the module
  option — add it to the **kernel command line** instead:
  append `bluetooth.disable_ertm=1` to the end of the single line in
  `/boot/firmware/cmdline.txt` (older images: `/boot/cmdline.txt`), then reboot.

---

## Step 2 — Install the watchdog service (belt-and-suspenders)

BlueZ's `AutoEnable`/reconnect plus a **trusted** pad usually reconnects on its
own. The watchdog is a safety net: it polls and issues `bluetoothctl connect`
on a capped backoff, so the pad rejoins within seconds of powering on even if
BlueZ misses it — again, **without restarting the robot**.

```bash
cd ~/Open_Duck_Mini_Runtime/ops/bluetooth
sudo install -m 0755 duck-pad-reconnect.sh /usr/local/bin/duck-pad-reconnect.sh
sudo install -m 0644 duck-pad-reconnect.service /etc/systemd/system/duck-pad-reconnect.service
sudo systemctl daemon-reload
sudo systemctl enable --now duck-pad-reconnect.service
systemctl status duck-pad-reconnect.service
journalctl -u duck-pad-reconnect -f      # watch it reconnect
```

The service reads `PAD_MAC` from `/etc/default/duck-pad-reconnect` (written by
the installer). Tunables you can add to that file: `POLL_INTERVAL` (default 5s),
`BACKOFF_MIN` (2s), `BACKOFF_MAX` (30s). After editing:
`sudo systemctl restart duck-pad-reconnect`.

To remove it later:
```bash
sudo systemctl disable --now duck-pad-reconnect.service
sudo rm /etc/systemd/system/duck-pad-reconnect.service /usr/local/bin/duck-pad-reconnect.sh
sudo systemctl daemon-reload
```

---

## Step 3 — Verify

### A. It survives >10 minutes of use

1. Reboot if the installer asked you to.
2. Confirm the fixes are live:
   ```bash
   cat /sys/module/btusb/parameters/enable_autosuspend      # expect: N
   cat /sys/module/bluetooth/parameters/disable_ertm        # expect: Y
   for f in /sys/bus/usb/devices/*/power/control; do echo "$f = $(cat "$f")"; done   # BT device: on
   bluetoothctl info AA:BB:CC:DD:EE:FF | grep -E 'Trusted|Connected'  # both: yes
   ```
3. Connect the pad and **use it continuously for >10 minutes** (or leave it idle
   >10 min, whichever reproduced the bug). In another shell, watch:
   ```bash
   journalctl -u bluetooth -f
   ```
   Expected: **no disconnect** lines. Previously it dropped around the 10-minute
   mark; now the radio is never suspended so the link holds.

### B. It auto-reconnects after the pad is powered off/on (no robot restart)

1. Leave the robot running.
2. **Turn the controller off** (hold the Xbox button ~6s). Confirm:
   `bluetoothctl info <MAC>` → `Connected: no`.
3. Wait ~15s, then **turn the controller back on** (tap the Xbox button).
4. Within a few seconds it should reconnect. Confirm:
   ```bash
   bluetoothctl info AA:BB:CC:DD:EE:FF | grep Connected   # -> Connected: yes
   journalctl -u duck-pad-reconnect -n 20 --no-pager      # see the reconnect logged
   ```
   You did **not** restart the robot or the control loop.

### If it still drops

- Re-check `dmesg | grep -i -E 'blue|btusb|ertm'` right after a drop.
- If drops correlate with Wi-Fi activity, apply the **cause #4** mitigations the
  installer prints (move Pi to 5 GHz, or `dtoverlay=disable-wifi` if headless).
- For the most reliable long-term fix, install **xpadneo** — see
  `install-xpadneo.md`. It handles Xbox-specific quirks (including ERTM and
  reconnection) at the driver level.
