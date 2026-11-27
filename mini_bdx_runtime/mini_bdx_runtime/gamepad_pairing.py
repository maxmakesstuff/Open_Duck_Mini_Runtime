"""
Bluetooth gamepad pairing for the phone Web UI.

Lets the Gamepad tab list, connect, disconnect, forget and pair Bluetooth gamepads, so
a lost Xbox pad can be brought back without a terminal. Works the same in walk and in
head-puppet mode, because it hangs off the web server and not off either script.

It only shells out to the tools BlueZ ships (`busctl` to read the state in one call,
`bluetoothctl` to act), from the web server's own threads. Nothing here runs in the
50 Hz control loop, and nothing is spawned unless the tab is open.

The parsers are pure functions, and the manager takes its process runner as an
argument, so all of it unit-tests off the robot.
"""
import json
import re
import subprocess
import threading
import time

MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
_MAC_AS_NAME_RE = re.compile(r"^([0-9A-F]{2}[-:]){5}[0-9A-F]{2}$", re.I)
_GAMEPAD_NAME_RE = re.compile(
    r"xbox|controller|gamepad|joystick|dualsense|dualshock|8bitdo|joy-con", re.I)
_DEVICE_LINE_RE = re.compile(r"^Device\s+(([0-9A-F]{2}:){5}[0-9A-F]{2})\s*(.*)$", re.I)

SCAN_SECONDS = 20
MAX_DEVICES = 25
FALLBACK_INFO_LIMIT = 10
ACTIONS = ("scan", "stop_scan", "pair", "connect", "disconnect", "forget")
_NEEDS_MAC = ("pair", "connect", "disconnect", "forget")

_BUSCTL_OBJECTS = ["busctl", "--system", "--json=short", "call", "org.bluez", "/",
                   "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"]


def normalize_mac(text):
    """Upper-case MAC, or None if `text` is not exactly one. Everything that reaches a
    command line goes through here first."""
    if not isinstance(text, str):
        return None
    mac = text.strip().upper()
    return mac if MAC_RE.match(mac) else None


def looks_like_gamepad(name, icon=""):
    return icon == "input-gaming" or bool(_GAMEPAD_NAME_RE.search(name or ""))


def _device(mac, name="", paired=False, trusted=False, connected=False, icon="", rssi=None):
    name = (name or "").strip()
    if _MAC_AS_NAME_RE.match(name):      # BlueZ shows the address when it has no name
        name = ""
    return {"mac": mac, "name": name, "paired": bool(paired), "trusted": bool(trusted),
            "connected": bool(connected), "gamepad": looks_like_gamepad(name, icon),
            "rssi": rssi if isinstance(rssi, int) else None}


def _prop(props, key, default=None):
    v = props.get(key, default)
    return v.get("data", default) if isinstance(v, dict) else v


def parse_managed_objects(text):
    """Parse `busctl --json=short call org.bluez / ...ObjectManager GetManagedObjects`.
    Returns {"devices": [...], "powered": bool, "discovering": bool}, or raises
    ValueError if the text is not that reply."""
    try:
        data = json.loads(text)["data"][0]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"unexpected busctl reply: {exc}")
    if not isinstance(data, dict):
        raise ValueError("unexpected busctl reply")
    devices, powered, discovering = [], False, False
    for interfaces in data.values():
        if not isinstance(interfaces, dict):
            continue
        adapter = interfaces.get("org.bluez.Adapter1")
        if isinstance(adapter, dict):
            powered = powered or bool(_prop(adapter, "Powered", False))
            discovering = discovering or bool(_prop(adapter, "Discovering", False))
        dev = interfaces.get("org.bluez.Device1")
        if isinstance(dev, dict):
            mac = normalize_mac(_prop(dev, "Address", ""))
            if mac is None:
                continue
            devices.append(_device(
                mac, _prop(dev, "Name") or _prop(dev, "Alias") or "",
                _prop(dev, "Paired", False), _prop(dev, "Trusted", False),
                _prop(dev, "Connected", False), _prop(dev, "Icon", "") or "",
                _prop(dev, "RSSI")))
    return {"devices": devices, "powered": powered, "discovering": discovering}


def parse_devices(text):
    """Parse `bluetoothctl devices` into [(mac, name)]."""
    out = []
    for line in (text or "").splitlines():
        m = _DEVICE_LINE_RE.match(line.strip())
        if m:
            out.append((m.group(1).upper(), m.group(3).strip()))
    return out


def parse_info(text):
    """Parse `bluetoothctl info <mac>` / `bluetoothctl show` into a {key: value} dict."""
    out = {}
    for line in (text or "").splitlines():
        key, sep, value = line.strip().partition(":")
        if sep and key and " " not in key.strip() and key not in out:
            out[key.strip()] = value.strip()
    return out


def sort_devices(devices):
    """Connected first, then paired, then likely gamepads, then by signal. Devices that
    are neither paired nor named are dropped: nobody can pick one from a bare address."""
    keep = [d for d in devices if d["paired"] or d["name"]]
    keep.sort(key=lambda d: (not d["connected"], not d["paired"], not d["gamepad"],
                             -(d["rssi"] if d["rssi"] is not None else -999), d["name"]))
    return keep[:MAX_DEVICES]


def _run(args, timeout):
    """Run one command. Returns (returncode, combined output); never raises."""
    try:
        p = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace")
    except FileNotFoundError:
        return 127, f"{args[0]} is not installed"
    except subprocess.TimeoutExpired as exc:
        return 124, (exc.stdout or b"").decode("utf-8", "replace")
    except OSError as exc:
        return 126, str(exc)


def _spawn_scan(seconds):
    """Start a scan that ends by itself. stdin is a pipe we never write to, so the tool
    does not see end-of-file and quit early when there is no terminal."""
    return subprocess.Popen(["bluetoothctl", "--timeout", str(int(seconds)), "scan", "on"],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


class GamepadManager:
    """State + actions behind /api/gamepad. One action at a time; actions run on a
    worker thread and report through status()."""

    def __init__(self, run=_run, spawn_scan=_spawn_scan, clock=time.monotonic,
                 sleep=time.sleep, cache_s=1.0, background=True):
        self._run = run
        self._spawn_scan = spawn_scan
        self._clock = clock
        self._sleep = sleep
        self._cache_s = cache_s
        self._background = background
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._state = {"available": False, "error": "", "powered": False,
                       "discovering": False, "devices": []}
        self._state_t = None
        self._busy = ""
        self._busy_mac = ""
        self._message = ""
        self._message_ok = True
        self._scan = None
        self._scan_until = 0.0

    # ------------------------------------------------------------------ reading
    def _read_state(self):
        rc, out = self._run(_BUSCTL_OBJECTS, 4)
        if rc == 0:
            try:
                st = parse_managed_objects(out)
                return dict(st, available=True, error="")
            except ValueError:
                pass
        return self._read_state_bluetoothctl()

    def _read_state_bluetoothctl(self):
        """Slower fallback (one process per device) for systems without busctl."""
        rc, out = self._run(["bluetoothctl", "devices"], 4)
        if rc != 0:
            return {"available": False, "error": (out.strip().splitlines() or
                    ["Bluetooth is not available"])[-1], "powered": False,
                    "discovering": False, "devices": []}
        _, show = self._run(["bluetoothctl", "show"], 4)
        adapter = parse_info(show)
        pairs = parse_devices(out)
        pairs.sort(key=lambda p: not looks_like_gamepad(p[1]))
        devices = []
        for mac, name in pairs[:FALLBACK_INFO_LIMIT]:
            _, text = self._run(["bluetoothctl", "info", mac], 4)
            info = parse_info(text)
            devices.append(_device(mac, info.get("Name", name),
                                   info.get("Paired") == "yes", info.get("Trusted") == "yes",
                                   info.get("Connected") == "yes", info.get("Icon", "")))
        return {"available": True, "error": "", "devices": devices,
                "powered": adapter.get("Powered") == "yes",
                "discovering": adapter.get("Discovering") == "yes"}

    def _refresh(self, force=False):
        now = self._clock()
        with self._lock:
            fresh = self._state_t is not None and now - self._state_t < self._cache_s
        if fresh and not force:
            return
        # one reader at a time; a second poller just gets the cached state
        if not self._refresh_lock.acquire(blocking=force):
            return
        try:
            st = self._read_state()
            with self._lock:
                self._state = st
                self._state_t = self._clock()
        finally:
            self._refresh_lock.release()

    def _find(self, mac):
        with self._lock:
            for d in self._state["devices"]:
                if d["mac"] == mac:
                    return dict(d)
        return None

    def _scan_left(self):
        with self._lock:
            scan, until = self._scan, self._scan_until
        if scan is None:
            return 0
        if scan.poll() is not None:
            with self._lock:
                if self._scan is scan:
                    self._scan = None
            return 0
        return max(0, int(round(until - self._clock())))

    def status(self):
        self._refresh()
        left = self._scan_left()
        with self._lock:
            st = self._state
            return {"available": st["available"], "error": st["error"],
                    "powered": st["powered"], "scanning": left > 0, "scan_left_s": left,
                    "busy": self._busy, "busy_mac": self._busy_mac,
                    "message": self._message, "message_ok": self._message_ok,
                    "devices": sort_devices(st["devices"])}

    # ------------------------------------------------------------------ acting
    def request(self, action, mac=None):
        """Start an action. Returns (ok, error). ok only means it was accepted; the
        outcome shows up in status() as `message`."""
        if action not in ACTIONS:
            return False, "unknown action"
        if action in _NEEDS_MAC:
            mac = normalize_mac(mac)
            if mac is None:
                return False, "not a valid address"
        else:
            mac = ""
        if action == "stop_scan":
            self._stop_scan()
            return True, ""
        with self._lock:
            if self._busy:
                return False, "busy"
            self._busy, self._busy_mac = action, mac
            self._message = ""
        if self._background:
            threading.Thread(target=self._work, args=(action, mac), daemon=True).start()
        else:
            self._work(action, mac)
        return True, ""

    def _work(self, action, mac):
        try:
            ok, message = getattr(self, "_do_" + action)(mac)
        except Exception as exc:  # noqa: BLE001 - a worker must never die silently
            ok, message = False, f"Failed: {exc}"
        try:
            self._refresh(force=True)
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            self._busy = self._busy_mac = ""
            self._message, self._message_ok = message, ok

    def _label(self, mac):
        d = self._find(mac)
        return (d and d["name"]) or mac

    def _stop_scan(self):
        with self._lock:
            scan, self._scan = self._scan, None
        if scan is not None and scan.poll() is None:
            try:
                scan.terminate()
                scan.wait(timeout=3)
            except Exception:  # noqa: BLE001
                pass

    def _power_on(self):
        self._run(["bluetoothctl", "power", "on"], 6)

    def _do_scan(self, _mac):
        self._stop_scan()
        self._power_on()
        try:
            scan = self._spawn_scan(SCAN_SECONDS)
        except OSError as exc:
            return False, f"Could not start the search: {exc}"
        with self._lock:
            self._scan, self._scan_until = scan, self._clock() + SCAN_SECONDS
        return True, ""

    def _do_pair(self, mac):
        self._refresh(force=True)
        if self._find(mac) is None:
            return False, "That gamepad is no longer in range. Search again."
        label = self._label(mac)
        self._power_on()
        for attempt in (0, 1):
            self._run(["bluetoothctl", "pair", mac], 30)
            self._refresh(force=True)
            d = self._find(mac)
            if d is not None and d["paired"]:
                break
            if attempt == 0:          # some pads refuse to pair while a scan is running
                self._stop_scan()
                self._sleep(1.0)
        else:
            return False, (f"Could not pair {label}. Put it back into pairing mode "
                           "(light blinking fast) and try again.")
        # trusted = BlueZ lets the pad reconnect by itself when it is switched on
        self._run(["bluetoothctl", "trust", mac], 6)
        self._stop_scan()
        self._run(["bluetoothctl", "connect", mac], 20)
        self._refresh(force=True)
        d = self._find(mac)
        if d is not None and d["connected"]:
            return True, f"{label} is paired and connected."
        return True, f"{label} is paired. Press its Xbox button to connect."

    def _do_connect(self, mac):
        label = self._label(mac)
        self._power_on()
        self._run(["bluetoothctl", "connect", mac], 20)
        self._refresh(force=True)
        d = self._find(mac)
        if d is not None and d["connected"]:
            return True, f"{label} is connected."
        return False, (f"Could not reach {label}. Switch it on (press the Xbox button) "
                       "and try again. If it keeps failing, forget it and pair it again.")

    def _do_disconnect(self, mac):
        label = self._label(mac)
        self._run(["bluetoothctl", "disconnect", mac], 10)
        self._refresh(force=True)
        d = self._find(mac)
        if d is not None and d["connected"]:
            return False, f"{label} is still connected."
        return True, f"{label} is disconnected."

    def _do_forget(self, mac):
        label = self._label(mac)
        self._run(["bluetoothctl", "remove", mac], 10)
        self._refresh(force=True)
        d = self._find(mac)
        if d is not None and d["paired"]:
            return False, f"Could not forget {label}."
        return True, f"{label} is forgotten. Pair it again to use it."
