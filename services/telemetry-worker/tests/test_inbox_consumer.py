"""Inbox consumer: rounds, failure handling, concurrency, the single-consumer lock."""

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from telemetry_worker.inbox import InboxRow, NotificationInbox
from telemetry_worker.inbox_consumer import CONSUMER_ACTIVE, InboxConsumer
from telemetry_worker.notification_handler import TransientProcessingError

UTC = timezone.utc
T0 = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)


def row(row_id, entity_id, tenant="acme", minute=0, attempts=0):
    payload = {"id": entity_id, "type": "DeviceMeasurement", "n": row_id}
    return InboxRow(row_id, tenant, entity_id, "DeviceMeasurement",
                    T0 + timedelta(minutes=minute), payload, attempts)


class FakeInbox:
    """Serves claimed batches in order; records ack/retry/bury; optional failures."""

    def __init__(self, *batches, queue="telemetry"):
        self.queue = queue
        self.batches = list(batches)
        self.calls = []  # ("ack", ids) / ("retry", id, err) / ("bury", id, err)
        self.fail_claim = 0
        self.fail_ack = 0

    async def claim_round(self, limit):
        if self.fail_claim:
            self.fail_claim -= 1
            raise OSError("db down")
        return self.batches.pop(0) if self.batches else []

    async def ack(self, ids):
        if self.fail_ack:
            self.fail_ack -= 1
            raise OSError("db down")
        self.calls.append(("ack", sorted(ids)))

    async def retry(self, r, error):
        self.calls.append(("retry", r.id, error))

    async def bury(self, r, error):
        self.calls.append(("bury", r.id, error))


class Recorder:
    """A `process` fake: records calls; raises per row number in the payload."""

    def __init__(self, raises=None, delay=0.0):
        self.calls = []
        self.raises = raises or {}
        self.delay = delay
        self.in_flight = 0
        self.max_in_flight = 0

    async def __call__(self, entities, tenant_id):
        self.calls.append((entities, tenant_id))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            exc = self.raises.get(entities[0]["n"])
            if exc is not None:
                raise exc
        finally:
            self.in_flight -= 1

    def rows(self):
        return [e[0]["n"] for e, _ in self.calls]


def consumer(inbox, process, **kw):
    return InboxConsumer(inbox, lock_pool=None, process=process, **kw)


# --- run_once -----------------------------------------------------------------


def test_transient_failure_retries_and_holds_back_only_that_entity():
    # Round 1: E(1) fails transiently, F(2) ok. Round 2: E(3) must not run, F(4) ok.
    inbox = FakeInbox([[row(1, "E"), row(2, "F")], [row(3, "E", minute=1), row(4, "F", minute=1)]])
    proc = Recorder(raises={1: TransientProcessingError("pg down")})
    acked = asyncio.run(consumer(inbox, proc).run_once())
    assert acked == 2
    assert sorted(proc.rows()) == [1, 2, 4]
    # Per round: blocked set updated, successes acked, then failures retried; row 3 untouched.
    assert inbox.calls == [("ack", [2]), ("retry", 1, "pg down"), ("ack", [4])]


def test_permanent_failure_buries_and_skips_entity_rest_of_batch():
    inbox = FakeInbox([[row(1, "E")], [row(2, "E", minute=1)]])
    proc = Recorder(raises={1: ValueError("bad")})
    acked = asyncio.run(consumer(inbox, proc).run_once())
    assert acked == 0
    assert proc.rows() == [1]
    assert inbox.calls == [("bury", 1, repr(ValueError("bad")))]


def test_rounds_run_in_order_for_one_entity():
    inbox = FakeInbox([[row(1, "E")], [row(2, "E", minute=1)], [row(3, "E", minute=2)]])
    proc = Recorder()
    acked = asyncio.run(consumer(inbox, proc).run_once())
    assert acked == 3
    assert proc.rows() == [1, 2, 3]
    assert inbox.calls == [("ack", [1]), ("ack", [2]), ("ack", [3])]


def test_one_entity_per_process_call_with_its_tenant():
    inbox = FakeInbox([[row(1, "E", tenant="acme"), row(2, "F", tenant="tenant-a")]])
    proc = Recorder()
    asyncio.run(consumer(inbox, proc).run_once())
    assert sorted((e[0]["n"], len(e), t) for e, t in proc.calls) == [
        (1, 1, "acme"), (2, 1, "tenant-a")]


def test_same_entity_id_in_two_tenants_does_not_block_each_other():
    inbox = FakeInbox([[row(1, "E", tenant="acme"), row(2, "E", tenant="tenant-a")],
                       [row(3, "E", tenant="acme", minute=1), row(4, "E", tenant="tenant-a", minute=1)]])
    proc = Recorder(raises={1: TransientProcessingError("x")})
    acked = asyncio.run(consumer(inbox, proc).run_once())
    assert acked == 2
    assert sorted(proc.rows()) == [1, 2, 4]


def test_empty_claim_returns_zero():
    assert asyncio.run(consumer(FakeInbox(), Recorder()).run_once()) == 0


def test_distinct_entities_run_concurrently_bounded():
    batch = [row(i, f"E{i}") for i in range(1, 11)]
    proc = Recorder(delay=0.02)
    acked = asyncio.run(consumer(FakeInbox([batch]), proc, concurrency=3).run_once())
    assert acked == 10
    assert proc.max_in_flight == 3


def test_cancellation_neither_retries_nor_buries():
    inbox = FakeInbox([[row(1, "E"), row(2, "F")]])
    proc = Recorder(delay=10)

    async def main():
        task = asyncio.create_task(consumer(inbox, proc).run_once())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert inbox.calls == []


def test_store_failure_aborts_the_pass():
    inbox = FakeInbox([[row(1, "E")], [row(2, "F")]])
    inbox.fail_ack = 1
    proc = Recorder()
    with pytest.raises(OSError):
        asyncio.run(consumer(inbox, proc).run_once())
    assert proc.rows() == [1]


# --- run (lock + loop), with a fake lock pool ------------------------------------


async def _wait(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.02)



class FakeConn:
    def __init__(self, lock_free=True):
        self.lock_free = lock_free
        self.closed = False
        self.queries = []
        self.terminated = 0

    async def fetchval(self, sql, *args):
        if self.closed:
            raise OSError("connection lost")
        self.queries.append((sql, args))
        if "pg_try_advisory_lock" in sql:
            return self.lock_free
        return 1

    def is_closed(self):
        return self.closed

    def terminate(self):
        self.terminated += 1


class FakeLockPool:
    def __init__(self, conn):
        self.conn = conn
        self.acquired = 0
        self.released = 0

    async def acquire(self):
        self.acquired += 1
        return self.conn

    async def release(self, conn):
        self.released += 1


def gauge(queue="telemetry"):
    return CONSUMER_ACTIVE.labels(queue=queue)._value.get()


def fast(c):
    c._lock_retry_s = 0.01
    c._error_sleep_s = 0.01
    c._lock_check_s = 0.0
    return c


def test_run_survives_store_failures_and_holds_gauge():
    inbox = FakeInbox([[row(1, "E")]], [[row(2, "F")]])
    inbox.fail_claim = 1
    inbox.fail_ack = 1
    conn = FakeConn()
    proc = Recorder()
    seen = {}

    async def main():
        c = fast(InboxConsumer(inbox, FakeLockPool(conn), process=proc, idle_s=0.01))
        task = asyncio.create_task(c.run())
        for _ in range(200):
            await asyncio.sleep(0.01)
            if ("ack", [2]) in inbox.calls:
                break
        seen["active"] = gauge()
        seen["holds"] = c.holds_lock
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        seen["after"] = gauge()
        seen["holds_after"] = c.holds_lock

    asyncio.run(main())
    assert proc.rows() == [1, 2]  # row 1 processed, ack failed; row 2 acked
    assert ("ack", [2]) in inbox.calls
    assert seen == {"active": 1, "holds": True, "after": 0, "holds_after": False}
    assert conn.terminated == 1  # the tenure ended by ending the session
    lock_sql = conn.queries[0][0]
    assert "pg_try_advisory_lock(hashtext('inbox:' || $1::text))" in lock_sql
    assert conn.queries[0][1] == ("telemetry",)


def test_run_without_lock_does_not_consume():
    inbox = FakeInbox([[row(1, "E")]])
    pool = FakeLockPool(FakeConn(lock_free=False))
    proc = Recorder()

    async def main():
        c = fast(InboxConsumer(inbox, pool, process=proc))
        task = asyncio.create_task(c.run())
        await _wait(lambda: pool.acquired >= 3)
        active = gauge()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return active

    assert asyncio.run(main()) == 0
    assert proc.calls == []
    assert pool.acquired >= 2 and pool.released == pool.acquired  # retried, never leaked
    assert pool.conn.terminated == 0  # a healthy connection without the lock goes back as is


def test_run_reacquires_after_lock_connection_closes():
    inbox = FakeInbox()
    conn = FakeConn()
    pool = FakeLockPool(conn)

    async def main():
        c = fast(InboxConsumer(inbox, pool, process=Recorder(), idle_s=0.01))
        task = asyncio.create_task(c.run())
        await _wait(lambda: c.holds_lock)
        conn.closed = True
        await _wait(lambda: not c.holds_lock)
        await asyncio.sleep(0.05)  # attempts while closed fail and never take the lock
        lost = c.holds_lock
        conn.closed = False
        await _wait(lambda: c.holds_lock)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return lost

    assert asyncio.run(main()) is False
    assert pool.acquired >= 2 and pool.released == pool.acquired
    assert conn.terminated == pool.acquired  # every tenure / failed attempt ends the session


# --- Postgres: the advisory lock --------------------------------------------------

DSN = os.getenv("INBOX_TEST_DSN")
MIGRATION = (
    Path(__file__).resolve().parents[3] / "config/timescaledb/migrations/119_notification_inbox.sql"
)
db = pytest.mark.skipif(not DSN, reason="INBOX_TEST_DSN not set")


@pytest.fixture(scope="module")
def dbname():
    """Fresh database per module, migration 119 applied, dropped afterwards."""
    import asyncpg

    name = f"inbox_consumer_test_{uuid.uuid4().hex[:8]}"

    async def setup():
        admin = await asyncpg.connect(DSN)
        try:
            exists = await admin.fetchval("SELECT 1 FROM pg_roles WHERE rolname='nkz_core'")
            if not exists:
                await admin.execute("CREATE ROLE nkz_core NOLOGIN")
            await admin.execute(f'CREATE DATABASE "{name}"')
        finally:
            await admin.close()
        conn = await asyncpg.connect(DSN, database=name)
        try:
            await conn.execute(MIGRATION.read_text())
        finally:
            await conn.close()

    async def teardown():
        admin = await asyncpg.connect(DSN)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin.close()

    asyncio.run(setup())
    yield name
    asyncio.run(teardown())


@db
def test_single_consumer_lock_and_handover(dbname):
    import asyncpg

    async def main():
        data_pool = await asyncpg.create_pool(DSN, database=dbname, min_size=1, max_size=2)
        lock_a = await asyncpg.create_pool(DSN, database=dbname, min_size=0, max_size=1)
        lock_b = await asyncpg.create_pool(DSN, database=dbname, min_size=0, max_size=1)
        admin = await asyncpg.connect(DSN, database=dbname)
        inbox = NotificationInbox(data_pool)
        a = InboxConsumer(inbox, lock_a, process=Recorder(), idle_s=0.02)
        b = InboxConsumer(inbox, lock_b, process=Recorder(), idle_s=0.02)
        for c in (a, b):
            c._lock_retry_s = 0.05
            c._lock_check_s = 0.0
        a._lock_retry_s = 30.0  # after losing it, A waits: the handover goes to B
        tasks = []
        try:
            tasks.append(asyncio.create_task(a.run()))
            await _wait(lambda: a.holds_lock)
            tasks.append(asyncio.create_task(b.run()))
            await asyncio.sleep(0.3)  # B retried several times
            assert (a.holds_lock, b.holds_lock) == (True, False)

            holders = await admin.fetch(
                "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted")
            assert len(holders) == 1
            await admin.execute("SELECT pg_terminate_backend($1)", holders[0]["pid"])

            await _wait(lambda: b.holds_lock)
            assert not a.holds_lock
            new = await admin.fetch(
                "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted")
            assert len(new) == 1 and new[0]["pid"] != holders[0]["pid"]

            # Cancelling the holder releases the lock (connection back to its pool).
            tasks[1].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[1]
            assert not b.holds_lock

            async def lock_count():
                return await admin.fetchval(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted")

            for _ in range(100):
                if await lock_count() == 0:
                    break
                await asyncio.sleep(0.02)
            assert await lock_count() == 0
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await admin.close()
            for p in (lock_a, lock_b, data_pool):
                await asyncio.wait_for(p.close(), 5)  # a leaked lock connection hangs here

    asyncio.run(main())


@db
def test_lock_reacquired_after_server_drops_connection(dbname):
    """The one-connection lock pool must not stay checked out after a server-side drop."""
    import asyncpg

    async def main():
        data_pool = await asyncpg.create_pool(DSN, database=dbname, min_size=1, max_size=2)
        lock_pool = await asyncpg.create_pool(DSN, database=dbname, min_size=0, max_size=1)
        admin = await asyncpg.connect(DSN, database=dbname)
        c = InboxConsumer(NotificationInbox(data_pool), lock_pool, process=Recorder(), idle_s=0.02)
        c._lock_retry_s = 0.05
        c._lock_check_s = 0.0
        task = asyncio.create_task(c.run())
        try:
            for _ in range(3):
                await _wait(lambda: c.holds_lock)
                pid = await admin.fetchval(
                    "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted")
                await admin.execute("SELECT pg_terminate_backend($1)", pid)
                await _wait(lambda: not c.holds_lock)
            await _wait(lambda: c.holds_lock)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await admin.close()
            await asyncio.wait_for(lock_pool.close(), 5)
            await data_pool.close()

    asyncio.run(main())
