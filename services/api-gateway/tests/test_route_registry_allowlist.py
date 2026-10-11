"""backend_service comes from a JSONB column any publisher can write; the
gateway must only ever proxy to in-cluster service names."""
from unittest.mock import MagicMock

import pytest

import fiware_api_gateway as gw


def _registry(rows, monkeypatch):
    cur = MagicMock()
    cur.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cur
    monkeypatch.setattr(gw.psycopg2, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(gw, "_route_cache", {})
    monkeypatch.setattr(gw, "_route_cache_ts", 0.0)
    return gw._refresh_route_registry()


@pytest.mark.parametrize(
    "backend",
    [
        "http://10.43.0.1:443",
        "http://user@svc:8000",
        "http://svc:8000/admin",
        "https://evil.example:443",
        "http://svc.other.svc.cluster.local:8000",
        "http://svc",
        "http://svc:8000?x=1",
        "ftp://svc:21",
        "http://2130706433:8000",
        "http://0x7f000001:8000",
        "http://127:8000",
    ],
)
def test_unsafe_backend_service_is_skipped(monkeypatch, backend):
    routes = _registry(
        [{"id": "m", "metadata": {"api_prefix": "/api/m", "backend_service": backend}}],
        monkeypatch,
    )
    assert routes == {}


@pytest.mark.parametrize(
    "backend",
    [
        "http://m-backend-service:8000",
        "http://agent-backend-service.nekazari.svc.cluster.local:8000",
        "http://soil-module-service.nekazari:8000",
    ],
)
def test_cluster_service_backend_is_accepted(monkeypatch, backend):
    routes = _registry(
        [{"id": "m", "metadata": {"api_prefix": "/api/m", "backend_service": backend}}],
        monkeypatch,
    )
    assert routes["/api/m"]["backend_service"] == backend


def test_prefix_must_live_under_api(monkeypatch):
    routes = _registry(
        [{"id": "m", "metadata": {"api_prefix": "/internal", "backend_service": "http://svc:8000"}}],
        monkeypatch,
    )
    assert routes == {}


def _row(**meta):
    base = {"api_prefix": "/api/m", "backend_service": "http://svc:8000"}
    base.update(meta)
    return [{"id": "m", "metadata": base}]


@pytest.mark.parametrize("mount", ["@evil.example", "evil", "/a@evil.example", "/a/../b", 123])
def test_unsafe_backend_mount_is_skipped(monkeypatch, mount):
    assert _registry(_row(backend_mount=mount), monkeypatch) == {}


def test_normal_backend_mount_is_kept(monkeypatch):
    routes = _registry(_row(backend_mount="/api/m"), monkeypatch)
    assert routes["/api/m"]["backend_mount"] == "/api/m"


def test_missing_backend_mount_defaults_to_prefix(monkeypatch):
    routes = _registry(_row(), monkeypatch)
    assert routes["/api/m"]["backend_mount"] == "/api/m"


@pytest.mark.parametrize("strip", ["@x", "/a@x", "/a/../b", 123])
def test_unsafe_strip_remainder_prefix_is_skipped(monkeypatch, strip):
    assert _registry(_row(strip_remainder_prefix=strip), monkeypatch) == {}


@pytest.mark.parametrize("strip", ["", "/v1/soil", "v1/soil"])
def test_strip_remainder_prefix_empty_or_path_is_kept(monkeypatch, strip):
    routes = _registry(_row(strip_remainder_prefix=strip), monkeypatch)
    assert routes["/api/m"]["strip_remainder_prefix"] == strip
