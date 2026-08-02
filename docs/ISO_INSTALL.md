# 💾 Pre-built Image (ISO) — Install & First Boot

A **ready-to-flash Raspberry Pi SD-card image** that gets your duck up and walking in
minutes. It is a fully configured system based on **Raspberry Pi OS Lite (64-bit,
Bookworm)** with the complete **Open Duck Supercharged** stack pre-installed:

- the runtime with **all dependencies** already inside the project virtualenv,
- the **trained walk policy** (`BEST_WALK_ONNX_2.onnx`) — no separate download,
- **foot-switch autostart** for Walk / Head-puppet mode,
- the duck's own **`Openduck` Wi-Fi hotspot** (automatic fallback when no known Wi-Fi is found),
- the **phone control console** with captive portal (join the duck's Wi-Fi → console pops up),
- **Xbox controller Bluetooth auto-reconnect** (pair once, reconnects every boot),
- auto-activated virtualenv + a login banner that walks you through first setup,
- the guided **`first_time_setup.py`** wizard for the per-robot configuration.

> **Hardware assumption:** your duck follows the **standard Open Duck Mini build**
> (reference BOM and wiring). Tested on the **Raspberry Pi Zero 2W**. If you changed
> wiring, sensors, or motors, adjust the configuration accordingly.

> **You still have to calibrate!** Motor offsets and IMU calibration are **unique to
> every physical duck** and are *not* part of the image. The duck will **not walk**
> before you run the first-time setup (Section 5) — this is by design, so you get
> *your own* duck, not somebody else's calibration.

---

## At a glance

| | |
|---|---|
| **Image** | OpenDuck Supercharged v2.5, in the [Releases](../../../releases) section |
| **Target media** | microSD, 32 GB recommended (image uses ≈7 GB, expandable) |
| **Tested hardware** | Raspberry Pi Zero 2W, standard-BOM duck |
| **Default SSH login** | `bdxv2` @ `bdxv2.local`, password `ilovemyduck` |
| **Duck hotspot** | SSID `Openduck`, password `openduck1234`, duck = `10.42.0.1` |

---

## 1. Flash the image

We use the official **[Raspberry Pi Imager](https://www.raspberrypi.com/software/)**
(any OS).

1. Download the release archive from the [Releases](../../../releases) section.
2. In the Imager choose *Use custom* and select the **downloaded archive directly — do
   not unpack it first**. (The image is compressed; flashing an extracted file can fail.)
3. *Optional:* use the Imager's OS customization to pre-set your **home Wi-Fi**, an
   **SSH username/password**, or a hostname. If you skip this, the duck opens its own
   hotspot instead (next section) — you don't need a screen or keyboard either way.

> This guide uses the standard user **`bdxv2`**. If you set a custom username while
> flashing, substitute it everywhere below (the autostart launcher detects your
> username automatically — see also the [manual-run note](#8-running-scripts-manually)).

---

## 2. First boot: Wi-Fi & SSH

Give the first boot a little extra time: the image generates **fresh, unique SSH keys
and a new machine identity** on first start (every flashed duck is its own duck), and
the hotspot check adds ~15–20 seconds.

### 2.1 The duck's own hotspot (works anywhere)

If the duck can't find a known Wi-Fi network shortly after boot, it opens its own
access point:

- **SSID:** `Openduck`
- **Password:** `openduck1234`
- **The duck's address:** `10.42.0.1` (or `bdxv2.local`)

Connect your laptop or phone directly to this network. This fallback is always on, so
you can reach your duck at shows, outdoors, anywhere — no router needed.

> **Change the hotspot password** if you keep using this feature, so you don't get
> *duck-napped*:
>
> ```bash
> sudo nmcli connection modify Openduck wifi-sec.psk "NEWstrongPASS123"
> sudo nmcli connection down Openduck || true
> sudo nmcli connection up Openduck
> ```
>
> To disable the fallback entirely:
> `sudo systemctl disable openduck-wifi-fallback.service`

### 2.2 Your home Wi-Fi

If you pre-set Wi-Fi in the Imager (or add one later with
`sudo nmcli device wifi connect "<SSID>" password "<PASSWORD>"`), the duck joins it at
boot and skips the hotspot. Reach it as `bdxv2.local` or via its IP from your router.

### 2.3 SSH in

```bash
ssh bdxv2@bdxv2.local        # or: ssh bdxv2@10.42.0.1 when on the hotspot
# password: ilovemyduck
```

**Change the password right away:**

```bash
passwd
```

On login you'll see the **Open Duck welcome banner** with your remaining setup steps,
and the project virtualenv activates automatically — you can run any project script
straight away. (Mute the banner with `openduck-login welcome off`, toggle the
auto-venv with `openduck-login venv on|off`.)

---

## 3. Expand the filesystem

The flashed image initially exposes only ≈7 GB. If it didn't grow automatically on
first boot, claim the full card:

```bash
sudo raspi-config nonint do_expand_rootfs
sudo reboot
```

---

## 4. Pair your Xbox controller

Pair your own gamepad once — the pre-installed auto-reconnect service then reconnects
it on every boot (turn the controller on before powering the duck):

```bash
sudo bluetoothctl
# inside bluetoothctl:
power on
agent on
default-agent
scan on                # wait until your controller shows up
pair XX:XX:XX:XX:XX:XX
trust XX:XX:XX:XX:XX:XX
connect XX:XX:XX:XX:XX:XX
quit
```

The controller LED stops blinking and stays lit when connected.

---

## 5. First configuration — the guided wizard

Every duck is mechanically slightly different, so calibration is per-robot. The image
ships one command that walks you through **everything, in the right order** — each
step is skippable and progress is remembered, so you can stop and resume anytime:

```bash
cd ~/Open_Duck_Mini_Runtime
python scripts/first_time_setup.py
```

It covers: dependency check, `duck_config.json` creation, motor IDs (only for freshly
built ducks), motor PID baseline, **joint zero offsets**, **IMU calibration + mounting
trim**, the known-good walk tuning, expression features (eyes, speaker, camera, …),
the Xbox auto-reconnect service, the phone captive portal, and a final verification
that tells you the duck is good to go.

> Re-using another duck's offsets or IMU trim will cause poor tracking or unstable
> motion — always measure your own. The wizard ends with a green
> "**ready to go**" when everything checks out.

---

## 6. Foot-switch autostart

The image chooses what to run at boot from the duck's **foot switches** — no keyboard
or terminal needed. Power the duck on, let it finish booting, then **press and hold
the foot switch for the mode you want for ~5 seconds** until the duck springs to life
(the launcher re-checks the switches every ~5 s, so a firm hold always catches it):

| Hold this foot (~5 s, seen from the front) | Starts |
|---|---|
| 🦶 **RIGHT foot switch** | **Walk mode** — the AI walking policy (drive, sprint, record). |
| 🦶 **LEFT foot switch** | **Head-puppet mode** — puppet the head, live camera, face-tracking. |
| *(no foot held)* | Nothing — the duck waits quietly. |

**To switch modes,** restart the duck and hold the other foot.

> **Requirement:** the **Xbox controller must be powered on and paired** (Section 4),
> otherwise the started mode fails with an error-clicking noise from the speaker.

**Then grab your phone:** join the duck's Wi-Fi and the **control console pops up**
automatically (captive portal), or browse to `http://10.42.0.1:8080/`. See the
[User Guide](USER_GUIDE.md) for everything the console and gamepad can do.

### Managing the autostart service

```bash
sudo systemctl status  open-duck-walk.service   # check
sudo systemctl stop    open-duck-walk.service   # stop now
sudo systemctl start   open-duck-walk.service   # start now
sudo systemctl disable open-duck-walk.service   # don't run at boot
sudo systemctl enable  open-duck-walk.service   # run at boot (default)
```

---

## 7. How the image runs project scripts (venv)

The whole project lives in a Python **virtualenv** (`open-duck-mini-runtime`) so it
never conflicts with the system Python. The image activates it **automatically when
you log in** — if you ever land in a shell without it, activate it yourself:

```bash
workon open-duck-mini-runtime
```

---

## 8. Running scripts manually

To start a mode by hand (instead of the foot switches) — also the fix if manual runs
fail after picking a **custom username** during flashing — use the same `PYTHONPATH`
the autostart launcher uses (it is `$HOME`-based, so it works for any username):

```bash
workon open-duck-mini-runtime
export PYTHONPATH="$HOME/Open_Duck_Mini_Runtime/mini_bdx_runtime:$HOME/Open_Duck_Mini_Runtime/src:$HOME/Open_Duck_Mini_Runtime:$PYTHONPATH"
cd "$HOME/Open_Duck_Mini_Runtime/scripts"

# Walk:
python v2_rl_walk_mujoco.py --onnx_model_path BEST_WALK_ONNX_2.onnx

# Head-puppet:
python head_puppet.py
```

### Advanced: launcher overrides

`/etc/default/openduck` lets you override what the autostart launcher uses — the run
user (`OPENDUCK_USER`), the model file (`ONNX_MODEL_PATH`), and the switch GPIOs
(`PIN_WALK=22`, `PIN_HEAD=27`). Launcher logs:
`sudo journalctl -f | grep openduck`.

---

## 9. Troubleshooting

- **Can't SSH to `bdxv2.local`?** Use the IP instead (`10.42.0.1` on the hotspot, or
  check your router). Install Bonjour/mDNS support if your OS can't resolve `.local`.
- **The `Openduck` hotspot doesn't appear:** give it ~20 s after boot; on the very
  first boot after flashing it can take one extra power cycle. Remember it only opens
  when the duck finds **no** known Wi-Fi.
- **Nothing starts at boot:** hold the foot switch firmly for ~5 s *after* boot has
  finished; make sure the controller is paired **and powered on**; check
  `sudo systemctl status open-duck-walk.service`.
- **Error-clicking noise from the speaker:** the mode started without a connected
  Xbox controller — turn it on and reboot (or restart the service).
- **Only ~7 GB of the card is usable:** run the filesystem expansion (Section 3).
- **Motion feels off / duck won't balance:** re-run the offsets and IMU steps of
  `first_time_setup.py` — calibration is per-robot.
- **Security:** change the SSH password (`passwd`) and the hotspot password
  (Section 2.1) as part of setup, so you don't get duck-napped.

---

## Notes & feedback

- The image tracks this repository, but **hardware parity** with the reference build
  is assumed — custom builds may need adjusted configuration.
- For public demos: change all credentials and disable services you don't need.
- Problems or feedback about the image? Message **MaxMakesStuff** on the Open Duck
  community Discord.

Have fun — and don't let anyone steal your duck! 🦆
