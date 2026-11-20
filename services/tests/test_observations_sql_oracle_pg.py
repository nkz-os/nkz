"""pg_integration: differential oracle of the SQL-aggregated read path (contract 8.9, 8.10).

`best()` through the SQL-aggregated fetch must give the same response as `best()` through the
raw-row fetch (`observed_tiers(..., aggregate_in_sql=False)`, the pre-8.10 path), field by field:
values with relative tolerance 1e-9, everything else exact (coverage, n, sources, tier, fidelity,
warning strings and their order).

Seed (fixed random seed): sources in tiers 1, 2 and 3 (incl. a mobile device whose rows fall in
tiers 2, 3 and outside), heights 2.0 / 2.1 / 2.3 (one cluster) and 3.0 (another), a depth
sub-context, a group whose readings are all out of range, out-of-range readings, duplicate
(source, t) rows, calibrated and uncalibrated rows (telemetry_events linked by source_event_id),
rows the reader must ignore (other medium, suspect), outages so the fall-through reaches tiers 2
and 3, and compressed chunks. Requests: P1D over the Europe/Madrid DST days (25 h and 23 h) and in
Asia/Kolkata, PT1H, PT15M, a sub-context filter, a geometry request, a `now` inside the current
day; `latest` at several instants (SQL newest-row lookup vs raw rows, same 8.9 semantics).
"""
import functools
import json
import math
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

_SVC = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_READER = os.path.join(_SVC, "timeseries-reader")
for _p in (_SVC, _READER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytestmark = pytest.mark.pg_integration

UTC = timezone.utc
SEED = 1616
STEP = timedelta(minutes=5)
RANGES = [(datetime(2026, 10, 19, tzinfo=UTC), datetime(2026, 10, 28, 6, tzinfo=UTC)),
          (datetime(2026, 3, 26, 12, tzinfo=UTC), datetime(2026, 3, 31, tzinfo=UTC))]
NOW = datetime(2026, 10, 27, 13, 20, tzinfo=UTC)
LON0, LAT0, LON1, LAT1 = -1.6000, 42.8000, -1.5980, 42.8020
PARCEL = {"type": "Polygon", "coordinates": [[[LON0, LAT0], [LON0, LAT1], [LON1, LAT1],
                                               [LON1, LAT0], [LON0, LAT0]]]}
ZONE = {"type": "Polygon", "coordinates": [[[-1.5995, 42.8005], [-1.5995, 42.8010],
                                             [-1.5990, 42.8010], [-1.5990, 42.8005],
                                             [-1.5995, 42.8005]]]}
FAR_ZONE = {"type": "Polygon", "coordinates": [[[-1.5880, 42.8005], [-1.5880, 42.8010],
                                                 [-1.5875, 42.8010], [-1.5875, 42.8005],
                                                 [-1.5880, 42.8005]]]}
INSIDE = (-1.5992, 42.8008)
EAST_600M = (-1.5980 + 0.0073, 42.8010)   # ~600 m east of the parcel edge
EAST_400M = (-1.5980 + 0.0049, 42.8012)
EAST_900M = (-1.5980 + 0.0110, 42.8008)
AWAY_5KM = (-1.5390, 42.8010)

# Tier-1 outages (all tier-1 devices) and tier-2 outages (overlapping): the fall-through has to
# use tiers 2 and 3 there. The Oct 25 one is the repeated DST hour in Madrid.
T1_OUT = [(datetime(2026, 10, 21, 3, tzinfo=UTC), datetime(2026, 10, 21, 10, tzinfo=UTC)),
          (datetime(2026, 10, 25, 0, 10, tzinfo=UTC), datetime(2026, 10, 25, 2, 40, tzinfo=UTC)),
          (datetime(2026, 10, 26, 12, tzinfo=UTC), datetime(2026, 10, 26, 17, 30, tzinfo=UTC)),
          (datetime(2026, 3, 29, 0, 30, tzinfo=UTC), datetime(2026, 3, 29, 6, tzinfo=UTC))]
T1_OUT_NO_DST = [o for o in T1_OUT if o[0].date() != datetime(2026, 10, 25).date()]
T2_OUT = [(datetime(2026, 10, 21, 4, tzinfo=UTC), datetime(2026, 10, 21, 9, tzinfo=UTC)),
          (datetime(2026, 10, 25, 1, tzinfo=UTC), datetime(2026, 10, 25, 1, 50, tzinfo=UTC)),
          (datetime(2026, 10, 26, 15, tzinfo=UTC), datetime(2026, 10, 26, 15, 40, tzinfo=UTC))]


def _pt(lonlat):
    return f"SRID=4326;POINT({lonlat[0]} {lonlat[1]})"


def _devices(tenant):
    p = f"urn:ngsi-ld:AgriParcel:{tenant}:p1"
    z = f"urn:ngsi-ld:AgriParcelZone:{tenant}:z1"
    zf = f"urn:ngsi-ld:AgriParcelZone:{tenant}:zfar"
    sub2 = {"heightAboveGround": 2.0}
    # key: (feature, location (None = per row), context, kind, outages, all_out_of_range, mobility)
    devs = {
        "t1a": (p, INSIDE, dict(sub2), "device", T1_OUT, False, "fixed"),
        "t1b": (p, INSIDE, {"heightAboveGround": 2.3}, "device", T1_OUT, False, "fixed"),
        # t1c reads through the repeated DST hour (both passes): a grain that merges them loses
        # one coverage hour on the 25 h day
        "t1c": (p, INSIDE, {"heightAboveGround": 3.0}, "device", T1_OUT_NO_DST, False, "fixed"),
        "t1d": (p, INSIDE, {"depthFrom": 20, "depthTo": 40}, "manual", T1_OUT, False, "fixed"),
        "t1e": (p, INSIDE, {"heightAboveGround": 7.0}, "device", [], True, "fixed"),
        "t2a": (z, INSIDE, dict(sub2), "device", T2_OUT, False, "fixed"),
        "t2b": (None, INSIDE, {"heightAboveGround": 2.1}, "device", T2_OUT, False, "fixed"),
        "t3a": (None, EAST_600M, dict(sub2), "device", [], False, "fixed"),
        "t3b": (zf, EAST_900M, dict(sub2), "device", [], False, "fixed"),
        "mob": (None, None, dict(sub2), "device", T2_OUT, False, "mobile"),
    }
    return p, z, zf, devs


def _seed(su, tenant):
    from psycopg2.extras import execute_values

    rng = random.Random(SEED)
    p, z, zf, devs = _devices(tenant)
    with su.cursor() as cur:
        for urn, geo in ((p, PARCEL), (z, ZONE), (zf, FAR_ZONE)):
            cur.execute("INSERT INTO observation_geometry_cache VALUES "
                        "(%s, %s, 'AgriParcel', ST_GeomFromGeoJSON(%s)::geography, now())",
                        (tenant, urn, json.dumps(geo)))
        cur.execute(
            "INSERT INTO sensor_profiles (tenant_id, code, name, sdm_entity_type, mapping) "
            "VALUES (%s, %s, 'p', 'Device', %s::jsonb)",
            (tenant, f"prof_{tenant}", json.dumps({"measurements": [
                {"sdmAttribute": "airTemperature", "observedProperty": "airTemperature"}]})))
        deps = {}
        for k, (feat, loc, ctx, kind, _o, _b, mobility) in devs.items():
            cur.execute(
                "INSERT INTO device_deployments (tenant_id, device_urn, valid, observes_feature,"
                " observation_context, location, mobility, created_by) VALUES (%s, %s,"
                " tstzrange(%s, NULL), %s, %s::jsonb, %s::geography, %s, 'oracle') RETURNING id::text",
                (tenant, f"urn:ngsi-ld:Device:{tenant}:{k}", datetime(2026, 1, 1, tzinfo=UTC), feat,
                 json.dumps(dict(ctx, medium="ambientAir")), _pt(loc) if loc else None, mobility))
            deps[k] = cur.fetchone()[0]

        rows, events = [], []
        eid = 0
        for di, (k, (feat, loc, ctx, kind, outages, all_bad, _m)) in enumerate(devs.items()):
            for lo, hi in RANGES:
                t = lo + timedelta(seconds=7 * di + 1)  # distinct instants across devices
                while t < hi:
                    t_cur = t
                    t += STEP + timedelta(seconds=rng.randint(-20, 20))
                    if any(a <= t_cur < b for a, b in outages) or rng.random() < 0.08:
                        continue
                    v = round(rng.uniform(-5, 35), 3)
                    if all_bad or rng.random() < 0.04:
                        v = rng.choice([-99.5, 99.5, 61.0])
                    if loc is None:  # mobile: inside, nearby, far
                        here = rng.choice([INSIDE, EAST_400M, AWAY_5KM])
                    else:
                        here = loc
                    flag, medium = "valid", "ambientAir"
                    r = rng.random()
                    if r < 0.01:
                        flag = "suspect"
                    elif r < 0.02:
                        medium = "ambientAirUnderStructure"
                    n_dup = 2 if rng.random() < 0.03 else 1
                    for j in range(n_dup):
                        eid += 1
                        vv = v if j == 0 else round(v + rng.uniform(0.5, 3), 3)
                        rows.append((tenant, t_cur, eid, f"urn:ngsi-ld:DeviceMeasurement:{tenant}:{k}:"
                                     "airTemperature", "DeviceMeasurement", k, "airTemperature", vv,
                                     "airTemperature", feat, medium, ctx.get("depthFrom"),
                                     ctx.get("depthTo"), ctx.get("heightAboveGround"),
                                     deps[k], kind, _pt(here), flag))
                        # t1c: every reading calibrated (its group has calibrated points);
                        # the others: some readings, so buckets mix both
                        if k == "t1c" or rng.random() < 0.5:
                            payload = {"raw": {"id": k}}
                            if k == "t1c" or rng.random() < 0.6:
                                payload["calibration_period_ids"] = {"airTemperature": "c1"}
                            events.append((tenant, t_cur, eid, k, json.dumps(payload)))
        execute_values(cur, (
            "INSERT INTO telemetry_measurements (tenant_id, observed_at, source_event_id,"
            " entity_id, entity_type, device_id, attribute_name, value, observed_property,"
            " feature_of_interest, medium, depth_from_cm, depth_to_cm, height_m, deployment_id,"
            " source_kind, location, quality_flag) VALUES %s"),
            rows, template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::uuid,%s,%s::geography,%s)",
            page_size=2000)
        # payload without 'measurements': the 115 trigger writes nothing for these events
        execute_values(cur, "INSERT INTO telemetry_events (tenant_id, observed_at, id, device_id,"
                       " payload) VALUES %s", events, template="(%s,%s,%s,%s,%s::jsonb)",
                       page_size=2000)
        cur.execute("SELECT count(*) FROM (SELECT compress_chunk(c, if_not_compressed => true)"
                    " FROM show_chunks('telemetry_measurements',"
                    " older_than => '2026-10-23'::timestamptz) c) x")
        cur.execute("SELECT count(*) FILTER (WHERE is_compressed), count(*) FROM"
                    " timescaledb_information.chunks WHERE hypertable_name = 'telemetry_measurements'")
        comp, total = cur.fetchone()
        assert comp >= 2 and total > comp, (comp, total)  # compressed + uncompressed chunks
        cur.execute("ANALYZE telemetry_measurements")
    return p, z, len(rows)


@pytest.fixture(scope="module")
def oracle_dsn(pg_schema_factory):
    """Own database: the seed compresses chunks, which would block other tests' inserts."""
    return pg_schema_factory(None)


@pytest.fixture(scope="module")
def bench(oracle_dsn):
    import psycopg2

    pg_dsn = oracle_dsn
    su = psycopg2.connect(pg_dsn)
    su.autocommit = True
    try:
        tenant = f"orc_{uuid.uuid4().hex[:8]}"
        p, z, n = _seed(su, tenant)
    finally:
        su.close()
    return {"dsn": pg_dsn, "tenant": tenant, "p": p, "z": z, "rows": n}


def _best(bench, body, *, sql: bool, now=NOW):
    """`best()` as nkz_rls_test; returns (response, executed statement tags)."""
    import psycopg2

    from observations import best as best_mod
    from observations import db as obs_db
    from observations import sources
    from observations.request import parse_best

    tags = []
    real_cls = obs_db.RealDictCursor

    class Recording(real_cls):
        def execute(self, q, args=None):
            tags.append(q.split("\n", 1)[0].strip())
            return super().execute(q, args)

    conn = psycopg2.connect(bench["dsn"])
    real_observed = best_mod.observed_tiers
    try:
        with conn.cursor() as c:
            c.execute('SET ROLE "nkz_rls_test"')
        conn.commit()
        obs_db.RealDictCursor = Recording
        if not sql:
            best_mod.observed_tiers = functools.partial(sources.observed_tiers,
                                                        aggregate_in_sql=False)
        out = best_mod.best(conn, bench["tenant"], parse_best(body), now=now,
                            fetch=lambda t, u: ("ok", {"id": u, "type": "AgriParcel"}))
    finally:
        obs_db.RealDictCursor = real_cls
        best_mod.observed_tiers = real_observed
        conn.close()
    return out, tags


def _assert_same(new, old, path="$"):
    if isinstance(old, float) and isinstance(new, (int, float)) and not isinstance(new, bool):
        assert math.isclose(new, old, rel_tol=1e-9, abs_tol=0.0), f"{path}: {new} != {old}"
    elif isinstance(old, dict):
        assert isinstance(new, dict) and list(new) == list(old), f"{path}: keys {list(new)} != {list(old)}"
        for k in old:
            _assert_same(new[k], old[k], f"{path}.{k}")
    elif isinstance(old, list):
        assert isinstance(new, list) and len(new) == len(old), f"{path}: len {len(new)} != {len(old)}"
        for i, (a, b) in enumerate(zip(new, old)):
            _assert_same(a, b, f"{path}[{i}]")
    else:
        assert new == old, f"{path}: {new!r} != {old!r}"


def _body(bench, **kw):
    base = {"feature": bench["p"], "property": "airTemperature"}
    base.update(kw)
    return base


SERIES_CASES = {
    "P1D_madrid_25h_day_now_inside": dict(resolution="P1D", tz="Europe/Madrid",
                                          **{"from": "2026-10-19T22:00:00Z", "to": "2026-10-27T23:00:00Z"}),
    "P1D_kolkata": dict(resolution="P1D", tz="Asia/Kolkata",
                        **{"from": "2026-10-19T18:30:00Z", "to": "2026-10-27T18:30:00Z"}),
    "P1D_madrid_23h_day": dict(resolution="P1D", tz="Europe/Madrid",
                               **{"from": "2026-03-26T23:00:00Z", "to": "2026-03-30T22:00:00Z"}),
    "PT1H": dict(resolution="PT1H", **{"from": "2026-10-19T00:00:00Z", "to": "2026-10-28T06:00:00Z"}),
    "PT15M": dict(resolution="PT15M", **{"from": "2026-10-24T12:00:00Z", "to": "2026-10-26T12:00:00Z"}),
    "PT1H_subcontext_height": dict(resolution="PT1H", subContext={"height": 2.0},
                                   **{"from": "2026-10-20T00:00:00Z", "to": "2026-10-23T00:00:00Z"}),
}


@pytest.mark.parametrize("case", list(SERIES_CASES))
def test_series_sql_path_equals_raw_path(bench, case):
    body = _body(bench, **SERIES_CASES[case])
    new, tags_new = _best(bench, body, sql=True)
    old, tags_old = _best(bench, body, sql=False)
    # the comparison is not vacuous
    assert "-- obs:series" in tags_new and "-- obs:values" not in tags_new
    assert "-- obs:values" in tags_old and "-- obs:series" not in tags_old
    pts = [pt for g in old["groups"] for pt in g["points"] if pt["value"] is not None]
    assert len(pts) > (5 if body["resolution"] == "P1D" else 50)
    assert any("reading_out_of_range" in w for w in old["warnings"])
    if case != "PT1H_subcontext_height":
        assert len(old["groups"]) == 4  # heights 2.0 cluster, 3.0, 7.0 (all out of range), depth
        assert any(s["n"] > 1 for pt in pts for s in pt["sources"])
        assert any(pt["calibrated"] for pt in pts) and not all(pt["calibrated"] for pt in pts)
    if case in ("P1D_madrid_25h_day_now_inside", "PT1H", "PT15M"):
        assert old["tiersUsed"] == [1, 2, 3], old["tiersUsed"]
    _assert_same(new, old)


def test_series_geometry_request_equals_raw_path(bench):
    body = {"geometry": PARCEL, "property": "airTemperature", "resolution": "PT1H",
            "from": "2026-10-20T00:00:00Z", "to": "2026-10-23T00:00:00Z"}
    new, _ = _best(bench, body, sql=True)
    old, _ = _best(bench, body, sql=False)
    assert old["tiersUsed"] == [2, 3]
    _assert_same(new, old)


@pytest.mark.parametrize("now", [NOW, datetime(2026, 10, 21, 7, 30, tzinfo=UTC),
                                 datetime(2026, 10, 25, 2, 0, tzinfo=UTC),
                                 datetime(2026, 3, 29, 5, 0, tzinfo=UTC)])
def test_latest_sql_path_equals_raw_path(bench, now):
    body = _body(bench, mode="latest", **{"from": "2026-10-01T00:00:00Z", "to": "2026-10-02T00:00:00Z"})
    new, tags_new = _best(bench, body, sql=True, now=now)
    old, tags_old = _best(bench, body, sql=False, now=now)
    assert "-- obs:latest" in tags_new and "-- obs:values" not in tags_new
    pts = [pt for g in old["groups"] for pt in g["points"]]
    assert pts and all(len(g["points"]) <= 1 for g in old["groups"])
    _assert_same(new, old)


def test_latest_fallthrough_reaches_tier2_during_tier1_outage(bench):
    body = _body(bench, mode="latest", **{"from": "2026-10-01T00:00:00Z", "to": "2026-10-02T00:00:00Z"})
    out, _ = _best(bench, body, sql=True, now=datetime(2026, 10, 21, 10, 0, tzinfo=UTC))
    (g,) = [g for g in out["groups"] if g["subContext"] == {"heightM": 2.0}]
    (pt,) = g["points"]
    assert pt["tier"] == 2 and len(pt["sources"]) >= 2
    assert all(s["n"] == 1 for s in pt["sources"])


# ------------------------------------------------- latest SQL edge cases (8.9)

def test_latest_sql_multi_subcontext_and_two_channel_datalogger(oracle_dsn):
    """One deployment, several stored series: heights 2 m / 5 m (the 5 m row is older), and two
    channels on two trees. Every series keeps its own newest row (none is lost to LIMIT 1)."""
    import psycopg2

    tenant = f"orc_{uuid.uuid4().hex[:8]}"
    t1 = f"urn:ngsi-ld:AgriTree:{tenant}:t1"
    t2 = f"urn:ngsi-ld:AgriTree:{tenant}:t2"
    urn = f"urn:ngsi-ld:Device:{tenant}:log"
    trees = {t1: {"type": "Polygon", "coordinates": [[[-1.5997, 42.8012], [-1.5997, 42.8013],
                                                      [-1.5996, 42.8013], [-1.5996, 42.8012],
                                                      [-1.5997, 42.8012]]]},
             t2: {"type": "Polygon", "coordinates": [[[-1.5985, 42.8012], [-1.5985, 42.8013],
                                                      [-1.5984, 42.8013], [-1.5984, 42.8012],
                                                      [-1.5985, 42.8012]]]}}
    su = psycopg2.connect(oracle_dsn)
    su.autocommit = True
    try:
        with su.cursor() as cur:
            for u in (t1, t2):
                cur.execute("INSERT INTO observation_geometry_cache VALUES "
                            "(%s, %s, 'AgriTree', ST_GeomFromGeoJSON(%s)::geography, now())",
                            (tenant, u, json.dumps(trees[u])))
            cur.execute(
                "INSERT INTO sensor_profiles (tenant_id, code, name, sdm_entity_type, mapping) "
                "VALUES (%s, %s, 'p', 'Device', %s::jsonb)",
                (tenant, f"prof_{tenant}", json.dumps({"measurements": [
                    {"sdmAttribute": "airTemperature", "observedProperty": "airTemperature"},
                    {"sdmAttribute": "airTemperature2", "observedProperty": "airTemperature"}]})))
            cur.execute(
                "INSERT INTO device_deployments (tenant_id, device_urn, valid, observation_context,"
                " channel_overrides, created_by) VALUES (%s, %s, tstzrange(%s, NULL), %s::jsonb,"
                " %s::jsonb, 'test') RETURNING id::text",
                (tenant, urn, datetime(2026, 1, 1, tzinfo=UTC), json.dumps({"medium": "ambientAir"}),
                 json.dumps({"airTemperature": {"observesFeature": t1},
                             "airTemperature2": {"observesFeature": t2}})))
            dep = cur.fetchone()[0]
            rows = [  # (minutes before NOW, attribute, feature, height, value)
                (40, "airTemperature", t1, 2.0, 10.0), (5, "airTemperature", t1, 2.0, 11.0),
                (30, "airTemperature", t1, 5.0, 7.0), (50, "airTemperature", t1, 5.0, 6.0),
                (20, "airTemperature2", t2, 2.0, 20.0), (2, "airTemperature2", t2, 2.0, 99.0),
            ]
            for i, (m, attr, feat, h, v) in enumerate(rows):
                cur.execute(
                    "INSERT INTO telemetry_measurements (tenant_id, observed_at, source_event_id,"
                    " entity_type, device_id, attribute_name, value, observed_property, medium,"
                    " height_m, deployment_id, source_kind, quality_flag, feature_of_interest)"
                    " VALUES (%s, %s, %s, 'DeviceMeasurement', 'log', %s, %s, 'airTemperature',"
                    " 'ambientAir', %s, %s, 'device', 'valid', %s)",
                    (tenant, NOW - timedelta(minutes=m), i + 1, attr, v, h, dep, feat))
    finally:
        su.close()
    bench_ = {"dsn": oracle_dsn, "tenant": tenant}
    body = {"feature": t1, "property": "airTemperature", "mode": "latest",
            "from": "2026-10-01T00:00:00Z", "to": "2026-10-02T00:00:00Z"}
    out, tags = _best(bench_, body, sql=True)
    assert "-- obs:latest" in tags
    got = [(g["subContext"], [(p["value"], p["t"], p["tier"]) for p in g["points"]])
           for g in out["groups"]]
    assert got == [({"heightM": 2.0}, [(11.0, "2026-10-27T13:15:00Z", 1)]),
                   ({"heightM": 5.0}, [(7.0, "2026-10-27T12:50:00Z", 1)])]
    out, _ = _best(bench_, dict(body, feature=t2), sql=True)
    (g,) = out["groups"]
    # the newest t2 row is out of range: its previous valid row is used, the oor one is reported
    assert [(p["value"], p["t"]) for p in g["points"]] == [(20.0, "2026-10-27T13:00:00Z")]
    assert out["warnings"] == [f"reading_out_of_range src={urn} (2026-10-27T13:18:00Z)"]


def test_latest_mobile_device_5s_equals_raw_path_and_is_not_quadratic(oracle_dsn):
    """Regression (Task 16b review): a mobile deployment stores a distinct point per reading.
    Latest must not re-scan per location (it was O(n^2): 4.2 s at 5 s cadence). 3 h at 5 s =
    2160 rows wandering inside the parcel, 400 m away and 5 km away; equal to the raw path.
    The 150 ms latest budget is gated in test_observations_perf_pg.py, not here. The timing
    assert below is only a flake-proof regression guard: the O(n^2) version measured 4.2 s, the
    fixed one 50-200 ms depending on host load, so median <= 1500 ms catches the former only."""
    import statistics
    import time

    import psycopg2

    tenant = f"mob_{uuid.uuid4().hex[:8]}"
    p = f"urn:ngsi-ld:AgriParcel:{tenant}:p1"
    urn = f"urn:ngsi-ld:Device:{tenant}:mob"
    su = psycopg2.connect(oracle_dsn)
    su.autocommit = True
    try:
        with su.cursor() as cur:
            cur.execute("INSERT INTO observation_geometry_cache VALUES "
                        "(%s, %s, 'AgriParcel', ST_GeomFromGeoJSON(%s)::geography, now())",
                        (tenant, p, json.dumps(PARCEL)))
            cur.execute(
                "INSERT INTO sensor_profiles (tenant_id, code, name, sdm_entity_type, mapping) "
                "VALUES (%s, %s, 'p', 'Device', %s::jsonb)",
                (tenant, f"prof_{tenant}", json.dumps({"measurements": [
                    {"sdmAttribute": "airTemperature", "observedProperty": "airTemperature"}]})))
            cur.execute(
                "INSERT INTO device_deployments (tenant_id, device_urn, valid, observation_context,"
                " mobility, created_by) VALUES (%s, %s, tstzrange(%s, NULL), %s::jsonb, 'mobile',"
                " 'test') RETURNING id::text",
                (tenant, urn, datetime(2026, 1, 1, tzinfo=UTC),
                 json.dumps({"medium": "ambientAir", "heightAboveGround": 2})))
            dep = cur.fetchone()[0]
            # n = 1 is the newest; the device wanders: inside P (n % 3 == 0), ~400 m east
            # (n % 3 == 1, tier 3), ~5 km east (n % 3 == 2, no tier); every 7th reading is out of
            # range. The newest valid tier-2 row is older than the newest tier-3 row.
            cur.execute(
                "INSERT INTO telemetry_measurements (tenant_id, observed_at, source_event_id,"
                " entity_id, entity_type, device_id, attribute_name, value, observed_property,"
                " medium, height_m, deployment_id, source_kind, location, quality_flag)"
                " SELECT %s, %s::timestamptz - g.n * interval '5 seconds', g.n, %s,"
                " 'DeviceMeasurement', 'mob', 'airTemperature',"
                " CASE WHEN g.n %% 7 = 0 THEN 99.5 ELSE 15 + (g.n %% 50) / 10.0 END,"
                " 'airTemperature', 'ambientAir', 2, %s::uuid, 'device',"
                " ST_SetSRID(ST_MakePoint(CASE g.n %% 3 WHEN 0 THEN -1.5995 + g.n * 1e-8"
                "   WHEN 1 THEN -1.5931 + g.n * 1e-8 ELSE -1.5390 END, 42.8008), 4326)::geography,"
                " 'valid' FROM generate_series(1, 2160) g(n)",
                (tenant, NOW, f"urn:ngsi-ld:DeviceMeasurement:{tenant}:mob:airTemperature", dep))
            cur.execute("ANALYZE telemetry_measurements")
    finally:
        su.close()
    bench_ = {"dsn": oracle_dsn, "tenant": tenant}
    body = {"feature": p, "property": "airTemperature", "mode": "latest",
            "from": "2026-10-01T00:00:00Z", "to": "2026-10-02T00:00:00Z"}
    new, tags = _best(bench_, body, sql=True)
    old, _ = _best(bench_, body, sql=False)
    assert "-- obs:latest" in tags
    (g,) = old["groups"]
    (pt,) = g["points"]
    assert pt["tier"] == 2 and pt["sources"][0]["n"] == 1  # the older tier-2 row wins
    assert any("reading_out_of_range" in w for w in old["warnings"])
    _assert_same(new, old)
    ms = []
    for _ in range(6):
        t0 = time.perf_counter()
        _best(bench_, body, sql=True)
        ms.append((time.perf_counter() - t0) * 1000)
    assert statistics.median(ms[1:]) <= 1500, f"latest mobile 5 s: {[round(x) for x in ms]} ms"
