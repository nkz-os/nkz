"""pg_integration: DeviceMeasurement notification -> telemetry_events -> telemetry_measurements."""
import asyncio
import json
import logging
import os
import sys
import uuid

import pytest

_SVC = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_WORKER = os.path.join(_SVC, "telemetry-worker")
for _p in (_SVC, _WORKER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytestmark = pytest.mark.pg_integration


def _notify(pg_dsn, pg_conn, profile_unit, shape="legacy", channel="temperature",
            statistics=None, measurement_type="instant"):
    """Run one notification through the worker; return (row, deployment id).

    The device's profile (sensor_profiles row, referenced by the DeviceMeasurement
    deviceType) declares ``profile_unit`` and the catalogue name airTemperature for
    temperature; None omits the unit from the measurement. ``shape`` "legacy" is the
    Phase 1 DM (channel in controlledProperty, catalogue name from the profile); "sdm" is
    the SDM DM (channel in `name`, catalogue name in controlledProperty). ``channel`` is the
    published channel, read back as attribute_name.
    """
    from telemetry_worker import notification_handler as nh
    from telemetry_worker.config import Settings
    from telemetry_worker.event_sink import PostgreSQLSink
    from telemetry_worker.observation_lookup import ObservationLookup

    su = pg_conn(None)
    tenant = f"t_{uuid.uuid4().hex[:8]}"
    dev = f"d{uuid.uuid4().hex[:6]}"
    urn = f"urn:ngsi-ld:Device:{tenant}:{dev}"
    profile_code = f"p_{uuid.uuid4().hex[:8]}"
    measurement = {"sdmAttribute": "temperature", "type": "temperature",
                   "observedProperty": "airTemperature"}
    if statistics:
        measurement["statistics"] = statistics
    if profile_unit is not None:
        measurement["unit"] = profile_unit
    with su.cursor() as cur:
        cur.execute(
            """INSERT INTO public.sensor_profiles
               (tenant_id, code, name, sdm_entity_type, mapping)
               VALUES (%s, %s, 'p', 'Device', %s::jsonb)""",
            (tenant, profile_code, json.dumps({"measurements": [measurement]})),
        )
        cur.execute(
            """INSERT INTO public.device_deployments
               (tenant_id, device_urn, valid, observation_context, mobility, location, created_by)
               VALUES (%s, %s, tstzrange('2026-01-01', NULL, '[)'), '{"medium":"air"}'::jsonb,
                       'fixed', ST_GeogFromText('POINT(-3.7 40.4)'), 'test') RETURNING id""",
            (tenant, urn),
        )
        dep_id = str(cur.fetchone()[0])
        cur.execute("SELECT 1 FROM public.observation_properties WHERE name='airTemperature' LIMIT 1")
        if cur.fetchone() is None:
            pytest.skip("airTemperature not in catalogue")

    entity = {
        "id": f"urn:ngsi-ld:DeviceMeasurement:{tenant}:{dev}:{channel}",
        "type": "DeviceMeasurement",
        "refDevice": {"type": "Relationship", "object": urn},
        "numValue": {"type": "Property", "value": 21.5},
        "dateObserved": {"type": "Property", "value": "2026-08-29T10:00:00Z"},
        "timeSource": {"type": "Property", "value": "device"},
        "deviceType": {"type": "Property", "value": profile_code},
    }
    if shape == "sdm":
        entity["name"] = {"type": "Property", "value": channel}
        entity["controlledProperty"] = {"type": "Property", "value": "airTemperature"}
        entity["measurementType"] = {"type": "Property", "value": measurement_type}
    else:
        entity["controlledProperty"] = {"type": "Property", "value": channel}
        entity["measurementType"] = {"type": "Property", "value": "temperature"}

    class _Profiles:
        def get_profile(self, device_type, device_id=None, tenant_id=None):
            from telemetry_worker.profiles import DEFAULT_PROFILE_CONFIG, ProcessingProfile
            return ProcessingProfile(device_type=device_type, device_id=device_id,
                                     tenant_id=tenant_id, config=DEFAULT_PROFILE_CONFIG.copy())
        def should_persist(self, *a): return True
        def filter_attributes(self, profile, m): return dict(m)
        def update_last_values(self, *a): pass

    async def main():
        sink = PostgreSQLSink(dsn=pg_dsn, min_pool=1, max_pool=2)
        await sink.start()
        try:
            nh.init_handler(Settings(postgres_url=pg_dsn), _Profiles(), sink,
                            observation_lookup=ObservationLookup(sink._pool))
            ev = await nh._process_entity(entity, tenant)
            assert ev is not None
            await sink.write(ev)
        finally:
            nh.init_handler(Settings(postgres_url=pg_dsn), _Profiles(), None)
            await sink.stop()

    asyncio.run(main())

    with su.cursor() as cur:
        cur.execute(
            """SELECT observed_property, deployment_id::text, time_source, source_kind,
                      ST_X(location::geometry), quality_flag
               FROM telemetry_measurements WHERE tenant_id=%s AND attribute_name=%s""",
            (tenant, channel),
        )
        row = cur.fetchone()
    assert row is not None
    return row, dep_id


def test_notification_row_carries_observation(pg_dsn, pg_conn):
    row, dep_id = _notify(pg_dsn, pg_conn, "°C")
    assert row[0] == "airTemperature"
    assert row[1] == dep_id
    assert row[2] == "device"
    assert row[3] == "device"
    assert round(row[4], 1) == -3.7
    assert row[5] == "valid"


def test_notification_without_profile_unit_writes_no_observation(pg_dsn, pg_conn, caplog):
    with caplog.at_level(logging.WARNING):
        row, _ = _notify(pg_dsn, pg_conn, None)
    # Unit cannot be verified: observation columns stay NULL, the measurement is kept.
    assert row[0] is None  # observed_property
    assert row[1] is None  # deployment_id
    assert any("observation_unit_mismatch" in r.getMessage() for r in caplog.records)


def test_sdm_shape_row_carries_observation_keyed_by_channel(pg_dsn, pg_conn):
    row, dep_id = _notify(pg_dsn, pg_conn, "°C", shape="sdm")
    assert row[0] == "airTemperature"
    assert row[1] == dep_id
    assert row[5] == "valid"


@pytest.mark.parametrize("shape", ["legacy", "sdm"])
def test_statistic_channel_is_stored_under_its_channel_without_observation(
        pg_dsn, pg_conn, caplog, shape):
    with caplog.at_level(logging.DEBUG):
        row, _ = _notify(pg_dsn, pg_conn, "°C", shape=shape, channel="airTemperatureMin",
                         statistics=["mean", "min", "max"], measurement_type="min")
    assert row is not None  # attribute_name = airTemperatureMin, never collapsed
    assert row[0] is None and row[1] is None
    assert any("observation_skipped_statistic" in r.getMessage() for r in caplog.records)
    assert not any("observation_unit_mismatch" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("shape", ["legacy", "sdm"])
def test_mean_base_channel_of_a_statistics_profile_carries_observation(pg_dsn, pg_conn, shape):
    """Design A.2: the mean is published under the base channel and is its representative value."""
    row, dep_id = _notify(pg_dsn, pg_conn, "°C", shape=shape, channel="airTemperature",
                          statistics=["mean", "min", "max"], measurement_type="mean")
    assert row[0] == "airTemperature"
    assert row[1] == dep_id
