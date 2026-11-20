"""pg_integration tests for migration 116 (real TimescaleDB 2.10 + PostGIS)."""
import json
import uuid

import pytest

from tests.pg_schema import MIGRATIONS_DIR

pytestmark = pytest.mark.pg_integration

NEW_COLUMNS = [
    "observed_property", "feature_of_interest", "medium", "depth_from_cm",
    "depth_to_cm", "height_m", "organ", "context_ref", "deployment_id",
    "source_kind", "location", "time_source", "quality_flag",
]
MIG_116 = MIGRATIONS_DIR / "116_telemetry_measurements_observations.sql"


def _columns(cur):
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'telemetry_measurements'"
    )
    return {r[0] for r in cur.fetchall()}


def _insert_event(cur, payload, observed_at="now()", tenant="t1", entity="e1"):
    cur.execute(
        f"""INSERT INTO telemetry_events
            (tenant_id, observed_at, entity_id, entity_type, device_id, payload)
            VALUES (%s, {observed_at}, %s, 'DeviceMeasurement', 'dev1', %s::jsonb)
            RETURNING id""",
        (tenant, entity, json.dumps(payload)),
    )
    return cur.fetchone()[0]


def _rows(cur, event_id):
    cur.execute(
        """SELECT attribute_name, observed_property, feature_of_interest, medium,
                  depth_from_cm, depth_to_cm, height_m, organ, context_ref,
                  deployment_id, source_kind, time_source, quality_flag,
                  ST_X(location::geometry)
           FROM telemetry_measurements WHERE source_event_id = %s
           ORDER BY attribute_name""",
        (event_id,),
    )
    return cur.fetchall()


@pytest.fixture
def cur(pg_conn):
    conn = pg_conn(None)  # superuser: trigger behaviour, not RLS
    with conn.cursor() as c:
        yield c


def test_add_column_on_compressed_chunk(pg_schema_factory):
    import psycopg2

    dsn = pg_schema_factory(114)
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            assert not set(NEW_COLUMNS) & _columns(cur)
            cur.execute("SELECT id FROM telemetry_events LIMIT 0")
            ev = _insert_event(
                cur, {"measurements": {"temperature": 21.5}},
                observed_at="now() - interval '10 days'",
            )
            cur.execute(
                "SELECT compress_chunk(c) FROM show_chunks('telemetry_measurements') c"
            )
            cur.execute(
                "SELECT count(*) FROM timescaledb_information.chunks "
                "WHERE hypertable_name='telemetry_measurements' AND is_compressed"
            )
            assert cur.fetchone()[0] >= 1

            sql = MIG_116.read_text(encoding="utf-8")
            cur.execute(sql)
            assert set(NEW_COLUMNS) <= _columns(cur)
            cur.execute(
                "SELECT value, " + ", ".join(NEW_COLUMNS) +
                " FROM telemetry_measurements WHERE source_event_id = %s", (ev,)
            )
            row = cur.fetchone()
            assert row[0] == 21.5
            assert all(v is None for v in row[1:])

            cur.execute(sql)  # idempotent
            assert set(NEW_COLUMNS) <= _columns(cur)
    finally:
        conn.close()


def test_schema_has_columns_after_115(cur):
    assert set(NEW_COLUMNS) <= _columns(cur)


def test_trigger_copies_observations(cur):
    dep = str(uuid.uuid4())
    ev = _insert_event(cur, {
        "measurements": {"temperature": 21.5},
        "observations": {"temperature": {
            "observedProperty": "airTemperature",
            "featureOfInterest": "urn:ngsi-ld:AgriParcel:t:p1",
            "medium": "ambientAir", "heightM": 2.0, "qualityFlag": "valid"}},
        "deploymentId": dep, "sourceKind": "device", "timeSource": "device",
        "location": {"type": "Point", "coordinates": [-1.6, 42.8]},
    })
    rows = _rows(cur, ev)
    assert rows == [(
        "temperature", "airTemperature", "urn:ngsi-ld:AgriParcel:t:p1",
        "ambientAir", None, None, 2.0, None, None, dep,
        "device", "device", "valid", pytest.approx(-1.6),
    )]


def test_trigger_without_observations_keeps_nulls(cur):
    ev = _insert_event(cur, {"measurements": {"temperature": 20}})
    rows = _rows(cur, ev)
    assert len(rows) == 1
    assert all(v is None for v in rows[0][1:])


def test_trigger_rejects_bad_vocabulary(cur):
    ev = _insert_event(cur, {
        "measurements": {"temperature": 20},
        "observations": {"temperature": {
            "observedProperty": "airTemperature",
            "medium": "lava", "qualityFlag": "great"}},
        "sourceKind": "robot", "timeSource": "device",
    })
    (r,) = _rows(cur, ev)
    assert r[1] == "airTemperature"
    assert r[3] is None      # medium
    assert r[10] is None     # source_kind
    assert r[11] == "device"  # time_source valid
    assert r[12] is None     # quality_flag


def test_trigger_malformed_fields_never_abort_insert(cur):
    ev = _insert_event(cur, {
        "measurements": {"temperature": 20},
        "observations": {"temperature": {
            "observedProperty": "airTemperature", "heightM": "tall",
            "depthFromCm": {"x": 1}, "depthToCm": 30}},
        "deploymentId": "not-a-uuid",
        "location": {"type": "Point", "coordinates": ["a", "b"]},
    })
    (r,) = _rows(cur, ev)
    assert r[1] == "airTemperature"
    assert r[4] is None and r[6] is None  # depth_from, height
    assert r[5] == 30.0                    # valid sibling kept
    assert r[9] is None and r[13] is None  # deployment_id, location
    cur.execute("SELECT count(*) FROM telemetry_events WHERE id = %s", (ev,))
    assert cur.fetchone()[0] == 1


def test_trigger_observations_wrong_shape(cur):
    ev = _insert_event(cur, {
        "measurements": {"a": 1, "b": 2}, "observations": [1, 2],
    })
    assert len(_rows(cur, ev)) == 2
    ev2 = _insert_event(cur, {
        "measurements": {"a": 1}, "observations": {"a": "str"},
    })
    assert len(_rows(cur, ev2)) == 1


def test_trigger_attribute_without_observation(cur):
    ev = _insert_event(cur, {
        "measurements": {"temperature": 20, "humidity": 55},
        "observations": {"temperature": {
            "observedProperty": "airTemperature", "medium": "ambientAir"}},
        "sourceKind": "device",
    })
    rows = {r[0]: r for r in _rows(cur, ev)}
    assert rows["temperature"][1] == "airTemperature"
    assert all(v is None for v in rows["humidity"][1:])


def test_trigger_keeps_066_numeric_filter(cur):
    ev = _insert_event(cur, {"measurements": {"ok": 1, "bad": "NaN", "txt": "abc"}})
    assert [r[0] for r in _rows(cur, ev)] == ["ok"]
