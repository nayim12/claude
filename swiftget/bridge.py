"""Local bridge between the SwiftGet browser extension and the app.

A tiny HTTP server bound to 127.0.0.1 only. The extension POSTs captured
downloads here; requests are turned into events on a queue that the GUI
thread drains (Tk is not thread-safe).

Security:
  * listens on the loopback interface only
  * every request must carry the custom header ``X-SwiftGet``. Ordinary web
    pages cannot add a custom header cross-origin without a CORS preflight,
    and preflights are only approved for browser-extension origins
  * requests that carry an Origin must come from a browser extension
  * the Host header must be the loopback address (blocks DNS rebinding)
"""

from __future__ import annotations

import json
import queue
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 9614
EXT_ORIGINS = ("chrome-extension://", "moz-extension://", "extension://",
               "safari-web-extension://")
MAX_BODY = 4 * 1024 * 1024


class _Handler(BaseHTTPRequestHandler):
    server_version = "SwiftGetBridge/1.0"
    bridge: "Bridge" = None  # set on subclass

    def log_message(self, *args):
        pass

    # ---- helpers
    def _origin_ok(self):
        origin = self.headers.get("Origin")
        return origin is None or origin.startswith(EXT_ORIGINS)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def _cors(self):
        origin = self.headers.get("Origin")
        if origin and origin.startswith(EXT_ORIGINS):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _guard(self):
        if not (self._host_ok() and self._origin_ok()):
            self._reply(403, {"ok": False, "error": "forbidden"})
            return False
        if not self.headers.get("X-SwiftGet"):
            self._reply(403, {"ok": False, "error": "missing X-SwiftGet header"})
            return False
        return True

    # ---- verbs
    def do_OPTIONS(self):
        origin = self.headers.get("Origin") or ""
        if not (self._host_ok() and origin.startswith(EXT_ORIGINS)):
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-SwiftGet")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self):
        if not self._guard():
            return
        if self.path == "/ping":
            self._reply(200, {"ok": True, "app": "SwiftGet",
                              "version": self.bridge.version,
                              "enabled": self.bridge.enabled()})
        else:
            self._reply(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not self._guard():
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("body too large")
            data = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
        except (ValueError, json.JSONDecodeError) as e:
            self._reply(400, {"ok": False, "error": str(e)})
            return

        if self.path == "/show":
            self.bridge.events.put(("show", {}))
            self._reply(200, {"ok": True})
            return
        if not self.bridge.enabled():
            self._reply(503, {"ok": False, "error": "browser integration is disabled"})
            return
        if self.path == "/add":
            url = str(data.get("url", ""))
            if not url.lower().startswith(("http://", "https://")):
                self._reply(400, {"ok": False, "error": "only http(s) URLs are supported"})
                return
            self.bridge.events.put(("add", _clean(data)))
            self._reply(200, {"ok": True})
        elif self.path == "/batch":
            items = data.get("items") or []
            items = [i for i in items if isinstance(i, dict) and
                     str(i.get("url", "")).lower().startswith(("http://", "https://"))]
            if not items:
                self._reply(400, {"ok": False, "error": "no downloadable links"})
                return
            common = _clean(data)
            self.bridge.events.put(("batch", {
                "items": [{"url": str(i["url"]), "filename": str(i.get("filename") or ""),
                           "text": str(i.get("text") or "")[:200]} for i in items[:5000]],
                "headers": common["headers"],
            }))
            self._reply(200, {"ok": True, "count": len(items)})
        else:
            self._reply(404, {"ok": False, "error": "not found"})


def _clean(d):
    headers = {}
    if d.get("cookies"):
        headers["Cookie"] = str(d["cookies"])[:16000]
    if d.get("referrer"):
        headers["Referer"] = str(d["referrer"])[:4000]
    if d.get("userAgent"):
        headers["User-Agent"] = str(d["userAgent"])[:1000]
    size = d.get("fileSize")
    return {
        "url": str(d.get("url", "")),
        "filename": str(d.get("filename") or ""),
        "size": int(size) if isinstance(size, (int, float)) and size > 0 else -1,
        "mime": str(d.get("mime") or ""),
        "headers": headers,
    }


class Bridge:
    def __init__(self, version, enabled_fn):
        self.version = version
        self.enabled = enabled_fn
        self.events: "queue.Queue[tuple[str, dict]]" = queue.Queue()
        self.server = None
        self.error = ""

    def start(self):
        handler = type("Handler", (_Handler,), {"bridge": self})
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", PORT), handler)
        except OSError as e:
            self.error = f"port {PORT} unavailable ({e.strerror or e})"
            return False
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True,
                         name="bridge").start()
        return True

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()


def ping_existing(timeout=1.0):
    """Return True if another SwiftGet instance owns the bridge port."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/ping",
                                     headers={"X-SwiftGet": "1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r).get("app") == "SwiftGet"
    except (OSError, ValueError):
        return False


def show_existing():
    try:
        import sys
        if sys.platform == "win32":   # let the running instance take the foreground
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
    except (ImportError, AttributeError, OSError):
        pass
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/show", data=b"{}",
                                     headers={"X-SwiftGet": "1",
                                              "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2).close()
    except OSError:
        pass
