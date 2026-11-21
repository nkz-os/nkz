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
import pytest

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


@pytest.mark.parametrize("missing", [True, False], ids=["table_missing", "insert_fails"])
def test_dead_letter_failure_raises_from_write_batch(missing):
    """
    A poison record whose dead-letter insert also fails (DLQ table missing, or any
    other non-connection error) is stored nowhere: write_batch() raises instead of
    dropping it, so the caller (inbox) keeps the reading. Records before it stay
    inserted (per-record fallback, no wrapping transaction).
    """
    events = [_event("good-1"), _event("poison-1"), _event("good-2")]
    conn = FakeConnection(
        poison_devices={"poison-1"}, dlq_table_missing=missing, dlq_should_fail=not missing)
    sink = PostgreSQLSink(dsn="postgresql://fake")
    sink._pool = FakePool(conn)

    expected = asyncpg.exceptions.UndefinedTableError if missing else RuntimeError
    with pytest.raises(expected):
        asyncio.run(sink.write_batch(events))

    assert {r[2] for r in conn.inserted_records} == {"good-1"}
    assert conn.dlq_records == []


@pytest.mark.parametrize("missing", [True, False], ids=["table_missing", "insert_fails"])
def test_dead_letter_failure_raises_from_write(missing):
    conn = FakeConnection(
        poison_devices={"poison-1"}, dlq_table_missing=missing, dlq_should_fail=not missing)
    sink = PostgreSQLSink(dsn="postgresql://fake")
    sink._pool = FakePool(conn)

    expected = asyncpg.exceptions.UndefinedTableError if missing else RuntimeError
    with pytest.raises(expected):
        asyncio.run(sink.write(_event("poison-1")))
    assert conn.dlq_records == []


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


class _CopyOkConnection(FakeConnection):
    async def copy_records_to_table(self, table, records=None, columns=None):
        self.inserted_records.extend(records)


def test_write_batch_returns_only_the_inserted_events():
    """The caller records replay state only for events really inserted (never dead-lettered)."""
    events = [_event("good-1"), _event("poison-1"), _event("good-2")]
    sink = PostgreSQLSink(dsn="postgresql://fake")
    sink._pool = FakePool(FakeConnection(poison_devices={"poison-1"}))
    stored = asyncio.run(sink.write_batch(events))
    assert [e.device_id for e in stored] == ["good-1", "good-2"]

    sink._pool = FakePool(_CopyOkConnection())
    events = [_event("a"), _event("b")]
    assert asyncio.run(sink.write_batch(events)) == events
    assert asyncio.run(sink.write_batch([])) == []


def test_write_returns_true_when_inserted_false_when_dead_lettered():
    sink = PostgreSQLSink(dsn="postgresql://fake")
    sink._pool = FakePool(FakeConnection())
    assert asyncio.run(sink.write(_event("good-1"))) is True
    sink._pool = FakePool(FakeConnection(poison_devices={"poison-1"}))
    assert asyncio.run(sink.write(_event("poison-1"))) is False


# ── Connection-class errors propagate, never dead-lettered (retry by the inbox) ──


class ConnDropConnection(FakeConnection):
    """COPY fails with `copy_error`; per-record inserts of `drop_devices` raise `insert_error`."""

    def __init__(self, copy_error=None, insert_error=None, drop_devices=(), dlq_error=None, **kw):
        super().__init__(**kw)
        self.copy_error = copy_error or RuntimeError("simulated COPY failure (schema mismatch)")
        self.insert_error = insert_error
        self.drop_devices = set(drop_devices)
        self.dlq_error = dlq_error
        self.copy_calls = 0

    async def copy_records_to_table(self, table, records=None, columns=None):
        self.copy_calls += 1
        raise self.copy_error

    async def execute(self, sql, *args):
        if "telemetry_events_dlq" in sql and self.dlq_error is not None:
            raise self.dlq_error
        if "telemetry_events_dlq" not in sql and args[2] in self.drop_devices:
            raise self.insert_error
        return await super().execute(sql, *args)


class _BrokenPool:
    def __init__(self, error):
        self.error = error

    def acquire(self):
        raise self.error


def _sink_on(conn):
    sink = PostgreSQLSink(dsn="postgresql://fake")
    sink._pool = FakePool(conn)
    return sink



CONNECTION_ERRORS = [
    asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed in the middle of operation"),
    asyncpg.PostgresConnectionError("connection failure"),
    asyncpg.exceptions.InterfaceError("connection is closed"),
    ConnectionResetError("reset by peer"),
    asyncio.TimeoutError(),
]


@pytest.mark.parametrize("err", CONNECTION_ERRORS, ids=lambda e: type(e).__name__)
def test_copy_connection_error_propagates_without_fallback(err):
    conn = ConnDropConnection(copy_error=err)
    with pytest.raises(type(err)):
        asyncio.run(_sink_on(conn).write_batch([_event("good-1"), _event("good-2")]))
    assert conn.inserted_records == [] and conn.dlq_records == []


@pytest.mark.parametrize("err", CONNECTION_ERRORS, ids=lambda e: type(e).__name__)
def test_fallback_insert_connection_error_propagates_not_dead_lettered(err):
    conn = ConnDropConnection(insert_error=err, drop_devices={"good-2"})
    with pytest.raises(type(err)):
        asyncio.run(_sink_on(conn).write_batch([_event("good-1"), _event("good-2")]))
    assert conn.dlq_records == []


@pytest.mark.parametrize("err", CONNECTION_ERRORS, ids=lambda e: type(e).__name__)
def test_write_connection_error_propagates_not_dead_lettered(err):
    conn = ConnDropConnection(insert_error=err, drop_devices={"good-1"})
    with pytest.raises(type(err)):
        asyncio.run(_sink_on(conn).write(_event("good-1")))
    assert conn.dlq_records == []


@pytest.mark.parametrize("err", CONNECTION_ERRORS, ids=lambda e: type(e).__name__)
def test_dead_letter_connection_error_propagates(err):
    """A poison record whose DLQ insert hits a dead connection is not silently dropped."""
    conn = ConnDropConnection(poison_devices={"poison-1"}, dlq_error=err)
    with pytest.raises(type(err)):
        asyncio.run(_sink_on(conn).write_batch([_event("good-1"), _event("poison-1")]))
    conn = ConnDropConnection(poison_devices={"poison-1"}, dlq_error=err)
    with pytest.raises(type(err)):
        asyncio.run(_sink_on(conn).write(_event("poison-1")))


def test_pool_acquire_error_propagates():
    sink = PostgreSQLSink(dsn="postgresql://fake")
    sink._pool = _BrokenPool(ConnectionRefusedError("refused"))
    with pytest.raises(ConnectionRefusedError):
        asyncio.run(sink.write(_event("good-1")))
    with pytest.raises(ConnectionRefusedError):
        asyncio.run(sink.write_batch([_event("good-1"), _event("good-2")]))


@pytest.mark.parametrize("bad", [
    asyncpg.DataError("invalid input for query argument $6"),
    asyncpg.exceptions._base.DataError("invalid input"),  # an InterfaceError + ValueError
    asyncpg.exceptions.ClientConfigurationError("bad client configuration"),
], ids=["DataError", "InterfaceDataError", "ClientConfigurationError"])
def test_data_and_client_errors_are_still_dead_lettered(bad):
    """Bad input is a poison record even when its class is an InterfaceError: it is
    dead-lettered, not propagated as a connection failure."""
    conn = ConnDropConnection(insert_error=bad, drop_devices={"poison-1"})
    stored = asyncio.run(_sink_on(conn).write_batch([_event("good-1"), _event("poison-1")]))
    assert [e.device_id for e in stored] == ["good-1"]
    assert [r[2] for r in conn.dlq_records] == ["poison-1"]
    conn = ConnDropConnection(insert_error=bad, drop_devices={"poison-1"})
    assert asyncio.run(_sink_on(conn).write(_event("poison-1"))) is False
    assert [r[2] for r in conn.dlq_records] == ["poison-1"]


SERVER_STATE_ERRORS = [
    asyncpg.exceptions.CannotConnectNowError("the database system is starting up"),
    asyncpg.exceptions.TooManyConnectionsError("too many clients already"),
    asyncpg.exceptions.AdminShutdownError("terminating connection due to administrator command"),
    asyncpg.exceptions.CrashShutdownError("terminating connection due to crash of another process"),
]


@pytest.mark.parametrize("err", SERVER_STATE_ERRORS, ids=lambda e: type(e).__name__)
def test_server_state_errors_are_connection_errors(err):
    """Postgres restarting, full or shutting down: retry, never dead-letter."""
    from telemetry_worker.event_sink import is_connection_error

    assert is_connection_error(err)
    sink = PostgreSQLSink(dsn="postgresql://fake")
    sink._pool = _BrokenPool(err)
    with pytest.raises(type(err)):
        asyncio.run(sink.write(_event("good-1")))
    conn = ConnDropConnection(insert_error=err, drop_devices={"good-1"})
    with pytest.raises(type(err)):
        asyncio.run(_sink_on(conn).write(_event("good-1")))
    assert conn.dlq_records == []


def test_sink_not_started_is_a_connection_error():
    from telemetry_worker.event_sink import SinkNotStartedError, is_connection_error

    sink = PostgreSQLSink(dsn="postgresql://fake")
    for call in (lambda: sink.write(_event("x")), lambda: sink.write_batch([_event("x")])):
        with pytest.raises(SinkNotStartedError) as exc:
            asyncio.run(call())
        assert isinstance(exc.value, RuntimeError)  # existing callers catching RuntimeError
        assert is_connection_error(exc.value)
    assert not is_connection_error(RuntimeError("PostgreSQLSink not started"))
