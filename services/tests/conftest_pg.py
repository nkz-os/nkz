"""Fixtures for pg_integration tests against a real TimescaleDB/PostGIS server.

Enabled by POSTGRES_URL (superuser URL of a scratch server); skipped when unset
or when the driver is a stub. Each pytest session gets fresh databases
(nkz_obs_test_<random>), dropped at teardown.

Use:
    pg_schema_factory(upto=114) -> dsn of a fresh DB built up to migration 114
    pg_dsn                      -> dsn of a fresh DB with the full schema
    pg_conn(role)               -> connection to pg_dsn, SET ROLE <role>
"""
from __future__ import annotations

import os
import secrets
from urllib.parse import urlparse, urlunparse

import pytest

from tests.pg_schema import apply_minimal_schema


def _live_psycopg2():
    """Real psycopg2 module, or skip. Other test modules may stub psycopg2."""
    url = os.environ.get("POSTGRES_URL")
    if not url:
        pytest.skip("POSTGRES_URL not set")
    try:
        import psycopg2
    except ImportError:
        pytest.skip("psycopg2 not installed")
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                row = cur.fetchone()
        finally:
            conn.close()
        if tuple(row) != (1,):
            raise TypeError("not a live database")
    except Exception as exc:  # unreachable server or stubbed driver
        pytest.skip(f"no live PostgreSQL at POSTGRES_URL: {type(exc).__name__}")
    return psycopg2


def _with_dbname(url: str, dbname: str) -> str:
    parts = urlparse(url)
    return urlunparse(parts._replace(path="/" + dbname))


@pytest.fixture(scope="session")
def pg_schema_factory():
    """Factory(upto=None) -> dsn of a fresh database; all dropped at teardown."""
    psycopg2 = _live_psycopg2()
    admin_url = os.environ["POSTGRES_URL"]
    created: list[str] = []

    def make(upto: int | None = None) -> str:
        dbname = f"nkz_obs_test_{secrets.token_hex(6)}"
        admin = psycopg2.connect(admin_url)
        admin.autocommit = True
        try:
            with admin.cursor() as cur:
                cur.execute(f'CREATE DATABASE "{dbname}"')
        finally:
            admin.close()
        created.append(dbname)
        dsn = _with_dbname(admin_url, dbname)
        conn = psycopg2.connect(dsn)
        try:
            apply_minimal_schema(conn, upto=upto)
        finally:
            conn.close()
        return dsn

    try:
        yield make
    finally:
        admin = psycopg2.connect(admin_url)
        admin.autocommit = True
        try:
            with admin.cursor() as cur:
                for dbname in created:
                    cur.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        finally:
            admin.close()


@pytest.fixture(scope="session")
def pg_dsn(pg_schema_factory) -> str:
    """DSN of a fresh database with the full schema (shared per session)."""
    return pg_schema_factory(None)


@pytest.fixture
def pg_conn(pg_dsn):
    """Factory(role='nkz_rls_test') -> autocommit connection with SET ROLE.

    Connections are closed at teardown. role=None keeps the superuser.
    """
    import psycopg2

    opened = []

    def connect(role: str | None = "nkz_rls_test"):
        conn = psycopg2.connect(pg_dsn)
        conn.autocommit = True
        opened.append(conn)
        if role:
            with conn.cursor() as cur:
                cur.execute(f'SET ROLE "{role}"')
        return conn

    try:
        yield connect
    finally:
        for c in opened:
            c.close()
