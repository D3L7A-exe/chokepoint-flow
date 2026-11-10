import base64
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from chokepoint.models import Flow, LinkConfig
from chokepoint.server import Dashboard, make_server
from chokepoint.storage import SqliteStore


def flow(src, dst, nbytes, dport=443, out_if=2, age=5):
    now = time.time()
    return Flow(exporter="r1", src=src, dst=dst, sport=40000, dport=dport, proto=6, bytes=nbytes,
                packets=1, start=now - age - 1, end=now - age, in_if=1, out_if=out_if)


@pytest.fixture
def running(request):
    auth = getattr(request, "param", None)
    store = SqliteStore(":memory:")
    store.add_flows([
        flow("192.168.1.115", "192.168.1.200", 5_000_000, 8006),
        flow("192.168.1.200", "8.8.8.8", 1_000_000, 53),
        flow("192.168.1.201", "192.168.1.203", 20_000, 1514, out_if=3),
    ])
    dash = Dashboard(store, [LinkConfig("uplink", "r1", 2, 100)], {"192.168.1.200": "proxmox"}, auth)
    server = make_server("127.0.0.1", 0, dash)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    store.close()


def get(base, path, headers=None):
    req = urllib.request.Request(base + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def get_json(base, path, headers=None):
    status, _, body = get(base, path, headers)
    return status, json.loads(body)


def test_health(running):
    status, data = get_json(running, "/api/health")
    assert status == 200 and data["status"] == "ok"


def test_summary(running):
    _, data = get_json(running, "/api/summary?window=60")
    assert data["flows"] == 3
    assert data["bytes"] == 6_020_000


def test_graph_uses_names_and_collapses_internet(running):
    _, g = get_json(running, "/api/graph?window=60")
    labels = {n["id"]: n["label"] for n in g["nodes"]}
    assert labels["192.168.1.200"] == "proxmox"
    assert "internet" in labels
    _, raw = get_json(running, "/api/graph?window=60&collapse=0")
    assert "8.8.8.8" in {n["id"] for n in raw["nodes"]}


def test_top(running):
    _, t = get_json(running, "/api/top?window=60&limit=2")
    assert len(t["talkers"]) == 2
    assert t["talkers"][0]["ip"] == "192.168.1.115"
    assert t["flows"][0]["dst_label"] == "proxmox"


def test_links_status(running):
    _, data = get_json(running, "/api/links?window=60")
    assert data["configured"] is True
    link = data["links"][0]
    assert link["name"] == "uplink" and link["level"] in {"ok", "warning", "critical"}
    assert link["bytes"] == 6_000_000  # the two flows that leave interface 2


def test_timeline_is_continuous_and_sums_to_total(running):
    _, data = get_json(running, "/api/timeline?window=60&buckets=12")
    assert data["bucket_seconds"] == pytest.approx(5)
    ts = [p["t"] for p in data["points"]]
    assert all(b - a == pytest.approx(5) for a, b in zip(ts, ts[1:]))
    assert sum(p["bytes"] for p in data["points"]) == 6_020_000


@pytest.mark.parametrize("query", ["window=abc", "window=nan", "window=-5", "window=1e99", "limit=-1&window=60", "buckets=0"])
def test_bad_parameters_are_clamped_not_errors(running, query):
    for path in ("/api/summary", "/api/graph", "/api/top", "/api/timeline"):
        status, _ = get_json(running, f"{path}?{query}")
        assert status == 200


def test_static_files_and_security_headers(running):
    for path, marker in (("/", b"<title>Chokepoint</title>"), ("/app.js", b"use strict"), ("/app.css", b"--heat-0")):
        status, headers, body = get(running, path)
        assert status == 200 and marker in body
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "default-src 'self'" in headers["Content-Security-Policy"]


@pytest.mark.parametrize("path", ["/nope", "/../pyproject.toml", "/%2e%2e/pyproject.toml", "/app.js/../../cli.py", "/api/"])
def test_unknown_paths_404(running, path):
    status, _, _ = get(running, path)
    assert status == 404


@pytest.mark.parametrize("running", ["admin:s3cret"], indirect=True)
def test_basic_auth(running):
    status, headers, _ = get(running, "/api/health")
    assert status == 401 and "Basic" in headers["WWW-Authenticate"]

    def creds(value):
        return {"Authorization": "Basic " + base64.b64encode(value.encode()).decode()}

    assert get(running, "/api/health", creds("admin:wrong"))[0] == 401
    assert get(running, "/api/health", creds("admin:s3cret"))[0] == 200
    assert get(running, "/api/health", {"Authorization": "Basic !!!notbase64"})[0] == 401
    assert get(running, "/", )[0] == 401  # the UI itself is protected too
