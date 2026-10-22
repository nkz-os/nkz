"""Tests for PostgreSQLSink.write_batch() dead-letter fallback.

Async coroutines are driven with ``asyncio.run`` in sync test functions —
the telemetry-worker smoke lane has no pytest-asyncio plugin (mirrors
test_notification_dedup.py / the sensor-health-beat test pattern).

Covers the fix for: a single poison record in the individual-insert
fallback (after a batch COPY failure) used to run inside one
conn.transaction(), so one bad record rolled back every good record in
the batch and the exception propagated (batch lost/retried forever).
Now each fallback insert is its own error boundary; a record whose insert
still fails is dead-lettered into telemetry_events_dlq instead.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

# ── Path setup (mirrors other telemetry-worker tests) ──────────────────────
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")

for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import asyncpg

from telemetry_worker.event_sink import (
    PostgreSQLSink,
    TelemetryEvent,
    TELEMETRY_DLQ_TOTAL,
)

OBSERVED_AT = datetime(2026, 8, 4, 10, 0, 0, tzinfo=timezone.utc)
TENANT_ID = "tenant1"


def _event(device_id: str, entity_type: str = "AgriSensor") -> TelemetryEvent:
    return TelemetryEvent(
        tenant_id=TENANT_ID,
        observed_at=OBSERVED_AT,
        device_id=device_id,
        entity_id=f"urn:ngsi-ld:{entity_type}:{TENANT_ID}:{device_id}",
        entity_type=entity_type,
        payload={"temperature": 21.5},
        quality_flag="valid",
    )


class _AcquireCtx:
    """Minimal stand-in for asyncpg's PoolAcquireContext (async with pool.acquire())."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc_info):
        return False


class FakePool:
    """Minimal stand-in for asyncpg.Pool: acquire() always returns the same fake conn."""

    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _AcquireCtx(self._conn)


class FakeConnection:
    """
    Minimal stand-in for asyncpg.Connection driving write_batch()'s fallback path.

    - copy_records_to_table always raises, forcing the per-record fallback.
    - execute() against INSERT_SQL raises for device_ids in poison_devices
      (simulating a constraint violation / poison record), and records the
      args for every other (good) insert.
    - execute() against INSERT_DLQ_SQL records the dead-lettered args, unless
      configured to fail itself (dlq_should_fail / dlq_table_missing).
    """

    def __init__(
        self,
        poison_devices=None,
        dlq_should_fail=False,
        dlq_table_missing=False,
    ):
        self.poison_devices = poison_devices or set()
        self.dlq_should_fail = dlq_should_fail
        self.dlq_table_missing = dlq_table_missing
        self.inserted_records = []
        self.dlq_records = []

    async def copy_records_to_table(self, table, records=None, columns=None):
        raise RuntimeError("simulated COPY failure (schema mismatch)")

    async def execute(self, sql, *args):
        if "telemetry_events_dlq" in sql:
            if self.dlq_table_missing:
                raise asyncpg.exceptions.UndefinedTableError(
                    'relation "telemetry_events_dlq" does not exist'
                )
            if self.dlq_should_fail:
                raise RuntimeError("simulated DLQ insert failure")
            self.dlq_records.append(args)
            return

        # args = (tenant_id, observed_at, device_id, entity_id, entity_type, payload, quality_flag)
        device_id = args[2]
        if device_id in self.poison_devices:
            raise RuntimeError(f"simulated constraint violation for device={device_id}")
        self.inserted_records.append(args)


def _dlq_counter_value(tenant_id: str, entity_type: str) -> float:
    """Read the current TELEMETRY_DLQ_TOTAL sample for a label combo (0.0 if unseen)."""
    for metric in TELEMETRY_DLQ_TOTAL.collect():
        for sample in metric.samples:
            if sample.name.endswith("_total") and sample.labels == {
                "tenant_id": tenant_id,
                "entity_type": entity_type,
            }:
                return sample.value
    return 0.0


def test_poison_record_dead_lettered_good_records_persist():
    """
    COPY fails -> per-record fallback. One record (device 'poison-1') fails
    its individual insert. The two good records must still be persisted,
    the poison record must be written to the DLQ with its error message,
    and write_batch() must NOT raise.
    """
    events = [_event("good-1"), _event("poison-1"), _event("good-2")]
    conn = FakeConnection(poison_devices={"poison-1"})
    sink = PostgreSQLSink(dsn="postgresql://fake")
    sink._pool = FakePool(conn)

    before = _dlq_counter_value(TENANT_ID, "AgriSensor")

    asyncio.run(sink.write_batch(events))  # must not raise

    good_device_ids = {r[2] for r in conn.inserted_records}
    assert good_device_ids == {"good-1", "good-2"}
    assert len(conn.inserted_records) == 2

    assert len(conn.dlq_records) == 1
    dlq_args = conn.dlq_records[0]
    # (tenant_id, observed_at, device_id, entity_id, entity_type, payload, quality_flag, error_message)
    assert dlq_args[0] == TENANT_ID
    assert dlq_args[2] == "poison-1"
    assert dlq_args[4] == "AgriSensor"
    assert "poison-1" in dlq_args[-1]  # error_message mentions the failing device

    after = _dlq_counter_value(TENANT_ID, "AgriSensor")
    assert after == before + 1


def test_all_good_fallback_no_dlq_writes():
    """
    COPY fails -> per-record fallback, but every individual insert succeeds.
    No record should be dead-lettered and write_batch() must not raise.
    """
    events = [_event("good-1"), _event("good-2"), _event("good-3")]
    conn = FakeConnection(poison_devices=set())
    sink = PostgreSQLSink(dsn="postgresql://fake")
    sink._pool = FakePool(conn)

    asyncio.run(sink.write_batch(events))  # must not raise

    assert len(conn.inserted_records) == 3
    assert conn.dlq_records == []


def test_dlq_table_missing_pre_migration_does_not_crash():
    """
    If telemetry_events_dlq doesn't exist yet (deploy landed before migration
    098), the DLQ insert itself raises UndefinedTableError. write_batch()
    must still not raise, and the good record must still persist.
    """
    events = [_event("good-1"), _event("poison-1")]
    conn = FakeConnection(poison_devices={"poison-1"}, dlq_table_missing=True)
    sink = PostgreSQLSink(dsn="postgresql://fake")
    sink._pool = FakePool(conn)

    asyncio.run(sink.write_batch(events))  # must not raise even without the DLQ table

    assert {r[2] for r in conn.inserted_records} == {"good-1"}
    assert conn.dlq_records == []  # DLQ insert never landed (table missing)


def test_dlq_insert_itself_failing_does_not_crash_batch():
    """
    Last-resort safety: if the DLQ insert raises for some other reason
    (e.g. transient DB error), write_batch() must still not raise and must
    still persist the other good records in the batch.
    """
    events = [_event("good-1"), _event("poison-1"), _event("good-2")]
    conn = FakeConnection(poison_devices={"poison-1"}, dlq_should_fail=True)
    sink = PostgreSQLSink(dsn="postgresql://fake")
    sink._pool = FakePool(conn)

    asyncio.run(sink.write_batch(events))  # must not raise

    assert {r[2] for r in conn.inserted_records} == {"good-1", "good-2"}
    assert conn.dlq_records == []  # attempted, but failed silently (logged)


def test_write_batch_raises_if_pool_not_started():
    sink = PostgreSQLSink(dsn="postgresql://fake")
    try:
        asyncio.run(sink.write_batch([_event("x")]))
        assert False, "expected RuntimeError when pool not started"
    except RuntimeError:
        pass


def test_write_batch_empty_list_is_noop():
    conn = FakeConnection()
    sink = PostgreSQLSink(dsn="postgresql://fake")
    sink._pool = FakePool(conn)

    asyncio.run(sink.write_batch([]))  # must not raise, must not touch conn

    assert conn.inserted_records == []
    assert conn.dlq_records == []
