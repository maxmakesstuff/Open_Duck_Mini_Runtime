# Installing `xpadneo` (robust long-term Xbox controller driver)

`xpadneo` is an advanced Linux kernel driver for Xbox One / Series wireless
controllers. Compared with the in-kernel `hid-generic`/`xpad` path it gives:

- Correct button/axis mapping and rumble out of the box.
- **More reliable (re)connection** — it handles Xbox-specific HID quirks and
  works well with `bluetooth.disable_ertm=1`.
- Better behavior across pad power-off/on cycles.

For this robot it is the recommended **long-term** fix once the quick OS fixes
(`setup-bluetooth-reconnect.sh`) have stabilized things. It is **optional** — the
module options + trust + watchdog already solve the drop/reconnect problem for
most setups.

> **Caveats up front**
> - It builds via **DKMS**, so it needs the matching **kernel headers**.
> - A **reboot** is required after install so the freshly built module loads.
> - DKMS rebuilds the module automatically on future kernel updates (that's the
>   point of using it), but a kernel bump still means a reboot.

## 1. Prerequisites

```bash
sudo apt-get update
sudo apt-get install -y dkms git raspberrypi-kernel-headers
# On non-RPi-kernel images use: sudo apt-get install -y linux-headers-$(uname -r)
```

Verify headers match the running kernel:

```bash
uname -r
ls /lib/modules/$(uname -r)/build    # should exist (symlink to the headers)
```

If `build` is missing, the headers package didn't match your kernel — update the
system (`sudo apt-get full-upgrade`), reboot, and reinstall the headers.

## 2. Install xpadneo

```bash
git clone https://github.com/atar-axis/xpadneo.git
cd xpadneo
sudo ./install.sh
```

The installer registers the module with DKMS, builds it, and installs a modprobe
config. Confirm DKMS sees it:

```bash
dkms status | grep -i xpadneo    # expect: hid-xpadneo, <version>, <kernel>: installed
```

## 3. Keep ERTM disabled

xpadneo works best with ERTM off. `setup-bluetooth-reconnect.sh` already writes
`options bluetooth disable_ertm=1`. Confirm after reboot:

```bash
cat /sys/module/bluetooth/parameters/disable_ertm   # expect: Y
```

(If it still shows `N`, use the kernel-cmdline method from the README:
append `bluetooth.disable_ertm=1` to `/boot/firmware/cmdline.txt`.)

## 4. Reboot and re-pair

```bash
sudo reboot
```

After reboot, remove the old pairing and pair fresh so the pad binds to the new
driver, then trust it:

```bash
bluetoothctl remove AA:BB:CC:DD:EE:FF     # forget old pairing (optional but clean)
# put the pad in pairing mode (hold pair button until it flashes fast):
bluetoothctl --timeout 20 scan on
bluetoothctl pair    AA:BB:CC:DD:EE:FF
bluetoothctl trust   AA:BB:CC:DD:EE:FF
bluetoothctl connect AA:BB:CC:DD:EE:FF
```

Confirm the driver is bound:

```bash
dmesg | grep -i xpadneo | tail
# a /dev/input/js* and event device should appear; pygame will see the pad.
```

## 5. Coexistence with the watchdog service

The `duck-pad-reconnect.service` watchdog is complementary and can stay enabled —
it only issues `bluetoothctl connect` when the pad is disconnected, which is a
no-op while xpadneo holds the link. No conflict.

## Uninstall

```bash
cd xpadneo
sudo ./uninstall.sh
sudo reboot
```

## References

- xpadneo: https://github.com/atar-axis/xpadneo
- Its docs cover the ERTM requirement and Pi-specific notes in more detail.
