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
import os
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
SCAN_START_TRIES = 3
# While this file is fresh, the reconnect watchdog (ops/bluetooth/duck-pad-reconnect.sh)
# keeps its hands off the adapter. BlueZ refuses to search while a connection attempt is
# pending, and the watchdog is in one nearly all the time a paired pad is away.
HOLD_FILE = "/dev/shm/duck-gamepad-hold"
MAX_DEVICES = 25
FALLBACK_INFO_LIMIT = 10
ACTIONS = ("scan", "stop_scan", "pair", "connect", "disconnect", "forget")
_NEEDS_MAC = ("pair", "connect", "disconnect", "forget")

_BUSCTL_OBJECTS = ["busctl", "--system", "call", "org.bluez", "/",
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


_C_ESCAPES = {"n": b"\n", "t": b"\t", "r": b"\r", "a": b"\a", "b": b"\b", "f": b"\f",
              "v": b"\v"}


def _tokens(text):
    """Split busctl's plain reply into words. Quoted strings come back unescaped
    (busctl writes non-ASCII bytes as \\NNN octal, so names are decoded as UTF-8 here)."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c == '"':
            buf, j = bytearray(), i + 1
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    e = text[j + 1]
                    if e in "01234567" and j + 3 < n:
                        buf.append(int(text[j + 1:j + 4], 8) & 0xFF)
                        j += 4
                    elif e == "x" and j + 3 < n:
                        buf.append(int(text[j + 2:j + 4], 16))
                        j += 4
                    else:
                        buf += _C_ESCAPES.get(e, e.encode("utf-8"))
                        j += 2
                else:
                    buf += text[j].encode("utf-8")
                    j += 1
            yield buf.decode("utf-8", "replace")
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace():
                j += 1
            yield text[i:j]
            i = j


def _type_end(sig, i):
    """Index just past the one complete D-Bus type that starts at sig[i]."""
    c = sig[i]
    if c == "a":
        return _type_end(sig, i + 1)
    if c in "({":
        close, j = (")" if c == "(" else "}"), i + 1
        while sig[j] != close:
            j = _type_end(sig, j)
        return j + 1
    return i + 1


def _value(sig, words):
    c = sig[0]
    if c == "a":
        count, elem = int(next(words)), sig[1:]
        if elem[0] == "{":
            out = {}
            for _ in range(count):
                key = _value(elem[1], words)
                out[key] = _value(elem[2:-1], words)
            return out
        return [_value(elem, words) for _ in range(count)]
    if c == "(":
        out, j = [], 1
        while sig[j] != ")":
            end = _type_end(sig, j)
            out.append(_value(sig[j:end], words))
            j = end
        return out
    if c == "v":
        return _value(next(words), words)
    word = next(words)
    if c == "b":
        return word == "true"
    if c in "sog":
        return word
    return float(word) if c == "d" else int(word)


def parse_busctl_reply(text):
    """Parse the plain (not JSON) output of `busctl call`: a type signature followed by
    the value, e.g. `a{sv} 1 "Paired" b true`. Raises ValueError on anything else.

    Plain on purpose: `busctl --json` fails outright on BlueZ's ManufacturerData
    (a dict with integer keys), which every device that has advertised carries."""
    try:
        words = _tokens(text)
        return _value(next(words), words)
    except (StopIteration, ValueError, IndexError, KeyError, RecursionError) as exc:
        raise ValueError(f"unexpected busctl reply: {exc!r}")


def parse_managed_objects(text):
    """Parse `busctl call org.bluez / ...ObjectManager GetManagedObjects`.
    Returns {"devices": [...], "powered": bool, "discovering": bool}, or raises
    ValueError if the text is not that reply."""
    data = parse_busctl_reply(text)
    if not isinstance(data, dict):
        raise ValueError("unexpected busctl reply")
    devices, powered, discovering = [], False, False
    for interfaces in data.values():
        if not isinstance(interfaces, dict):
            continue
        adapter = interfaces.get("org.bluez.Adapter1")
        if isinstance(adapter, dict):
            powered = powered or bool(adapter.get("Powered", False))
            discovering = discovering or bool(adapter.get("Discovering", False))
        dev = interfaces.get("org.bluez.Device1")
        if isinstance(dev, dict):
            mac = normalize_mac(dev.get("Address", ""))
            if mac is None:
                continue
            devices.append(_device(
                mac, dev.get("Name") or dev.get("Alias") or "", dev.get("Paired", False),
                dev.get("Trusted", False), dev.get("Connected", False),
                dev.get("Icon", "") or "", dev.get("RSSI")))
    return {"devices": devices, "powered": powered, "discovering": discovering}


def fake_managed_objects(devices, powered=True, discovering=False):
    """Build a GetManagedObjects reply the way busctl prints it, from
    {mac: {"Name": str, "Paired": bool, "RSSI": int, ...}}. For the tests and the dev
    mock server only."""
    def quote(text):
        return '"' + "".join(
            ch if 32 <= ord(ch) < 127 and ch not in '"\\' else
            "".join("\\%03o" % b for b in ch.encode("utf-8")) for ch in text) + '"'

    def prop(key, value):
        if isinstance(value, bool):
            return f'{quote(key)} b {"true" if value else "false"}'
        if isinstance(value, int):
            return f"{quote(key)} n {value}"
        return f"{quote(key)} s {quote(value)}"

    out = [f"a{{oa{{sa{{sv}}}}}} {len(devices) + 1}",
           '"/org/bluez/hci0" 2 "org.freedesktop.DBus.Introspectable" 0 "org.bluez.Adapter1" 3',
           prop("Powered", powered), prop("Discovering", discovering),
           '"UUIDs" as 1 "00001801-0000-1000-8000-00805f9b34fb"']
    for mac, d in devices.items():
        props = [prop("Address", mac)] + [prop(k, v) for k, v in d.items()]
        props.append('"ManufacturerData" a{qv} 1 6 ay 3 3 0 128')
        out.append(f'"/org/bluez/hci0/dev_{mac.replace(":", "_")}" 1 "org.bluez.Device1" '
                   f"{len(props)} " + " ".join(props))
    return " ".join(out) + "\n"


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
                 sleep=time.sleep, cache_s=1.0, background=True, hold_file=HOLD_FILE):
        self._run = run
        self._spawn_scan = spawn_scan
        self._clock = clock
        self._sleep = sleep
        self._cache_s = cache_s
        self._background = background
        self._hold_file = hold_file
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
            self._hold_watchdog(False)
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
            self._refresh(force=True)      # names for the messages, state for the checks
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
        if scan is not None:
            self._hold_watchdog(False)
        if scan is not None and scan.poll() is None:
            try:
                scan.terminate()
                scan.wait(timeout=3)
            except Exception:  # noqa: BLE001
                pass

    def _power_on(self):
        self._run(["bluetoothctl", "power", "on"], 6)

    def _reset_adapter(self):
        """Last resort when BlueZ keeps refusing to search with nothing else going on
        (seen on a real duck, BlueZ 5.66: it answered InProgress for minutes): switch
        the adapter off and on. Never while a pad is connected, that would drop it."""
        with self._lock:
            if any(d["connected"] for d in self._state["devices"]):
                return
        self._run(["bluetoothctl", "power", "off"], 6)
        self._sleep(1.0)
        self._power_on()
        self._sleep(1.0)

    def _hold_watchdog(self, on=True):
        """Ask the reconnect watchdog to pause (it honours the file for a minute after
        the last touch), or let it go again."""
        if not self._hold_file:
            return
        try:
            if on:
                with open(self._hold_file, "w"):
                    pass
                os.utime(self._hold_file, None)
            elif os.path.exists(self._hold_file):
                os.remove(self._hold_file)
        except OSError:
            pass

    def _take_adapter(self, keep=None):
        """Pause the watchdog and cancel connection attempts that are still pending
        (its, or an earlier one of ours): BlueZ answers InProgress to a search or a
        pairing while one is open. `disconnect` on a pad that is not connected does
        exactly that and nothing else."""
        self._hold_watchdog(True)
        self._refresh(force=True)
        with self._lock:
            pending = [d["mac"] for d in self._state["devices"]
                       if d["paired"] and not d["connected"] and d["mac"] != keep]
        for mac in pending:
            self._run(["bluetoothctl", "disconnect", mac], 6)

    def _do_scan(self, _mac):
        self._stop_scan()
        self._power_on()
        self._take_adapter()
        for attempt in range(SCAN_START_TRIES):
            if attempt == SCAN_START_TRIES - 1:
                self._reset_adapter()
            try:
                scan = self._spawn_scan(SCAN_SECONDS)
            except OSError as exc:
                return False, f"Could not start the search: {exc}"
            with self._lock:
                self._scan, self._scan_until = scan, self._clock() + SCAN_SECONDS
            # the tool keeps running even when BlueZ refused: ask the adapter itself
            for _ in range(4):
                self._sleep(0.5)
                self._refresh(force=True)
                with self._lock:
                    if self._state["discovering"]:
                        return True, ""
            self._stop_scan()
            self._take_adapter()
        self._hold_watchdog(False)
        return False, "Bluetooth is busy and did not start the search. Try again in a moment."

    def _do_pair(self, mac):
        self._refresh(force=True)
        if self._find(mac) is None:
            return False, "That gamepad is no longer in range. Search again."
        label = self._label(mac)
        self._power_on()
        self._take_adapter(keep=mac)
        try:
            return self._pair(mac, label)
        finally:
            self._hold_watchdog(False)

    def _pair(self, mac, label):
        for attempt in (0, 1):
            self._run(["bluetoothctl", "pair", mac], 30)
            self._refresh(force=True)
            d = self._find(mac)
            if d is not None and d["paired"]:
                break
            if attempt == 0:          # some pads refuse to pair while a scan is running
                self._stop_scan()
                self._hold_watchdog(True)
                self._sleep(1.0)
        else:
            return False, (f"Could not pair {label}. Put it back into pairing mode "
                           "(light blinking fast) and try again.")
        # trusted = BlueZ lets the pad reconnect by itself when it is switched on
        self._run(["bluetoothctl", "trust", mac], 6)
        self._stop_scan()
        self._hold_watchdog(True)
        self._run(["bluetoothctl", "connect", mac], 20)
        self._refresh(force=True)
        d = self._find(mac)
        if d is not None and d["connected"]:
            return True, f"{label} is paired and connected."
        return True, f"{label} is paired. Press its Xbox button to connect."

    def _do_connect(self, mac):
        label = self._label(mac)
        self._stop_scan()
        self._power_on()
        self._take_adapter()
        try:
            self._run(["bluetoothctl", "connect", mac], 20)
        finally:
            self._hold_watchdog(False)
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
