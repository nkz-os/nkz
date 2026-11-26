"""Unit tests for the short-TTL Redis cache on GET /parcel/{id}/agro-status."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")
for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from app.services.agro_status_cache import (  # noqa: E402
    AgroStatusCache,
    cache_key,
    get_agro_status_cache,
    reset_agro_status_cache,
)


class FakeRedis:
    """Minimal in-memory Redis double supporting get and setex."""

    def __init__(self):
        self.store: dict[str, tuple[str, int]] = {}
        self.get_calls: list[str] = []
        self.setex_calls: list[tuple[str, int, str]] = []

    def get(self, key: str):
        self.get_calls.append(key)
        entry = self.store.get(key)
        return entry[0] if entry else None

    def setex(self, key: str, ttl: int, value: str):
        self.setex_calls.append((key, ttl, value))
        self.store[key] = (value, ttl)
        return True


class FailingRedis:
    """Redis double that always raises ConnectionError."""

    def get(self, key: str):
        raise ConnectionError("redis down")

    def setex(self, key: str, ttl: int, value: str):
        raise ConnectionError("redis down")


def _resp(status: int, payload):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = payload
    m.text = ""
    m.content = b"x"
    return m


def _setup_agro_endpoint(monkeypatch, cache: AgroStatusCache, tenant: str = "tenant-a"):
    from fastapi.testclient import TestClient
    from app.auth import require_auth as _require_auth
    from app.main import app
    import app.routers.parcels as parcels

    parcel_urn = "urn:ngsi-ld:AgriParcel:parcel-1"
    parcel_entity = {
        "id": parcel_urn,
        "type": "AgriParcel",
        "name": {"type": "Property", "value": "North Plot"},
        "location": {
            "type": "GeoProperty",
            "value": {"type": "Point", "coordinates": [-1.64, 42.81]},
        },
    }
    orion_calls = {"count": 0}

    def fake_get(url, headers=None, params=None, timeout=None):
        if "/ngsi-ld/v1/entities/urn:ngsi-ld:AgriParcel:" in url:
            orion_calls["count"] += 1
            return _resp(200, parcel_entity)
        return _resp(200, [])

    class _FakeCursor:
        def execute(self, *a, **k):
            pass

        def fetchall(self):
            return []

        def fetchone(self):
            return {
                "observed_at": datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc),
                "measurements": {"airTemperature": 18.5, "relativeHumidity": 55},
            }

        def close(self):
            pass

    class _FakeConn:
        def cursor(self, **kw):
            return _FakeCursor()

        def close(self):
            pass

    monkeypatch.setattr(parcels, "get_agro_status_cache", lambda: cache)
    monkeypatch.setattr(parcels.requests, "get", fake_get)
    monkeypatch.setattr(parcels.requests, "patch", lambda *a, **k: _resp(204, {}))
    monkeypatch.setattr(parcels, "_orion_headers", lambda tid: {})
    monkeypatch.setattr(parcels, "_orion_query_headers", lambda tid: {})
    monkeypatch.setattr(parcels, "_fetch_weather_map_stats", lambda pid, tid: {})
    monkeypatch.setattr(parcels, "get_db_connection", lambda tid: _FakeConn())

    calc_calls = {"count": 0}

    def fake_calc(*args, **kwargs):
        calc_calls["count"] += 1
        return {
            "parcel_id": parcel_urn,
            "tenant_marker": tenant,
            "semaphores": {"spraying": "optimal", "workability": "optimal", "irrigation": "satisfied"},
            "weather": {
                "temperature": 18.5,
                "observed_at": datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc),
            },
            "metrics": {"temperature": 18.5},
        }

    monkeypatch.setattr(parcels, "calculate_agro_status", fake_calc)
    app.dependency_overrides[_require_auth] = lambda: tenant
    return TestClient(app), app, _require_auth, orion_calls, calc_calls


def test_cache_miss_then_hit(monkeypatch):
    fake_redis = FakeRedis()
    cache = AgroStatusCache(fake_redis, ttl_s=300)
    client, app, dep, orion_calls, calc_calls = _setup_agro_endpoint(
        monkeypatch, cache, tenant="tenant-a"
    )
    try:
        r1 = client.get("/api/weather/parcel/parcel-1/agro-status")
        assert r1.status_code == 200
        assert orion_calls["count"] == 1
        assert calc_calls["count"] == 1
        assert len(fake_redis.setex_calls) == 1
        stored_key, stored_ttl, _ = fake_redis.setex_calls[0]
        assert stored_key == cache_key("tenant-a", "urn:ngsi-ld:AgriParcel:parcel-1")
        assert stored_ttl == 300

        # Second call hits Redis and skips Orion + calculation
        r2 = client.get("/api/weather/parcel/parcel-1/agro-status")
        assert r2.status_code == 200
        assert r2.json() == r1.json()
        assert orion_calls["count"] == 1
        assert calc_calls["count"] == 1
    finally:
        app.dependency_overrides.pop(dep, None)


def test_tenant_isolation_in_cache_key():
    fake_redis = FakeRedis()
    cache = AgroStatusCache(fake_redis, ttl_s=600)
    parcel_id = "urn:ngsi-ld:AgriParcel:shared-id"

    assert cache_key("tenant-a", parcel_id) != cache_key("tenant-b", parcel_id)

    cache.set("tenant-a", parcel_id, {"parcel_id": parcel_id, "source": "a"})
    cache.set("tenant-b", parcel_id, {"parcel_id": parcel_id, "source": "b"})

    assert cache.get("tenant-a", parcel_id) == {"parcel_id": parcel_id, "source": "a"}
    assert cache.get("tenant-b", parcel_id) == {"parcel_id": parcel_id, "source": "b"}
    assert cache.get("tenant-c", parcel_id) is None


def test_redis_failure_fails_open(monkeypatch, caplog):
    cache = AgroStatusCache(FailingRedis(), ttl_s=600)
    client, app, dep, orion_calls, calc_calls = _setup_agro_endpoint(
        monkeypatch, cache, tenant="tenant-a"
    )
    try:
        with caplog.at_level("WARNING"):
            r = client.get("/api/weather/parcel/parcel-1/agro-status")
        assert r.status_code == 200
        assert r.json()["semaphores"]["spraying"] == "optimal"
        assert orion_calls["count"] == 1
        assert calc_calls["count"] == 1
        assert any("cache read failed" in rec.message for rec in caplog.records)
        assert any("cache write failed" in rec.message for rec in caplog.records)
    finally:
        app.dependency_overrides.pop(dep, None)


def test_unconfigured_redis_disables_cache_cleanly(monkeypatch):
    reset_agro_status_cache()
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AGRO_STATUS_CACHE_TTL_S", "120")
    try:
        cache = get_agro_status_cache()
        assert not cache.enabled
        assert cache.ttl_s == 120
        assert cache.get("tenant-a", "urn:ngsi-ld:AgriParcel:1") is None
        assert cache.set("tenant-a", "urn:ngsi-ld:AgriParcel:1", {"ok": True}) is False
    finally:
        reset_agro_status_cache()


def test_zero_ttl_disables_cache(monkeypatch):
    fake_redis = FakeRedis()
    cache = AgroStatusCache(fake_redis, ttl_s=0)
    assert not cache.enabled
    assert cache.set("tenant-a", "urn:ngsi-ld:AgriParcel:1", {"ok": True}) is False
    assert cache.get("tenant-a", "urn:ngsi-ld:AgriParcel:1") is None
    assert fake_redis.get_calls == []
    assert fake_redis.setex_calls == []
