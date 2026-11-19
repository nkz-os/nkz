"""pg_integration: DeviceMeasurement notification -> telemetry_events -> telemetry_measurements."""
import asyncio
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


def test_notification_row_carries_observation(pg_dsn, pg_conn):
    import asyncpg
    from telemetry_worker import notification_handler as nh
    from telemetry_worker.config import Settings
    from telemetry_worker.event_sink import PostgreSQLSink
    from telemetry_worker.observation_lookup import ObservationLookup

    su = pg_conn(None)
    tenant = f"t_{uuid.uuid4().hex[:8]}"
    dev = f"d{uuid.uuid4().hex[:6]}"
    urn = f"urn:ngsi-ld:Device:{tenant}:{dev}"
    with su.cursor() as cur:
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
        "id": f"urn:ngsi-ld:DeviceMeasurement:{tenant}:{dev}:temperature",
        "type": "DeviceMeasurement",
        "refDevice": {"type": "Relationship", "object": urn},
        "controlledProperty": {"type": "Property", "value": "temperature"},
        "numValue": {"type": "Property", "value": 21.5},
        "dateObserved": {"type": "Property", "value": "2026-08-29T10:00:00Z"},
        "observedProperty": {"type": "Property", "value": "airTemperature"},
        "timeSource": {"type": "Property", "value": "device"},
    }

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
               FROM telemetry_measurements WHERE tenant_id=%s AND attribute_name='temperature'""",
            (tenant,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "airTemperature"
    assert row[1] == dep_id
    assert row[2] == "device"
    assert row[3] == "device"
    assert round(row[4], 1) == -3.7
    assert row[5] == "valid"
