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
