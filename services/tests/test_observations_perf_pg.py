"""perf + pg_integration: `best` read benchmark (contract 5): 30 days PT1H <= 500 ms p95, latest <= 150 ms p95.

Bench: one tenant, 100 devices with deployments. 10 devices observe parcel P (airTemperature
every SEED_FAST_S seconds, 30 days); 90 devices report every 60 s for other parcels, located far
outside the 1 km nearby radius. Chunks older than 7 days are compressed (as in production), then
ANALYZE. `best()` is called directly with a psycopg2 connection running as nkz_rls_test; only the
Orion fetch is replaced.

Seeding (ASSUMPTION): telemetry_measurements is filled directly, as the superuser, with every
column the 115 trigger would set (entity_id, observed_property, medium, deployment_id,
source_kind, quality_flag, feature_of_interest, location, height_m...); the 115 trigger path
(telemetry_events -> per-row plpgsql) is far too slow for ~9 M rows. telemetry_events is left
empty (calibration LEFT JOIN finds nothing), so the join cost on a populated events table is
NOT measured here.
"""
import json
import os
import statistics
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

_SVC = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_READER = os.path.join(_SVC, "timeseries-reader")
for _p in (_SVC, _READER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytestmark = [pytest.mark.pg_integration, pytest.mark.perf]

UTC = timezone.utc
DAYS = 30
SEED_FAST_S = int(os.environ.get("PERF_FAST_INTERVAL_S", "5"))
SEED_SLOW_S = 60
N_FAST, N_SLOW = 10, 90
WARMUP, RUNS = 3, 50
MEM_CAP_GB = 6
BUDGET_SERIES_MS, BUDGET_LATEST_MS = 500.0, 150.0
LON0, LAT0, LON1, LAT1 = -1.6000, 42.8000, -1.5980, 42.8020
PARCEL = {"type": "Polygon", "coordinates": [[[LON0, LAT0], [LON0, LAT1], [LON1, LAT1],
                                               [LON1, LAT0], [LON0, LAT0]]]}

_INSERT = """
INSERT INTO telemetry_measurements (
    tenant_id, observed_at, source_event_id, entity_id, entity_type, device_id,
    attribute_name, value, source, observed_property, feature_of_interest, medium,
    height_m, deployment_id, source_kind, location, time_source, quality_flag)
SELECT %(tenant)s, g.t, d.idx * 10000000 + %(dayno)s * 100000 + g.n, d.urn, 'Device', d.dev,
       'airTemperature', 15 + ((g.n %% 200) / 10.0), 'telemetry_events', 'airTemperature',
       d.feature, 'ambientAir', 2, d.dep::uuid, 'device',
       ST_SetSRID(ST_MakePoint(d.lon, d.lat), 4326)::geography, 'device', 'valid'
FROM bench_dev d
CROSS JOIN LATERAL generate_series(%(lo)s::timestamptz, %(hi)s::timestamptz,
                                   make_interval(secs => d.step)) WITH ORDINALITY AS g(t, n)
"""


def _seed(su, tenant, end):
    """Day by day (all devices per statement); chunks that are complete and older than 7 days are
    compressed as the seed advances, so the scratch DB never holds the full uncompressed set."""
    start = end - timedelta(days=DAYS)
    p = f"urn:ngsi-ld:AgriParcel:{tenant}:p0"
    with su.cursor() as cur:
        cur.execute("INSERT INTO observation_geometry_cache VALUES "
                    "(%s, %s, 'AgriParcel', ST_GeomFromGeoJSON(%s)::geography, now())",
                    (tenant, p, json.dumps(PARCEL)))
        cur.execute(
            "INSERT INTO sensor_profiles (tenant_id, code, name, sdm_entity_type, mapping) "
            "VALUES (%s, %s, 'p', 'Device', %s::jsonb)",
            (tenant, f"prof_{tenant}", json.dumps({"measurements": [
                {"sdmAttribute": "airTemperature", "observedProperty": "airTemperature"}]})))
        cur.execute("CREATE TEMP TABLE bench_dev (idx int, urn text, dev text, feature text,"
                    " dep text, lon float8, lat float8, step int)")
        for i in range(N_FAST + N_SLOW):
            fast = i < N_FAST
            dev = f"dev{i:03d}"
            urn = f"urn:ngsi-ld:Device:{tenant}:{dev}"
            feature = p if fast else f"urn:ngsi-ld:AgriParcel:{tenant}:p{i}"
            lon, lat = (-1.599, 42.801) if fast else (-1.2 + i * 0.001, 42.5 + i * 0.001)
            cur.execute(
                "INSERT INTO device_deployments (tenant_id, device_urn, valid, observes_feature,"
                " observation_context, location, created_by) VALUES (%s, %s, tstzrange(%s, NULL),"
                " %s, %s::jsonb, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 'perf')"
                " RETURNING id::text",
                (tenant, urn, start - timedelta(days=1), feature,
                 json.dumps({"medium": "ambientAir", "heightAboveGround": 2}), lon, lat))
            dep = cur.fetchone()[0]
            cur.execute("INSERT INTO bench_dev VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (i, urn, dev, feature, dep, lon, lat,
                         SEED_FAST_S if fast else SEED_SLOW_S))
        rows = 0
        day, dayno = start, 0
        while day < end:
            nxt = min(day + timedelta(days=1), end)
            cur.execute(_INSERT, {"tenant": tenant, "dayno": dayno, "lo": day,
                                  "hi": nxt - timedelta(seconds=1)})
            rows += cur.rowcount
            cur.execute("SELECT compress_chunk(c, if_not_compressed => true) FROM show_chunks("
                        "'telemetry_measurements', older_than => LEAST(%s::timestamptz,"
                        " now() - interval '7 days')) c", (nxt,))
            day, dayno = nxt, dayno + 1
    return p, rows


def _compress_old(su):
    with su.cursor() as cur:
        cur.execute("SELECT compress_chunk(c, if_not_compressed => true) FROM show_chunks("
                    "'telemetry_measurements', older_than => now() - interval '7 days') c")
        n = cur.rowcount
        cur.execute("ANALYZE telemetry_measurements")
        cur.execute("SELECT count(*) FILTER (WHERE is_compressed), count(*) FROM "
                    "timescaledb_information.chunks WHERE hypertable_name = 'telemetry_measurements'")
        comp, total = cur.fetchone()
    return comp, total, n


def _pct(xs, q):
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def test_best_read_performance(pg_dsn, pg_conn):
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    resource.setrlimit(resource.RLIMIT_AS, (MEM_CAP_GB << 30, hard))
    try:
        _bench(pg_dsn, pg_conn)
    finally:
        resource.setrlimit(resource.RLIMIT_AS, (soft, hard))


def _bench(pg_dsn, pg_conn):
    import psycopg2

    from observations import db as obs_db
    from observations.best import best
    from observations.request import parse_best

    su = pg_conn(None)
    tenant = f"perf_{uuid.uuid4().hex[:8]}"
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    t_seed = time.time()
    p, rows = _seed(su, tenant, end)
    seed_s = time.time() - t_seed
    t_c = time.time()
    comp, total, _ = _compress_old(su)
    comp_s = time.time() - t_c
    with su.cursor() as cur:
        cur.execute("SELECT pg_size_pretty(hypertable_size('telemetry_measurements'))")
        size = cur.fetchone()[0]
    print(f"\nSEED rows={rows} fast_interval_s={SEED_FAST_S} seed_s={seed_s:.0f} "
          f"compress_s={comp_s:.0f} chunks_compressed={comp}/{total} size={size}")

    fetch = lambda t, u: ("ok", {"id": u, "type": "AgriParcel"})
    series_req = parse_best({"feature": p, "property": "airTemperature",
                             "from": (end - timedelta(days=DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                             "to": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "resolution": "PT1H"})
    latest_req = parse_best({"feature": p, "property": "airTemperature", "mode": "latest",
                              "from": (end - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                              "to": end.strftime("%Y-%m-%dT%H:%M:%SZ")})

    conn = psycopg2.connect(pg_dsn)
    conn.autocommit = False
    with conn.cursor() as c:
        c.execute('SET ROLE "nkz_rls_test"')
    conn.commit()

    captured = []
    real_cls = obs_db.RealDictCursor
    real_timeout = obs_db.STATEMENT_TIMEOUT_SQL

    class Timing(real_cls):
        def execute(self, sql, args=None):
            t0 = time.perf_counter()
            try:
                return super().execute(sql, args)
            finally:
                captured.append(((time.perf_counter() - t0) * 1000, sql, args))

    def run(req):
        t0 = time.perf_counter()
        out = best(conn, tenant, req, fetch=fetch)
        return (time.perf_counter() - t0) * 1000, out

    results = {}
    obs_db.STATEMENT_TIMEOUT_SQL = "SET LOCAL statement_timeout = '300s'"  # measure, don't cancel
    try:
        for label, req in (("series_30d_PT1H", series_req), ("latest", latest_req)):
            budget = BUDGET_SERIES_MS if label.startswith("series") else BUDGET_LATEST_MS
            warm = []
            try:
                for _ in range(WARMUP):
                    ms_w, out = run(req)
                    warm.append(ms_w)
            except MemoryError:  # address-space cap below: report instead of OOM-killing the host
                conn.rollback()
                print(f"{label}: MemoryError (client address space capped at {MEM_CAP_GB} GiB)")
                results[label] = [float("inf")]
                continue
            print(f"{label}: warm-up ms = {[round(x, 1) for x in warm]}")
            pts = sum(1 for g in out["groups"] for pt in g["points"] if pt.get("value") is not None)
            assert pts > 0, f"{label}: benchmark returned no values (seed/query mismatch)"
            if min(warm) > 10 * budget:  # hopeless: do not spend 50 more minutes proving it
                ms = warm
                print(f"{label}: warm-up already > 10x budget, timed runs reduced to the warm-up runs")
            else:
                ms = [run(req)[0] for _ in range(RUNS)]
            results[label] = ms
            print(f"{label}: p50={statistics.median(ms):.1f} ms p95={_pct(ms, 0.95):.1f} ms "
                  f"max={max(ms):.1f} ms (points with value: {pts}, {len(ms)} runs)")
        # statement timings from one extra instrumented run per mode
        obs_db.RealDictCursor = Timing
        try:
            slow = {}
            for label, req in (("series_30d_PT1H", series_req), ("latest", latest_req)):
                captured.clear()
                try:
                    run(req)
                except MemoryError:
                    conn.rollback()
                slow[label] = sorted(captured, key=lambda x: -x[0])[:3]
                for ms_, sql, _a in slow[label]:
                    print(f"  [{label}] {ms_:.1f} ms  {sql.strip().splitlines()[0][:80]}")
        finally:
            obs_db.RealDictCursor = real_cls

        budgets = (("series_30d_PT1H", BUDGET_SERIES_MS), ("latest", BUDGET_LATEST_MS))
        failed = [(l, b) for l, b in budgets if _pct(results[l], 0.95) > b]
        if failed:
            for label, _b in failed:
                ms_, sql, args = slow[label][0]
                print(f"\nEXPLAIN (ANALYZE, BUFFERS) of slowest statement of {label} ({ms_:.1f} ms)")
                conn.rollback()
                with obs_db.tenant_cursor(conn, tenant) as cur:
                    cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, args)
                    print("\n".join(r["QUERY PLAN"] for r in cur.fetchall()))
                conn.rollback()
        for label, budget in budgets:
            assert _pct(results[label], 0.95) <= budget, f"{label} p95 over {budget} ms"
    finally:
        obs_db.RealDictCursor = real_cls
        obs_db.STATEMENT_TIMEOUT_SQL = real_timeout
        conn.close()
