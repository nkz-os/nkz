"""Integration tests for weather-worker's /healthz + /readyz HTTP server.

Spins up the real server on an OS-assigned ephemeral port (127.0.0.1:0) and
issues real HTTP requests -- this is the surface Kubernetes probes hit.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # services/weather-worker
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))  # services/

import pytest

import weather_worker.heartbeat as hb
from weather_worker.health_server import start_health_server


@pytest.fixture
def server():
    hb.reset()
    hb.heartbeat("parcel-engine")
    hb.heartbeat("meteoalarm-engine")
    httpd = start_health_server(
        "127.0.0.1",
        0,
        {"parcel-engine": 100.0, "meteoalarm-engine": 100.0},
        startup_grace_seconds=180.0,
    )
    port = httpd.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        hb.reset()


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_healthz_always_ok(server):
    status, body = _get(f"{server}/healthz")
    assert status == 200
    assert body["status"] == "ok"


def test_readyz_200_when_all_threads_fresh(server):
    status, body = _get(f"{server}/readyz")
    assert status == 200
    assert body["status"] == "ready"
    assert body["threads"]["parcel-engine"]["status"] == "healthy"
    assert body["threads"]["meteoalarm-engine"]["status"] == "healthy"


def test_readyz_503_names_the_stale_thread(server):
    with hb._lock:
        hb._last_beats["meteoalarm-engine"] = time.monotonic() - 1000.0

    status, body = _get(f"{server}/readyz")

    assert status == 503
    assert body["status"] == "not_ready"
    assert body["threads"]["meteoalarm-engine"]["status"] == "stale"
    # The still-healthy thread must not be dragged down in the response.
    assert body["threads"]["parcel-engine"]["status"] == "healthy"


def test_unknown_path_returns_404(server):
    status, _body = _get(f"{server}/nope")
    assert status == 404
