"""/metrics endpoint and the inbox counters/gauges."""

import asyncio
import math
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import telemetry_worker.inbox as inbox_mod
from telemetry_worker.inbox import InboxRow, NotificationInbox
from telemetry_worker.inbox_consumer import (
    INBOX_DEPTH, INBOX_OLDEST_AGE, INBOX_STATS_FAILED, InboxConsumer)
from prometheus_client import generate_latest

T0 = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)


def test_metrics_endpoint_serves_inbox_series():
    from app import app

    INBOX_DEPTH.labels(queue="telemetry").set(3)
    client = TestClient(app)  # no context manager: lifespan (DB) is not started
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    for name in ("inbox_depth", "inbox_oldest_age_seconds", "inbox_enqueued_total",
                 "inbox_enqueue_failed_total", "inbox_acked_total", "inbox_retried_total",
                 "inbox_dead_total", "consumer_active"):
        assert name in body, name
    assert 'inbox_depth{queue="telemetry"} 3.0' in body


def test_health_unchanged_by_metrics_mount():
    from app import app

    assert TestClient(app).get("/health").status_code in (200, 503)


def val(counter, queue):
    return counter.labels(queue=queue)._value.get()


class FakeConn:
    def __init__(self, attempts):
        self.attempts = attempts
        self.buried = 0

    async def fetchval(self, sql, *args):
        return self.attempts

    async def execute(self, sql, *args):
        if "notification_inbox_dead" in sql:
            self.buried += 1

    def transaction(self):
        return _Ctx()


class _Ctx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, conn=None):
        self.conn = conn
        self.executed = []

    def acquire(self):
        pool = self

        class _A:
            async def __aenter__(self_):
                return pool.conn

            async def __aexit__(self_, *exc):
                return False

        return _A()

    async def execute(self, sql, *args):
        self.executed.append(sql)


def row():
    return InboxRow(1, "acme", "urn:e:1", "DeviceMeasurement", T0, {}, 0)


def test_ack_retry_bury_counters_count_once():
    q = "t-counters"
    a0, r0, d0 = (val(inbox_mod.INBOX_ACKED, q), val(inbox_mod.INBOX_RETRIED, q),
                  val(inbox_mod.INBOX_DEAD, q))
    asyncio.run(NotificationInbox(FakePool(), q).ack([1, 2, 3]))
    asyncio.run(NotificationInbox(FakePool(), q).ack([]))
    asyncio.run(NotificationInbox(FakePool(FakeConn(attempts=3)), q).retry(row(), "e"))
    # the 12th attempt buries: dead, not retried
    asyncio.run(NotificationInbox(FakePool(FakeConn(attempts=12)), q).retry(row(), "e"))
    asyncio.run(NotificationInbox(FakePool(FakeConn(attempts=1)), q).bury(row(), "e"))
    assert val(inbox_mod.INBOX_ACKED, q) - a0 == 3
    assert val(inbox_mod.INBOX_RETRIED, q) - r0 == 1
    assert val(inbox_mod.INBOX_DEAD, q) - d0 == 2


def test_enqueue_failure_counter_and_enqueued():
    q = "t-enqueue"

    class Boom:
        def acquire(self):
            raise OSError("db down")

    e0, f0 = val(inbox_mod.INBOX_ENQUEUED, q), val(inbox_mod.INBOX_ENQUEUE_FAILED, q)
    ent = {"id": "urn:e:1", "type": "DeviceMeasurement"}
    with pytest.raises(OSError):
        asyncio.run(NotificationInbox(Boom(), q).enqueue("acme", [ent], None))
    assert val(inbox_mod.INBOX_ENQUEUE_FAILED, q) - f0 == 1
    # only malformed entities: nothing stored, nothing counted, no failure
    assert asyncio.run(NotificationInbox(Boom(), q).enqueue("acme", [{"x": 1}], None)) == 0
    assert val(inbox_mod.INBOX_ENQUEUED, q) - e0 == 0
    assert val(inbox_mod.INBOX_ENQUEUE_FAILED, q) - f0 == 1


class StatsInbox:
    def __init__(self, queue):
        self.queue = queue
        self.stats_calls = 0
        self.fail = False

    async def stats(self):
        self.stats_calls += 1
        if self.fail:
            raise OSError("db down")
        return 7, 42.5

    async def claim_round(self, limit):
        return []


class OkConn:
    def is_closed(self):
        return False

    async def fetchval(self, sql, *args):
        return True if "pg_try_advisory_lock" in sql else 1

    def terminate(self):
        pass


class OkPool:
    async def acquire(self):
        return OkConn()

    async def release(self, conn):
        pass


def test_consumer_refreshes_gauges_while_idle_and_survives_stats_failure():
    q = "t-gauges"
    inbox = StatsInbox(q)

    async def main():
        async def noop(*a):
            return None

        c = InboxConsumer(inbox, OkPool(), process=noop, idle_s=0.01)
        c._stats_refresh_s = 0.02
        c._lock_check_s = 0.0
        task = asyncio.create_task(c.run())
        for _ in range(200):
            await asyncio.sleep(0.01)
            if INBOX_DEPTH.labels(queue=q)._value.get() == 7:
                break
        assert INBOX_DEPTH.labels(queue=q)._value.get() == 7
        assert INBOX_OLDEST_AGE.labels(queue=q)._value.get() == 42.5
        inbox.fail = True
        calls = inbox.stats_calls
        await asyncio.sleep(0.2)
        assert inbox.stats_calls > calls  # still looping after failures
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())


def test_failed_stats_gives_nan_and_counter_then_recovers():
    q = "t-nan"
    inbox = StatsInbox(q)

    async def main():
        c = InboxConsumer(inbox, OkPool(), idle_s=0.01)
        f0 = INBOX_STATS_FAILED.labels(queue=q)._value.get()
        await c._refresh_stats()
        assert INBOX_DEPTH.labels(queue=q)._value.get() == 7
        inbox.fail = True
        await c._refresh_stats()
        assert math.isnan(INBOX_DEPTH.labels(queue=q)._value.get())
        assert math.isnan(INBOX_OLDEST_AGE.labels(queue=q)._value.get())
        assert INBOX_STATS_FAILED.labels(queue=q)._value.get() == f0 + 1
        inbox.fail = False
        await c._refresh_stats()
        assert INBOX_DEPTH.labels(queue=q)._value.get() == 7
        assert INBOX_OLDEST_AGE.labels(queue=q)._value.get() == 42.5

    asyncio.run(main())


def test_lock_loss_removes_the_gauge_series():
    q = "t-lockloss"
    inbox = StatsInbox(q)

    async def main():
        c = InboxConsumer(inbox, OkPool(), idle_s=0.01)
        c._set_active(True)
        await c._refresh_stats()
        out = generate_latest().decode()
        assert f'inbox_depth{{queue="{q}"}}' in out
        c._set_active(False)
        out = generate_latest().decode()
        assert f'inbox_depth{{queue="{q}"}}' not in out
        assert f'inbox_oldest_age_seconds{{queue="{q}"}}' not in out
        c._set_active(False)  # idempotent: nothing to remove

    asyncio.run(main())
