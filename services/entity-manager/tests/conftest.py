"""Environment read while entity_management_api is imported, whichever test file imports it first."""
import os

os.environ.setdefault("CORS_ORIGINS", "https://app.invalid")

# Several files replace the `common` package in sys.modules. Loaded first, this
# constants module stays importable under the stub.
import common.active_tenants  # noqa: E402,F401

# ---- writer move lock: a working fake lock for every test ----------------------------
import psycopg2  # noqa: E402
import pytest  # noqa: E402


class FakeLockCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if self.conn.dead:
            raise psycopg2.OperationalError("server closed the connection unexpectedly")
        self.conn.sql = sql
        if "pg_try_advisory" in sql:
            self.conn.queries.append((sql, params))

    def fetchone(self):
        return (self.conn.grant,) if "pg_try_advisory" in self.conn.sql else (1,)


class FakeLockConn:
    """A lock connection that records, in `events`, when the lock is taken, answers a ping and is
    released. `dead` makes every statement fail (the connection is gone); `grant` is the answer of
    the lock query."""

    def __init__(self, events, grant=True):
        self.events, self.grant = events, grant
        self.dead, self.closed, self.sql = False, 0, ""
        self.queries = []          # (sql, params) of every lock query

    def cursor(self):
        return FakeLockCursor(self)

    def commit(self):
        if self.dead:
            raise psycopg2.OperationalError("server closed the connection unexpectedly")
        if "pg_try_advisory" in self.sql:
            self.events.append(("lock", "taken" if self.grant else "busy"))
        else:
            self.events.append(("lock", "alive"))

    def rollback(self):
        if "pg_try_advisory" not in self.sql:
            self.events.append(("lock", "dead"))

    def close(self):
        self.closed += 1
        self.events.append(("lock", "released"))
        if self.close_error:
            raise self.close_error

    close_error = None


class FakeMoveLock:
    """What `writer_move_lock._connect` returns during a test. `events` is a list the test may
    replace by `FakeState.log` to see the lock in the order of the writes. `plan` lists what the
    next connections do: 'ok', 'busy' or 'unavailable'; once empty every connection is 'ok'."""

    def __init__(self):
        self.events = []
        self.plan = []
        self.conns = []

    def lock_queries(self):
        """(kind, lock class, tenant) of every lock query, in order: `kind` is "shared" or
        "exclusive", read from the SQL the way the server would."""
        out = []
        for conn in self.conns:
            for sql, params in conn.queries:
                kind = "shared" if "pg_try_advisory_lock_shared(" in sql else (
                    "exclusive" if "pg_try_advisory_lock(" in sql else sql)
                out.append((kind, params[0], params[1]))
        return out

    def connect(self):
        mode = self.plan.pop(0) if self.plan else "ok"
        if mode == "unavailable":
            import writer_move_lock
            raise writer_move_lock.LockUnavailable("cannot connect (OperationalError)")
        conn = FakeLockConn(self.events, grant=(mode != "busy"))
        self.conns.append(conn)
        return conn


@pytest.fixture(autouse=True)
def move_lock(request):
    """Every test gets a lock that can be taken, unless it tests the lock module itself."""
    if request.module.__name__.endswith(("test_writer_move_lock", "test_writer_move_lock_pg",
                                         "test_writer_move_pg_cross")):
        yield None
        return
    import writer_move_lock
    fake = FakeMoveLock()
    original = writer_move_lock._connect
    writer_move_lock._connect = fake.connect
    try:
        yield fake
    finally:
        writer_move_lock._connect = original
