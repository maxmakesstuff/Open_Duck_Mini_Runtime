"""
Standalone tests for gamepad_pairing (parsers + manager against a fake BlueZ).

Run:  python3 test_gamepad_pairing.py
"""
import json

from control_bus import ControlBus
from gamepad_pairing import (
    GamepadManager, looks_like_gamepad, normalize_mac, parse_devices, parse_info,
    parse_managed_objects, sort_devices,
)
from web_control import handle_api

PAD = "AA:BB:CC:DD:EE:01"
NEW = "AA:BB:CC:DD:EE:02"
TV = "AA:BB:CC:DD:EE:03"


class FakeScan:
    def __init__(self):
        self.alive = True

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive = False

    def wait(self, timeout=None):
        return 0


class FakeBluez:
    """Answers the commands the manager runs, from a dict of devices."""

    def __init__(self, busctl=True):
        self.busctl = busctl
        self.calls = []
        self.scans = []
        self.pair_fails_while_scanning = False
        self.reachable = {PAD, NEW}
        self.dev = {
            PAD: dict(Name="Xbox Wireless Controller", Paired=True, Trusted=True,
                      Connected=False, Icon="input-gaming"),
            NEW: dict(Name="Xbox Wireless Controller", Paired=False, Trusted=False,
                      Connected=False, Icon="input-gaming", RSSI=-48),
            TV: dict(Name="Living room TV", Paired=False, Trusted=False,
                     Connected=False, Icon="video-display", RSSI=-70),
            "AA:BB:CC:DD:EE:04": dict(Name="AA-BB-CC-DD-EE-04", Paired=False, Trusted=False,
                                      Connected=False, Icon="", RSSI=-80),
        }

    def scanning(self):
        return any(s.alive for s in self.scans)

    def spawn_scan(self, seconds):
        s = FakeScan()
        self.scans.append(s)
        return s

    def run(self, args, timeout):
        self.calls.append(list(args))
        if args[0] == "busctl":
            if not self.busctl:
                return 127, "busctl is not installed"
            objs = {"/org/bluez/hci0": {"org.bluez.Adapter1": {
                "Powered": {"type": "b", "data": True},
                "Discovering": {"type": "b", "data": self.scanning()}}}}
            for mac, d in self.dev.items():
                props = {"Address": {"type": "s", "data": mac}}
                for k, v in d.items():
                    props[k] = {"type": "x", "data": v}
                objs["/org/bluez/hci0/dev_" + mac.replace(":", "_")] = {
                    "org.bluez.Device1": props}
            return 0, json.dumps({"type": "a{oa{sa{sv}}}", "data": [objs]})
        assert args[0] == "bluetoothctl"
        cmd = args[1]
        if cmd == "devices":
            return 0, "".join(f"Device {m} {d['Name']}\n" for m, d in self.dev.items())
        if cmd == "show":
            return 0, "Controller B8:27:EB:00:00:00 (public)\n\tPowered: yes\n\tDiscovering: no\n"
        if cmd == "power":
            return 0, "Changing power on succeeded"
        mac = args[2]
        d = self.dev.get(mac)
        if cmd == "info":
            if d is None:
                return 1, f"Device {mac} not available"
            yn = lambda v: "yes" if v else "no"  # noqa: E731
            return 0, (f"Device {mac} (public)\n\tName: {d['Name']}\n\tIcon: {d['Icon']}\n"
                       f"\tPaired: {yn(d['Paired'])}\n\tTrusted: {yn(d['Trusted'])}\n"
                       f"\tConnected: {yn(d['Connected'])}\n")
        if d is None:
            return 1, f"Device {mac} not available"
        if cmd == "pair":
            if self.pair_fails_while_scanning and self.scanning():
                return 1, "Failed to pair: org.bluez.Error.InProgress"
            if mac not in self.reachable:
                return 1, "Failed to pair: org.bluez.Error.ConnectionAttemptFailed"
            d["Paired"] = True
            return 0, "Pairing successful"
        if cmd == "trust":
            d["Trusted"] = True
            return 0, "trust succeeded"
        if cmd == "connect":
            if mac not in self.reachable:
                return 1, "Failed to connect: org.bluez.Error.Failed"
            d["Connected"] = True
            return 0, "Connection successful"
        if cmd == "disconnect":
            d["Connected"] = False
            return 0, "Successful disconnected"
        if cmd == "remove":
            del self.dev[mac]
            return 0, "Device has been removed"
        raise AssertionError(f"unexpected command {args}")


def manager(bz, **kw):
    return GamepadManager(run=bz.run, spawn_scan=bz.spawn_scan, sleep=lambda s: None,
                          cache_s=0.0, background=False, **kw)


def by_mac(status):
    return {d["mac"]: d for d in status["devices"]}


# ------------------------------------------------------------------ parsers
def test_mac_is_validated_and_normalised():
    assert normalize_mac(" aa:bb:cc:dd:ee:01 ") == PAD
    for bad in ("", None, 5, "AA:BB:CC:DD:EE", "AA:BB:CC:DD:EE:0G", PAD + "; reboot",
                "--help", PAD + "\n" + PAD):
        assert normalize_mac(bad) is None, bad


def test_gamepad_guess():
    assert looks_like_gamepad("Xbox Wireless Controller")
    assert looks_like_gamepad("", "input-gaming")
    assert not looks_like_gamepad("Living room TV", "video-display")


def test_parse_devices_and_info():
    assert parse_devices("Device AA:BB:CC:DD:EE:01 Xbox Wireless Controller\n"
                         "[NEW] junk\nDevice aa:bb:cc:dd:ee:02 Pad 2\n") == [
        (PAD, "Xbox Wireless Controller"), (NEW, "Pad 2")]
    info = parse_info("Device AA:BB:CC:DD:EE:01 (public)\n\tName: Xbox: Elite\n"
                      "\tPaired: yes\n\tUUID: Human Interface Device (00001812)\n")
    assert info["Name"] == "Xbox: Elite" and info["Paired"] == "yes"


def test_parse_managed_objects():
    bz = FakeBluez()
    st = parse_managed_objects(bz.run(["busctl"], 1)[1])
    assert st["powered"] and not st["discovering"]
    d = {x["mac"]: x for x in st["devices"]}
    assert d[PAD]["paired"] and d[PAD]["trusted"] and d[PAD]["gamepad"]
    assert d[NEW]["rssi"] == -48 and not d[NEW]["paired"]
    assert d["AA:BB:CC:DD:EE:04"]["name"] == ""       # address-as-name is no name
    for bad in ("", "nope", "{}", '{"data":[]}', '{"data":[5]}'):
        try:
            parse_managed_objects(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_sorting_puts_usable_pads_first_and_drops_nameless():
    bz = FakeBluez()
    bz.dev[NEW]["Connected"] = bz.dev[NEW]["Paired"] = True
    macs = [d["mac"] for d in sort_devices(parse_managed_objects(bz.run(["busctl"], 1)[1])["devices"])]
    assert macs == [NEW, PAD, TV]


# ------------------------------------------------------------------ manager
def test_status_lists_devices():
    st = manager(FakeBluez()).status()
    assert st["available"] and st["powered"] and not st["scanning"] and st["busy"] == ""
    assert [d["mac"] for d in st["devices"]] == [PAD, NEW, TV]


def test_status_without_busctl_falls_back_to_bluetoothctl():
    st = manager(FakeBluez(busctl=False)).status()
    assert st["available"] and st["powered"]
    assert by_mac(st)[PAD]["paired"] and by_mac(st)[PAD]["gamepad"]


def test_status_when_bluetooth_is_missing():
    m = GamepadManager(run=lambda a, t: (127, f"{a[0]} is not installed"), cache_s=0.0,
                       background=False)
    st = m.status()
    assert st["available"] is False and "not installed" in st["error"] and st["devices"] == []


def test_connect_and_disconnect():
    bz = FakeBluez()
    m = manager(bz)
    assert m.request("connect", PAD.lower()) == (True, "")
    st = m.status()
    assert by_mac(st)[PAD]["connected"] and st["message_ok"] and "connected" in st["message"]
    assert m.request("disconnect", PAD) == (True, "")
    st = m.status()
    assert not by_mac(st)[PAD]["connected"] and st["message_ok"]


def test_connect_failure_is_reported_from_the_real_state():
    bz = FakeBluez()
    bz.reachable = set()
    m = manager(bz)
    m.request("connect", PAD)
    st = m.status()
    assert st["message_ok"] is False and "Switch it on" in st["message"]
    assert st["busy"] == ""


def test_scan_then_pair_trusts_and_connects():
    bz = FakeBluez()
    m = manager(bz)
    assert m.request("scan") == (True, "")
    st = m.status()
    assert st["scanning"] and 0 < st["scan_left_s"] <= 20
    assert m.request("pair", NEW) == (True, "")
    d = by_mac(m.status())[NEW]
    assert d["paired"] and d["trusted"] and d["connected"]
    assert not bz.scanning() and not m.status()["scanning"]


def test_pair_retries_once_without_the_scan():
    bz = FakeBluez()
    bz.pair_fails_while_scanning = True
    m = manager(bz)
    m.request("scan")
    m.request("pair", NEW)
    assert by_mac(m.status())[NEW]["paired"]
    assert sum(1 for c in bz.calls if c[:2] == ["bluetoothctl", "pair"]) == 2


def test_pair_failure_and_unknown_device():
    bz = FakeBluez()
    bz.reachable = set()
    m = manager(bz)
    m.request("pair", NEW)
    st = m.status()
    assert st["message_ok"] is False and "pairing mode" in st["message"]
    assert not by_mac(st)[NEW]["paired"]
    m.request("pair", "AA:BB:CC:DD:EE:99")
    assert "Search again" in m.status()["message"]
    assert not any(c[:2] == ["bluetoothctl", "pair"] and c[2].endswith("99") for c in bz.calls)


def test_forget_removes_the_pad():
    bz = FakeBluez()
    m = manager(bz)
    m.request("forget", PAD)
    st = m.status()
    assert PAD not in by_mac(st) and st["message_ok"] and "forgotten" in st["message"]


def test_bad_requests_never_reach_a_command_line():
    bz = FakeBluez()
    m = manager(bz)
    assert m.request("reboot", PAD) == (False, "unknown action")
    for mac in (None, "", "all", PAD + ";id", "-h"):
        assert m.request("forget", mac) == (False, "not a valid address")
    assert bz.calls == []


def test_only_one_action_at_a_time():
    bz = FakeBluez()
    m = GamepadManager(run=bz.run, spawn_scan=bz.spawn_scan, cache_s=0.0, background=False)
    seen = []
    real = m._do_connect
    m._do_connect = lambda mac: (seen.append(m.request("forget", PAD)), real(mac))[1]
    m.request("connect", PAD)
    assert seen == [(False, "busy")] and PAD in bz.dev


def test_stop_scan():
    bz = FakeBluez()
    m = manager(bz)
    m.request("scan")
    assert m.request("stop_scan") == (True, "")
    assert not bz.scanning() and not m.status()["scanning"]


def test_cache_spares_the_pi():
    bz = FakeBluez()
    t = [100.0]
    m = GamepadManager(run=bz.run, spawn_scan=bz.spawn_scan, clock=lambda: t[0],
                       cache_s=1.0, background=False)
    m.status(); m.status(); m.status()
    assert len(bz.calls) == 1
    t[0] += 1.5
    m.status()
    assert len(bz.calls) == 2


# ------------------------------------------------------------------ HTTP routing
def test_api_routes():
    bus, bz = ControlBus(), FakeBluez()
    m = manager(bz)
    st, ct, body = handle_api("GET", "/api/gamepad", b"", bus, 0.0, m)
    assert st == 200 and ct == "application/json" and json.loads(body)["available"]
    post = lambda d: handle_api("POST", "/api/gamepad", json.dumps(d).encode(), bus, 0.0, m)  # noqa: E731
    assert post({"action": "connect", "mac": PAD})[0] == 200
    assert bz.dev[PAD]["Connected"]
    assert post({"action": "connect", "mac": "nope"})[0] == 400
    assert post({"action": "format", "mac": PAD})[0] == 400
    assert handle_api("POST", "/api/gamepad", b"[1]", bus, 0.0, m)[0] == 400
    assert handle_api("GET", "/api/gamepad", b"", bus, 0.0, None)[0] == 503


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); n += 1
    print(f"OK - {n} gamepad_pairing tests passed")
