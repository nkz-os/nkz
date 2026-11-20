"""pg_integration tests for migration 117 (observation geometry cache) under RLS.

Runs as nkz_rls_test (non-superuser, no BYPASSRLS): the superuser bypasses RLS.
"""
import pytest

pytestmark = pytest.mark.pg_integration

TENANT_A = "tenant_a"
TENANT_B = "tenant_b"
URN_A = "urn:ngsi-ld:AgriParcel:isolation-a"
GEOM_A = "POINT(-3.70 40.42)"


@pytest.fixture
def rls_conn(pg_conn):
    conn = pg_conn("nkz_rls_test")
    conn.autocommit = False
    yield conn
    conn.rollback()


def _set_tenant(cur, tenant):
    cur.execute("SELECT set_config('app.current_tenant', %s, true)", (tenant,))


def test_cache_isolated_by_tenant(rls_conn):
    import psycopg2

    conn = rls_conn

    # Tenant A writes its own row.
    with conn.cursor() as cur:
        _set_tenant(cur, TENANT_A)
        cur.execute(
            """INSERT INTO public.observation_geometry_cache
               (tenant_id, entity_urn, entity_type, geom, updated_at)
               VALUES (%s, %s, 'AgriParcel', ST_GeogFromText(%s), now())""",
            (TENANT_A, URN_A, GEOM_A),
        )
    conn.commit()

    # Tenant A cannot insert a row for tenant B (WITH CHECK fails).
    with conn.cursor() as cur:
        _set_tenant(cur, TENANT_A)
        with pytest.raises(psycopg2.Error) as exc:
            cur.execute(
                """INSERT INTO public.observation_geometry_cache
                   (tenant_id, entity_urn, entity_type, geom, updated_at)
                   VALUES (%s, %s, 'AgriParcel', ST_GeogFromText(%s), now())""",
                (TENANT_B, "urn:ngsi-ld:AgriParcel:isolation-b", GEOM_A),
            )
        assert "row-level security" in str(exc.value)
    conn.rollback()

    # Tenant B sees none of tenant A's rows, even with an explicit urn filter.
    with conn.cursor() as cur:
        _set_tenant(cur, TENANT_B)
        cur.execute("SELECT count(*) FROM public.observation_geometry_cache")
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT count(*) FROM public.observation_geometry_cache WHERE entity_urn = %s",
            (URN_A,),
        )
        assert cur.fetchone()[0] == 0
    conn.rollback()

    # Tenant A still sees its own row.
    with conn.cursor() as cur:
        _set_tenant(cur, TENANT_A)
        cur.execute(
            "SELECT count(*) FROM public.observation_geometry_cache WHERE tenant_id = %s",
            (TENANT_A,),
        )
        assert cur.fetchone()[0] == 1
    conn.rollback()
