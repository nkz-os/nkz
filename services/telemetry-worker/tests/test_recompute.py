"""RecomputeLoop unit tests with an in-memory fake asyncpg pool (no DB).

The fake dispatches on the `-- recompute:<name>` tag each SQL statement carries,
keeps runs and measurement rows in memory and applies the UPDATE batches, so the
loop's control flow (claim -> plan -> decompress -> update -> audit) and the
planner's idempotency are checked without PostgreSQL. Real SQL behaviour (RLS,
SKIP LOCKED, compressed chunks) is covered by services/tests/test_recompute_pg.py.
"""

import asyncio
import functools
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from asyncpg import Range

from telemetry_worker.observations import Deployment, PropertyDef
from telemetry_worker import recompute as rc
from telemetry_worker.recompute import (
    RecomputeLoop,
    StoredRow,
    plan_row,
    recompute_interval_from_env,
)


def _sync(fn):
    @functools.wraps(fn)
    def wrapper():
        asyncio.run(fn())

    return wrapper


TENANT = "tenant_a"
URN = "urn:ngsi-ld:Device:tenant_a:d1"
DEP_ID = "11111111-1111-1111-1111-111111111111"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
PROP = PropertyDef("airTemperature", "instant", -50.0, 70.0)


def _dep(height=2.0, mobility="fixed", dep_id=DEP_ID, medium="ambientAir", location=(-3.7, 40.4)):
    return Deployment(
        id=dep_id,
        device_urn=URN,
        observes_feature="urn:ngsi-ld:AgriParcel:p1",
        observation_context={"medium": medium, "heightAboveGround": height},
        channel_overrides={},
        mobility=mobility,
        location=location,
    )


def _row(i=0, **kw):
    base = dict(
        observed_at=T0 + timedelta(hours=i),
        source_event_id=100 + i,
        attribute_name="temperature",
        value=21.5,
        observed_property=None,
        feature_of_interest=None,
        medium=None,
        depth_from_cm=None,
        depth_to_cm=None,
        height_m=None,
        organ=None,
        context_ref=None,
        deployment_id=None,
        source_kind=None,
        quality_flag=None,
        lon=None,
        lat=None,
        raw_observed_property="airTemperature",
        raw_profile_code=None,
    )
    base.update(kw)
    return StoredRow(**base)


# ---------------------------------------------------------------------------
# Fake pool
# ---------------------------------------------------------------------------


class FakeDB:
    def __init__(self, tenants=(TENANT,)):
        self.tenants = list(tenants)
        self.runs = {}  # id -> dict
        self.rows = {}  # (tenant, key) -> StoredRow
        self.deployments = {}  # (tenant, urn) -> [(Range, Deployment)]
        self.compressed = []  # chunk names reported as compressed
        self.decompressed = []
        self.updates = 0
        self.fail_rows_for = set()  # run ids whose row fetch raises
        self.set_configs = []
        self.tenant_queries = 0

    def add_run(self, run_id, tenant=TENANT, range_from=T0, range_to=None, age_s=600):
        self.runs[run_id] = dict(
            id=run_id, tenant_id=tenant, device_urn=URN, deployment_id=DEP_ID,
            range_from=range_from, range_to=range_to, status="pending",
            created_age_s=age_s, rows_changed=None, error=None,
        )

    def add_row(self, row, tenant=TENANT):
        self.rows[(tenant, (row.observed_at, row.source_event_id, row.attribute_name))] = row

    def pool(self):
        return FakePool(self)


class FakeConn:
    def __init__(self, db):
        self.db = db
        self.tenant = None
        self.current_run = None

    def transaction(self):
        class _Tx:
            async def __aenter__(self_):
                return None

            async def __aexit__(self_, *exc):
                return False

        return _Tx()

    @staticmethod
    def _tag(sql):
        for line in sql.splitlines():
            line = line.strip()
            if line.startswith("-- recompute:"):
                return line.split(":", 1)[1].strip()
        raise AssertionError(f"untagged SQL: {sql[:80]}")

    async def execute(self, sql, *args, timeout=None):
        tag = self._tag(sql)
        db = self.db
        if tag == "set_tenant":
            self.tenant = args[0]
            db.set_configs.append(args[0])
            return "SELECT 1"
        if tag == "mark_running":
            assert self.tenant == args[0], "run status written without tenant context"
            db.runs[str(args[1])]["status"] = "running"
            return "UPDATE 1"
        if tag in ("mark_done", "mark_failed"):
            assert self.tenant == args[0], "run status written without tenant context"
            run = db.runs[str(args[1])]
            run["status"] = "done" if tag == "mark_done" else "failed"
            run["rows_changed"] = args[2]
            if tag == "mark_failed":
                run["error"] = args[3]
            return "UPDATE 1"
        if tag == "decompress":
            db.decompressed.append(args[0])
            return "SELECT 1"
        if tag == "update_rows":
            tenant, lo, hi = args[0], args[1], args[2]
            flat = args[3:]
            n = len(rc.UPDATE_COLUMNS)
            assert len(flat) % n == 0 and len(flat) // n <= rc.UPDATE_BATCH_ROWS
            changed = 0
            for i in range(0, len(flat), n):
                v = dict(zip(rc.UPDATE_COLUMNS, flat[i:i + n]))
                key = (tenant, (v["observed_at"], v["source_event_id"], v["attribute_name"]))
                assert lo <= v["observed_at"] <= hi
                cur = db.rows[key]
                new = cur._replace(
                    deployment_id=v["deployment_id"],
                    feature_of_interest=v["feature_of_interest"],
                    medium=v["medium"],
                    depth_from_cm=v["depth_from_cm"],
                    depth_to_cm=v["depth_to_cm"],
                    height_m=v["height_m"],
                    organ=v["organ"],
                    context_ref=v["context_ref"],
                    lon=cur.lon if v["keep_location"] else v["lon"],
                    lat=cur.lat if v["keep_location"] else v["lat"],
                    observed_property=cur.observed_property or v["observed_property"],
                    quality_flag=cur.quality_flag or v["quality_flag"],
                    source_kind=cur.source_kind or v["source_kind"],
                )
                if new != cur:
                    changed += 1
                db.rows[key] = new
            db.updates += changed
            return f"UPDATE {changed}"
        raise AssertionError(f"unexpected execute {tag}")

    async def fetch(self, sql, *args, timeout=None):
        tag = self._tag(sql)
        db = self.db
        if tag == "tenants":
            db.tenant_queries += 1
            return [{"tenant_id": t} for t in db.tenants]
        if tag == "deployments":
            assert self.tenant == args[0]
            out = []
            for rng, dep in db.deployments.get((args[0], args[1]), []):
                out.append({
                    "id": dep.id, "device_urn": dep.device_urn, "valid": rng,
                    "observes_feature": dep.observes_feature,
                    "observation_context": dep.observation_context,
                    "channel_overrides": dep.channel_overrides,
                    "mobility": dep.mobility,
                    "lon": dep.location[0] if dep.location else None,
                    "lat": dep.location[1] if dep.location else None,
                })
            return out
        if tag == "rows":
            tenant, device_id, lo, hi, after_at, after_id, after_attr, limit = args
            if self.current_run in db.fail_rows_for:
                raise RuntimeError("boom while reading rows")
            assert device_id == URN.rsplit(":", 1)[-1]
            keys = sorted(k for (t, k) in db.rows if t == tenant)
            out = []
            for k in keys:
                if k[0] < lo or (hi is not None and k[0] >= hi):
                    continue
                if after_at is not None and k <= (after_at, after_id, after_attr):
                    continue
                out.append(db.rows[(tenant, k)]._asdict())
                if len(out) >= limit:
                    break
            return out
        if tag == "chunks":
            return [{"chunk": c} for c in db.compressed]
        raise AssertionError(f"unexpected fetch {tag}")

    async def fetchrow(self, sql, *args, timeout=None):
        tag = self._tag(sql)
        db = self.db
        if tag == "claim":
            assert self.tenant == args[0], "claim without tenant context"
            pending = [
                r for r in db.runs.values()
                if r["tenant_id"] == args[0] and r["status"] == "pending"
                and r["created_age_s"] > 90
            ]
            if not pending:
                return None
            r = pending[0]
            self.db._last_claimed = r["id"]
            return {k: r[k] for k in ("id", "tenant_id", "device_urn", "deployment_id",
                                      "range_from", "range_to")}
        raise AssertionError(f"unexpected fetchrow {tag}")


class FakePool:
    def __init__(self, db):
        self.db = db

    def acquire(self):
        db = self.db

        class _Acq:
            async def __aenter__(self_):
                conn = FakeConn(db)
                conn.current_run = getattr(db, "_processing", None)
                return conn

            async def __aexit__(self_, *exc):
                return False

        return _Acq()


class FakeLookup:
    def __init__(self, props=None, mdefs=None):
        self.props = props if props is not None else {"airTemperature": PROP}
        self.mdefs = mdefs or {}
        self.invalidated = []

    def invalidate(self, tenant_id):
        self.invalidated.append(tenant_id)

    async def property(self, tenant_id, name):
        return self.props.get(name)

    async def measurement_def(self, tenant_id, profile_code, attr):
        return self.mdefs.get((profile_code, attr))


def _loop(db, lookup=None, **kw):
    loop = RecomputeLoop(db.pool(), lookup or FakeLookup(), **kw)
    # Let the fake know which run is processed (to inject a failure on its row fetch).
    orig = loop._process_run

    async def tracked(run, progress):
        db._processing = str(run["id"])
        try:
            return await orig(run, progress)
        finally:
            db._processing = None

    loop._process_run = tracked
    return loop


def _with_deployment(db, dep=None, lower=T0 - timedelta(days=1), upper=None):
    dep = dep or _dep()
    db.deployments.setdefault((TENANT, URN), []).append((Range(lower, upper), dep))
    return dep


# ---------------------------------------------------------------------------
# Required unit tests (brief)
# ---------------------------------------------------------------------------


@_sync
async def test_run_marks_done_with_count():
    db = FakeDB()
    _with_deployment(db)
    for i in range(3):
        db.add_row(_row(i))
    db.add_run("r1")
    lookup = FakeLookup()

    processed = await _loop(db, lookup).run_once()

    assert processed == 1
    run = db.runs["r1"]
    assert run["status"] == "done" and run["rows_changed"] == 3 and run["error"] is None
    assert lookup.invalidated == [TENANT]
    for row in db.rows.values():
        assert row.deployment_id == DEP_ID
        assert row.height_m == 2.0
        assert row.observed_property == "airTemperature"
        assert row.quality_flag == "valid"
        assert row.source_kind == "device"
        assert (row.lon, row.lat) == (-3.7, 40.4)


@_sync
async def test_failure_marks_failed_and_continues():
    db = FakeDB()
    _with_deployment(db)
    db.add_row(_row(0))
    db.add_run("bad", age_s=900)
    db.add_run("good", age_s=600)
    db.fail_rows_for.add("bad")

    processed = await _loop(db).run_once()

    assert processed == 2
    assert db.runs["bad"]["status"] == "failed"
    assert "boom while reading rows" in db.runs["bad"]["error"]
    assert len(db.runs["bad"]["error"]) <= rc.ERROR_MAX_CHARS
    assert db.runs["good"]["status"] == "done"
    assert db.runs["good"]["rows_changed"] == 1


@_sync
async def test_idempotent_second_run_changes_zero():
    db = FakeDB()
    _with_deployment(db)
    for i in range(3):
        db.add_row(_row(i))
    db.compressed = ["_timescaledb_internal._hyper_1_1_chunk"]
    db.add_run("r1")
    loop = _loop(db)
    await loop.run_once()
    assert db.runs["r1"]["rows_changed"] == 3
    assert db.decompressed == ["_timescaledb_internal._hyper_1_1_chunk"]

    db.add_run("r2")
    db.decompressed.clear()
    before = dict(db.rows)
    assert await loop.run_once() == 1
    assert db.runs["r2"]["status"] == "done" and db.runs["r2"]["rows_changed"] == 0
    assert db.rows == before
    assert db.decompressed == []  # nothing differs -> no chunk is touched


# ---------------------------------------------------------------------------
# Claim, tenants, loop
# ---------------------------------------------------------------------------


@_sync
async def test_young_run_not_claimed():
    db = FakeDB()
    db.add_run("young", age_s=30)
    assert await _loop(db).run_once() == 0
    assert db.runs["young"]["status"] == "pending"


@_sync
async def test_tenants_cached_for_ten_minutes():
    db = FakeDB()
    now = [1000.0]
    loop = _loop(db, clock=lambda: now[0])
    await loop.run_once()
    await loop.run_once()
    assert db.tenant_queries == 1
    now[0] += rc.TENANTS_TTL_S + 1
    await loop.run_once()
    assert db.tenant_queries == 2


@_sync
async def test_run_stops_cleanly_on_cancel():
    db = FakeDB()
    loop = _loop(db, interval_s=0.01)
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert db.tenant_queries >= 1


@_sync
async def test_run_survives_run_once_error():
    db = FakeDB()
    loop = _loop(db, interval_s=0.01)
    calls = []

    async def boom():
        calls.append(1)
        raise RuntimeError("db down")

    loop.run_once = boom
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) >= 2


def test_interval_from_env(monkeypatch):
    monkeypatch.delenv("OBSERVATION_RECOMPUTE_INTERVAL_S", raising=False)
    assert recompute_interval_from_env() == 30.0
    monkeypatch.setenv("OBSERVATION_RECOMPUTE_INTERVAL_S", "5")
    assert recompute_interval_from_env() == 5.0
    for bad in ("0", "-1", "abc", "nan"):
        monkeypatch.setenv("OBSERVATION_RECOMPUTE_INTERVAL_S", bad)
        with pytest.raises(ValueError):
            recompute_interval_from_env()


# ---------------------------------------------------------------------------
# Planner rules
# ---------------------------------------------------------------------------


def _plan(row, dep=_dep(), prev=None, prop=PROP, mdef=None):
    return plan_row(row, dep, prev, prop, mdef, URN)


def test_plan_never_changes_set_quality_value_or_property():
    row = _row(quality_flag="suspect", observed_property="airTemperature", value=999.0)
    u = _plan(row)
    assert u.quality_flag == "suspect"
    assert u.observed_property == "airTemperature"
    assert u.deployment_id == DEP_ID


def test_plan_quality_uses_counter_max_for_cumulative():
    rain = PropertyDef("precipitation", "cumulative", 0.0, 10.0)
    row = _row(value=50.0, raw_observed_property="precipitation", raw_profile_code="P")
    assert _plan(row, prop=rain, mdef={"counterMax": 100}).quality_flag == "valid"
    assert _plan(row, prop=rain, mdef={"counterMax": 40}).quality_flag == "out_of_range"


def test_plan_no_deployment_nulls_context_keeps_property_and_quality():
    row = _row(
        deployment_id=DEP_ID, observed_property="airTemperature", quality_flag="valid",
        source_kind="device", height_m=1.5, medium="ambientAir", lon=-3.7, lat=40.4,
        feature_of_interest="urn:ngsi-ld:AgriParcel:p1",
    )
    u = _plan(row, dep=None, prev=_dep())
    assert u.deployment_id is None and u.height_m is None and u.medium is None
    assert u.feature_of_interest is None
    assert u.keep_location is False and u.lon is None
    assert (u.observed_property, u.quality_flag, u.source_kind) == ("airTemperature", "valid", "device")


def test_plan_no_deployment_keeps_location_of_mobile_reading():
    row = _row(deployment_id=DEP_ID, observed_property="airTemperature", lon=1.0, lat=2.0)
    u = _plan(row, dep=None, prev=_dep(mobility="mobile"))
    assert u.keep_location is True


def test_plan_mobile_deployment_keeps_reading_location():
    row = _row(lon=1.0, lat=2.0)
    u = _plan(row, dep=_dep(mobility="mobile"))
    assert u.keep_location is True
    assert u.deployment_id == DEP_ID


def test_plan_unknown_raw_property_leaves_row_untouched():
    row = _row(raw_observed_property="notInCatalogue")
    assert _plan(row, prop=None) is None


def test_plan_without_any_property_gets_no_context():
    assert _plan(_row(raw_observed_property=None), prop=None) is None


def test_plan_existing_property_reresolved_even_if_catalogue_misses_it():
    row = _row(observed_property="legacyProp", raw_observed_property=None)
    u = _plan(row, prop=None)
    assert u.deployment_id == DEP_ID and u.quality_flag is None


def test_plan_off_vocabulary_medium_stored_as_null():
    u = _plan(_row(), dep=_dep(medium="air"))
    assert u.medium is None
    assert u.height_m == 2.0


def test_plan_returns_none_when_nothing_differs():
    row = _row(
        deployment_id=DEP_ID, observed_property="airTemperature", quality_flag="valid",
        source_kind="device", height_m=2.0, medium="ambientAir", lon=-3.7, lat=40.4,
        feature_of_interest="urn:ngsi-ld:AgriParcel:p1",
    )
    assert _plan(row) is None
