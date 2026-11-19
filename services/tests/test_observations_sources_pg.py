"""pg_integration: tier planning and fetch of the `best` service against a seeded bench.

Runs the reader code as nkz_rls_test (FORCE RLS tables, no BYPASSRLS). Seed rows are written as
the superuser. Each test uses its own tenant.
"""
import json
import os
import sys
import uuid
from datetime import datetime, timezone

import pytest

_SVC = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_READER = os.path.join(_SVC, "timeseries-reader")
for _p in (_SVC, _READER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytestmark = pytest.mark.pg_integration

UTC = timezone.utc
T0 = datetime(2026, 11, 1, 0, tzinfo=UTC)
T1 = datetime(2026, 11, 2, 0, tzinfo=UTC)
READ_AT = datetime(2026, 11, 1, 6, tzinfo=UTC)
# parcel ~ 150 m x 220 m near Pamplona
LON0, LAT0, LON1, LAT1 = -1.6000, 42.8000, -1.5980, 42.8020
PARCEL = {"type": "Polygon", "coordinates": [[[LON0, LAT0], [LON0, LAT1], [LON1, LAT1],
                                               [LON1, LAT0], [LON0, LAT0]]]}
ZONE = {"type": "Polygon", "coordinates": [[[-1.5995, 42.8005], [-1.5995, 42.8010],
                                             [-1.5990, 42.8010], [-1.5990, 42.8005],
                                             [-1.5995, 42.8005]]]}


def _point_sql(lon, lat):
    return f"ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326)::geography"


def _seed(su, tenant):
    p = f"urn:ngsi-ld:AgriParcel:{tenant}:p1"
    z = f"urn:ngsi-ld:AgriParcelZone:{tenant}:z1"
    devs = {k: f"urn:ngsi-ld:Device:{tenant}:{k}" for k in ("d1", "d2", "d3")}
    with su.cursor() as cur:
        for urn, typ, geo in ((p, "AgriParcel", PARCEL), (z, "AgriParcelZone", ZONE)):
            cur.execute(
                "INSERT INTO observation_geometry_cache VALUES "
                "(%s, %s, %s, ST_GeomFromGeoJSON(%s)::geography, now())",
                (tenant, urn, typ, json.dumps(geo)))
        cur.execute(
            "INSERT INTO sensor_profiles (tenant_id, code, name, sdm_entity_type, mapping) "
            "VALUES (%s, %s, 'p', 'Device', %s::jsonb)",
            (tenant, f"prof_{tenant}", json.dumps({"measurements": [
                {"sdmAttribute": "temperature", "observedProperty": "airTemperature"}]})))
        mid_lat = (LAT0 + LAT1) / 2
        locs = {
            "d1": None,
            "d2": _point_sql(-1.5990, 42.8015),
            # 500 m due east of the parcel's east edge
            "d3": f"ST_Project({_point_sql(LON1, mid_lat)}, 500, radians(90))",
        }
        ctxs = {"d1": {"medium": "ambientAir", "heightAboveGround": 2},
                "d2": {"medium": "ambientAirUnderStructure"},
                "d3": {"medium": "ambientAir"}}
        feats = {"d1": z, "d2": None, "d3": None}
        dep_ids = {}
        for k, urn in devs.items():
            cur.execute(
                "INSERT INTO device_deployments (tenant_id, device_urn, valid, observes_feature,"
                " observation_context, location, created_by) VALUES "
                f"(%s, %s, tstzrange(%s, NULL), %s, %s::jsonb, {locs[k] or 'NULL'}, 'test') "
                "RETURNING id::text",
                (tenant, urn, datetime(2026, 1, 1, tzinfo=UTC), feats[k], json.dumps(ctxs[k])))
            dep_ids[k] = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO telemetry_measurements (tenant_id, observed_at, source_event_id,"
                " entity_type, device_id, attribute_name, value, observed_property, medium,"
                " height_m, deployment_id, source_kind, quality_flag, feature_of_interest, location)"
                " VALUES (%s, %s, %s, 'DeviceMeasurement', %s, 'airTemperature', %s,"
                f" 'airTemperature', %s, %s, %s, 'device', 'valid', %s, {locs[k] or 'NULL'})",
                (tenant, READ_AT, int(k[1]), k, 10.0 + int(k[1]), ctxs[k]["medium"],
                 2.0 if k == "d1" else None, dep_ids[k], feats[k]))
    return p, z, devs, dep_ids


def _run(pg_dsn, tenant, feature, p_urn, model_point=None, prop_over=None):
    import psycopg2

    from observations.catalogue import get_property
    from observations.db import tenant_cursor
    from observations.geometry import geometry_of
    from observations.request import BestRequest
    from observations.sources import observed_tiers, resolve_medium

    def no_orion(t, e):
        raise AssertionError("geometry must come from the fresh cache")

    conn = psycopg2.connect(pg_dsn)
    try:
        with conn.cursor() as c:
            c.execute('SET ROLE "nkz_rls_test"')
        # geometries first, own short transaction, committed (Task 11 ruling)
        with tenant_cursor(conn, tenant) as cur:
            area = geometry_of(cur, tenant, feature, fetch=no_orion)
        conn.commit()
        req = BestRequest(feature=feature, geometry=None, property="airTemperature",
                          start=T0, end=T1, mode="series", resolution="PT1H", tz="UTC", medium=None)
        with tenant_cursor(conn, tenant) as cur:
            prop = dict(get_property(cur, tenant, "airTemperature"))
            prop["nearby_radius_m"] = 1000
            medium = resolve_medium(req, prop, None)
            out = observed_tiers(cur, tenant, req, prop, medium, area)
            if model_point is not None:
                from observations.sources import model_tiers
                prop["has_model"] = True
                out = dict(out)
                out.update(model_tiers(cur, tenant, req, prop, None, model_point))
        conn.rollback()
        return out
    finally:
        conn.close()


@pytest.fixture
def su(pg_conn):
    return pg_conn(None)


def test_tiers_against_seeded_bench(pg_dsn, su):
    tenant = f"t_{uuid.uuid4().hex[:8]}"
    p, z, devs, dep_ids = _seed(su, tenant)

    # feature = Z -> tier 1 = D1; D3 (east of P, Z inside P) is nearby, ~580 m from Z
    out_z = _run(pg_dsn, tenant, z, p)
    assert [r.source_id for r in out_z[1]] == [devs["d1"]]
    assert out_z[1][0].sub_context == {"heightAboveGround": 2.0}
    assert out_z[1][0].deployment_id == dep_ids["d1"]
    assert [r.source_id for r in out_z[3]] == [devs["d3"]]
    assert 500 < out_z[3][0].distance_m < 700
    assert devs["d2"] not in {r.source_id for t in out_z.values() for r in t}

    # feature = P -> tier 2 = D1 only (D2 is another medium), D3 is tier 3 at ~500 m
    out_p = _run(pg_dsn, tenant, p, p)
    assert out_p[1] == []
    assert [r.source_id for r in out_p[2]] == [devs["d1"]]
    assert [r.source_id for r in out_p[3]] == [devs["d3"]]
    assert out_p[3][0].distance_m == pytest.approx(500, abs=5)
    assert out_p[2][0].distance_m is None
    assert devs["d2"] not in {r.source_id for t in out_p.values() for r in t}


def test_other_tenant_sees_nothing(pg_dsn, su):
    tenant = f"t_{uuid.uuid4().hex[:8]}"
    other = f"t_{uuid.uuid4().hex[:8]}"
    p, z, _, _ = _seed(su, tenant)
    with su.cursor() as cur:  # the other tenant caches the same geometry, owns no devices
        cur.execute(
            "INSERT INTO observation_geometry_cache VALUES "
            "(%s, %s, 'AgriParcel', ST_GeomFromGeoJSON(%s)::geography, now())",
            (other, p, json.dumps(PARCEL)))
    out = _run(pg_dsn, other, p, p)
    assert out == {1: [], 2: [], 3: []}


TREE1 = {"type": "Polygon", "coordinates": [[[-1.5997, 42.8012], [-1.5997, 42.8013],
                                              [-1.5996, 42.8013], [-1.5996, 42.8012],
                                              [-1.5997, 42.8012]]]}
TREE2 = {"type": "Polygon", "coordinates": [[[-1.5985, 42.8012], [-1.5985, 42.8013],
                                              [-1.5984, 42.8013], [-1.5984, 42.8012],
                                              [-1.5985, 42.8012]]]}


def test_datalogger_two_channels_two_trees(pg_dsn, su):
    tenant = f"t_{uuid.uuid4().hex[:8]}"
    t1 = f"urn:ngsi-ld:AgriTree:{tenant}:t1"
    t2 = f"urn:ngsi-ld:AgriTree:{tenant}:t2"
    urn = f"urn:ngsi-ld:Device:{tenant}:log"
    with su.cursor() as cur:
        for u, g in ((t1, TREE1), (t2, TREE2)):
            cur.execute("INSERT INTO observation_geometry_cache VALUES "
                        "(%s, %s, 'AgriTree', ST_GeomFromGeoJSON(%s)::geography, now())",
                        (tenant, u, json.dumps(g)))
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
        dep_id = cur.fetchone()[0]
        for i, (attr, feat, val) in enumerate((("airTemperature", t1, 10.0),
                                               ("airTemperature2", t2, 20.0))):
            cur.execute(
                "INSERT INTO telemetry_measurements (tenant_id, observed_at, source_event_id,"
                " entity_type, device_id, attribute_name, value, observed_property, medium,"
                " deployment_id, source_kind, quality_flag, feature_of_interest) VALUES "
                "(%s, %s, %s, 'DeviceMeasurement', 'log', %s, %s, 'airTemperature', 'ambientAir',"
                " %s, 'device', 'valid', %s)", (tenant, READ_AT, i + 1, attr, val, dep_id, feat))
    out = _run(pg_dsn, tenant, t1, None)
    assert [r.value for r in out[1]] == [10.0]
    assert out[2] == [] and out[3] == []
    out = _run(pg_dsn, tenant, t2, None)
    assert [r.value for r in out[1]] == [20.0]


def _weather_event(cur, tenant, entity_id, lon, lat, temp, i):
    cur.execute(
        "INSERT INTO telemetry_events (tenant_id, observed_at, entity_id, entity_type, payload)"
        " VALUES (%s, %s, %s, 'WeatherObserved', %s::jsonb)",
        (tenant, READ_AT, entity_id, json.dumps({
            "measurements": {"airTemperature": temp},
            "raw": {"location": {"value": {"type": "Point", "coordinates": [lon, lat]}}}})))


def test_regional_tier5_real_query(pg_dsn, su):
    tenant = f"t_{uuid.uuid4().hex[:8]}"
    p, z, _, _ = _seed(su, tenant)
    centre = ((LON0 + LON1) / 2, (LAT0 + LAT1) / 2)
    with su.cursor() as cur:
        _weather_event(cur, tenant, f"urn:ngsi-ld:WeatherObserved:{tenant}:parcel-p1",
                       centre[0], centre[1], 11.0, 1)
        _weather_event(cur, tenant, f"urn:ngsi-ld:WeatherObserved:{tenant}:parcel-p1-daily",
                       centre[0], centre[1], 12.0, 2)
    out = _run(pg_dsn, tenant, p, p, model_point=centre)
    assert out[5] == []  # only parcel weather exists
    with su.cursor() as cur:
        _weather_event(cur, tenant, f"urn:ngsi-ld:WeatherObserved:{tenant}:station-x",
                       centre[0], centre[1] + 0.027, 7.5, 3)  # ~3 km north
        _weather_event(cur, tenant, f"urn:ngsi-ld:WeatherObserved:{tenant}:station-far",
                       centre[0], centre[1] + 0.5, 1.5, 4)  # ~55 km: outside 10 km
    out = _run(pg_dsn, tenant, p, p, model_point=centre)
    assert [(r.source_id, r.value, r.kind) for r in out[5]] == [("regional_proxy", 7.5, "model")]
    assert out[5][0].distance_m == pytest.approx(3000, abs=100)
