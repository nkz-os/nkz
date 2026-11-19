"""pg_integration tests for ObservationLookup under real RLS.

RLS tests run as nkz_rls_test (non-superuser, no BYPASSRLS). Two tests run as
nkz_core, the production worker role: OWNER of device_deployments under FORCE
RLS, so the owner must be filtered too, and must resolve once the tenant is set.
Setup rows are written as the superuser (bypasses RLS) through psycopg2.
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

T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 4, 1, tzinfo=timezone.utc)


def _unique(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _run_as(dsn, role, body):
    """Run `async body(pool)` with an asyncpg pool whose connections SET ROLE."""
    import asyncpg

    async def _init(conn):
        await conn.execute(f'SET ROLE "{role}"')

    async def main():
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, init=_init)
        try:
            return await body(pool)
        finally:
            await pool.close()

    return asyncio.run(main())


@pytest.fixture
def su(pg_conn):
    return pg_conn(None)


def _add_deployment(su, tenant, urn, lower, upper, feature="urn:ngsi-ld:AgriParcel:p1"):
    with su.cursor() as cur:
        cur.execute(
            """INSERT INTO public.device_deployments
               (tenant_id, device_urn, valid, observes_feature, observation_context,
                mobility, location, created_by)
               VALUES (%s, %s, tstzrange(%s, %s, '[)'), %s, %s::jsonb, 'fixed',
                       ST_GeogFromText('POINT(-3.7 40.4)'), 'test')
               RETURNING id""",
            (tenant, urn, lower, upper, feature, json.dumps({"medium": "soil"})),
        )
        return str(cur.fetchone()[0])


def test_tenant_a_never_resolves_tenant_b_deployment(pg_dsn, su):
    from telemetry_worker.observation_lookup import ObservationLookup

    ta, tb = _unique("ta"), _unique("tb")
    urn = _unique("urn:ngsi-ld:Device:shared")
    _add_deployment(su, tb, urn, T0, None)

    async def body(pool):
        lk = ObservationLookup(pool)
        assert await lk.deployment_at(ta, urn, T0 + timedelta(days=1)) is None
        dep = await lk.deployment_at(tb, urn, T0 + timedelta(days=1))
        assert dep is not None and dep.device_urn == urn
        assert dep.location == pytest.approx((-3.7, 40.4))
        assert dep.observation_context == {"medium": "soil"}

    _run_as(pg_dsn, "nkz_rls_test", body)


def test_query_without_set_config_sees_nothing(pg_dsn, su):
    tenant = _unique("tn")
    urn = _unique("urn:ngsi-ld:Device:raw")
    _add_deployment(su, tenant, urn, T0, None)

    async def body(pool):
        async with pool.acquire() as c:
            raw = await c.fetchval(
                "SELECT count(*) FROM public.device_deployments WHERE tenant_id=$1", tenant
            )
            async with c.transaction():
                await c.execute("SELECT set_config('app.current_tenant', $1, true)", tenant)
                scoped = await c.fetchval(
                    "SELECT count(*) FROM public.device_deployments WHERE tenant_id=$1", tenant
                )
        assert raw == 0
        assert scoped == 1

    _run_as(pg_dsn, "nkz_rls_test", body)


def test_boundary_inclusive_lower_exclusive_upper(pg_dsn, su):
    from telemetry_worker.observation_lookup import ObservationLookup

    tenant = _unique("tb")
    urn = _unique("urn:ngsi-ld:Device:bound")
    first = _add_deployment(su, tenant, urn, T0, T1)
    second = _add_deployment(su, tenant, urn, T1, None)

    async def body(pool):
        lk = ObservationLookup(pool)
        assert (await lk.deployment_at(tenant, urn, T0)).id == first
        assert (await lk.deployment_at(tenant, urn, T1 - timedelta(microseconds=1))).id == first
        assert (await lk.deployment_at(tenant, urn, T1)).id == second
        assert await lk.deployment_at(tenant, urn, T0 - timedelta(microseconds=1)) is None

    _run_as(pg_dsn, "nkz_rls_test", body)


def test_property_tenant_row_wins_over_global_and_deprecated_ignored(pg_dsn, su):
    from telemetry_worker.observation_lookup import ObservationLookup

    tenant = _unique("tp")
    with su.cursor() as cur:
        cur.execute(
            "SELECT name FROM public.observation_properties WHERE tenant_id IS NULL "
            "AND deprecated_at IS NULL LIMIT 1"
        )
        glob = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO public.observation_properties
               (tenant_id, name, domain, unit, default_medium, allowed_media,
                temporal_semantics, valid_min, valid_max)
               VALUES (%s, %s, 'other', 'x', 'soil', ARRAY['soil'], 'instant', -123, 456)""",
            (tenant, glob),
        )

    async def body(pool):
        lk = ObservationLookup(pool)
        mine = await lk.property(tenant, glob)
        assert (mine.valid_min, mine.valid_max) == (-123, 456)
        other = await lk.property(_unique("other"), glob)  # falls back to the global row
        assert other is not None and other.valid_min != -123
        assert await lk.property(tenant, "doesNotExist") is None

    _run_as(pg_dsn, "nkz_rls_test", body)


def test_measurement_def_tenant_profile_wins_over_global(pg_dsn, su):
    from telemetry_worker.observation_lookup import ObservationLookup

    tenant = _unique("tm")
    code = _unique("PROF")
    with su.cursor() as cur:
        for t, cmax in ((None, 100), (tenant, 200)):
            cur.execute(
                """INSERT INTO public.sensor_profiles
                   (tenant_id, code, name, sdm_entity_type, mapping)
                   VALUES (%s, %s, 'p', 'Device', %s::jsonb)""",
                (t, code, json.dumps({"measurements": [
                    {"sdmAttribute": "rainfall", "counterMax": cmax}]})),
            )

    async def body(pool):
        lk = ObservationLookup(pool)
        assert (await lk.measurement_def(tenant, code, "rainfall"))["counterMax"] == 200
        assert (await lk.measurement_def(_unique("x"), code, "rainfall"))["counterMax"] == 100

    _run_as(pg_dsn, "nkz_rls_test", body)


def test_owner_role_nkz_core_is_filtered_and_resolves_with_tenant(pg_dsn, su):
    """nkz_core owns device_deployments; FORCE RLS still applies to the owner."""
    from telemetry_worker.observation_lookup import ObservationLookup

    with su.cursor() as cur:
        cur.execute("GRANT USAGE ON SCHEMA public TO nkz_core")
        cur.execute("SELECT relowner::regrole::text, relforcerowsecurity FROM pg_class "
                    "WHERE oid = 'public.device_deployments'::regclass")
        owner, forced = cur.fetchone()
    assert owner == "nkz_core" and forced is True

    ta, tb = _unique("ta"), _unique("tb")
    urn = _unique("urn:ngsi-ld:Device:owner")
    dep_b = _add_deployment(su, tb, urn, T0, None)

    async def body(pool):
        async with pool.acquire() as c:
            assert await c.fetchval("SELECT current_user") == "nkz_core"
            assert await c.fetchval(
                "SELECT count(*) FROM public.device_deployments WHERE tenant_id=$1", tb
            ) == 0  # owner without tenant context sees nothing
        lk = ObservationLookup(pool)
        assert await lk.deployment_at(ta, urn, T0 + timedelta(days=1)) is None
        assert (await lk.deployment_at(tb, urn, T0 + timedelta(days=1))).id == dep_b

    _run_as(pg_dsn, "nkz_core", body)


def test_telemetry_trigger_works_under_production_role(pg_dsn, su):
    """Migration 115 trigger (unqualified PostGIS calls) as nkz_core, no search_path set."""
    tenant = _unique("tt")
    dep_id = str(uuid.uuid4())
    with su.cursor() as cur:
        cur.execute("GRANT USAGE ON SCHEMA public TO nkz_core")
        cur.execute("GRANT SELECT, INSERT ON telemetry_events, telemetry_measurements TO nkz_core")
        cur.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO nkz_core")

    payload = {
        "measurements": {"airTemperature": 21.5},
        "deploymentId": dep_id,
        "sourceKind": "device",
        "timeSource": "device",
        "location": {"type": "Point", "coordinates": [-3.7, 40.4]},
        "observations": {"airTemperature": {
            "observedProperty": "airTemperature",
            "medium": "ambientAir",
            "qualityFlag": "valid",
        }},
    }

    async def body(pool):
        async with pool.acquire() as c, c.transaction():
            await c.execute("SELECT set_config('app.current_tenant', $1, true)", tenant)
            assert await c.fetchval("SELECT current_user") == "nkz_core"
            await c.execute(
                "INSERT INTO public.telemetry_events (tenant_id, observed_at, device_id, payload) "
                "VALUES ($1, $2, 'dev1', $3::jsonb)",
                tenant, T0, json.dumps(payload),
            )
            row = await c.fetchrow(
                "SELECT observed_property, medium, quality_flag, deployment_id::text AS dep, "
                "ST_X(location::geometry) AS lon FROM public.telemetry_measurements "
                "WHERE tenant_id=$1 AND attribute_name='airTemperature'", tenant)
        assert row is not None
        assert (row["observed_property"], row["medium"], row["quality_flag"]) == (
            "airTemperature", "ambientAir", "valid")
        assert row["dep"] == dep_id
        assert row["lon"] == pytest.approx(-3.7)

    _run_as(pg_dsn, "nkz_core", body)
