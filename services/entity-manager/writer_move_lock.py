"""Advisory lock that keeps the DeviceMeasurement writer of a tenant from changing under a profile change.

One lock per tenant, taken on a dedicated connection (never the pool). The profile change takes it
shared; the writer move takes it exclusive. Closing the connection releases the lock, so the
connection is held for the whole critical section and never reconnected: a dead connection means a
lost lock, and `alive()` says so.

Every statement is followed by a commit or a rollback, so the session is never left idle in a
transaction. Errors carry only exception type names: a DSN must never reach a log or a traceback.
"""
import logging
import math
import os
import time
from contextlib import contextmanager
from typing import Iterator, Optional

import psycopg2

logger = logging.getLogger(__name__)

LOCK_CLASS = "dm-writer-move"
_SHARED_SQL = "SELECT pg_try_advisory_lock_shared(hashtext(%s), hashtext(%s))"
_EXCLUSIVE_SQL = "SELECT pg_try_advisory_lock(hashtext(%s), hashtext(%s))"
_SET_TENANT_SQL = "SELECT set_current_tenant(%s)"
_READ_SQL = ("SELECT dm_writer, dm_shape FROM iot_datalogger_devices "
             "WHERE tenant_id = %s AND device_urn = %s")
_UPDATE_SQL = ("UPDATE iot_datalogger_devices SET dm_writer = %s, dm_shape = %s "
               "WHERE tenant_id = %s AND device_urn = %s AND dm_writer = %s "
               "AND dm_shape IS NOT DISTINCT FROM %s")

_KEEPALIVE_IDLE = 30
_KEEPALIVE_INTERVAL = 10
_KEEPALIVE_COUNT = 3
_TCP_USER_TIMEOUT_MS = 5000
_CONNECT_TIMEOUT = 5
_STATEMENT_TIMEOUT_MS = 5000


class MoveInProgress(Exception):
    """The other side holds the lock."""


class LockUnavailable(Exception):
    """Cannot connect or query. The message never carries a DSN."""


class RowChanged(Exception):
    """The row no longer matches the state the caller read."""


class CommitOutcomeUnknown(psycopg2.Error):
    """COMMIT itself failed: the row may be in either state. The caller must re-read it."""


def _connect():
    """Dedicated connection. Keepalives are set on both ends so a lock whose holder vanished without
    closing TCP is released by the server, and `alive()` cannot hang."""
    url = os.environ.get("POSTGRES_URL")
    if not url:
        raise LockUnavailable("POSTGRES_URL is not set")
    try:
        return psycopg2.connect(
            url,
            connect_timeout=_CONNECT_TIMEOUT,
            options=(f"-c tcp_keepalives_idle={_KEEPALIVE_IDLE} "
                     f"-c tcp_keepalives_interval={_KEEPALIVE_INTERVAL} "
                     f"-c tcp_keepalives_count={_KEEPALIVE_COUNT} "
                     f"-c statement_timeout={_STATEMENT_TIMEOUT_MS}"),
            keepalives=1,
            keepalives_idle=_KEEPALIVE_IDLE,
            keepalives_interval=_KEEPALIVE_INTERVAL,
            keepalives_count=_KEEPALIVE_COUNT,
            tcp_user_timeout=_TCP_USER_TIMEOUT_MS,
        )
    except psycopg2.Error as exc:
        raise LockUnavailable(f"cannot connect ({type(exc).__name__})") from None


def _close(conn) -> None:
    """Closing releases the lock. A failure here must neither hide the caller's exception nor raise."""
    try:
        conn.close()
    except Exception as exc:  # noqa: BLE001 - nothing may escape a cleanup
        logger.warning("writer move lock: close failed (%s)", type(exc).__name__)


def _rollback(conn) -> None:
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001 - the connection is probably gone; the caller already has the error
        pass


def _try_lock(conn, sql: str, tenant: str) -> bool:
    """One try. Raises LockUnavailable on a database error. Any other interruption (KeyboardInterrupt
    included) rolls back and propagates."""
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (LOCK_CLASS, tenant))
            got = bool(cur.fetchone()[0])
        conn.commit()
        return got
    except psycopg2.Error as exc:
        _rollback(conn)
        raise LockUnavailable(f"lock query failed ({type(exc).__name__})") from None
    except BaseException:
        _rollback(conn)
        raise


def _ping(conn) -> bool:
    """SELECT 1. False on any psycopg2 error or closed connection. The statement timeout bounds a
    server that answers with an error; a frozen server that keeps acknowledging TCP but never answers
    is NOT bounded here."""
    if conn is None or conn.closed:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        conn.commit()
        return True
    except psycopg2.Error:
        _rollback(conn)
        return False
    except BaseException:
        _rollback(conn)
        raise


class SharedLock:
    def __init__(self, conn):
        self._conn = conn
        self.last_alive = time.monotonic()

    def alive(self) -> bool:
        """last_alive is the time taken BEFORE the ping that succeeded (a conservative stamp)."""
        started = time.monotonic()
        ok = _ping(self._conn)
        if ok:
            self.last_alive = started
        return ok


@contextmanager
def shared(tenant: str) -> Iterator[SharedLock]:
    """One try at the shared lock. MoveInProgress if the move holds it, LockUnavailable on a database
    error. The connection is always closed; an exception of the body propagates unchanged."""
    conn = _connect()
    try:
        if not _try_lock(conn, _SHARED_SQL, tenant):
            raise MoveInProgress(tenant)
        yield SharedLock(conn)
    finally:
        _close(conn)


class ExclusiveLock:
    """Context manager: takes the exclusive lock (polling, never the blocking form, so a queued
    exclusive cannot stall every profile change) and holds the connection until `__exit__`."""

    def __init__(self, tenant: str, wait: float = 30.0):
        if isinstance(wait, bool) or not isinstance(wait, (int, float)) or not math.isfinite(wait) or wait <= 0:
            raise ValueError("wait must be a finite number of seconds > 0")
        self.tenant = tenant
        self.wait = float(wait)
        self._conn = None

    def __enter__(self) -> "ExclusiveLock":
        if self._conn is not None:
            raise RuntimeError("exclusive lock is already held")
        conn = _connect()
        try:
            deadline = time.monotonic() + self.wait
            while True:
                if _try_lock(conn, _EXCLUSIVE_SQL, self.tenant):
                    self._conn = conn
                    return self
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MoveInProgress(self.tenant)
                time.sleep(min(1.0, remaining))
        except BaseException:
            _close(conn)
            raise

    def __exit__(self, exc_type, exc, tb) -> bool:
        conn, self._conn = self._conn, None
        if conn is not None:
            _close(conn)
        return False

    def _require_conn(self):
        if self._conn is None:
            raise RuntimeError("exclusive lock is not held")
        return self._conn

    def alive(self) -> bool:
        return _ping(self._conn)

    def read_state(self, urn: str) -> Optional[tuple]:
        """(dm_writer, dm_shape) of the device, None when there is no row. Raises on database errors."""
        conn = self._require_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(_SET_TENANT_SQL, (self.tenant,))
                cur.execute(_READ_SQL, (self.tenant, urn))
                row = cur.fetchone()
            conn.commit()
        except BaseException:
            _rollback(conn)
            raise
        return None if row is None else (row[0], row[1])

    def commit_state(self, urn: str, previous: tuple, target: tuple) -> None:
        """Compare-and-set the row in one transaction, on the connection that holds the lock.

        RowChanged: no row matched; nothing was written. psycopg2.Error: failed before COMMIT; nothing
        was written. CommitOutcomeUnknown (a psycopg2.Error): COMMIT itself failed, so the row may be in
        either state and the caller must re-read it. Any other interruption rolls back and propagates."""
        conn = self._require_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(_SET_TENANT_SQL, (self.tenant,))
                cur.execute(_UPDATE_SQL, (target[0], target[1], self.tenant, urn, previous[0], previous[1]))
                changed = cur.rowcount
        except BaseException:
            _rollback(conn)
            raise
        if changed != 1:
            _rollback(conn)
            raise RowChanged(urn)
        try:
            conn.commit()
        except psycopg2.Error as exc:
            _rollback(conn)
            raise CommitOutcomeUnknown(f"commit failed ({type(exc).__name__})") from None
        except BaseException:
            _rollback(conn)
            raise
