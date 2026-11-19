"""RecomputeLoop unit tests with an in-memory fake asyncpg pool (no DB).

The fake dispatches on the `-- recompute:<name>` tag each SQL statement carries,
keeps runs, chunks and measurement rows in memory and applies the UPDATE batches,
so the loop's control flow (claim -> plan -> decompress -> update -> audit, retries,
lease) and the planner's convergence are checked without PostgreSQL. Real SQL
behaviour (RLS, SKIP LOCKED, compressed chunks, the audit UPDATEs) is covered by
services/tests/test_recompute_pg.py.
"""

import asyncio
import functools
import os
import sys
from datetime import datetime, timedelta, timezone

import asyncpg
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
    def wrapper(*a, **kw):
        asyncio.run(fn(*a, **kw))

    return wrapper


TENANT = "tenant_a"
URN = "urn:ngsi-ld:Device:tenant_a:d1"
DEP_ID = "11111111-1111-1111-1111-111111111111"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
PROP = PropertyDef("airTemperature", "instant", -50.0, 70.0, "°C")
MDEF = {"unit": "°C"}
GPS = {"type": "Point", "coordinates": [1.25, 42.5]}


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
        raw_profile_code="P",
        raw_location=None,
    )
    base.update(kw)
    return StoredRow(**base)


COMPRESSED_ERR = 'cannot update/delete rows from chunk "_hyper_1_1_chunk" as it is compressed'


# ---------------------------------------------------------------------------
# Fake pool
# ---------------------------------------------------------------------------


class FakeDB:
    def __init__(self, tenants=(TENANT,)):
        self.tenants = list(tenants)
        self.runs = {}  # id -> dict
        self.rows = {}  # (tenant, key) -> StoredRow
        self.deployments = {}  # (tenant, urn) -> [(Range, Deployment)]
        self.chunks = []  # [name, start, end, is_compressed]
        self.decompressed = []
        self.lock_timeouts = 0
        self.recompress_once = False  # policy recompresses right after the first decompress
        self.fail_rows_for = set()  # run ids whose row fetch raises
        self.tenant_queries = 0
        self.block_rows = None  # asyncio.Event: row fetch waits on it (cancellation test)
        self.rows_started = None
        self.lease_renewals = 0

    def add_run(self, run_id, tenant=TENANT, range_from=T0, range_to=None, age_s=600,
                status="pending", started_age_s=None, attempts=0):
        self.runs[run_id] = dict(
            id=run_id, tenant_id=tenant, device_urn=URN, deployment_id=DEP_ID,
            range_from=range_from, range_to=range_to, status=status,
            created_age_s=age_s, started_age_s=started_age_s, attempts=attempts,
            rows_changed=None, error=None,
        )

    def add_row(self, row, tenant=TENANT):
        self.rows[(tenant, (row.observed_at, row.source_event_id, row.attribute_name))] = row

    def pool(self):
        return FakePool(self)

    def chunk_of(self, at):
        for ch in self.chunks:
            if ch[1] <= at < ch[2]:
                return ch
        return None


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

    def _run(self, args):
        assert self.tenant == args[0], "run status written without tenant context"
        return self.db.runs[str(args[1])]

    async def execute(self, sql, *args, timeout=None):
        tag = self._tag(sql)
        db = self.db
        if tag == "set_tenant":
            self.tenant = args[0]
            return "SELECT 1"
        if tag == "lock_timeout":
            db.lock_timeouts += 1
            return "SET"
        if tag == "renew_lease":
            self._run(args)["started_age_s"] = 0
            db.lease_renewals += 1
            return "UPDATE 1"
        if tag == "mark_done":
            run = self._run(args)
            run.update(status="done", rows_changed=(run["rows_changed"] or 0) + args[2], error=None)
            return "UPDATE 1"
        if tag == "mark_cancelled":
            run = self._run(args)
            run.update(status="pending", started_age_s=None,
                       rows_changed=(run["rows_changed"] or 0) + args[2])
            return "UPDATE 1"
        if tag == "mark_failed":
            run = self._run(args)
            run.update(status="failed", rows_changed=(run["rows_changed"] or 0) + args[2],
                       error=args[3])
            return "UPDATE 1"
        if tag == "decompress":
            assert db.lock_timeouts > 0, "decompress without lock_timeout"
            db.decompressed.append(args[0])
            for ch in db.chunks:
                if ch[0] == args[0]:
                    ch[3] = db.recompress_once  # the policy may recompress it at once
            db.recompress_once = False
            return "SELECT 1"
        if tag == "update_rows":
            tenant, lo, hi = args[0], args[1], args[2]
            flat = args[3:]
            n = len(rc.UPDATE_COLUMNS)
            assert len(flat) % n == 0 and len(flat) // n <= rc.UPDATE_BATCH_ROWS
            batch = [dict(zip(rc.UPDATE_COLUMNS, flat[i:i + n])) for i in range(0, len(flat), n)]
            # TimescaleDB 2.10.2 (measured): the UPDATE fails when its observed_at
            # window overlaps any compressed chunk, even one without matching rows.
            for ch in db.chunks:
                if ch[3] and ch[1] <= hi and ch[2] > lo:
                    raise asyncpg.exceptions.InternalServerError(COMPRESSED_ERR)
            changed = 0
            for v in batch:
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
                    lon=v["lon"],
                    lat=v["lat"],
                    observed_property=cur.observed_property or v["observed_property"],
                    quality_flag=cur.quality_flag or v["quality_flag"],
                    source_kind=cur.source_kind or v["source_kind"],
                )
                if new != cur:
                    changed += 1
                db.rows[key] = new
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
            assert timeout is not None, "row page fetched without an explicit timeout"
            tenant, device_id, lo, hi, after_at, after_id, after_attr, limit = args
            if after_at is not None:
                assert lo == after_at, "page lower bound must be the last page's observed_at"
            if self.current_run in db.fail_rows_for:
                raise RuntimeError("boom while reading rows")
            if db.block_rows is not None:
                db.rows_started.set()
                await db.block_rows.wait()
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
            lo, hi = args
            return [
                {"chunk": c[0], "range_start": c[1], "range_end": c[2]}
                for c in db.chunks if c[3] and c[1] <= hi and c[2] > lo
            ]
        raise AssertionError(f"unexpected fetch {tag}")

    async def fetchrow(self, sql, *args, timeout=None):
        tag = self._tag(sql)
        db = self.db
        if tag == "claim":
            assert self.tenant == args[0], "claim without tenant context"
            exclude = set(str(x) for x in args[1])
            eligible = [
                r for r in db.runs.values()
                if r["tenant_id"] == args[0] and str(r["id"]) not in exclude and (
                    (r["status"] == "pending" and r["created_age_s"] > 90)
                    or (r["status"] == "running" and (r["started_age_s"] or 0) > 900)
                )
            ]
            if not eligible:
                return None
            r = eligible[0]
            return {k: r[k] for k in ("id", "tenant_id", "device_urn", "deployment_id",
                                      "range_from", "range_to")}
        raise AssertionError(f"unexpected fetchrow {tag}")

    async def fetchval(self, sql, *args, timeout=None):
        tag = self._tag(sql)
        if tag == "mark_running":
            run = self._run(args)
            if run["status"] == "running":
                run["attempts"] += 1  # the previous holder's lease expired
            run.update(status="running", started_age_s=0)
            return run["attempts"]
        if tag == "mark_retry":
            run = self._run(args)
            run["attempts"] += 1
            run["rows_changed"] = (run["rows_changed"] or 0) + args[2]
            run["error"] = args[3]
            if run["attempts"] >= args[4]:
                run["status"] = "failed"
            else:
                run.update(status="pending", started_age_s=None)
            return run["status"]
        raise AssertionError(f"unexpected fetchval {tag}")


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
        self.mdefs = mdefs if mdefs is not None else {("P", "temperature"): MDEF}
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
    """A failing run goes back to pending (attempt counted); the next run still runs."""
    db = FakeDB()
    _with_deployment(db)
    db.add_row(_row(0))
    db.add_run("bad", age_s=900)
    db.add_run("good", age_s=600)
    db.fail_rows_for.add("bad")

    processed = await _loop(db).run_once()

    assert processed == 2  # "bad" is not retried within the same tick
    assert db.runs["bad"]["status"] == "pending" and db.runs["bad"]["attempts"] == 1
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
    db.chunks = [["_timescaledb_internal._hyper_1_1_chunk", T0, T0 + timedelta(days=7), True]]
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
# Location convergence (fix round 1, #1)
# ---------------------------------------------------------------------------


async def _two_runs(db):
    loop = _loop(db)
    db.add_run("first")
    await loop.run_once()
    db.add_run("second")
    await loop.run_once()
    return db.runs["first"]["rows_changed"], db.runs["second"]["rows_changed"]


@_sync
async def test_mobile_reading_outside_deployment_converges():
    """Run 1 clears the context and location; run 2 changes nothing."""
    db = FakeDB()
    _with_deployment(db, _dep(mobility="mobile"), upper=T0)  # ended before the reading
    db.add_row(_row(1, deployment_id=DEP_ID, observed_property="airTemperature",
                    quality_flag="valid", source_kind="device", medium="ambientAir",
                    height_m=2.0, lon=1.25, lat=42.5, raw_location=GPS))
    first, second = await _two_runs(db)
    (row,) = db.rows.values()
    assert (row.deployment_id, row.lon, row.lat) == (None, None, None)
    assert (first, second) == (1, 0)


@_sync
async def test_mobile_deployment_takes_raw_point_and_converges():
    """A fixed deployment's point on a row whose deployment is now mobile is replaced."""
    db = FakeDB()
    _with_deployment(db, _dep(mobility="mobile"))
    db.add_row(_row(1, deployment_id=DEP_ID, observed_property="airTemperature",
                    quality_flag="valid", source_kind="device", medium="ambientAir",
                    height_m=2.0, feature_of_interest="urn:ngsi-ld:AgriParcel:p1",
                    lon=-3.7, lat=40.4, raw_location=GPS))
    db.add_row(_row(2, raw_location=None))  # mobile without its own point: no location
    first, second = await _two_runs(db)
    rows = [db.rows[k] for k in sorted(db.rows)]
    assert (rows[0].lon, rows[0].lat) == (1.25, 42.5)
    assert (rows[1].lon, rows[1].lat, rows[1].deployment_id) == (None, None, DEP_ID)
    assert (first, second) == (2, 0)


# ---------------------------------------------------------------------------
# Liveness: retries, cancellation, lease (fix round 1, #2)
# ---------------------------------------------------------------------------


@_sync
async def test_failure_retried_then_failed_at_fifth_attempt_rows_accumulate(monkeypatch):
    db = FakeDB()
    db.add_run("r1")
    loop = _loop(db)
    orig = loop._process_run

    async def flaky(run, progress):
        progress.rows_changed += 2  # committed batches before the failure
        raise RuntimeError("transient")

    loop._process_run = flaky
    for attempt in range(1, rc.MAX_ATTEMPTS):
        assert await loop.run_once() == 1
        assert db.runs["r1"]["status"] == "pending" and db.runs["r1"]["attempts"] == attempt
    assert await loop.run_once() == 1
    run = db.runs["r1"]
    assert run["status"] == "failed" and run["attempts"] == rc.MAX_ATTEMPTS
    assert run["rows_changed"] == 2 * rc.MAX_ATTEMPTS
    assert "transient" in run["error"]
    assert await loop.run_once() == 0  # failed runs are never claimed again
    loop._process_run = orig


@_sync
async def test_cancel_mid_run_returns_run_to_pending_and_propagates():
    db = FakeDB()
    _with_deployment(db)
    db.add_row(_row(0))
    db.add_run("r1")
    db.block_rows, db.rows_started = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(_loop(db).run_once())
    await asyncio.wait_for(db.rows_started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    run = db.runs["r1"]
    assert run["status"] == "pending" and run["started_age_s"] is None
    assert run["attempts"] == 0 and run["rows_changed"] == 0


@_sync
async def test_stale_running_run_is_reclaimed():
    db = FakeDB()
    _with_deployment(db)
    db.add_row(_row(0))
    db.add_run("stale", status="running", started_age_s=1000)
    db.add_run("live", status="running", started_age_s=60)
    assert await _loop(db).run_once() == 1
    assert db.runs["stale"]["status"] == "done" and db.runs["stale"]["attempts"] == 1
    assert db.runs["live"]["status"] == "running"


@_sync
async def test_stale_run_past_attempt_limit_fails_without_processing():
    db = FakeDB()
    _with_deployment(db)
    db.add_row(_row(0))
    db.add_run("stale", status="running", started_age_s=1000, attempts=rc.MAX_ATTEMPTS - 1)
    assert await _loop(db).run_once() == 1
    run = db.runs["stale"]
    assert run["status"] == "failed" and "lease expired" in run["error"]
    assert next(iter(db.rows.values())).deployment_id is None  # not processed


@_sync
async def test_lease_renewed_per_page(monkeypatch):
    monkeypatch.setattr(rc, "PAGE_ROWS", 2)
    db = FakeDB()
    _with_deployment(db)
    for i in range(5):
        db.add_row(_row(i))
    db.add_run("r1")
    await _loop(db).run_once()
    assert db.runs["r1"]["rows_changed"] == 5
    assert db.lease_renewals >= 3


# ---------------------------------------------------------------------------
# Decompression scope (fix round 1, #4)
# ---------------------------------------------------------------------------


@_sync
async def test_only_chunks_holding_changed_rows_are_decompressed():
    db = FakeDB()
    _with_deployment(db)
    week = timedelta(days=7)
    db.chunks = [
        ["c_jan1", T0, T0 + week, True],  # changed rows
        ["c_jan2", T0 + week, T0 + 2 * week, True],  # no changed rows, inside [min, max]
        ["c_jan3", T0 + 2 * week, T0 + 3 * week, True],  # changed rows
    ]
    db.add_row(_row(0))
    db.add_row(_row(15 * 24))  # day 15 -> c_jan3
    db.add_run("r1")
    await _loop(db).run_once()
    assert db.runs["r1"]["rows_changed"] == 2
    assert sorted(db.decompressed) == ["c_jan1", "c_jan3"]
    assert db.chunks[1][3] is True


@_sync
async def test_recompressed_before_update_is_decompressed_again():
    db = FakeDB()
    _with_deployment(db)
    db.chunks = [["c1", T0, T0 + timedelta(days=7), True]]
    db.recompress_once = True
    db.add_row(_row(0))
    db.add_run("r1")
    await _loop(db).run_once()
    assert db.runs["r1"]["status"] == "done" and db.runs["r1"]["rows_changed"] == 1
    assert db.decompressed == ["c1", "c1"]


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


def _plan(row, dep=_dep(), prop=PROP, mdef=MDEF):
    return plan_row(row, dep, prop, mdef, URN)


def test_plan_never_changes_set_quality_value_or_property():
    row = _row(quality_flag="suspect", observed_property="airTemperature", value=999.0)
    u = _plan(row)
    assert u.quality_flag == "suspect"
    assert u.observed_property == "airTemperature"
    assert u.deployment_id == DEP_ID


def test_plan_quality_uses_counter_max_for_cumulative():
    rain = PropertyDef("precipitation", "cumulative", 0.0, 10.0, "mm")
    row = _row(value=50.0, raw_observed_property="precipitation", raw_profile_code="P")
    assert _plan(row, prop=rain, mdef={"unit": "mm", "counterMax": 100}).quality_flag == "valid"
    assert _plan(row, prop=rain, mdef={"unit": "mm", "counterMax": 40}).quality_flag == "out_of_range"


def test_plan_no_deployment_nulls_context_and_location_keeps_property_and_quality():
    row = _row(
        deployment_id=DEP_ID, observed_property="airTemperature", quality_flag="valid",
        source_kind="device", height_m=1.5, medium="ambientAir", lon=1.25, lat=42.5,
        feature_of_interest="urn:ngsi-ld:AgriParcel:p1", raw_location=GPS,
    )
    u = _plan(row, dep=None)
    assert u.deployment_id is None and u.height_m is None and u.medium is None
    assert u.feature_of_interest is None
    assert (u.lon, u.lat) == (None, None)  # never kept from the row itself
    assert (u.observed_property, u.quality_flag, u.source_kind) == ("airTemperature", "valid", "device")


def test_plan_mobile_deployment_uses_raw_point_only():
    assert (_plan(_row(raw_location=GPS), dep=_dep(mobility="mobile")).lon,) == (1.25,)
    u = _plan(_row(lon=9.0, lat=9.0, raw_location=None), dep=_dep(mobility="mobile"))
    assert (u.lon, u.lat) == (None, None)
    # Raw point as the jsonb text asyncpg returns, and invalid WGS84 -> NULL (trigger rule).
    assert _plan(_row(raw_location='{"type":"Point","coordinates":[1.25,42.5]}'),
                 dep=_dep(mobility="mobile")).lat == 42.5
    bad = {"type": "Point", "coordinates": [200.0, 42.5]}
    assert _plan(_row(raw_location=bad), dep=_dep(mobility="mobile")).lon is None


def test_plan_fixed_deployment_ignores_raw_point():
    u = _plan(_row(raw_location=GPS))
    assert (u.lon, u.lat) == (-3.7, 40.4)


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


@_sync
async def test_recompute_unit_mismatch_leaves_observed_property_null():
    db = FakeDB()
    _with_deployment(db)
    db.add_row(_row(0))
    db.add_run("r1")
    lookup = FakeLookup(mdefs={("P", "temperature"): {"unit": "µm"}})

    await _loop(db, lookup).run_once()

    for row in db.rows.values():
        assert row.observed_property is None
        assert row.quality_flag is None
        assert row.deployment_id is None


def test_plan_unit_mismatch_or_missing_profile_leaves_property_null():
    for mdef in ({"unit": "µm"}, {}, None):
        assert _plan(_row(), mdef=mdef) is None  # nothing to change: row already bare
    # A row already carrying observed_property is not re-checked.
    u = _plan(_row(observed_property="airTemperature"), mdef={"unit": "µm"})
    assert u.observed_property == "airTemperature"
