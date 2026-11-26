"""Migration 123 (gateway devices) against a real PostgreSQL (throwaway server).

Skipped unless GATEWAY_MIGRATION_PG_URL is set to a SUPERUSER url of a server that may be written
to. The test creates its own database, applies migration 109 (the table) and then 123 twice, and
drops the database at the end.
"""
import os
import pathlib
import uuid

import psycopg2
import pytest
from psycopg2 import errors, extensions

ADMIN_URL = os.environ.get("GATEWAY_MIGRATION_PG_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="GATEWAY_MIGRATION_PG_URL not set")

MIGRATIONS = pathlib.Path(__file__).resolve().parents[3] / "config" / "timescaledb" / "migrations"
BASE = (MIGRATIONS / "109_iot_dataloggers.sql").read_text(encoding="utf-8")
ADD = (MIGRATIONS / "123_gateway_devices.sql").read_text(encoding="utf-8")


def _connect(dbname=None):
    dsn = extensions.make_dsn(ADMIN_URL, **({"dbname": dbname} if dbname else {}))
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    return conn


@pytest.fixture(scope="module")
def db():
    name = "gw_mig_" + uuid.uuid4().hex[:10]
    admin = _connect()
    cur = admin.cursor()
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'nkz_core'")
    made_role = cur.fetchone() is None
    if made_role:
        cur.execute("CREATE ROLE nkz_core NOLOGIN")
    cur.execute(f"CREATE DATABASE {name}")
    conn = _connect(name)
    try:
        yield conn
    finally:
        conn.close()
        cur.execute(f"DROP DATABASE IF EXISTS {name}")
        if made_role:
            cur.execute("DROP ROLE IF EXISTS nkz_core")
        admin.close()


def _state(cur):
    cur.execute("SELECT pg_get_userbyid(relowner), relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = 'public.iot_dataloggers'::regclass")
    table = cur.fetchone()
    cur.execute("SELECT policyname, cmd, qual, with_check FROM pg_policies "
                "WHERE tablename = 'iot_dataloggers' ORDER BY policyname")
    return table, cur.fetchall()


def _insert(cur, tenant, gateway_id=None, name="box"):
    cur.execute("INSERT INTO public.iot_dataloggers (tenant_id, name, kind, mqtt_username, gateway_external_id) "
                "VALUES (%s, %s, 'datak', %s, %s)", (tenant, name, "dl_" + uuid.uuid4().hex[:12], gateway_id))


def test_migration_is_additive_idempotent_and_keeps_owner_and_rls(db):
    cur = db.cursor()
    cur.execute(BASE)
    cur.execute("INSERT INTO public.iot_dataloggers (tenant_id, name, kind, mqtt_username) "
                "VALUES ('acme', 'old box', 'datak', 'dl_old')")
    before = _state(cur)

    cur.execute(ADD)
    after_first = _state(cur)
    cur.execute(ADD)  # twice: no error, nothing changes
    after_second = _state(cur)
    assert before == after_first == after_second

    cur.execute("SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'iot_dataloggers' AND column_name LIKE 'gateway_%' ORDER BY column_name")
    assert cur.fetchall() == [("gateway_external_id", "text", "YES"),
                              ("gateway_provisioned_at", "timestamp with time zone", "YES")]
    cur.execute("SELECT count(*) FROM pg_indexes WHERE indexname = 'iot_dataloggers_tenant_gateway_external_id_key'")
    assert cur.fetchone() == (1,)
    cur.execute("SELECT count(*) FROM pg_constraint WHERE conname = 'iot_dataloggers_gateway_external_id_check'")
    assert cur.fetchone() == (1,)
    # the existing row is untouched: no id yet, no marker
    cur.execute("SELECT gateway_external_id, gateway_provisioned_at FROM public.iot_dataloggers WHERE mqtt_username = 'dl_old'")
    assert cur.fetchone() == (None, None)


def test_pattern_and_uniqueness(db):
    cur = db.cursor()
    cur.execute(ADD)
    _insert(cur, "acme", "gw-0123456789ab")
    for bad in ("gw-0123456789AB", "gw-0123456789a", "gw-0123456789abc", "sensor-0123456789ab",
                "gw-0123456789ag", "", " gw-0123456789ab", "gw-0123456789ab\n"):
        with pytest.raises(errors.CheckViolation):
            _insert(cur, "acme", bad)
    # same id, same tenant: refused; same id, other tenant: fine; many boxes without an id: fine
    with pytest.raises(errors.UniqueViolation):
        _insert(cur, "acme", "gw-0123456789ab")
    _insert(cur, "other", "gw-0123456789ab")
    _insert(cur, "acme")
    _insert(cur, "acme")
