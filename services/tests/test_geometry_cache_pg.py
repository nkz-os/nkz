"""pg_integration: geometry cache upsert under FORCE RLS (migration 117)."""
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

POLY = {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]}
POINT = {"type": "Point", "coordinates": [0.5, 0.5]}


def _run_as(dsn, role, body):
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


@pytest.mark.parametrize("role", ["nkz_rls_test", "nkz_core"])
def test_upsert_then_update_then_delete(pg_dsn, su, role):
    from telemetry_worker.geometry_cache import delete_geometry, upsert_geometry

    dsn = pg_dsn
    tenant = f"t_{uuid.uuid4().hex[:8]}"
    other = f"t_{uuid.uuid4().hex[:8]}"
    urn = f"urn:ngsi-ld:AgriParcel:{tenant}:p1"

    async def body(pool):
        ent = {"id": urn, "type": "AgriParcel", "location": {"type": "GeoProperty", "value": POLY}}
        assert await upsert_geometry(pool, tenant, ent)
        ent2 = {"id": urn, "type": "AgriPlant", "location": {"type": "GeoProperty", "value": POINT}}
        assert await upsert_geometry(pool, tenant, ent2)
        if role == "nkz_core":  # nkz_rls_test has no DELETE grant (pg_schema)
            # another tenant cannot delete it
            assert not await delete_geometry(pool, other, urn)
        return True

    assert _run_as(dsn, role, body)
    with su.cursor() as cur:
        cur.execute(
            "SELECT entity_type, ST_AsText(geom::geometry), count(*) OVER () "
            "FROM observation_geometry_cache WHERE tenant_id=%s AND entity_urn=%s",
            (tenant, urn),
        )
        rows = cur.fetchall()
    assert rows == [("AgriPlant", "POINT(0.5 0.5)", 1)]

    if role == "nkz_core":
        async def body2(pool):
            return await delete_geometry(pool, tenant, urn)

        assert _run_as(dsn, role, body2)
    else:
        with su.cursor() as cur:
            cur.execute("DELETE FROM observation_geometry_cache WHERE tenant_id=%s", (tenant,))
    with su.cursor() as cur:
        cur.execute("SELECT count(*) FROM observation_geometry_cache WHERE tenant_id=%s", (tenant,))
        assert cur.fetchone()[0] == 0
