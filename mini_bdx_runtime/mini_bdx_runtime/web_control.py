"""
Tiny phone Web UI server for the Open Duck Mini.

Zero external dependencies: a stdlib ThreadingHTTPServer on a daemon thread. The
phone connects to the robot's own Wi-Fi/AP and loads a single self-contained page
(webui/index.html). It polls GET /api/state (~4 Hz) and POSTs joystick/button
intents into a shared ControlBus, which the input reader folds in alongside the
gamepad. CPU cost is negligible next to ONNX inference: a lock-guarded snapshot
and a few tiny JSON responses per second.

The API routing is a pure function (handle_api) so it unit-tests without sockets.
See docs/webui-api.md for the contract.
"""
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
WEBUI_DIR = os.path.join(HERE, "webui")
DEFAULT_PORT = 8080

_FALLBACK_HTML = (
    "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
    "<style>body{font-family:system-ui;background:#1a1712;color:#f0e6d2;padding:2rem}"
    "code{color:#ffb347}</style><h1>Duck control</h1>"
    "<p>The UI page <code>webui/index.html</code> isn't installed, but the API is up.</p>"
    "<p>Try <code>GET /api/state</code>.</p>"
)


def _json_bytes(obj):
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


def handle_api(method, path, body_bytes, bus, now):
    """Route an /api/* request purely (no sockets). Returns
    (status:int, content_type:str, body:bytes) or None if `path` isn't an API
    route (caller then serves a static file)."""
    if not path.startswith("/api/"):
        return None

    if method == "GET" and path == "/api/state":
        return 200, "application/json", _json_bytes(bus.get_telemetry())

    if method == "POST" and path == "/api/command":
        try:
            d = json.loads(body_bytes or b"{}")
        except (ValueError, TypeError):
            return 400, "application/json", _json_bytes({"ok": False, "error": "bad json"})
        bus.set_command(
            now=now,
            active=bool(d.get("active", False)),
            l_x=d.get("l_x", 0.0), l_y=d.get("l_y", 0.0),
            r_x=d.get("r_x", 0.0), r_y=d.get("r_y", 0.0),
            left_trigger=d.get("left_trigger", 0.0),
            right_trigger=d.get("right_trigger", 0.0),
        )
        return 200, "application/json", _json_bytes({"ok": True})

    if method == "POST" and path == "/api/button":
        try:
            d = json.loads(body_bytes or b"{}")
        except (ValueError, TypeError):
            return 400, "application/json", _json_bytes({"ok": False, "error": "bad json"})
        ok = bus.push_button(str(d.get("button", "")), str(d.get("action", "press")))
        return (200 if ok else 400), "application/json", _json_bytes({"ok": bool(ok)})

    if method == "POST" and path == "/api/trim":
        try:
            d = json.loads(body_bytes or b"{}")
        except (ValueError, TypeError):
            return 400, "application/json", _json_bytes({"ok": False, "error": "bad json"})
        if str(d.get("action", "")) == "save":
            bus.push_trim_save()
            return 200, "application/json", _json_bytes({"ok": True})
        try:
            ok = bus.push_trim(str(d.get("axis", "")), float(d.get("delta", 0.0)))
        except (ValueError, TypeError):
            return 400, "application/json", _json_bytes({"ok": False, "error": "bad delta"})
        return (200 if ok else 400), "application/json", _json_bytes({"ok": bool(ok)})

    return 404, "application/json", _json_bytes({"ok": False, "error": "not found"})


# GET paths the server owns. Any OTHER path is treated as a captive-portal probe
# (iOS /hotspot-detect.html, Android /generate_204, Windows /connecttest.txt, or
# any stray http URL a client opens) and 302-redirected to the control page, so
# joining the duck's Wi-Fi pops the UI up automatically.
_APP_GET_PATHS = ("/", "/index.html", "/healthz", "/favicon.ico")


def captive_target(path, portal_url):
    """Return None if `path` is an app route the server should serve normally,
    else the portal URL to redirect to. Pure -> unit-tested off-robot."""
    if path.startswith("/api/") or path in _APP_GET_PATHS:
        return None
    return portal_url


# iOS/macOS look for EXACTLY this page (they match the "Success" body) to decide a
# network has real internet. Return it verbatim, 200 OK, and iOS concludes there is
# NO captive portal -> it never force-opens the CNA popup and STAYS connected.
_APPLE_SUCCESS = b"<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>"


def captive_probe_response(path, host="", user_agent=""):
    """If this GET is a known OS connectivity-check probe, return the
    (status, content_type, body) that SATISFIES it, so the device thinks the network
    is online, doesn't force the captive popup, and stays connected. Return None for
    anything else (the caller then serves the app route or 302s to the control page,
    preserving the open-any-URL-and-get-forwarded behaviour). Pure -> unit-tested.

    Matching on the probe PATH is the robust signal (the nft rule sends every AP
    :80 request here regardless of Host); Host / User-Agent are belt-and-suspenders."""
    host = (host or "").split(":")[0].lower()
    ua = user_agent or ""
    # iOS / macOS  (captive.apple.com/hotspot-detect.html, or the CNA agent's UA)
    if (path in ("/hotspot-detect.html", "/library/test/success.html")
            or host == "captive.apple.com"
            or ua.startswith("CaptiveNetworkSupport")):
        return 200, "text/html", _APPLE_SUCCESS
    # Android  (expects HTTP 204, empty body)
    if (path in ("/generate_204", "/gen_204")
            or host in ("connectivitycheck.gstatic.com", "connectivitycheck.android.com")):
        return 204, "text/plain", b""
    # Windows
    if path == "/ncsi.txt" or host == "www.msftncsi.com":
        return 200, "text/plain", b"Microsoft NCSI"
    if path == "/connecttest.txt" or host == "www.msftconnecttest.com":
        return 200, "text/plain", b"Microsoft Connect Test"
    return None


def _read_index():
    path = os.path.join(WEBUI_DIR, "index.html")
    try:
        with open(path, "rb") as f:
            return f.read(), "text/html; charset=utf-8"
    except OSError:
        return _FALLBACK_HTML.encode("utf-8"), "text/html; charset=utf-8"


def get_lan_ip():
    """Best-effort LAN IP for printing the phone URL (no traffic actually sent).

    The UDP-connect trick fails to find a route on an access-point-only setup
    (the duck is often its own AP at 10.42.0.1), so we try a few targets and then
    fall back to `hostname -I`, skipping loopback."""
    for target in ("10.255.255.255", "192.168.255.255", "8.8.8.8"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((target, 1))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        except OSError:
            pass
        finally:
            s.close()
    try:
        import subprocess
        for ip in subprocess.check_output(["hostname", "-I"], text=True).split():
            if ip and not ip.startswith("127.") and ":" not in ip:
                return ip
    except Exception:  # noqa: BLE001
        pass
    return "127.0.0.1"


def _make_handler(bus, clock, portal_url):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # silence per-request stderr spam (4 Hz poll)
            pass

        def _send(self, status, content_type, body, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _redirect(self, location):
            body = (f'<html><head><meta http-equiv="refresh" content="0; url={location}">'
                    f'</head><body><a href="{location}">Open Duck control</a></body></html>'
                    ).encode()
            self._send(302, "text/html; charset=utf-8", body, extra={"Location": location})

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path.startswith("/api/"):
                r = handle_api("GET", path, b"", bus, clock())
                self._send(*r)
                return
            if path in ("/", "/index.html"):
                body, ct = _read_index()
                self._send(200, ct, body)
                return
            if path == "/healthz":
                self._send(200, "text/plain", b"ok")
                return
            if path == "/favicon.ico":       # avoid a noisy 404 per page load
                self._send(204, "image/x-icon", b"")
                return
            # OS connectivity-check probe? Answer it so the phone believes the network
            # is online: no forced captive popup, and it STAYS connected (fixes iOS
            # dropping the Wi-Fi when you dismiss the popup).
            probe = captive_probe_response(
                path, self.headers.get("Host", ""), self.headers.get("User-Agent", ""))
            if probe is not None:
                self._send(*probe)
                return
            # Every other GET (a stray http URL the user opened) still 302s to the
            # control page, so opening the browser forwards you without typing an IP.
            self._redirect(portal_url)

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b""
            r = handle_api("POST", path, body, bus, clock())
            if r is None:
                self._send(404, "text/plain", b"not found")
            else:
                self._send(*r)

    return Handler


class WebControlServer:
    """Owns the HTTP server thread. Construct with the shared ControlBus, call
    start(); the control loop calls bus.set_telemetry(...) each tick."""

    def __init__(self, bus, host="0.0.0.0", port=DEFAULT_PORT, clock=None):
        import time as _time
        self.bus = bus
        self.host = host
        self.port = port
        self._clock = clock or _time.time
        self._httpd = None
        self._thread = None

    def start(self):
        portal_url = f"http://{get_lan_ip()}:{self.port}/"
        handler = _make_handler(self.bus, self._clock, portal_url)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        print(f"[web] control UI at {portal_url}  (also http://<robot>.local:{self.port}/)")
        return portal_url

    def stop(self):
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
