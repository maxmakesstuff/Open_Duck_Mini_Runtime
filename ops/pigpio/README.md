# Ear-antenna jitter fix (pigpio)

The two ear antennas are **analog servos** on GPIO13 (left) / GPIO12 (right). Driven
by CircuitPython `pwmio`, the PWM is generated in software, so its pulse edges drift
whenever the Pi is busy and the ears **twitch constantly**. `pigpio` drives the same
pins from a **hardware timer**, so the pulse width is exact and the ears are still
unless commanded.

`mini_bdx_runtime/antennas.py` **prefers pigpio automatically** and falls back to
`pwmio` if the daemon isn't reachable — installing this kit is all that's needed to
turn the fix on; no code change, and nothing breaks if you don't.

> Credit: antenna fix by **Brad3D**; I2S coexistence by **Elpidiovaldez5**.

## ⚠️ I2S / speaker safety

This duck's **max98357A speaker uses I2S**, which is clocked by the Pi's **PCM**
peripheral. pigpio **defaults to that same PCM clock** — running it that way corrupts
the audio stream and can **damage the speaker** (a Class-D amp fed a stuck-high signal
can sit at full voltage). The installer therefore runs pigpiod on the **PWM clock**
(`pigpiod -t 0`) via a systemd override, so servo PWM and audio never fight.

**If your build has no speaker/I2S**, `-t 0` is still fine (it just isn't required).

## Install (on the Pi)

```bash
cd ~/Open_Duck_Mini_Runtime/ops/pigpio
sudo ./setup-pigpio.sh
```

It is idempotent: installs `pigpio` + `python3-pigpio`, writes the `-t 0` override,
enables `pigpiod` on boot, and verifies the daemon + python client + the clock flag.

Then restart the walk or head puppet. On startup `antennas.py` prints:

```
[antennas] pigpio hardware PWM (jitter-free)
```

(If it instead prints the `pwmio` fallback line, pigpiod isn't running — see below.)

## Verify / manage

```bash
systemctl status pigpiod            # is it running?
systemctl cat pigpiod               # shows the -t 0 override
systemctl show pigpiod -p ExecStart # confirm '-t 0' on the running command
python3 -c "import pigpio;print(pigpio.pi().connected)"   # True = client can talk to it
```

Manual control:

```bash
sudo systemctl start pigpiod        # start now
sudo systemctl stop pigpiod         # stop (antennas.py then falls back to pwmio)
sudo systemctl disable pigpiod      # don't start on boot
```

## Free animation vs. hand control

With the jitter gone the ears go dead still, so the "alive" wiggle is now **scripted
on purpose** (`antenna_anim.py`) and **switchable** from the phone Web UI in both walk
and head-puppet modes:

- **Free animation ON** — a gentle, organic idle sway + occasional twitch when you're
  not touching the ears.
- **Free animation OFF** — the ears rest still and only move when you drive them
  (triggers / recorded playback / face-track greeting), for precise hand control.

The toggle persists to `~/duck_config.json` as `antenna_free_anim` when you press Save.

## Note on socket exposure

pigpiod listens on TCP 8888 for its client. On the duck's isolated AP this is low
risk, but if you want to harden it you can restrict the daemon to localhost by adding
`-l` to the override's `ExecStart` line (the python client connects to 127.0.0.1, so
it keeps working). Left off by default to match the upstream fix exactly.
