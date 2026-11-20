"""pg_integration tests for the observation recompute job (contract 8.6).

Runs as nkz_rls_test (non-superuser, no BYPASSRLS) unless noted. The compressed
chunk test runs as nkz_core, the production worker role, in its own database
where nkz_core owns telemetry_measurements as in production (migration 106):
decompress_chunk requires hypertable ownership. Setup rows are written as the
superuser (bypasses RLS) through psycopg2. Each test uses its own tenant, device
and a year of its own, so tests sharing the session database never overlap.
"""
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

_SVC = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_WORKER = os.path.join(_SVC, "telemetry-worker")
for _p in (_SVC, _WORKER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytestmark = pytest.mark.pg_integration


def _unique(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _run_as(dsn, role, body):
    """Run `async body(pool)` with an asyncpg pool whose connections SET ROLE."""
    import asyncpg

    async def _init(conn):
        await conn.execute(f'SET ROLE "{role}"')

    async def main():
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3, init=_init)
        try:
            return await body(pool)
        finally:
            await pool.close()

    return asyncio.run(main())


def _loop(pool):
    from telemetry_worker.observation_lookup import ObservationLookup
    from telemetry_worker.recompute import RecomputeLoop

    return RecomputeLoop(pool, ObservationLookup(pool))


@pytest.fixture
def su(pg_conn):
    return pg_conn(None)


class Device:
    """A tenant + device with helpers that write setup rows as the superuser."""

    def __init__(self, su):
        self.su = su
        self.tenant = _unique("t")
        self.dev = _unique("d")
        self.urn = f"urn:ngsi-ld:Device:{self.tenant}:{self.dev}"
        with su.cursor() as cur:
            cur.execute("INSERT INTO tenants (tenant_id) VALUES (%s)", (self.tenant,))
            # Unit guard: the profile must declare the catalogue unit for the attribute.
            self.profile_code = _unique("p")
            cur.execute(
                """INSERT INTO public.sensor_profiles
                   (tenant_id, code, name, sdm_entity_type, mapping)
                   VALUES (%s, %s, 'p', 'Device', %s::jsonb)""",
                (self.tenant, self.profile_code, json.dumps({"measurements": [
                    {"sdmAttribute": "temperature", "unit": "°C",
                     "observedProperty": "airTemperature"}]})),
            )

    def deploy(self, lower, upper=None, height=1.5, mobility="fixed"):
        with self.su.cursor() as cur:
            cur.execute(
                """INSERT INTO public.device_deployments
                   (tenant_id, device_urn, valid, observes_feature, observation_context,
                    mobility, location, created_by)
                   VALUES (%s, %s, tstzrange(%s, %s, '[)'), 'urn:ngsi-ld:AgriParcel:p1',
                           %s::jsonb, %s, ST_GeogFromText('POINT(-3.7 40.4)'), 'test')
                   RETURNING id""",
                (self.tenant, self.urn, lower, upper,
                 json.dumps({"medium": "ambientAir", "heightAboveGround": height}), mobility),
            )
            return str(cur.fetchone()[0])

    def correct_height(self, dep_id, height):
        with self.su.cursor() as cur:
            cur.execute(
                "UPDATE public.device_deployments SET observation_context = %s::jsonb WHERE id = %s",
                (json.dumps({"medium": "ambientAir", "heightAboveGround": height}), dep_id),
            )

    def ingest(self, observed_at, value=21.5, dep_id=None, height=None, gps=None, mobile=False,
               attr="temperature", raw_attrs=None):
        """A DeviceMeasurement event as the worker writes it (observations iff dep_id).

        gps: the reading's own point (raw `location`); stored as the location when
        mobile, else the fixed deployment's point is stored.
        """
        raw = {
            "id": f"urn:ngsi-ld:DeviceMeasurement:{self.tenant}:{self.dev}:{attr}",
            "type": "DeviceMeasurement",
            "refDevice": {"type": "Relationship", "object": self.urn},
            "observedProperty": {"type": "Property", "value": "airTemperature"},
            "deviceType": {"type": "Property", "value": self.profile_code},
        }
        # raw_attrs: attribute -> value, or None to drop it (shape of the stored raw payload).
        for key, val in (raw_attrs or {}).items():
            if val is None:
                raw.pop(key, None)
            else:
                raw[key] = {"type": "Property", "value": val}
        if gps is not None:
            raw["location"] = {"type": "GeoProperty",
                               "value": {"type": "Point", "coordinates": list(gps)}}
        payload = {"measurements": {attr: value}, "raw": raw}
        if dep_id is not None:
            stored = list(gps) if mobile else [-3.7, 40.4]
            payload.update({
                "deploymentId": dep_id,
                "sourceKind": "device",
                "timeSource": "device",
                "location": {"type": "Point", "coordinates": stored},
                "observations": {attr: {
                    "observedProperty": "airTemperature",
                    "featureOfInterest": "urn:ngsi-ld:AgriParcel:p1",
                    "medium": "ambientAir", "heightM": height, "qualityFlag": "valid",
                }},
            })
        with self.su.cursor() as cur:
            cur.execute(
                """INSERT INTO public.telemetry_events
                   (tenant_id, observed_at, device_id, entity_id, entity_type, payload)
                   VALUES (%s, %s, %s, %s, 'DeviceMeasurement', %s::jsonb)""",
                (self.tenant, observed_at, self.dev, raw["id"], json.dumps(payload)),
            )

    def enqueue(self, dep_id, range_from, range_to=None, age="5 minutes"):
        with self.su.cursor() as cur:
            cur.execute(
                """INSERT INTO public.observation_recompute_runs
                   (tenant_id, device_urn, deployment_id, range_from, range_to,
                    requested_by, reason, created_at)
                   VALUES (%s, %s, %s, %s, %s, 'test', 'correction', now() - %s::interval)
                   RETURNING id""",
                (self.tenant, self.urn, dep_id, range_from, range_to, age),
            )
            return str(cur.fetchone()[0])

    def run_row(self, run_id):
        with self.su.cursor() as cur:
            cur.execute(
                "SELECT status, rows_changed, error, started_at IS NOT NULL, "
                "finished_at IS NOT NULL FROM public.observation_recompute_runs WHERE id = %s",
                (run_id,),
            )
            return cur.fetchone()

    def attempts(self, run_id):
        with self.su.cursor() as cur:
            cur.execute("SELECT attempts FROM public.observation_recompute_runs WHERE id = %s",
                        (run_id,))
            return cur.fetchone()[0]

    def set_mobility(self, dep_id, mobility):
        with self.su.cursor() as cur:
            cur.execute("UPDATE public.device_deployments SET mobility = %s WHERE id = %s",
                        (mobility, dep_id))

    def rows(self):
        with self.su.cursor() as cur:
            cur.execute(
                """SELECT observed_at, height_m, deployment_id::text, observed_property,
                          quality_flag, source_kind, medium, feature_of_interest,
                          ST_X(location::geometry), ST_Y(location::geometry), value, time_source
                   FROM public.telemetry_measurements
                   WHERE tenant_id = %s ORDER BY observed_at""",
                (self.tenant,),
            )
            return cur.fetchall()


def test_correction_reapplies_height(pg_dsn, su):
    d = Device(su)
    t0 = datetime(2031, 3, 1, tzinfo=timezone.utc)
    dep = d.deploy(t0)
    for i in range(3):
        d.ingest(t0 + timedelta(hours=i), dep_id=dep, height=1.5)
    assert [r[1] for r in d.rows()] == [1.5, 1.5, 1.5]
    d.correct_height(dep, 2.0)
    run = d.enqueue(dep, t0)

    async def body(pool):
        return await _loop(pool).run_once()

    assert _run_as(pg_dsn, "nkz_rls_test", body) == 1
    rows = d.rows()
    assert [r[1] for r in rows] == [2.0, 2.0, 2.0]
    assert all(r[2] == dep and r[4] == "valid" and r[11] == "device" for r in rows)
    assert d.run_row(run) == ("done", 3, None, True, True)

    # A re-run converges: nothing differs, nothing is written.
    rerun = d.enqueue(dep, t0)
    assert _run_as(pg_dsn, "nkz_rls_test", body) == 1
    assert d.run_row(rerun)[:2] == ("done", 0)
    assert d.rows() == rows


def test_row_without_context_gets_context_and_quality(pg_dsn, su):
    d = Device(su)
    t0 = datetime(2032, 3, 1, tzinfo=timezone.utc)
    d.ingest(t0 + timedelta(hours=1))  # ingested before any deployment existed
    (before,) = d.rows()
    assert before[2:8] == (None,) * 6
    dep = d.deploy(t0, height=2.0)  # registered later, valid from the past
    run = d.enqueue(dep, t0)

    async def body(pool):
        return await _loop(pool).run_once()

    assert _run_as(pg_dsn, "nkz_rls_test", body) == 1
    (row,) = d.rows()
    assert row[2] == dep
    assert row[3] == "airTemperature"
    assert row[4] == "valid"
    assert row[5] == "device"
    assert row[6] == "ambientAir"
    assert row[7] == "urn:ngsi-ld:AgriParcel:p1"
    assert row[1] == 2.0
    assert (row[8], row[9]) == pytest.approx((-3.7, 40.4))
    assert row[10] == 21.5 and row[11] is None  # value and time_source untouched
    assert d.run_row(run)[:3] == ("done", 1, None)


def test_rows_outside_any_deployment_are_cleared(pg_dsn, su):
    """A deployment shortened by a correction: rows after its end lose the context."""
    d = Device(su)
    t0 = datetime(2033, 3, 1, tzinfo=timezone.utc)
    dep = d.deploy(t0)
    d.ingest(t0 + timedelta(hours=1), dep_id=dep, height=1.5)
    d.ingest(t0 + timedelta(days=2), dep_id=dep, height=1.5)
    with su.cursor() as cur:
        cur.execute(
            "UPDATE public.device_deployments SET valid = tstzrange(%s, %s, '[)') WHERE id = %s",
            (t0, t0 + timedelta(days=1), dep),
        )
    run = d.enqueue(dep, t0)

    async def body(pool):
        return await _loop(pool).run_once()

    assert _run_as(pg_dsn, "nkz_rls_test", body) == 1
    inside, outside = d.rows()
    assert inside[2] == dep and inside[1] == 1.5
    assert outside[2] is None and outside[1] is None and outside[8] is None
    assert outside[3] == "airTemperature" and outside[4] == "valid"  # kept
    assert d.run_row(run)[:2] == ("done", 1)


def test_run_younger_than_90s_not_claimed(pg_dsn, su):
    d = Device(su)
    t0 = datetime(2034, 3, 1, tzinfo=timezone.utc)
    dep = d.deploy(t0)
    run = d.enqueue(dep, t0, age="30 seconds")

    async def body(pool):
        return await _loop(pool).run_once()

    assert _run_as(pg_dsn, "nkz_rls_test", body) == 0
    assert d.run_row(run)[:2] == ("pending", None)
    with su.cursor() as cur:  # keep the shared database free of claimable leftovers
        cur.execute("DELETE FROM public.observation_recompute_runs WHERE id = %s", (run,))


def test_claim_update_under_rls_skip_locked(pg_dsn, su):
    d = Device(su)
    t0 = datetime(2035, 3, 1, tzinfo=timezone.utc)
    dep = d.deploy(t0)
    run = d.enqueue(dep, t0)
    other_tenant = _unique("t")

    async def body(pool):
        loop = _loop(pool)
        async with pool.acquire() as a:
            # Another tenant's context never sees the run (FORCE RLS).
            async with a.transaction():
                assert await loop.claim_next(a, other_tenant) is None
            tx = a.transaction()
            await tx.start()
            try:
                claimed = await loop.claim_next(a, d.tenant)
                assert claimed is not None and str(claimed["id"]) == run
                # A concurrent claimer skips the locked run instead of waiting on it.
                assert await asyncio.wait_for(loop.run_once(), timeout=10) == 0
            finally:
                await tx.rollback()
        # Released (claim rolled back): claimable and processed now.
        return await loop.run_once()

    assert _run_as(pg_dsn, "nkz_rls_test", body) == 1
    assert d.run_row(run)[:2] == ("done", 0)


def test_compressed_chunk_is_decompressed_then_updated(pg_schema_factory):
    """As nkz_core (owner of telemetry_measurements, as in production)."""
    import psycopg2

    dsn = pg_schema_factory(None)
    su = psycopg2.connect(dsn)
    su.autocommit = True
    try:
        with su.cursor() as cur:
            cur.execute("ALTER TABLE public.telemetry_measurements OWNER TO nkz_core")
            cur.execute("GRANT USAGE ON SCHEMA public TO nkz_core")
            cur.execute("GRANT SELECT ON public.telemetry_events, public.tenants, "
                        "public.sensor_profiles TO nkz_core")
        d = Device(su)
        jan = datetime(2026, 1, 5, tzinfo=timezone.utc)
        mar = datetime(2026, 3, 5, tzinfo=timezone.utc)
        dep = d.deploy(jan - timedelta(days=1))
        d.ingest(jan, dep_id=dep, height=1.5)
        d.ingest(mar, dep_id=dep, height=1.5)
        with su.cursor() as cur:
            cur.execute("SELECT compress_chunk(c) FROM show_chunks('telemetry_measurements') c")
        d.correct_height(dep, 2.0)
        run = d.enqueue(dep, jan - timedelta(days=1), jan + timedelta(days=1))

        def compressed():
            with su.cursor() as cur:
                cur.execute(
                    "SELECT range_start, is_compressed FROM timescaledb_information.chunks "
                    "WHERE hypertable_name = 'telemetry_measurements' ORDER BY range_start"
                )
                return [(rs.date(), c) for rs, c in cur.fetchall()]

        chunks = compressed()
        assert len(chunks) == 2 and all(c for _, c in chunks)

        async def body(pool):
            async with pool.acquire() as c:
                assert await c.fetchval("SELECT current_user") == "nkz_core"
            return await _loop(pool).run_once()

        assert _run_as(dsn, "nkz_core", body) == 1
        assert d.run_row(run)[:3] == ("done", 1, None)
        jan_row, mar_row = d.rows()
        assert jan_row[1] == 2.0
        assert mar_row[1] == 1.5  # outside the run range: untouched
        after = compressed()
        assert after[0][1] is False  # the chunk holding the changed row was decompressed
        assert after[1][1] is True  # the chunk outside the range stays compressed
    finally:
        su.close()


def test_paged_batched_update_on_compressed_chunk_generic_plan(pg_schema_factory, monkeypatch):
    """Keyset paging, several UPDATE batches and a generic plan, next to a compressed chunk.

    As nkz_core. A generic plan cannot fold the $2/$3 window into constants, so this
    proves the still-compressed chunk is not touched by the multi-row UPDATE anyway.
    """
    import asyncpg
    import psycopg2
    from telemetry_worker import recompute as rc

    monkeypatch.setattr(rc, "PAGE_ROWS", 4)
    monkeypatch.setattr(rc, "UPDATE_BATCH_ROWS", 2)

    dsn = pg_schema_factory(None)
    su = psycopg2.connect(dsn)
    su.autocommit = True
    try:
        with su.cursor() as cur:
            cur.execute("ALTER TABLE public.telemetry_measurements OWNER TO nkz_core")
            cur.execute("GRANT USAGE ON SCHEMA public TO nkz_core")
            cur.execute("GRANT SELECT ON public.telemetry_events, public.tenants, "
                        "public.sensor_profiles TO nkz_core")
        d = Device(su)
        jan = datetime(2026, 1, 5, tzinfo=timezone.utc)
        mar = datetime(2026, 3, 5, tzinfo=timezone.utc)
        dep = d.deploy(jan - timedelta(days=1))
        for i in range(7):
            d.ingest(jan + timedelta(minutes=i), dep_id=dep, height=1.5)
        d.ingest(mar, dep_id=dep, height=1.5)
        with su.cursor() as cur:
            cur.execute("SELECT compress_chunk(c) FROM show_chunks('telemetry_measurements') c")
        d.correct_height(dep, 2.0)
        run = d.enqueue(dep, jan - timedelta(days=1), jan + timedelta(days=1))

        async def _init(conn):
            await conn.execute('SET ROLE "nkz_core"')
            await conn.execute("SET plan_cache_mode = force_generic_plan")

        async def main():
            pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3, init=_init)
            try:
                return await _loop(pool).run_once()
            finally:
                await pool.close()

        assert asyncio.run(main()) == 1
        assert d.run_row(run)[:3] == ("done", 7, None)
        rows = d.rows()
        assert [r[1] for r in rows] == [2.0] * 7 + [1.5]
        with su.cursor() as cur:
            cur.execute(
                "SELECT is_compressed FROM timescaledb_information.chunks "
                "WHERE hypertable_name = 'telemetry_measurements' ORDER BY range_start"
            )
            assert [c for (c,) in cur.fetchall()] == [False, True]
    finally:
        su.close()


# ---------------------------------------------------------------------------
# Fix round 1: location convergence, liveness, page pruning, decompress scope
# ---------------------------------------------------------------------------

GPS = (1.25, 42.5)


def _one_tick(dsn, role="nkz_rls_test", patch=None):
    async def body(pool):
        loop = _loop(pool)
        if patch is not None:
            patch(loop)
        try:
            return await loop.run_once()
        except asyncio.CancelledError:
            return "cancelled"

    return _run_as(dsn, role, body)


def test_mobile_reading_outside_deployment_converges(pg_dsn, su):
    d = Device(su)
    t0 = datetime(2036, 3, 1, tzinfo=timezone.utc)
    dep = d.deploy(t0, mobility="mobile")
    d.ingest(t0 + timedelta(hours=1), dep_id=dep, height=1.5, gps=GPS, mobile=True)
    d.ingest(t0 + timedelta(days=2), dep_id=dep, height=1.5, gps=GPS, mobile=True)
    assert [(r[8], r[9]) for r in d.rows()] == [GPS, GPS]
    with su.cursor() as cur:
        cur.execute("UPDATE public.device_deployments SET valid = tstzrange(%s, %s, '[)') "
                    "WHERE id = %s", (t0, t0 + timedelta(days=1), dep))
    first = d.enqueue(dep, t0)
    assert _one_tick(pg_dsn) == 1
    inside, outside = d.rows()
    assert (inside[2], inside[8], inside[9]) == (dep, *GPS)  # reading's own point kept
    assert (outside[2], outside[8], outside[9]) == (None, None, None)
    assert d.run_row(first)[:2] == ("done", 1)
    second = d.enqueue(dep, t0)
    assert _one_tick(pg_dsn) == 1
    assert d.run_row(second)[:2] == ("done", 0)
    assert d.rows() == [inside, outside]


def test_fixed_to_mobile_takes_raw_point_and_converges(pg_dsn, su):
    d = Device(su)
    t0 = datetime(2037, 3, 1, tzinfo=timezone.utc)
    dep = d.deploy(t0)
    d.ingest(t0 + timedelta(hours=1), dep_id=dep, height=1.5, gps=GPS)
    assert (d.rows()[0][8], d.rows()[0][9]) == pytest.approx((-3.7, 40.4))
    d.set_mobility(dep, "mobile")
    first = d.enqueue(dep, t0)
    assert _one_tick(pg_dsn) == 1
    (row,) = d.rows()
    assert (row[8], row[9]) == GPS
    assert d.run_row(first)[:2] == ("done", 1)
    second = d.enqueue(dep, t0)
    assert _one_tick(pg_dsn) == 1
    assert d.run_row(second)[:2] == ("done", 0)


def test_cancelled_run_returns_to_pending(pg_dsn, su):
    d = Device(su)
    t0 = datetime(2038, 3, 1, tzinfo=timezone.utc)
    dep = d.deploy(t0)
    run = d.enqueue(dep, t0)

    def patch(loop):
        async def cancelled(run_, progress):
            progress.rows_changed += 3
            raise asyncio.CancelledError()

        loop._process_run = cancelled

    assert _one_tick(pg_dsn, patch=patch) == "cancelled"
    status, rows_changed, _err, started, finished = d.run_row(run)
    assert (status, rows_changed, started, finished) == ("pending", 3, False, False)
    assert d.attempts(run) == 0
    with su.cursor() as cur:  # keep the shared database free of claimable leftovers
        cur.execute("DELETE FROM public.observation_recompute_runs WHERE id = %s", (run,))


def test_stale_running_run_is_reclaimed(pg_dsn, su):
    d = Device(su)
    t0 = datetime(2039, 3, 1, tzinfo=timezone.utc)
    dep = d.deploy(t0)
    d.ingest(t0 + timedelta(hours=1))
    stale = d.enqueue(dep, t0)
    live = d.enqueue(dep, t0, age="10 minutes")
    with su.cursor() as cur:
        cur.execute("UPDATE public.observation_recompute_runs SET status = 'running', "
                    "started_at = now() - interval '20 minutes' WHERE id = %s", (stale,))
        cur.execute("UPDATE public.observation_recompute_runs SET status = 'running', "
                    "started_at = now() - interval '1 minute' WHERE id = %s", (live,))
    assert _one_tick(pg_dsn) == 1
    assert d.run_row(stale)[:2] == ("done", 1)
    assert d.attempts(stale) == 1
    assert d.run_row(live)[0] == "running"
    with su.cursor() as cur:
        cur.execute("DELETE FROM public.observation_recompute_runs WHERE id = %s", (live,))


def test_failure_retried_then_failed_at_fifth_attempt(pg_dsn, su):
    from telemetry_worker.recompute import MAX_ATTEMPTS

    d = Device(su)
    t0 = datetime(2040, 3, 1, tzinfo=timezone.utc)
    dep = d.deploy(t0)
    run = d.enqueue(dep, t0)

    def patch(loop):
        async def flaky(run_, progress):
            progress.rows_changed += 1
            raise RuntimeError("transient lock timeout")

        loop._process_run = flaky

    for attempt in range(1, MAX_ATTEMPTS):
        assert _one_tick(pg_dsn, patch=patch) == 1  # one attempt per tick
        status, rows_changed, error, started, finished = d.run_row(run)
        assert (status, rows_changed, started, finished) == ("pending", attempt, False, False)
        assert "transient lock timeout" in error and d.attempts(run) == attempt
    assert _one_tick(pg_dsn, patch=patch) == 1
    status, rows_changed, error, _started, finished = d.run_row(run)
    assert (status, rows_changed, finished) == ("failed", MAX_ATTEMPTS, True)
    assert d.attempts(run) == MAX_ATTEMPTS
    assert _one_tick(pg_dsn, patch=patch) == 0  # failed runs are not claimed again


def test_page_query_excludes_earlier_chunks(pg_dsn, su):
    from telemetry_worker import recompute as rc

    d = Device(su)
    jan = datetime(2041, 1, 5, tzinfo=timezone.utc)
    mar = datetime(2041, 3, 5, tzinfo=timezone.utc)
    d.ingest(jan)
    d.ingest(mar)
    with su.cursor() as cur:
        cur.execute(
            "SELECT chunk_name FROM timescaledb_information.chunks "
            "WHERE hypertable_name = 'telemetry_measurements' "
            "AND range_start <= %s AND range_end > %s", (jan, jan))
        (jan_chunk,) = cur.fetchone()
        cur.execute("SELECT source_event_id FROM telemetry_measurements "
                    "WHERE tenant_id = %s AND observed_at = %s", (d.tenant, jan))
        (jan_event,) = cur.fetchone()

    async def body(pool):
        async with pool.acquire() as c:
            # Second page after the Jan row: the bound is the last page's observed_at.
            plan = await c.fetch("EXPLAIN " + rc.ROWS_SQL, d.tenant, d.dev, jan, None,
                                 jan, jan_event, "temperature", 10)
            later = await c.fetch("EXPLAIN " + rc.ROWS_SQL, d.tenant, d.dev, mar, None,
                                  mar, jan_event, "temperature", 10)
        return ("\n".join(r[0] for r in plan), "\n".join(r[0] for r in later))

    first_page, later_page = _run_as(pg_dsn, "nkz_rls_test", body)
    assert jan_chunk in first_page
    assert jan_chunk not in later_page


def test_only_chunks_with_changed_rows_are_decompressed(pg_schema_factory):
    """As nkz_core. A compressed chunk between changed rows, with none of them, stays compressed."""
    import psycopg2

    dsn = pg_schema_factory(None)
    su = psycopg2.connect(dsn)
    su.autocommit = True
    try:
        with su.cursor() as cur:
            cur.execute("ALTER TABLE public.telemetry_measurements OWNER TO nkz_core")
            cur.execute("GRANT USAGE ON SCHEMA public TO nkz_core")
            cur.execute("GRANT SELECT ON public.telemetry_events, public.tenants, "
                        "public.sensor_profiles TO nkz_core")
        d = Device(su)
        other = Device(su)
        jan = datetime(2026, 1, 5, tzinfo=timezone.utc)
        feb = datetime(2026, 2, 5, tzinfo=timezone.utc)
        mar = datetime(2026, 3, 5, tzinfo=timezone.utc)
        dep = d.deploy(jan - timedelta(days=1))
        d.ingest(jan, dep_id=dep, height=1.5)
        other.ingest(feb)  # another tenant's chunk content, nothing to change
        d.ingest(mar, dep_id=dep, height=1.5)
        with su.cursor() as cur:
            cur.execute("SELECT compress_chunk(c) FROM show_chunks('telemetry_measurements') c")
        d.correct_height(dep, 2.0)
        run = d.enqueue(dep, jan - timedelta(days=1))
        assert _one_tick(dsn, role="nkz_core") == 1
        assert d.run_row(run)[:3] == ("done", 2, None)
        assert [r[1] for r in d.rows()] == [2.0, 2.0]
        with su.cursor() as cur:
            cur.execute(
                "SELECT is_compressed FROM timescaledb_information.chunks "
                "WHERE hypertable_name = 'telemetry_measurements' ORDER BY range_start"
            )
            assert [c for (c,) in cur.fetchall()] == [False, True, False]
    finally:
        su.close()


def _recompute_late_deployment(pg_dsn, d, t0):
    """Deploy after the reading was ingested, recompute, return its row + attribute name."""
    dep = d.deploy(t0, height=2.0)
    run = d.enqueue(dep, t0)

    async def body(pool):
        return await _loop(pool).run_once()

    assert _run_as(pg_dsn, "nkz_rls_test", body) == 1
    with d.su.cursor() as cur:
        cur.execute("SELECT attribute_name FROM public.telemetry_measurements WHERE tenant_id = %s",
                    (d.tenant,))
        attrs = [r[0] for r in cur.fetchall()]
    (row,) = d.rows()
    return dep, row, attrs, d.run_row(run)


def test_new_shape_raw_gets_context_and_keeps_the_channel(pg_dsn, su):
    d = Device(su)
    t0 = datetime(2034, 3, 1, tzinfo=timezone.utc)
    d.ingest(t0 + timedelta(hours=1), raw_attrs={
        "name": "temperature", "controlledProperty": "airTemperature",
        "measurementType": "instant"})
    dep, row, attrs, run = _recompute_late_deployment(pg_dsn, d, t0)
    assert row[2] == dep and row[3] == "airTemperature" and row[1] == 2.0
    assert attrs == ["temperature"] and run[:2] == ("done", 1)


def test_legacy_raw_with_device_type_gets_context(pg_dsn, su):
    d = Device(su)
    t0 = datetime(2034, 4, 1, tzinfo=timezone.utc)
    d.ingest(t0 + timedelta(hours=1), raw_attrs={
        "controlledProperty": "temperature", "measurementType": "SoilMoisture",
        "observedProperty": None})  # the profile type; the catalogue name is the profile's
    dep, row, attrs, _ = _recompute_late_deployment(pg_dsn, d, t0)
    assert row[2] == dep and row[3] == "airTemperature" and attrs == ["temperature"]


def test_phase1_raw_with_neither_device_type_nor_name_keeps_null_context(pg_dsn, su):
    d = Device(su)
    t0 = datetime(2034, 5, 1, tzinfo=timezone.utc)
    d.ingest(t0 + timedelta(hours=1), raw_attrs={"deviceType": None})
    dep, row, attrs, run = _recompute_late_deployment(pg_dsn, d, t0)
    assert row[2:8] == (None,) * 6 and attrs == ["temperature"]
    assert run[:3] == ("done", 0, None)


def test_new_shape_min_channel_gets_no_context(pg_dsn, su):
    d = Device(su)
    t0 = datetime(2034, 6, 1, tzinfo=timezone.utc)
    with su.cursor() as cur:  # the profile opts the Min channel in: only the gate stops it
        cur.execute(
            "UPDATE public.sensor_profiles SET mapping = %s::jsonb WHERE tenant_id = %s AND code = %s",
            (json.dumps({"measurements": [{"sdmAttribute": "airTemperatureMin", "unit": "°C",
                                           "observedProperty": "airTemperature"}]}),
             d.tenant, d.profile_code),
        )
    d.ingest(t0 + timedelta(hours=1), attr="airTemperatureMin", raw_attrs={
        "name": "airTemperatureMin", "controlledProperty": "airTemperature",
        "measurementType": "min"})
    dep, row, attrs, run = _recompute_late_deployment(pg_dsn, d, t0)
    assert row[2:8] == (None,) * 6
    assert attrs == ["airTemperatureMin"] and run[:3] == ("done", 0, None)


def test_stale_name_after_rollback_is_legacy(pg_dsn, su):
    """Flag rolled back: `name` is stale (= the channel), `measurementType` is a profile type."""
    d = Device(su)
    t0 = datetime(2034, 7, 1, tzinfo=timezone.utc)
    d.ingest(t0 + timedelta(hours=1), raw_attrs={
        "name": "temperature", "controlledProperty": "temperature",
        "measurementType": "Temperature"})
    dep, row, attrs, _ = _recompute_late_deployment(pg_dsn, d, t0)
    assert row[2] == dep and row[3] == "airTemperature" and attrs == ["temperature"]
