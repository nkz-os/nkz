"""Geometry cache filling (contract 1.4): upsert from Orion location notifications."""

import asyncio
import functools
import json
import os
import sys

import pytest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import FastAPI
from fastapi.testclient import TestClient

import telemetry_worker.notification_handler as nh
import telemetry_worker.subscription_manager as sm
from telemetry_worker.geometry_cache import delete_geometry, upsert_geometry

TENANT = "tenant_a"
POLY = {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}
POINT = {"type": "Point", "coordinates": [-3.7, 40.4]}


def _sync(fn):
    @functools.wraps(fn)
    def wrapper():
        asyncio.run(fn())

    return wrapper


class FakeConn:
    def __init__(self, log):
        self.log = log

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self_):
                conn.log.append("tx-enter")

            async def __aexit__(self_, *exc):
                conn.log.append("tx-exit")
                return False

        return _Tx()

    async def execute(self, sql, *args):
        if "set_config('app.current_tenant'" in sql:
            self.log.append(("set_config", args[0]))
        else:
            self.log.append(("sql", sql, args))
        return "DELETE 1" if sql.lstrip().upper().startswith("DELETE") else "INSERT 0 1"


class FakePool:
    def __init__(self):
        self.log = []

    def acquire(self):
        pool = self

        class _Acq:
            async def __aenter__(self_):
                return FakeConn(pool.log)

            async def __aexit__(self_, *exc):
                return False

        return _Acq()

    @property
    def sqls(self):
        return [e for e in self.log if isinstance(e, tuple) and e[0] == "sql"]


def _entity(etype, geo, urn="urn:ngsi-ld:AgriParcel:tenant_a:p1"):
    ent = {"id": urn, "type": etype}
    if geo is not None:
        ent["location"] = {"type": "GeoProperty", "value": geo}
    return ent


@_sync
async def test_polygon_upserted_with_type():
    pool = FakePool()
    assert await upsert_geometry(pool, TENANT, _entity("AgriParcel", POLY)) is True
    assert pool.log[0] == "tx-enter"
    assert pool.log[1] == ("set_config", TENANT)
    (_, sql, args), = pool.sqls
    assert "ST_GeomFromGeoJSON" in sql and "::geography" in sql
    assert "ON CONFLICT (tenant_id, entity_urn) DO UPDATE" in sql
    assert args[0] == TENANT
    assert args[1] == "urn:ngsi-ld:AgriParcel:tenant_a:p1"
    assert args[2] == "AgriParcel"
    assert json.loads(args[3]) == POLY


@_sync
async def test_point_upserted():
    pool = FakePool()
    ent = _entity("AgriPlant", POINT, "urn:ngsi-ld:AgriPlant:tenant_a:x")
    assert await upsert_geometry(pool, TENANT, ent) is True
    assert json.loads(pool.sqls[0][2][3]) == POINT


@_sync
async def test_entity_without_location_ignored():
    pool = FakePool()
    assert await upsert_geometry(pool, TENANT, _entity("AgriParcel", None)) is False
    assert pool.log == []


@_sync
async def test_invalid_geometry_skipped_not_raised():
    pool = FakePool()
    for bad in ({"type": "Circle", "coordinates": [0, 0]}, {"type": "Point"}, "x", None,
                {"type": "Polygon", "coordinates": []}):
        ent = _entity("AgriParcel", bad)
        assert await upsert_geometry(pool, TENANT, ent) is False
    assert pool.log == []


@_sync
async def test_missing_tenant_or_urn_skipped():
    pool = FakePool()
    assert await upsert_geometry(pool, "", _entity("AgriParcel", POLY)) is False
    assert await upsert_geometry(pool, TENANT, {"type": "AgriParcel", "location": {"value": POLY}}) is False
    assert pool.log == []


@_sync
async def test_delete_geometry_scoped_to_tenant():
    pool = FakePool()
    assert await delete_geometry(pool, TENANT, "urn:x") is True
    assert pool.log[1] == ("set_config", TENANT)
    (_, sql, args), = pool.sqls
    assert sql.lstrip().upper().startswith("DELETE") and "tenant_id" in sql
    assert args == (TENANT, "urn:x")


# ---- route + auth --------------------------------------------------------

def _client(pool=None):
    app = FastAPI()
    app.include_router(nh.router)
    nh._geometry_pool = pool
    return TestClient(app)


def test_unauthenticated_notify_rejected(monkeypatch):
    monkeypatch.setenv("NOTIFY_REQUIRE_INTERNAL_SECRET", "true")
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "s3cret")
    resp = _client(FakePool()).post("/notify/geometry", json={"data": []})
    assert resp.status_code == 401


def test_geometry_notify_upserts_with_tenant_header(monkeypatch):
    monkeypatch.delenv("NOTIFY_REQUIRE_INTERNAL_SECRET", raising=False)
    pool = FakePool()
    body = {"data": [_entity("AgriParcel", POLY), _entity("AgriParcelZone", None)]}
    resp = _client(pool).post("/notify/geometry", json=body, headers={"NGSILD-Tenant": TENANT})
    assert resp.status_code == 204
    assert len(pool.sqls) == 1 and pool.sqls[0][2][0] == TENANT


def test_geometry_notify_fiware_service_header(monkeypatch):
    monkeypatch.delenv("NOTIFY_REQUIRE_INTERNAL_SECRET", raising=False)
    pool = FakePool()
    resp = _client(pool).post(
        "/notify/geometry", json={"data": [_entity("AgriParcel", POLY)]},
        headers={"Fiware-Service": TENANT},
    )
    assert resp.status_code == 204
    assert pool.sqls[0][2][0] == TENANT


def test_geometry_notify_malformed_body_400(monkeypatch):
    monkeypatch.delenv("NOTIFY_REQUIRE_INTERNAL_SECRET", raising=False)
    resp = _client(FakePool()).post("/notify/geometry", content=b"not json",
                                    headers={"NGSILD-Tenant": TENANT})
    assert resp.status_code == 400


# ---- subscription --------------------------------------------------------

def test_subscription_declared():
    subs = [s for s in sm.SUBSCRIPTIONS
            if s["notification"]["endpoint"]["uri"].endswith("/notify/geometry")]
    assert len(subs) == 1
    sub = subs[0]
    assert sub["watchedAttributes"] == ["location"]
    assert sub["notification"]["format"] == "normalized"
    assert "throttling" not in sub
    types = {e["type"] for e in sub["entities"]}
    assert types == set(sm.GEOMETRY_ENTITY_TYPES)
    assert sm._subscription_id(sub) not in {
        sm._subscription_id(s) for s in sm.SUBSCRIPTIONS if s is not sub
    }
