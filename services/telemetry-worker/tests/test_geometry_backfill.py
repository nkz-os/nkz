"""Startup backfill of observation_geometry_cache from Orion (pre-existing features)."""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

import pytest
import requests
from requests.structures import CaseInsensitiveDict

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import telemetry_worker.geometry_backfill as gb
import telemetry_worker.subscription_manager as sm

TENANT = "tenant_a"
POLY = {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}
STARTED = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)


def _parcel(n, location=True, etype="AgriParcel"):
    ent = {"id": f"urn:ngsi-ld:{etype}:{TENANT}:p{n}", "type": etype}
    if location:
        ent["location"] = {"type": "GeoProperty", "value": POLY}
    return ent


class _Resp:
    def __init__(self, status, body, count=None):
        self.status_code = status
        self._body = body
        self.headers = CaseInsensitiveDict()
        if count is not None:
            self.headers["NGSILD-Results-Count"] = str(count)

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeOrion:
    """Serves GET /ngsi-ld/v1/entities pages by type/offset/limit, with optional faults."""

    def __init__(self, by_type, *, fail_at_offset=None, status=500, count_delta=0,
                 no_count=False, repeat_page=False):
        self.by_type = by_type
        self.fail_at_offset = fail_at_offset
        self.status = status
        self.count_delta = count_delta
        self.no_count = no_count
        self.repeat_page = repeat_page
        self.calls = []

    def __call__(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "params": dict(params)})
        if self.fail_at_offset is not None and params["offset"] == self.fail_at_offset:
            if self.status is None:
                raise requests.ConnectionError("broker down")
            return _Resp(self.status, {"detail": "boom"})
        ents = self.by_type.get(params["type"], [])
        off = 0 if self.repeat_page else params["offset"]
        page = ents[off: off + params["limit"]]
        count = None if self.no_count else len(ents) + self.count_delta
        return _Resp(200, page, count)


class FakeStore:
    """In-memory stand-in for the geometry_cache functions (keyed like the table)."""

    def __init__(self, rows=None, fail_upsert=()):
        # (tenant, urn) -> (entity_type, updated_at)
        self.rows = dict(rows or {})
        self.fail_upsert = set(fail_upsert)
        self.upserts = []
        self.deletes = []

    async def upsert(self, pool, tenant_id, entity):
        if entity["id"] in self.fail_upsert:
            raise RuntimeError("db down")
        if not entity.get("location"):
            return False
        self.upserts.append((tenant_id, entity["id"]))
        self.rows[(tenant_id, entity["id"])] = (entity["type"], STARTED.replace(hour=11))
        return True

    async def delete(self, pool, tenant_id, urn):
        self.deletes.append((tenant_id, urn))
        return self.rows.pop((tenant_id, urn), None) is not None

    async def cached(self, pool, tenant_id, entity_type, before):
        return {u for (t, u), (et, ts) in self.rows.items()
                if t == tenant_id and et == entity_type and ts < before}

    async def now(self, pool):
        return STARTED


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setattr(gb, "upsert_geometry", s.upsert)
    monkeypatch.setattr(gb, "delete_geometry", s.delete)
    monkeypatch.setattr(gb, "cached_urns", s.cached)
    monkeypatch.setattr(gb, "db_now", s.now)
    return s


def _lister(orion, page_size=2):
    def lister(tenant_id, entity_type):
        return gb.list_entities(tenant_id, entity_type, http_get=orion, page_size=page_size)
    return lister


def _run(store, orion, types=("AgriParcel",), page_size=2):
    return asyncio.run(gb.backfill_tenant(
        None, TENANT, lister=_lister(orion, page_size), types=types))


OLD = STARTED.replace(hour=9)


# ---- listing ---------------------------------------------------------------

def test_listing_pages_until_a_short_page():
    orion = FakeOrion({"AgriParcel": [_parcel(i) for i in range(5)]})
    listing = gb.list_entities(TENANT, "AgriParcel", http_get=orion, page_size=2)
    assert [e["id"] for e in listing.entities] == [_parcel(i)["id"] for i in range(5)]
    assert listing.complete
    assert [c["params"]["offset"] for c in orion.calls] == [0, 2, 4]
    first = orion.calls[0]
    assert first["url"].endswith("/ngsi-ld/v1/entities")
    assert first["params"]["type"] == "AgriParcel"
    assert first["params"]["count"] == "true"
    assert first["headers"] == sm._make_headers(TENANT)  # same headers as the subscriptions


def test_listing_exact_multiple_of_page_size_needs_an_empty_page():
    orion = FakeOrion({"AgriParcel": [_parcel(i) for i in range(4)]})
    listing = gb.list_entities(TENANT, "AgriParcel", http_get=orion, page_size=2)
    assert len(listing.entities) == 4 and listing.complete
    assert [c["params"]["offset"] for c in orion.calls] == [0, 2, 4]


@pytest.mark.parametrize("status", [500, 404, 401, None])
def test_listing_error_raises(status):
    orion = FakeOrion({"AgriParcel": [_parcel(i) for i in range(5)]},
                      fail_at_offset=2, status=status)
    with pytest.raises(gb.ListingFailed):
        gb.list_entities(TENANT, "AgriParcel", http_get=orion, page_size=2)


def test_listing_non_list_body_raises():
    def get(url, headers=None, params=None, timeout=None):
        return _Resp(200, {"not": "a list"}, 0)
    with pytest.raises(gb.ListingFailed):
        gb.list_entities(TENANT, "AgriParcel", http_get=get, page_size=2)


def test_listing_count_mismatch_is_not_complete():
    orion = FakeOrion({"AgriParcel": [_parcel(i) for i in range(3)]}, count_delta=1)
    assert not gb.list_entities(TENANT, "AgriParcel", http_get=orion, page_size=2).complete


def test_listing_without_count_is_not_complete():
    orion = FakeOrion({"AgriParcel": [_parcel(i) for i in range(3)]}, no_count=True)
    assert not gb.list_entities(TENANT, "AgriParcel", http_get=orion, page_size=2).complete


def test_listing_that_never_ends_raises():
    orion = FakeOrion({"AgriParcel": [_parcel(i) for i in range(2)]}, repeat_page=True)
    with pytest.raises(gb.ListingFailed):
        gb.list_entities(TENANT, "AgriParcel", http_get=orion, page_size=2, max_pages=5)


# ---- backfill --------------------------------------------------------------

def test_backfill_upserts_located_and_skips_unlocated(store):
    orion = FakeOrion({"AgriParcel": [_parcel(0), _parcel(1), _parcel(2, location=False)]})
    summary = _run(store, orion)
    assert summary.upserted == 2
    assert summary.skipped == 1
    assert summary.deleted == 0
    assert summary.incomplete_types == []


def test_backfill_invalid_geometry_is_skipped_by_the_real_helper(monkeypatch, store):
    from telemetry_worker import geometry_cache

    monkeypatch.setattr(gb, "upsert_geometry", geometry_cache.upsert_geometry)
    bad = _parcel(0)
    bad["location"] = {"type": "GeoProperty", "value": {"type": "Polygon", "coordinates": []}}
    summary = _run(store, FakeOrion({"AgriParcel": [bad]}))
    assert summary.skipped == 1 and summary.upserted == 0  # no pool touched


def test_backfill_deletes_rows_of_entities_gone_after_full_listing(store):
    gone = _parcel(9)["id"]
    store.rows[(TENANT, gone)] = ("AgriParcel", OLD)
    store.rows[(TENANT, "urn:ngsi-ld:AgriPlant:x")] = ("AgriPlant", OLD)  # other type
    store.rows[("tenant_b", "urn:ngsi-ld:AgriParcel:tenant_b:p1")] = ("AgriParcel", OLD)
    summary = _run(store, FakeOrion({"AgriParcel": [_parcel(0), _parcel(1), _parcel(2)]}))
    assert summary.deleted == 1
    assert store.deletes == [(TENANT, gone)]
    assert (TENANT, "urn:ngsi-ld:AgriPlant:x") in store.rows
    assert ("tenant_b", "urn:ngsi-ld:AgriParcel:tenant_b:p1") in store.rows


def test_backfill_keeps_rows_written_after_the_listing_started(store):
    fresh = "urn:ngsi-ld:AgriParcel:tenant_a:new"  # created by a live notification
    store.rows[(TENANT, fresh)] = ("AgriParcel", STARTED.replace(hour=10, minute=1))
    summary = _run(store, FakeOrion({"AgriParcel": [_parcel(0)]}))
    assert summary.deleted == 0
    assert (TENANT, fresh) in store.rows


def test_backfill_keeps_listed_entity_without_location(store):
    store.rows[(TENANT, _parcel(0)["id"])] = ("AgriParcel", OLD)
    summary = _run(store, FakeOrion({"AgriParcel": [_parcel(0, location=False)]}))
    assert summary.deleted == 0 and summary.skipped == 1


@pytest.mark.parametrize("status", [500, 404, None])
def test_backfill_orion_error_leaves_cache_untouched(store, status):
    gone = _parcel(9)["id"]
    store.rows[(TENANT, gone)] = ("AgriParcel", OLD)
    before = dict(store.rows)
    orion = FakeOrion({"AgriParcel": [_parcel(i) for i in range(5)]},
                      fail_at_offset=2, status=status)
    summary = _run(store, orion)
    assert store.rows == before
    assert store.upserts == [] and store.deletes == []
    assert summary.incomplete_types == ["AgriParcel"]


def test_backfill_partial_listing_upserts_but_never_deletes(store):
    gone = _parcel(9)["id"]
    store.rows[(TENANT, gone)] = ("AgriParcel", OLD)
    summary = _run(store, FakeOrion({"AgriParcel": [_parcel(0), _parcel(1)]}, count_delta=1))
    assert summary.upserted == 2
    assert store.deletes == []
    assert summary.incomplete_types == ["AgriParcel"]


def test_backfill_one_type_failing_does_not_stop_the_others(store):
    class Mixed(FakeOrion):
        def __call__(self, url, headers=None, params=None, timeout=None):
            if params["type"] == "AgriParcelZone":
                return _Resp(503, {})
            return super().__call__(url, headers=headers, params=params, timeout=timeout)

    orion = Mixed({"AgriParcel": [_parcel(0)], "AgriPlant": [_parcel(1, etype="AgriPlant")]})
    summary = _run(store, orion, types=("AgriParcel", "AgriParcelZone", "AgriPlant"))
    assert summary.upserted == 2
    assert summary.incomplete_types == ["AgriParcelZone"]


def test_backfill_upsert_error_is_counted_not_raised(store):
    store.fail_upsert = {_parcel(1)["id"]}
    summary = _run(store, FakeOrion({"AgriParcel": [_parcel(0), _parcel(1)]}))
    assert summary.upserted == 1 and summary.failed == 1


def test_backfill_rerun_is_harmless(store):
    orion = FakeOrion({"AgriParcel": [_parcel(0), _parcel(1)]})
    first = _run(store, orion)
    rows = dict(store.rows)
    second = _run(store, orion)
    assert (first.upserted, first.deleted) == (second.upserted, second.deleted) == (2, 0)
    assert set(store.rows) == set(rows)


def test_run_covers_every_managed_tenant_and_logs_a_summary(store, caplog):
    orion = FakeOrion({"AgriParcel": [_parcel(0)]})
    caplog.set_level(logging.INFO, logger=gb.logger.name)
    summaries = asyncio.run(gb.run_geometry_backfill(
        None, tenants_fn=lambda: [TENANT, "tenant_b"], lister=_lister(orion),
        types=("AgriParcel",)))
    assert [s.tenant_id for s in summaries] == [gb.orion_tenant(TENANT), gb.orion_tenant("tenant_b")]
    lines = [r.getMessage() for r in caplog.records if "geometry backfill" in r.getMessage()]
    assert any(f"tenant_id={gb.orion_tenant(TENANT)}" in m and "upserted=1" in m and "deleted=0" in m
               and "skipped=0" in m for m in lines)


def test_run_never_raises(store):
    def boom():
        raise RuntimeError("db down")
    assert asyncio.run(gb.run_geometry_backfill(None, tenants_fn=boom)) == []


def test_default_types_are_the_geometry_subscription_types():
    assert gb.GEOMETRY_ENTITY_TYPES == sm.GEOMETRY_ENTITY_TYPES
    sub = next(s for s in sm.SUBSCRIPTIONS
               if s["notification"]["endpoint"]["uri"].endswith("/notify/geometry"))
    assert tuple(e["type"] for e in sub["entities"]) == sm.GEOMETRY_ENTITY_TYPES


def test_worker_starts_the_backfill_in_the_background():
    with open(os.path.join(_SVC_DIR, "app.py"), encoding="utf-8") as f:
        src = f.read()
    start = src.index("asyncio.create_task(run_geometry_backfill(sink._pool))")
    assert src.index("ensure_subscriptions_for_all_tenants)") < start < src.index("yield")
    assert "backfill_task.cancel()" in src


def test_rows_are_keyed_by_the_tenant_orion_notifies_with(store):
    """Live notifications key the cache by the NGSILD-Tenant header Orion echoes, which is
    the normalized id: the backfill must write the same key, not the raw tenants row."""
    seen = []

    def lister(tenant_id, entity_type):
        seen.append(tenant_id)
        return gb.Listing(entities=[_parcel(0)], complete=True)

    raw = "Tenant_A"
    expected = sm._make_headers(raw)["NGSILD-Tenant"]
    assert expected != raw  # the case this guards
    summaries = asyncio.run(gb.run_geometry_backfill(
        None, tenants_fn=lambda: [raw], lister=lister, types=("AgriParcel",)))
    assert [s.tenant_id for s in summaries] == [expected]
    assert seen == [expected]
    assert store.upserts == [(expected, _parcel(0)["id"])]
