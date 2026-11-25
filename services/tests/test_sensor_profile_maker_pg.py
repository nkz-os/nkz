"""Migration 121 adds three optional maker columns to sensor_profiles and changes nothing else."""
import pytest

from tests.pg_schema import MIGRATIONS_DIR

pytestmark = pytest.mark.pg_integration

MIGRATION = MIGRATIONS_DIR / "121_sensor_profile_maker.sql"
COLUMNS = ("manufacturer_name", "brand_name", "model_name")


def _rls_state(cur):
    cur.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE oid = 'public.sensor_profiles'::regclass"
    )
    flags = cur.fetchone()
    cur.execute(
        "SELECT policyname, permissive, roles::text, cmd, qual, with_check FROM pg_policies "
        "WHERE schemaname = 'public' AND tablename = 'sensor_profiles' ORDER BY policyname"
    )
    return flags, cur.fetchall()


def test_maker_columns_are_added_idempotently_without_touching_rls(pg_schema_factory):
    import psycopg2

    conn = psycopg2.connect(pg_schema_factory(None))
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM sensor_profiles")
            rows_before = cur.fetchone()[0]
            assert rows_before > 0
            cur.execute(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'sensor_profiles' AND column_name = ANY(%s)", (list(COLUMNS),)
            )
            assert cur.fetchone()[0] == 0
            rls_before = _rls_state(cur)
            assert rls_before[0][0] is True  # ENABLE is set by migration 010
            assert len(rls_before[1]) == 2

            sql = MIGRATION.read_text(encoding="utf-8")
            cur.execute(sql)
            cur.execute(sql)  # second application must be a no-op

            cur.execute(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'sensor_profiles' "
                "AND column_name = ANY(%s) ORDER BY column_name", (list(COLUMNS),)
            )
            assert cur.fetchall() == [
                ("brand_name", "text", "YES", None),
                ("manufacturer_name", "text", "YES", None),
                ("model_name", "text", "YES", None),
            ]
            cur.execute(
                "SELECT count(*) FROM sensor_profiles WHERE manufacturer_name IS NOT NULL "
                "OR brand_name IS NOT NULL OR model_name IS NOT NULL"
            )
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM sensor_profiles")
            assert cur.fetchone()[0] == rows_before
            assert _rls_state(cur) == rls_before
    finally:
        conn.close()
