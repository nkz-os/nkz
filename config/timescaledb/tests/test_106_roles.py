"""Migration 106 is re-runnable and gives each role exactly its objects.

Runs against a throwaway timescale/timescaledb-ha:pg14 container loaded with
the schema-only dump at $NKZ_SCHEMA_DUMP (never committed). Skipped without it.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

DUMP = os.environ.get("NKZ_SCHEMA_DUMP")
MIG = Path(__file__).resolve().parents[1] / "migrations" / "106_db_roles_and_ownership.sql"
IMAGE = "timescale/timescaledb-ha:pg14-latest"
PORT = os.environ.get("NKZ_TEST_PG_PORT", "55432")

pytestmark = pytest.mark.skipif(not (DUMP and shutil.which("docker")), reason="needs docker and NKZ_SCHEMA_DUMP")


def _psql(cid, sql_file, stop_on_error):
    with open(sql_file, "rb") as fh:
        subprocess.run(
            ["docker", "exec", "-i", cid, "psql", "-q", "-U", "postgres", "-d", "nekazari", "-v",
             "ON_ERROR_STOP=" + ("1" if stop_on_error else "0"), "-f", "/dev/stdin"],
            stdin=fh, check=stop_on_error, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


@pytest.fixture(scope="module")
def db():
    cid = subprocess.check_output(
        ["docker", "run", "-d", "--rm", "-e", "POSTGRES_PASSWORD=t", "-p", f"{PORT}:5432", IMAGE]
    ).decode().strip()
    admin = f"postgresql://postgres:t@127.0.0.1:{PORT}/postgres"
    try:
        # the image restarts the server once after initdb: wait for three
        # consecutive successful connections, not the first one
        ok = 0
        for _ in range(180):
            try:
                psycopg2.connect(admin).close()
                ok += 1
                if ok >= 3:
                    break
            except psycopg2.OperationalError:
                ok = 0
            time.sleep(1)
        else:
            raise RuntimeError("postgres did not come up")
        c = psycopg2.connect(admin)
        c.autocommit = True
        c.cursor().execute("CREATE DATABASE nekazari")
        c.close()
        _psql(cid, DUMP, stop_on_error=False)
        yield cid, f"postgresql://postgres:t@127.0.0.1:{PORT}/nekazari"
    finally:
        subprocess.call(["docker", "stop", cid], stdout=subprocess.DEVNULL)


def _exec(dsn, sql):
    c = psycopg2.connect(dsn)
    c.autocommit = True
    c.cursor().execute(sql)
    c.close()


def _q(dsn, sql, *args):
    with psycopg2.connect(dsn) as c:
        cur = c.cursor()
        cur.execute(sql, args)
        return cur.fetchall()


def _as(dsn, role, password):
    return dsn.replace("postgres:t@", f"{role}:{password}@")


def test_migration_is_idempotent(db):
    cid, dsn = db
    assert MIG.exists(), "migration 106 not generated yet"
    _psql(cid, MIG, stop_on_error=True)
    _psql(cid, MIG, stop_on_error=True)
    assert _q(dsn, "select count(*) from pg_roles where rolname='nkz_core'") == [(1,)]


def test_core_owns_tenants_and_module_owns_its_tables(db):
    _, dsn = db
    assert _q(dsn, "select pg_get_userbyid(relowner) from pg_class where relname='tenants'") == [("nkz_core",)]
    assert _q(dsn, "select pg_get_userbyid(relowner) from pg_class where relname='cue_estado_envio'") == [("nkz_module_cue",)]


def test_hypertable_owner_transferred(db):
    _, dsn = db
    assert _q(dsn, "select pg_get_userbyid(relowner) from pg_class where relname='telemetry_events'") == [("nkz_core",)]
    assert _q(dsn, "select pg_get_userbyid(c.relowner) from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relname='telemetry_1h'") == [("nkz_core",)]


def test_owner_reads_enable_table_without_tenant(db):
    _, dsn = db
    _exec(dsn, "ALTER ROLE nkz_core PASSWORD 'c'")
    # farmers has ENABLE policies; the owner is not filtered and sees no error
    assert _q(_as(dsn, "nkz_core", "c"), "select count(*) from farmers") == [(0,)]


def test_module_role_cannot_read_platform_tables(db):
    _, dsn = db
    _exec(dsn, "ALTER ROLE nkz_module_cue PASSWORD 'm'")
    mdsn = _as(dsn, "nkz_module_cue", "m")
    for table in ("tenants", "api_keys", "farmers", "activation_codes"):
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            _q(mdsn, f"select 1 from {table} limit 1")
    assert _q(mdsn, "select count(*) from module_tenant_plan") == [(0,)]
    assert _q(mdsn, "select count(*) from cue_estado_envio") == [(0,)]


def test_public_cannot_connect(db):
    _, dsn = db
    _exec(dsn, "CREATE ROLE nobody_role LOGIN PASSWORD 'n'")
    with pytest.raises(psycopg2.OperationalError):
        psycopg2.connect(_as(dsn, "nobody_role", "n"))
