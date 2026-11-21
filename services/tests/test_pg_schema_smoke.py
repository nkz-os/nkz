import pytest

pytestmark = pytest.mark.pg_integration


def test_telemetry_measurements_readable_as_rls_role(pg_conn):
    conn = pg_conn("nkz_rls_test")
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM telemetry_measurements LIMIT 0")
        assert cur.fetchall() == []
        cur.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        assert cur.fetchone() == (False, False)


def test_schema_factory_upto_114_stops_before_observation_columns(pg_schema_factory):
    import psycopg2

    dsn = pg_schema_factory(114)
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.device_deployments')")
            assert cur.fetchone()[0] is not None
            cur.execute("SELECT count(*) FROM observation_properties")
            assert cur.fetchone()[0] > 0
            cur.execute(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'telemetry_measurements' "
                "AND column_name = 'observed_property'"
            )
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()

