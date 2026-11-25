"""Writer move lock against a real PostgreSQL (throwaway server).

Skipped unless WRITER_MOVE_PG_URL is set to a SUPERUSER url of a server that may be written to.
The test creates its own database and a NON-superuser role (owner of a FORCE ROW LEVEL SECURITY
table shaped like migration 109/120) and drops both at the end.
"""
import os
import uuid

import psycopg2
import pytest
from psycopg2 import extensions

import writer_move_lock as wml

ADMIN_URL = os.environ.get("WRITER_MOVE_PG_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="WRITER_MOVE_PG_URL not set")

ROLE_PASSWORD = "role-" + uuid.uuid4().hex
URN = "urn:ngsi-ld:Device:acme:d1"

SCHEMA = """
CREATE FUNCTION set_current_tenant(tenant TEXT) RETURNS VOID AS $$
BEGIN PERFORM set_config('app.current_tenant', tenant, false); END; $$ LANGUAGE plpgsql;
CREATE TABLE public.iot_datalogger_devices (
    datalogger_id uuid NOT NULL,
    tenant_id text NOT NULL CHECK (tenant_id <> ''),
    device_urn text NOT NULL,
    external_id text NOT NULL,
    dm_writer text NOT NULL DEFAULT 'em' CHECK (dm_writer IN ('em', 'iota')),
    dm_shape text CHECK (dm_shape IN ('legacy', 'sdm')),
    PRIMARY KEY (datalogger_id, device_urn),
    UNIQUE (tenant_id, device_urn)
);
ALTER TABLE public.iot_datalogger_devices OWNER TO {role};
ALTER TABLE public.iot_datalogger_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.iot_datalogger_devices FORCE ROW LEVEL SECURITY;
CREATE POLICY iot_datalogger_devices_tenant_isolation ON public.iot_datalogger_devices
  USING (tenant_id = current_setting('app.current_tenant', true))
  WITH CHECK (tenant_id = current_setting('app.current_tenant', true));
GRANT EXECUTE ON FUNCTION set_current_tenant(TEXT) TO {role};
"""


def _admin(dbname=None):
    dsn = psycopg2.extensions.make_dsn(ADMIN_URL, **({"dbname": dbname} if dbname else {}))
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    return conn


@pytest.fixture(scope="module")
def pg():
    suffix = uuid.uuid4().hex[:10]
    dbname, role = f"wml_test_{suffix}", f"wml_role_{suffix}"
    admin = _admin()
    cur = admin.cursor()
    cur.execute(f"CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD %s", (ROLE_PASSWORD,))
    cur.execute(f"CREATE DATABASE {dbname} OWNER {role}")
    db = _admin(dbname)
    db.cursor().execute(SCHEMA.format(role=role))
    role_url = extensions.make_dsn(ADMIN_URL, dbname=dbname, user=role, password=ROLE_PASSWORD)
    try:
        yield {"db": db, "role_url": role_url, "role": role, "dbname": dbname}
    finally:
        db.close()
        cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (dbname,))
        cur.execute(f"DROP DATABASE IF EXISTS {dbname}")
        cur.execute(f"DROP ROLE IF EXISTS {role}")
        admin.close()


@pytest.fixture(autouse=True)
def env(pg, monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", pg["role_url"])
    cur = pg["db"].cursor()
    cur.execute("TRUNCATE public.iot_datalogger_devices")
    cur.execute("INSERT INTO public.iot_datalogger_devices (datalogger_id, tenant_id, device_urn, external_id) "
                "VALUES (gen_random_uuid(), 'acme', %s, 'ext1')", (URN,))
    return pg


def _row(pg):
    cur = pg["db"].cursor()
    cur.execute("SELECT dm_writer, dm_shape FROM public.iot_datalogger_devices WHERE device_urn = %s", (URN,))
    return cur.fetchone()


def test_the_role_is_not_a_superuser(pg):
    cur = pg["db"].cursor()
    cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", (pg["role"],))
    assert cur.fetchone() == (False, False)


def test_shared_and_shared_are_both_held():
    with wml.shared("acme") as a, wml.shared("acme") as b:
        assert a.alive() and b.alive()


def test_shared_blocks_exclusive():
    with wml.shared("acme"):
        with pytest.raises(wml.MoveInProgress):
            with wml.ExclusiveLock("acme", wait=0.2):
                pytest.fail("must not be granted")


def test_exclusive_blocks_shared_and_releases_on_exit():
    with wml.ExclusiveLock("acme", wait=0.2):
        with pytest.raises(wml.MoveInProgress):
            with wml.shared("acme"):
                pytest.fail("must not be granted")
    with wml.shared("acme") as lock:
        assert lock.alive()


def test_exclusive_blocks_exclusive():
    with wml.ExclusiveLock("acme", wait=0.2):
        with pytest.raises(wml.MoveInProgress):
            with wml.ExclusiveLock("acme", wait=0.2):
                pytest.fail("must not be granted")


def test_other_tenant_is_unaffected():
    with wml.ExclusiveLock("acme", wait=0.2):
        with wml.shared("other-tenant") as lock:
            assert lock.alive()
        with wml.ExclusiveLock("other-tenant", wait=0.2) as other:
            assert other.alive()


def test_read_and_commit_state_through_force_rls(pg):
    with wml.ExclusiveLock("acme", wait=0.2) as lock:
        assert lock.read_state(URN) == ("em", None)
        assert lock.read_state("urn:ngsi-ld:Device:acme:none") is None
        lock.commit_state(URN, ("em", None), ("iota", "sdm"))
        assert lock.read_state(URN) == ("iota", "sdm")
        lock.commit_state(URN, ("iota", "sdm"), ("em", None))
        assert lock.read_state(URN) == ("em", None)
    assert _row(pg) == ("em", None)


def test_another_tenant_row_is_invisible(pg):
    with wml.ExclusiveLock("other-tenant", wait=0.2) as lock:
        assert lock.read_state(URN) is None
        with pytest.raises(wml.RowChanged):
            lock.commit_state(URN, ("em", None), ("iota", "sdm"))
    assert _row(pg) == ("em", None)


def test_commit_state_with_a_different_previous_is_row_changed(pg):
    with wml.ExclusiveLock("acme", wait=0.2) as lock:
        with pytest.raises(wml.RowChanged):
            lock.commit_state(URN, ("iota", "legacy"), ("iota", "sdm"))
        with pytest.raises(wml.RowChanged):
            lock.commit_state(URN, ("em", "sdm"), ("iota", "sdm"))  # NULL shape is not 'sdm'
        assert lock.alive()
    assert _row(pg) == ("em", None)


def test_session_is_never_idle_in_transaction(pg):
    with wml.ExclusiveLock("acme", wait=0.2) as lock:
        lock.alive()
        lock.read_state(URN)
        lock.commit_state(URN, ("em", None), ("iota", "legacy"))
        with pytest.raises(wml.RowChanged):
            lock.commit_state(URN, ("em", None), ("iota", "sdm"))
        pid = lock._conn.get_backend_pid()
        cur = pg["db"].cursor()
        cur.execute("SELECT state FROM pg_stat_activity WHERE pid = %s", (pid,))
        assert cur.fetchone()[0] == "idle"


def test_killed_holder_is_not_alive_cannot_commit_and_releases_the_lock(pg):
    with wml.ExclusiveLock("acme", wait=0.2) as lock:
        pid = lock._conn.get_backend_pid()
        cur = pg["db"].cursor()
        cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
        assert cur.fetchone()[0] is True
        assert lock.alive() is False
        with pytest.raises(psycopg2.Error) as ei:
            lock.commit_state(URN, ("em", None), ("iota", "sdm"))
        assert not isinstance(ei.value, wml.CommitOutcomeUnknown)
        assert _row(pg) == ("em", None)
        with wml.ExclusiveLock("acme", wait=3) as other:  # the server released it
            assert other.alive()


def test_killed_shared_holder_is_not_alive():
    with wml.shared("acme") as lock:
        admin_pid = lock._conn.get_backend_pid()
        killer = psycopg2.connect(psycopg2.extensions.make_dsn(ADMIN_URL))
        killer.autocommit = True
        try:
            killer.cursor().execute("SELECT pg_terminate_backend(%s)", (admin_pid,))
        finally:
            killer.close()
        assert lock.alive() is False


def test_keepalives_are_set_over_tcp():
    with wml.ExclusiveLock("acme", wait=0.2) as lock:
        cur = lock._conn.cursor()
        for name, value in (("tcp_keepalives_idle", "30"), ("tcp_keepalives_interval", "10"),
                            ("tcp_keepalives_count", "3")):
            cur.execute(f"SHOW {name}")
            assert cur.fetchone()[0] == value
        cur.execute("SHOW statement_timeout")
        assert cur.fetchone()[0] == "5s"
        lock._conn.rollback()


class _InterruptingCursor:
    """Runs the real statement, then raises KeyboardInterrupt right after an UPDATE (before the commit)."""

    def __init__(self, cur):
        self._cur = cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._cur.close()
        return False

    def __getattr__(self, name):
        return getattr(self._cur, name)

    def execute(self, sql, params=None):
        self._cur.execute(sql, params)
        if sql.startswith("UPDATE"):
            raise KeyboardInterrupt()


class _InterruptingConn:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def cursor(self):
        return _InterruptingCursor(self._conn.cursor())


def test_interrupted_commit_state_of_one_device_does_not_leak_into_the_next(pg):
    urn_b = "urn:ngsi-ld:Device:acme:d2"
    pg["db"].cursor().execute(
        "INSERT INTO public.iot_datalogger_devices (datalogger_id, tenant_id, device_urn, external_id) "
        "VALUES (gen_random_uuid(), 'acme', %s, 'ext2')", (urn_b,))
    with wml.ExclusiveLock("acme", wait=0.2) as lock:
        real = lock._conn
        lock._conn = _InterruptingConn(real)
        with pytest.raises(KeyboardInterrupt):
            lock.commit_state(URN, ("em", None), ("iota", "sdm"))
        lock._conn = real
        lock.commit_state(urn_b, ("em", None), ("iota", "legacy"))
    cur = pg["db"].cursor()
    cur.execute("SELECT device_urn, dm_writer, dm_shape FROM public.iot_datalogger_devices ORDER BY device_urn")
    assert cur.fetchall() == [(URN, "em", None), (urn_b, "iota", "legacy")]
