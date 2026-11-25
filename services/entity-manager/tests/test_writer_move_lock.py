"""Writer move lock: unit tests with a fake connection (no database)."""
import logging
from unittest.mock import patch

import psycopg2
import pytest

import writer_move_lock as wml

TENANT = "acme"
URN = "urn:ngsi-ld:Device:acme:d1"


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.calls.append(("execute", " ".join(sql.split()), params))
        if self.conn.fail_on and self.conn.fail_on in sql:
            raise psycopg2.OperationalError("server closed the connection")
        self.conn.last_sql = sql
        self.rowcount = self.conn.rowcount

    def fetchone(self):
        if "pg_try_advisory" in self.conn.last_sql:
            return (self.conn.lock_results.pop(0),)
        if "SELECT 1" in self.conn.last_sql:
            return (1,)
        return self.conn.row


class FakeConn:
    def __init__(self, lock_results=(True,), row=None, rowcount=1, fail_on=None, close_error=None):
        self.calls = []
        self.lock_results = list(lock_results)
        self.row = row
        self.rowcount = rowcount
        self.fail_on = fail_on
        self.close_error = close_error
        self.closed = 0
        self.last_sql = ""

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.calls.append(("commit",))

    def rollback(self):
        self.calls.append(("rollback",))

    def close(self):
        self.closed += 1
        if self.close_error:
            raise self.close_error


def _sqls(conn):
    return [c[1] for c in conn.calls if c[0] == "execute"]


def test_shared_closes_the_connection_on_success_and_on_error():
    conn = FakeConn()
    with patch.object(wml, "_connect", return_value=conn):
        with wml.shared(TENANT) as lock:
            assert lock.alive() is True
    assert conn.closed == 1
    conn = FakeConn()
    with patch.object(wml, "_connect", return_value=conn):
        with pytest.raises(ValueError):
            with wml.shared(TENANT):
                raise ValueError("body")
    assert conn.closed == 1


def test_shared_busy_raises_move_in_progress():
    conn = FakeConn(lock_results=(False,))
    with patch.object(wml, "_connect", return_value=conn):
        with pytest.raises(wml.MoveInProgress):
            with wml.shared(TENANT):
                pytest.fail("body must not run")
    assert conn.closed == 1
    assert len(_sqls(conn)) == 1  # one try only


def test_shared_connect_error_is_lock_unavailable():
    err = psycopg2.OperationalError("could not connect to postgresql://u:secret@h/db")
    with patch.object(wml, "_connect", side_effect=err):
        with pytest.raises(wml.LockUnavailable) as ei:
            with wml.shared(TENANT):
                pytest.fail("body must not run")
    assert "secret" not in str(ei.value)
    assert ei.value.__cause__ is None and ei.value.__suppress_context__


def test_shared_query_error_is_lock_unavailable_and_closes():
    conn = FakeConn(fail_on="pg_try_advisory_lock_shared")
    with patch.object(wml, "_connect", return_value=conn):
        with pytest.raises(wml.LockUnavailable):
            with wml.shared(TENANT):
                pytest.fail("body must not run")
    assert conn.closed == 1


def test_missing_postgres_url_is_lock_unavailable(monkeypatch):
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    with pytest.raises(wml.LockUnavailable):
        wml._connect()
    with pytest.raises(wml.LockUnavailable):
        with wml.shared(TENANT):
            pytest.fail("body must not run")


def test_connect_uses_the_keepalive_settings(monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:p@h:5432/db")
    with patch.object(wml.psycopg2, "connect") as connect:
        wml._connect()
    args, kw = connect.call_args
    assert args == ("postgresql://u:p@h:5432/db",)
    assert kw["connect_timeout"] == 5
    assert kw["options"] == "-c tcp_keepalives_idle=30 -c tcp_keepalives_interval=10 -c tcp_keepalives_count=3"
    assert kw["keepalives"] == 1 and kw["keepalives_idle"] == 30
    assert kw["keepalives_interval"] == 10 and kw["keepalives_count"] == 3
    assert kw["tcp_user_timeout"] == 5000


def test_connect_failure_does_not_leak_the_dsn(monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:topsecret@h:5432/db")
    with patch.object(wml.psycopg2, "connect",
                      side_effect=psycopg2.OperationalError("bad postgresql://u:topsecret@h")):
        with pytest.raises(wml.LockUnavailable) as ei:
            wml._connect()
    assert "topsecret" not in str(ei.value)
    assert ei.value.__cause__ is None and ei.value.__suppress_context__


def test_exclusive_waits_up_to_the_limit_then_move_in_progress():
    conn = FakeConn(lock_results=[False] * 40)
    sleeps = []
    with patch.object(wml, "_connect", return_value=conn), \
            patch.object(wml.time, "sleep", side_effect=sleeps.append):
        with pytest.raises(wml.MoveInProgress):
            with wml.ExclusiveLock(TENANT, wait=30.0):
                pytest.fail("body must not run")
    assert len([s for s in _sqls(conn) if "pg_try_advisory_lock" in s]) == 30
    assert sleeps and set(sleeps) == {1}
    assert conn.closed == 1


def test_exclusive_acquires_on_a_later_try():
    conn = FakeConn(lock_results=[False, False, True])
    with patch.object(wml, "_connect", return_value=conn), patch.object(wml.time, "sleep"):
        with wml.ExclusiveLock(TENANT, wait=30.0) as lock:
            assert lock.alive() is True
            assert conn.closed == 0
    assert conn.closed == 1


def test_keys_are_the_fixed_class_and_the_tenant():
    conn = FakeConn(lock_results=[True, True])
    with patch.object(wml, "_connect", return_value=conn):
        with wml.shared(TENANT):
            pass
    execs = [c for c in conn.calls if c[0] == "execute"]
    assert execs[0][1] == "SELECT pg_try_advisory_lock_shared(hashtext(%s), hashtext(%s))"
    assert execs[0][2] == ("dm-writer-move", TENANT)
    conn = FakeConn(lock_results=[True])
    with patch.object(wml, "_connect", return_value=conn):
        with wml.ExclusiveLock(TENANT):
            pass
    execs = [c for c in conn.calls if c[0] == "execute"]
    assert execs[0][1] == "SELECT pg_try_advisory_lock(hashtext(%s), hashtext(%s))"
    assert execs[0][2] == ("dm-writer-move", TENANT)


def test_every_statement_ends_the_transaction():
    conn = FakeConn(lock_results=[True], row=("em", None))
    with patch.object(wml, "_connect", return_value=conn):
        with wml.ExclusiveLock(TENANT) as lock:
            lock.alive()
            lock.read_state(URN)
            lock.commit_state(URN, ("em", None), ("iota", "sdm"))
    assert conn.calls[-1][0] == "commit"
    for i, c in enumerate(conn.calls[:-1]):
        nxt = conn.calls[i + 1]
        if c[0] == "execute" and nxt[0] == "execute":
            assert "set_current_tenant" in c[1]  # the only pair sharing a transaction


def test_read_state_sets_the_tenant_and_returns_the_tuple():
    conn = FakeConn(lock_results=[True], row=("iota", None))
    with patch.object(wml, "_connect", return_value=conn):
        with wml.ExclusiveLock(TENANT) as lock:
            assert lock.read_state(URN) == ("iota", None)
    ex = [c for c in conn.calls if c[0] == "execute"]
    assert ex[1][1] == "SELECT set_current_tenant(%s)" and ex[1][2] == (TENANT,)
    assert "dm_writer, dm_shape FROM iot_datalogger_devices" in ex[2][1]
    assert ex[2][2] == (TENANT, URN)


def test_read_state_none_when_no_row():
    conn = FakeConn(lock_results=[True], row=None)
    with patch.object(wml, "_connect", return_value=conn):
        with wml.ExclusiveLock(TENANT) as lock:
            assert lock.read_state(URN) is None


def test_commit_state_updates_in_one_transaction():
    conn = FakeConn(lock_results=[True], rowcount=1)
    with patch.object(wml, "_connect", return_value=conn):
        with wml.ExclusiveLock(TENANT) as lock:
            lock.commit_state(URN, ("em", None), ("iota", "sdm"))
    ex = [c for c in conn.calls if c[0] == "execute"]
    assert ex[1][1] == "SELECT set_current_tenant(%s)"
    assert ex[2][1] == ("UPDATE iot_datalogger_devices SET dm_writer = %s, dm_shape = %s "
                        "WHERE tenant_id = %s AND device_urn = %s AND dm_writer = %s "
                        "AND dm_shape IS NOT DISTINCT FROM %s")
    assert ex[2][2] == ("iota", "sdm", TENANT, URN, "em", None)
    idx = conn.calls.index(ex[2])
    assert conn.calls[idx + 1] == ("commit",)


def test_commit_state_rolls_back_when_no_row_matches():
    conn = FakeConn(lock_results=[True], rowcount=0)
    with patch.object(wml, "_connect", return_value=conn):
        with wml.ExclusiveLock(TENANT) as lock:
            with pytest.raises(wml.RowChanged):
                lock.commit_state(URN, ("em", None), ("iota", "sdm"))
    ex_update = [c for c in conn.calls if c[0] == "execute" and c[1].startswith("UPDATE")][0]
    idx = conn.calls.index(ex_update)
    assert conn.calls[idx + 1] == ("rollback",)
    assert ("commit",) not in conn.calls[idx:]


def test_commit_state_db_error_rolls_back_and_raises():
    conn = FakeConn(lock_results=[True], fail_on="UPDATE")
    with patch.object(wml, "_connect", return_value=conn):
        with wml.ExclusiveLock(TENANT) as lock:
            with pytest.raises(psycopg2.Error):
                lock.commit_state(URN, ("em", None), ("iota", "sdm"))
    assert conn.calls[-1] == ("rollback",) or conn.calls[-2] == ("rollback",)


def test_alive_false_on_error_and_on_closed_connection():
    conn = FakeConn(lock_results=[True, True])
    with patch.object(wml, "_connect", return_value=conn):
        with wml.shared(TENANT) as lock:
            before = lock.last_alive
            conn.fail_on = "SELECT 1"
            assert lock.alive() is False
            assert lock.last_alive == before
    conn = FakeConn(lock_results=[True])
    conn.closed = 0
    with patch.object(wml, "_connect", return_value=conn):
        with wml.ExclusiveLock(TENANT) as lock:
            conn.fail_on = "SELECT 1"
            assert lock.alive() is False


def test_alive_updates_last_alive():
    conn = FakeConn()
    with patch.object(wml, "_connect", return_value=conn):
        with wml.shared(TENANT) as lock:
            with patch.object(wml.time, "monotonic", return_value=123.5):
                assert lock.alive() is True
            assert lock.last_alive == 123.5


def test_body_exception_is_not_replaced_when_close_fails(caplog):
    conn = FakeConn(close_error=psycopg2.OperationalError("boom postgresql://u:secret@h"))
    with patch.object(wml, "_connect", return_value=conn), caplog.at_level(logging.WARNING):
        with pytest.raises(ValueError, match="body"):
            with wml.shared(TENANT):
                raise ValueError("body")
    assert conn.closed == 1
    assert "secret" not in caplog.text
    assert "OperationalError" in caplog.text


def test_close_failure_alone_does_not_raise():
    conn = FakeConn(close_error=RuntimeError("x"))
    with patch.object(wml, "_connect", return_value=conn):
        with wml.shared(TENANT):
            pass
    conn = FakeConn(lock_results=[True], close_error=RuntimeError("x"))
    with patch.object(wml, "_connect", return_value=conn):
        with wml.ExclusiveLock(TENANT):
            pass
    assert conn.closed == 1
