"""HTTP server: JSON API plus the static web UI. Standard library only."""

from __future__ import annotations

import base64
import hmac
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

from . import __version__
from .analysis import analyze_links, build_graph
from .models import LinkConfig
from .storage.base import FlowStore

MIN_WINDOW = 10.0
MAX_WINDOW = 7 * 86400.0

# Only these paths are served as static files. A fixed allow-list means no path traversal.
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}
EMPTY_PATHS = {"/favicon.ico"}

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


class Dashboard:
    """Everything the request handlers need, bundled so tests can build one easily."""

    def __init__(
        self,
        store: FlowStore,
        links: list[LinkConfig] | None = None,
        names: dict[str, str] | None = None,
        auth: str | None = None,
    ):
        self.store = store
        self.links = links or []
        self.names = names or {}
        self.auth = auth  # "user:password" or None


def _num(qs: dict, key: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(qs.get(key, [default])[0])
    except (TypeError, ValueError):
        return default
    if value != value:  # NaN
        return default
    return min(max(value, lo), hi)


def _flag(qs: dict, key: str, default: bool = True) -> bool:
    raw = qs.get(key, [None])[0]
    if raw is None:
        return default
    return raw.lower() not in ("0", "false", "no", "off")


def api_summary(d: Dashboard, qs: dict) -> dict:
    window = _num(qs, "window", 300, MIN_WINDOW, MAX_WINDOW)
    flows, nbytes = d.store.totals(time.time() - window)
    return {"window_seconds": window, "flows": flows, "bytes": nbytes, "mbps": nbytes * 8 / window / 1e6}


def api_graph(d: Dashboard, qs: dict) -> dict:
    window = _num(qs, "window", 300, MIN_WINDOW, MAX_WINDOW)
    edges = d.store.graph_edges(time.time() - window)
    return build_graph(edges, window, d.names, collapse_external=_flag(qs, "collapse"))


def api_top(d: Dashboard, qs: dict) -> dict:
    window = _num(qs, "window", 300, MIN_WINDOW, MAX_WINDOW)
    limit = int(_num(qs, "limit", 10, 1, 100))
    since = time.time() - window
    return {
        "window_seconds": window,
        "talkers": [
            {"ip": ip, "label": d.names.get(ip, ip), "bytes": b} for ip, b in d.store.top_talkers(since, limit)
        ],
        "flows": [
            {"src": s, "dst": t, "dport": p, "src_label": d.names.get(s, s), "dst_label": d.names.get(t, t), "bytes": b}
            for s, t, p, b in d.store.top_flows(since, limit)
        ],
    }


def api_links(d: Dashboard, qs: dict) -> dict:
    window = _num(qs, "window", 300, MIN_WINDOW, MAX_WINDOW)
    statuses = analyze_links(d.store, d.links, window_seconds=window) if d.links else []
    return {
        "window_seconds": window,
        "configured": bool(d.links),
        "links": [
            {
                "name": s.link.name,
                "exporter": s.link.exporter,
                "interface": s.link.interface,
                "capacity_mbps": s.link.capacity_mbps,
                "bytes": s.bytes,
                "mbps": s.mbps,
                "utilization": s.utilization,
                "level": s.level,
            }
            for s in statuses
        ],
    }


def api_timeline(d: Dashboard, qs: dict) -> dict:
    window = _num(qs, "window", 900, MIN_WINDOW, MAX_WINDOW)
    buckets = int(_num(qs, "buckets", 60, 5, 240))
    bucket = window / buckets
    now = time.time()
    first = int((now - window) // bucket) * bucket
    last = int(now // bucket) * bucket
    found = {round(t, 3): b for t, b in d.store.timeline(now - window, bucket)}
    points = []
    t = first
    while t <= last + 1e-9:
        nbytes = found.get(round(t, 3), 0)
        points.append({"t": t, "bytes": nbytes, "mbps": nbytes * 8 / bucket / 1e6})
        t += bucket
    return {"window_seconds": window, "bucket_seconds": bucket, "points": points}


def api_health(d: Dashboard, qs: dict) -> dict:
    return {"status": "ok", "version": __version__, "time": time.time()}


ROUTES = {
    "/api/health": api_health,
    "/api/summary": api_summary,
    "/api/graph": api_graph,
    "/api/top": api_top,
    "/api/links": api_links,
    "/api/timeline": api_timeline,
}


def make_server(host: str, port: int, dashboard: Dashboard) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "Chokepoint"
        sys_version = ""

        def log_message(self, fmt, *args):  # the UI polls often; keep the console quiet
            pass

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in {**SECURITY_HEADERS, **(extra or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _authorized(self) -> bool:
            if not dashboard.auth:
                return True
            scheme, _, value = self.headers.get("Authorization", "").partition(" ")
            if scheme.lower() != "basic":
                return False
            try:
                supplied = base64.b64decode(value.strip(), validate=True)
            except ValueError:
                return False
            return hmac.compare_digest(supplied, dashboard.auth.encode())

        def do_GET(self) -> None:
            if not self._authorized():
                self._send(
                    401, b'{"error":"authentication required"}', "application/json",
                    {"WWW-Authenticate": 'Basic realm="Chokepoint", charset="UTF-8"'},
                )
                return
            url = urlparse(self.path)
            try:
                if url.path in STATIC:
                    name, ctype = STATIC[url.path]
                    body = (resources.files("chokepoint") / "web" / name).read_bytes()
                    self._send(200, body, ctype)
                elif url.path in EMPTY_PATHS:
                    self._send(204, b"", "image/x-icon")
                elif url.path in ROUTES:
                    self._json(200, ROUTES[url.path](dashboard, parse_qs(url.query)))
                else:
                    self._json(404, {"error": "not found"})
            except Exception as exc:  # never leak internals to the client
                print(f"chokepoint: error handling {url.path}: {exc!r}", file=sys.stderr)
                self._json(500, {"error": "internal error"})

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
