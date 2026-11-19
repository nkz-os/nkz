"""Tenant-scoped cursor with a statement timeout. The caller owns the connection."""
from __future__ import annotations

from contextlib import contextmanager

from psycopg2.extras import RealDictCursor

STATEMENT_TIMEOUT_SQL = "SET LOCAL statement_timeout = '10s'"
TIMEOUT_MESSAGE = "observation query timed out"


@contextmanager
def tenant_cursor(conn, tenant_id: str):
    """Yield a RealDictCursor inside the connection's transaction with the tenant set.

    ``set_config(..., true)`` and ``SET LOCAL`` are transaction-local: the caller must
    run every query inside this block and commit/rollback/close the connection itself.
    """
    if not tenant_id:
        raise ValueError("tenant_id required")
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT set_config('app.current_tenant', %s, true)", (tenant_id,))
        cur.execute(STATEMENT_TIMEOUT_SQL)
        yield cur
    finally:
        cur.close()


def timeout_response():
    """503 body for psycopg2.errors.QueryCanceled; reused by every observation route."""
    from flask import jsonify

    return jsonify({"error": TIMEOUT_MESSAGE}), 503
