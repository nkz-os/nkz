"""pg_integration: raw observations (keyset paging, flags, calibrated, CSV) as nkz_rls_test."""
import json
import os
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
T0 = datetime(2026, 11, 1, 0, tzinfo=UTC)
PARCEL = {"type": "Polygon", "coordinates": [[[-1.6, 42.8], [-1.6, 42.802], [-1.598, 42.802],
                                               [-1.598, 42.8], [-1.6, 42.8]]]}


@pytest.fixture
def su(pg_conn):
    return pg_conn(None)


def _seed(su, tenant):
    p = f"urn:ngsi-ld:AgriParcel:{tenant}:p1"
    other = f"urn:ngsi-ld:AgriParcel:{tenant}:p2"
    dev = f"urn:ngsi-ld:Device:{tenant}:d1"
    with su.cursor() as cur:
        cur.execute("INSERT INTO observation_geometry_cache VALUES "
                    "(%s, %s, 'AgriParcel', ST_GeomFromGeoJSON(%s)::geography, now())",
                    (tenant, p, json.dumps(PARCEL)))
        cur.execute(
            "INSERT INTO sensor_profiles (tenant_id, code, name, sdm_entity_type, mapping) "
            "VALUES (%s, %s, 'p', 'Device', %s::jsonb)",
            (tenant, f"prof_{tenant}", json.dumps({"measurements": [
                {"sdmAttribute": "temperature", "observedProperty": "airTemperature"}]})))
        cur.execute(
            "INSERT INTO device_deployments (tenant_id, device_urn, valid, observes_feature,"
            " observation_context, created_by) VALUES (%s, %s, tstzrange(%s, NULL), %s,"
            " %s::jsonb, 'test') RETURNING id::text",
            (tenant, dev, datetime(2026, 1, 1, tzinfo=UTC), p, json.dumps({"medium": "ambientAir"})))
        dep = cur.fetchone()[0]
        # events: odd ids calibrated
        for i in range(12):
            at = T0 + timedelta(minutes=i)
            cal = ["airTemperature"] if i % 2 else []
            cur.execute(
                "INSERT INTO telemetry_events (tenant_id, observed_at, id, device_id, entity_id,"
                " entity_type, payload) VALUES (%s, %s, %s, 'd1', %s, 'Device', %s::jsonb)",
                (tenant, at, i + 1, dev, json.dumps({"calibration_period_ids": cal})))
            feat = other if i % 3 == 0 else p          # interleaved rows of another feature
            flag = ["valid", "out_of_range", "suspect"][i % 3] if i < 9 else "valid"
            cur.execute(
                "INSERT INTO telemetry_measurements (tenant_id, observed_at, source_event_id,"
                " entity_type, device_id, attribute_name, value, observed_property, medium,"
                " deployment_id, source_kind, quality_flag, feature_of_interest, time_source,"
                " location) VALUES (%s, %s, %s, 'DeviceMeasurement', 'd1', 'airTemperature', %s,"
                " 'airTemperature', 'ambientAir', %s, 'device', %s, %s, 'device',"
                " ST_SetSRID(ST_MakePoint(%s, 42.801), 4326)::geography)",
                (tenant, at, i + 1, float(i), dep, flag, feat,
                 -1.599 if feat == p else -1.0))  # other-feature rows lie far outside the parcel
    return p, dev


def _plan(conn, tenant, feature):
    from observations import raw as raw_mod

    req = raw_mod.parse_raw({"feature": feature, "property": "airTemperature",
                             "from": "2026-11-01T00:00:00Z", "to": "2026-11-02T00:00:00Z"}).base
    return raw_mod.make_plan(conn, tenant, req,
                             fetch=lambda t, u: ("ok", {"id": u, "type": "AgriParcel"}))


def _conn(pg_dsn):
    import psycopg2

    conn = psycopg2.connect(pg_dsn)
    with conn.cursor() as c:
        c.execute('SET ROLE "nkz_rls_test"')
    conn.commit()
    return conn


def test_raw_paging_flags_calibrated_csv(pg_dsn, su):
    from observations import raw as raw_mod

    tenant = f"t_{uuid.uuid4().hex[:8]}"
    p, dev = _seed(su, tenant)
    conn = _conn(pg_dsn)
    try:
        plan = _plan(conn, tenant, p)
        got, cur_pos, pages = [], None, 0
        while True:
            items, nxt = raw_mod.raw_page(conn, tenant, plan, limit=3, cursor=cur_pos)
            got += items
            pages += 1
            if not nxt:
                break
            cur_pos = raw_mod.decode_cursor(nxt)
        vals = [g["value"] for g in got]
        assert vals == [float(i) for i in range(12) if i % 3 != 0]  # other-feature rows excluded
        assert pages == 3 and all(g["deviceId"] == dev and g["tier"] == 1 for g in got)
        assert [g["calibrated"] for g in got] == [int(v) % 2 == 1 for v in vals]
        assert {g["qualityFlag"] for g in got} == {"valid", "out_of_range", "suspect"}
        assert got[0]["timeSource"] == "device"
        assert got[0]["location"]["coordinates"] == [-1.599, 42.801]

        only, _ = raw_mod.raw_page(conn, tenant, plan, limit=100, quality_flags=("valid",))
        assert {g["qualityFlag"] for g in only} == {"valid"}

        text = "".join(raw_mod.csv_chunks(conn, tenant, plan))
        assert len(text.strip().splitlines()) == 1 + 8

        other = f"t_{uuid.uuid4().hex[:8]}"  # another tenant sees nothing (RLS + tenant filter)
        with su.cursor() as c:  # the other tenant caches the geometry but owns no deployments
            c.execute("INSERT INTO observation_geometry_cache VALUES "
                      "(%s, %s, 'AgriParcel', ST_GeomFromGeoJSON(%s)::geography, now())",
                      (other, p, json.dumps(PARCEL)))
        oplan = _plan(conn, other, p)
        assert raw_mod.raw_page(conn, other, oplan, limit=10) == ([], None)
    finally:
        conn.close()
