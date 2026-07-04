"""
Standalone tests for web_control.handle_api (pure routing; no sockets).

Run:  python3 test_web_control.py
"""
import json
from control_bus import ControlBus
from web_control import handle_api, captive_target

PORTAL = "http://10.42.0.1:8080/"


def test_captive_serves_app_routes():
    for p in ("/", "/index.html", "/healthz", "/favicon.ico",
              "/api/state", "/api/command"):
        assert captive_target(p, PORTAL) is None, f"{p} should serve normally"


def test_captive_redirects_probes_and_stray_urls():
    for p in ("/hotspot-detect.html", "/generate_204", "/connecttest.txt",
              "/success.txt", "/ncsi.txt", "/anything", "/l/v1/foo"):
        assert captive_target(p, PORTAL) == PORTAL, f"{p} should redirect to portal"


def test_non_api_returns_none():
    bus = ControlBus()
    assert handle_api("GET", "/", b"", bus, 0.0) is None
    assert handle_api("GET", "/index.html", b"", bus, 0.0) is None


def test_state_returns_telemetry():
    bus = ControlBus()
    bus.set_telemetry({"mode": "walk", "paused": True})
    status, ct, body = handle_api("GET", "/api/state", b"", bus, 1.0)
    assert status == 200 and ct == "application/json"
    assert json.loads(body) == {"mode": "walk", "paused": True}


def test_command_sets_bus_and_scales_active():
    bus = ControlBus(stale_s=1.0)
    body = json.dumps({"active": True, "l_x": 0.5, "l_y": -0.5,
                       "left_trigger": 0.3}).encode()
    status, ct, resp = handle_api("POST", "/api/command", body, bus, now=10.0)
    assert status == 200 and json.loads(resp)["ok"] is True
    active, l_x, l_y, r_x, r_y, lt, rt = bus.stick_override(now=10.1)
    assert active is True and l_x == 0.5 and l_y == -0.5 and lt == 0.3


def test_command_bad_json_400():
    bus = ControlBus()
    status, ct, resp = handle_api("POST", "/api/command", b"{not json", bus, 0.0)
    assert status == 400 and json.loads(resp)["ok"] is False


def test_button_press_routes_to_bus():
    bus = ControlBus()
    body = json.dumps({"button": "A", "action": "press"}).encode()
    status, ct, resp = handle_api("POST", "/api/button", body, bus, 0.0)
    assert status == 200 and json.loads(resp)["ok"] is True
    assert bus.consume_buttons()["A"] is True


def test_button_unknown_400():
    bus = ControlBus()
    body = json.dumps({"button": "NOPE"}).encode()
    status, ct, resp = handle_api("POST", "/api/button", body, bus, 0.0)
    assert status == 400 and json.loads(resp)["ok"] is False


def test_button_hold_down_up():
    bus = ControlBus()
    handle_api("POST", "/api/button", json.dumps({"button": "LB", "action": "down"}).encode(), bus, 0.0)
    assert bus.consume_buttons()["LB"] is True
    handle_api("POST", "/api/button", json.dumps({"button": "LB", "action": "up"}).encode(), bus, 0.0)
    assert bus.consume_buttons()["LB"] is False


def test_trim_nudge_routes_to_bus():
    bus = ControlBus()
    body = json.dumps({"axis": "pitch", "delta": 0.002}).encode()
    status, ct, resp = handle_api("POST", "/api/trim", body, bus, 0.0)
    assert status == 200 and json.loads(resp)["ok"] is True
    assert bus.consume_trim() == (0.002, 0.0, False)


def test_trim_save_routes_to_bus():
    bus = ControlBus()
    body = json.dumps({"action": "save"}).encode()
    status, ct, resp = handle_api("POST", "/api/trim", body, bus, 0.0)
    assert status == 200 and json.loads(resp)["ok"] is True
    assert bus.consume_trim() == (0.0, 0.0, True)


def test_trim_bad_axis_400():
    bus = ControlBus()
    body = json.dumps({"axis": "yaw", "delta": 0.002}).encode()
    status, ct, resp = handle_api("POST", "/api/trim", body, bus, 0.0)
    assert status == 400 and json.loads(resp)["ok"] is False


def test_unknown_api_404():
    bus = ControlBus()
    status, ct, body = handle_api("GET", "/api/nope", b"", bus, 0.0)
    assert status == 404


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} web_control tests passed.")
