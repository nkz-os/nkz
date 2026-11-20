"""perf + pg_integration: `best` read benchmark, contract 5 budgets on the 8.11 load (pass gate).

Gating load (contract 8.11): one tenant, 100 devices with deployments, 30 days, every device
reports airTemperature every 60 s:
  - 3 devices directly observe parcel P (tier 1);
  - 97 devices observe other parcels, located far outside the nearby radius.
Chunks older than 7 days are compressed as the seed advances (both hypertables), then ANALYZE.
Budgets: series 30 d PT1H p95 <= 500 ms, latest p95 <= 150 ms; 3 warm-ups then 50 timed runs,
end to end through `best()` as nkz_rls_test (tenant_cursor, production 10 s statement timeout).

Dense scenario (informational, env NKZ_PERF_DENSE=1): another tenant, 10 devices at 5 s on its
parcel. Only asserts that the series completes under the 10 s statement timeout; prints p50/p95.

Seeding:
  - tier-1 devices: one telemetry_events row per reading, inserted with the 115 trigger enabled,
    so their telemetry_measurements rows are exactly what production writes. 1 event in
    CAL_EVERY carries `calibration_period_ids` (the reader's calibration LEFT JOIN finds real rows).
  - the 97 other devices (and the dense devices): telemetry_measurements rows inserted directly,
    with every column the 115 trigger sets. ASSUMPTION: no telemetry_events rows for them; the
    reader's event join is bounded by the candidate device ids, so their events are never read.
    `_parity_check` checks one direct row against the trigger's row.
Disk guard: refuses to start below 8 GB free on '/', aborts and drops the scratch DB below 4 GB.

Opt-in: runs only with `-m perf` or env NKZ_PERF=1 (a plain `-m pg_integration` run skips it).
"""
import json
import os
import resource
import shutil
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
STEP_S = 60
N_DIRECT, N_OTHER = 3, 97
DENSE_N, DENSE_STEP_S = 10, 5
CAL_EVERY = 4
WARMUP, RUNS, DENSE_RUNS = 3, 50, 20
BUDGET_SERIES_MS, BUDGET_LATEST_MS = 500.0, 150.0
PROD_TIMEOUT_SQL = "SET LOCAL statement_timeout = '10s'"
MIN_FREE_START, MIN_FREE_SEED = 8 << 30, 4 << 30
MAX_RSS_GB = 4
LON0, LAT0, LON1, LAT1 = -1.6000, 42.8000, -1.5980, 42.8020
PARCEL = {"type": "Polygon", "coordinates": [[[LON0, LAT0], [LON0, LAT1], [LON1, LAT1],
                                               [LON1, LAT0], [LON0, LAT0]]]}


# ------------------------------------------------------------------ gate

def _perf_selected(config) -> bool:
    if os.environ.get("NKZ_PERF") == "1":
        return True
    tokens = (config.option.markexpr or "").replace("(", " ").replace(")", " ").split()
    return any(t == "perf" and (i == 0 or tokens[i - 1] != "not") for i, t in enumerate(tokens))


@pytest.fixture(autouse=True)
def _perf_gate(request):
    if not _perf_selected(request.config):
        pytest.skip("perf benchmark: run with -m perf or NKZ_PERF=1")


def _free() -> int:
    return shutil.disk_usage("/").free


class DiskGuardAbort(RuntimeError):
    pass


# ------------------------------------------------------------------ seed

# value: same expression for the event path and the direct path (parity check relies on it)
_VALUE = "(15 + ((g.n %% 200) / 10.0))::float8"

_SERIES = """
FROM bench_dev d
CROSS JOIN LATERAL generate_series(%(lo)s::timestamptz, %(hi)s::timestamptz,
                                   make_interval(secs => d.step)) WITH ORDINALITY AS g(t, n)
WHERE d.idx = ANY(%(idx)s)
"""

# Events as the telemetry-worker writes them for a DeviceMeasurement (payload keys of
# notification_handler: measurements, raw, calibration_period_ids/raw_measurements, observations
# plus the event-level deploymentId/sourceKind/timeSource/location); the 115 trigger fills
# telemetry_measurements from them.
_INSERT_EVENTS = f"""
INSERT INTO telemetry_events (tenant_id, observed_at, device_id, entity_id, entity_type,
                              payload, quality_flag)
SELECT %(tenant)s, g.t, d.dev, d.meas_urn, 'DeviceMeasurement',
       jsonb_build_object(
         'measurements', jsonb_build_object('airTemperature', {_VALUE}),
         'raw', jsonb_build_object(
            'id', d.meas_urn, 'type', 'DeviceMeasurement',
            'refDevice', jsonb_build_object('type', 'Relationship', 'object', d.urn),
            'controlledProperty', jsonb_build_object('type', 'Property', 'value', 'airTemperature'),
            'measurementType', jsonb_build_object('type', 'Property', 'value', 'temperature'),
            'numValue', jsonb_build_object('type', 'Property', 'value', {_VALUE},
                                           'unitCode', 'CEL'),
            'dateObserved', jsonb_build_object('type', 'Property',
                'value', to_char(g.t AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')),
            'timeSource', jsonb_build_object('type', 'Property', 'value', 'device'),
            'observedProperty', jsonb_build_object('type', 'Property', 'value', 'airTemperature'),
            'profileCode', jsonb_build_object('type', 'Property', 'value', d.profile),
            '@context', jsonb_build_array(
                'https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context-v1.8.jsonld')),
         'observations', jsonb_build_object('airTemperature', jsonb_build_object(
            'observedProperty', 'airTemperature', 'featureOfInterest', d.feature,
            'medium', 'ambientAir', 'heightM', 2, 'qualityFlag', 'valid')),
         'deploymentId', d.dep, 'sourceKind', 'device', 'timeSource', 'device',
         'location', jsonb_build_object('type', 'Point',
                                        'coordinates', jsonb_build_array(d.lon, d.lat)))
       || CASE WHEN g.n %% {CAL_EVERY} = 0 THEN jsonb_build_object(
            'calibration_period_ids', jsonb_build_object('airTemperature', d.cal),
            'raw_measurements', jsonb_build_object('airTemperature', {_VALUE} - 0.2))
          ELSE '{{}}'::jsonb END,
       'valid'
{_SERIES}
"""

# Direct rows: every column the 115 trigger writes for the event above (minus calibration).
_INSERT_DIRECT = f"""
INSERT INTO telemetry_measurements (
    tenant_id, observed_at, source_event_id, entity_id, entity_type, device_id, sensor_id,
    task_id, attribute_name, value, source, observed_property, feature_of_interest, medium,
    depth_from_cm, depth_to_cm, height_m, organ, context_ref, deployment_id, source_kind,
    location, time_source, quality_flag)
SELECT %(tenant)s, g.t, %(id_base)s + d.idx::bigint * 100000000 + g.n, d.meas_urn,
       'DeviceMeasurement', d.dev, NULL, NULL, 'airTemperature', {_VALUE}, 'telemetry_events',
       'airTemperature', d.feature, 'ambientAir', NULL, NULL, 2, NULL, NULL, d.dep::uuid,
       'device', ST_SetSRID(ST_GeomFromGeoJSON(jsonb_build_object('type', 'Point',
           'coordinates', jsonb_build_array(d.lon, d.lat))::text), 4326)::geography,
       'device', 'valid'
{_SERIES}
"""


def _setup_tenant(cur, tenant, n_direct, n_other, step_direct, start):
    """Geometry cache, profile, deployments and the bench_dev rows of one tenant."""
    p = f"urn:ngsi-ld:AgriParcel:{tenant}:p0"
    profile = f"prof_{tenant}"
    cur.execute("INSERT INTO observation_geometry_cache VALUES "
                "(%s, %s, 'AgriParcel', ST_GeomFromGeoJSON(%s)::geography, now())",
                (tenant, p, json.dumps(PARCEL)))
    cur.execute(
        "INSERT INTO sensor_profiles (tenant_id, code, name, sdm_entity_type, mapping) "
        "VALUES (%s, %s, 'p', 'Device', %s::jsonb)",
        (tenant, profile, json.dumps({"measurements": [
            {"sdmAttribute": "airTemperature", "observedProperty": "airTemperature",
             "type": "temperature", "unit": "°C"}]})))
    cur.execute("CREATE TEMP TABLE IF NOT EXISTS bench_dev (tenant text, idx int, urn text,"
                " meas_urn text, dev text, feature text, dep text, lon float8, lat float8,"
                " step int, direct bool, profile text, cal text)")
    cur.execute("DELETE FROM bench_dev")
    direct_urns = []
    for i in range(n_direct + n_other):
        direct = i < n_direct
        dev = f"dev{i:03d}"
        urn = f"urn:ngsi-ld:Device:{tenant}:{dev}"
        meas_urn = f"urn:ngsi-ld:DeviceMeasurement:{tenant}:{dev}:airTemperature"
        feature = p if direct else f"urn:ngsi-ld:AgriParcel:{tenant}:p{i}"
        # tier-1 devices inside P; the others ~40 km away (nearby radius is 2 km)
        lon, lat = ((-1.5995 + 0.0003 * i, 42.8005 + 0.0003 * i) if direct
                    else (-1.2 + i * 0.001, 42.5 + i * 0.001))
        cur.execute(
            "INSERT INTO device_deployments (tenant_id, device_urn, valid, observes_feature,"
            " observation_context, location, created_by) VALUES (%s, %s, tstzrange(%s, NULL),"
            " %s, %s::jsonb, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 'perf')"
            " RETURNING id::text",
            (tenant, urn, start - timedelta(days=1), feature,
             json.dumps({"medium": "ambientAir", "heightAboveGround": 2}), lon, lat))
        dep = cur.fetchone()[0]
        cur.execute("INSERT INTO bench_dev VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (tenant, i, urn, meas_urn, dev, feature, dep, lon, lat,
                     step_direct if direct else STEP_S, direct, profile, str(uuid.uuid4())))
        if direct:
            direct_urns.append(urn)
    return p, direct_urns


def _compress_older(cur, upto):
    for table in ("telemetry_measurements", "telemetry_events"):
        cur.execute("SELECT compress_chunk(c, if_not_compressed => true) FROM show_chunks("
                    f"'{table}', older_than => LEAST(%s::timestamptz, now() - interval '7 days')) c",
                    (upto,))


def _seed(su, tenant, end, *, n_direct, n_other, step_direct, direct_via_events, log):
    """Day by day; both hypertables are filled for a day before its chunks may be compressed.
    Free disk is checked every day (DiskGuardAbort below MIN_FREE_SEED)."""
    start = end - timedelta(days=DAYS)
    rows = 0
    with su.cursor() as cur:
        p, direct_urns = _setup_tenant(cur, tenant, n_direct, n_other, step_direct, start)
        direct_idx = list(range(n_direct))
        other_idx = list(range(n_direct, n_direct + n_other))
        day = start
        while day < end:
            if _free() < MIN_FREE_SEED:
                raise DiskGuardAbort(f"free disk {_free() / 2**30:.1f} GiB < 4 GiB during seeding")
            nxt = min(day + timedelta(days=1), end)
            args = {"tenant": tenant, "lo": day, "hi": nxt - timedelta(seconds=1)}
            if direct_via_events:
                cur.execute(_INSERT_EVENTS, dict(args, idx=direct_idx))
            else:
                cur.execute(_INSERT_DIRECT, dict(args, idx=direct_idx, id_base=0))
            rows += cur.rowcount
            if other_idx:
                cur.execute(_INSERT_DIRECT, dict(args, idx=other_idx, id_base=0))
                rows += cur.rowcount
            _compress_older(cur, nxt)
            day = nxt
        log(f"  seeded {tenant}: {rows} readings, free disk {_free() / 2**30:.1f} GiB")
        _compress_older(cur, end)
        cur.execute("ANALYZE telemetry_measurements")
        cur.execute("ANALYZE telemetry_events")
    return p, direct_urns, rows


_PARITY_COLS = ("tenant_id", "observed_at", "entity_id", "entity_type", "device_id", "sensor_id",
                "task_id", "attribute_name", "value", "source", "observed_property",
                "feature_of_interest", "medium", "depth_from_cm", "depth_to_cm", "height_m",
                "organ", "context_ref", "deployment_id", "source_kind",
                "ST_AsText(location)", "time_source", "quality_flag")


def _parity_check(su, tenant, t_probe):
    """One direct row and one trigger row for the same input (device dev099, t_probe, n=1);
    every column except source_event_id/ingested_at must match. Both rows are deleted after."""
    idx = [N_DIRECT + N_OTHER - 1]
    args = {"tenant": tenant, "lo": t_probe, "hi": t_probe, "idx": idx}
    cols = ", ".join(_PARITY_COLS)
    with su.cursor() as cur:
        cur.execute(_INSERT_EVENTS, args)
        cur.execute(_INSERT_DIRECT, dict(args, id_base=9_000_000_000_000))
        cur.execute(f"SELECT source_event_id >= 9000000000000 AS direct, {cols} FROM "
                    "telemetry_measurements WHERE tenant_id = %s AND observed_at = %s "
                    "ORDER BY 1", (tenant, t_probe))
        rows = cur.fetchall()
        cur.execute("DELETE FROM telemetry_measurements WHERE tenant_id = %s AND observed_at = %s",
                    (tenant, t_probe))
        cur.execute("DELETE FROM telemetry_events WHERE tenant_id = %s AND observed_at = %s",
                    (tenant, t_probe))
    assert len(rows) == 2, rows
    trig, direct = rows
    assert trig[0] is False and direct[0] is True
    assert trig[1:] == direct[1:], dict(zip(_PARITY_COLS, zip(trig[1:], direct[1:])))


# ----------------------------------------------------------------- bench

def _pct(xs, q):
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def _connect_rls(dsn):
    import psycopg2

    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    with conn.cursor() as c:
        c.execute('SET ROLE "nkz_rls_test"')
    conn.commit()
    return conn


def _requests(p, end):
    from observations.request import parse_best

    iso = "%Y-%m-%dT%H:%M:%SZ"
    series = parse_best({"feature": p, "property": "airTemperature",
                         "from": (end - timedelta(days=DAYS)).strftime(iso),
                         "to": end.strftime(iso), "resolution": "PT1H"})
    latest = parse_best({"feature": p, "property": "airTemperature", "mode": "latest",
                         "from": (end - timedelta(hours=1)).strftime(iso), "to": end.strftime(iso)})
    return series, latest


def _measure(conn, tenant, req, runs, budget, log, label):
    from observations.best import best

    fetch = lambda t, u: ("ok", {"id": u, "type": "AgriParcel"})  # noqa: E731

    def run():
        t0 = time.perf_counter()
        out = best(conn, tenant, req, fetch=fetch)
        return (time.perf_counter() - t0) * 1000, out

    warm = []
    out = None
    for _ in range(WARMUP):
        ms, out = run()
        warm.append(ms)
    log(f"{label}: warm-up ms = {[round(x, 1) for x in warm]}")
    if budget is not None and min(warm) > 10 * budget:
        ms_list = warm  # hopeless: do not spend minutes proving it
        log(f"{label}: warm-up > 10x budget: reduced runs (the {len(warm)} warm-up runs only)")
    else:
        ms_list = [run()[0] for _ in range(runs)]
    log(f"{label}: p50={statistics.median(ms_list):.1f} ms p95={_pct(ms_list, 0.95):.1f} ms "
        f"max={max(ms_list):.1f} ms ({len(ms_list)} runs; host load avg 1 min "
        f"{os.getloadavg()[0]:.1f} on {os.cpu_count()} CPUs)")
    return ms_list, out


def _slowest_statements(conn, tenant, req, log, label):
    """One instrumented run: the 3 slowest statements (ms, sql, args)."""
    from observations import db as obs_db
    from observations.best import best

    captured = []
    real_cls = obs_db.RealDictCursor

    class Timing(real_cls):
        def execute(self, sql, args=None):
            t0 = time.perf_counter()
            try:
                return super().execute(sql, args)
            finally:
                captured.append(((time.perf_counter() - t0) * 1000, sql, args))

    obs_db.RealDictCursor = Timing
    try:
        best(conn, tenant, req, fetch=lambda t, u: ("ok", {"id": u, "type": "AgriParcel"}))
    finally:
        obs_db.RealDictCursor = real_cls
    slow = sorted(captured, key=lambda x: -x[0])[:3]
    for ms, sql, _a in slow:
        log(f"  [{label}] {ms:.1f} ms  {sql.strip().splitlines()[0][:80]}")
    return slow


def _explain(conn, tenant, stmt, log, label):
    from observations import db as obs_db

    ms, sql, args = stmt
    log(f"\nEXPLAIN (ANALYZE, BUFFERS) of the slowest statement of {label} ({ms:.1f} ms)")
    conn.rollback()
    with obs_db.tenant_cursor(conn, tenant) as cur:
        cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, args)
        log("\n".join(r["QUERY PLAN"] for r in cur.fetchall()))
    conn.rollback()


def _drop_db(dsn):
    import psycopg2
    from urllib.parse import urlparse

    name = urlparse(dsn).path.lstrip("/")
    admin = psycopg2.connect(os.environ["POSTGRES_URL"])
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        admin.close()


def test_best_read_performance(pg_schema_factory):
    import psycopg2

    from observations import db as obs_db

    assert obs_db.STATEMENT_TIMEOUT_SQL == PROD_TIMEOUT_SQL, "production statement timeout changed"
    free = _free()
    if free < MIN_FREE_START:
        pytest.fail(f"free disk {free / 2**30:.1f} GiB < 8 GiB: refusing to seed")

    lines = []

    def log(msg):
        lines.append(msg)
        print(msg, flush=True)

    dsn = pg_schema_factory(None)
    try:
        su = psycopg2.connect(dsn)
        su.autocommit = True
        tenant = f"perf_{uuid.uuid4().hex[:8]}"
        end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        t0 = time.time()
        p, direct_urns, rows = _seed(su, tenant, end, n_direct=N_DIRECT, n_other=N_OTHER,
                                     step_direct=STEP_S, direct_via_events=True, log=log)
        seed_s = time.time() - t0
        _parity_check(su, tenant, end + timedelta(minutes=7))
        with su.cursor() as cur:
            cur.execute("SELECT count(*) FILTER (WHERE is_compressed), count(*) FROM "
                        "timescaledb_information.chunks WHERE hypertable_name = "
                        "'telemetry_measurements'")
            comp, total = cur.fetchone()
            cur.execute("SELECT pg_size_pretty(hypertable_size('telemetry_measurements')),"
                        " pg_size_pretty(hypertable_size('telemetry_events'))")
            size_m, size_e = cur.fetchone()
        log(f"\nSEED rows={rows} seed_s={seed_s:.0f} chunks_compressed={comp}/{total} "
            f"measurements={size_m} events={size_e} free={_free() / 2**30:.1f} GiB")

        series_req, latest_req = _requests(p, end)
        conn = _connect_rls(dsn)
        results, slow = {}, {}
        try:
            for label, req, budget in (("series_30d_PT1H", series_req, BUDGET_SERIES_MS),
                                       ("latest", latest_req, BUDGET_LATEST_MS)):
                ms, out = _measure(conn, tenant, req, RUNS, budget, log, label)
                results[label] = ms
                pts = [pt for g in out["groups"] for pt in g["points"] if pt["value"] is not None]
                assert pts, f"{label}: no values (seed/query mismatch)"
                ids = {s["id"] for pt in pts for s in pt["sources"]}
                assert ids == set(direct_urns), f"{label}: tier-1 sources {sorted(ids)}"
                assert {pt["tier"] for pt in pts} == {1}
                slow[label] = _slowest_statements(conn, tenant, req, log, label)
            rss_gb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20
            log(f"client peak RSS {rss_gb:.2f} GiB")
            budgets = (("series_30d_PT1H", BUDGET_SERIES_MS), ("latest", BUDGET_LATEST_MS))
            for label, budget in budgets:
                if _pct(results[label], 0.95) > budget:
                    _explain(conn, tenant, slow[label][0], log, label)

            if os.environ.get("NKZ_PERF_DENSE") == "1":
                _dense(pg_schema_factory, end, log)

            for label, budget in budgets:
                p95 = _pct(results[label], 0.95)
                assert p95 <= budget, f"{label}: p95 {p95:.1f} ms > budget {budget:.0f} ms"
            assert rss_gb < MAX_RSS_GB, f"client peak RSS {rss_gb:.2f} GiB"
        finally:
            conn.close()
            su.close()
    except DiskGuardAbort as exc:
        _drop_db(dsn)
        pytest.fail(str(exc))
    finally:
        _drop_db(dsn)


def _dense(pg_schema_factory, end, log):
    """Informational: 10 devices at 5 s on the parcel of a tenant in its own scratch DB (the
    gating DB's old chunks are compressed: 2.10 cannot insert into them)."""
    import psycopg2
    import psycopg2.errors

    if _free() < MIN_FREE_START:
        log(f"dense: skipped, free disk {_free() / 2**30:.1f} GiB < 8 GiB")
        return
    dsn = pg_schema_factory(None)
    su = psycopg2.connect(dsn)
    su.autocommit = True
    conn = None
    try:
        tenant = f"dense_{uuid.uuid4().hex[:8]}"
        t0 = time.time()
        p, direct_urns, rows = _seed(su, tenant, end, n_direct=DENSE_N, n_other=0,
                                     step_direct=DENSE_STEP_S, direct_via_events=False, log=log)
        log(f"\nDENSE SEED rows={rows} seed_s={time.time() - t0:.0f}")
        series_req, latest_req = _requests(p, end)
        conn = _connect_rls(dsn)
        _dense_runs(conn, tenant, series_req, latest_req, direct_urns, log)
    finally:
        if conn is not None:
            conn.close()
        su.close()
        _drop_db(dsn)


def _dense_runs(conn, tenant, series_req, latest_req, direct_urns, log):
    import psycopg2.errors

    for label, req in (("dense_series_30d_PT1H", series_req), ("dense_latest", latest_req)):
        try:
            ms, out = _measure(conn, tenant, req, DENSE_RUNS, None, log, label)
        except psycopg2.errors.QueryCanceled:
            conn.rollback()
            pytest.fail(f"{label}: cancelled by the production 10 s statement timeout")
        pts = [pt for g in out["groups"] for pt in g["points"] if pt["value"] is not None]
        assert pts and {s["id"] for pt in pts for s in pt["sources"]} == set(direct_urns)
        _slowest_statements(conn, tenant, req, log, label)
