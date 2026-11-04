"""Late data (e.g. lab samples dated weeks ago) landing in an already compressed chunk.

telemetry_events has a primary key; before TimescaleDB 2.11 an insert into a
compressed chunk of such a hypertable fails with FeatureNotSupportedError. The
sink then decompresses the chunk that covers the event time and retries once
(the compression policy recompresses it later). A record that still cannot be
stored is dead-lettered, never silently lost, also on the single-event path.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")
for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import asyncpg

from telemetry_worker.event_sink import PostgreSQLSink, TelemetryEvent

LATE = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
CHUNK = "_timescaledb_internal._hyper_3_513_chunk"
# The AFTER INSERT trigger copies measurements into telemetry_measurements (also
# compressed, with ON CONFLICT): its chunk for the same instant must be decompressed too.
MEASUREMENTS_CHUNK = "_timescaledb_internal._hyper_44_514_chunk"
COMPRESSED_MSG = "insert into a compressed chunk that has primary or unique constraint is not supported"
ON_CONFLICT_MSG = "insert with ON CONFLICT clause is not supported on compressed chunks"


def _event(device_id="s1", observed_at=LATE):
    return TelemetryEvent(
        tenant_id="tenant-a", observed_at=observed_at, device_id=device_id,
        entity_id=f"urn:ngsi-ld:AgriPlantSample:tenant-a:{device_id}", entity_type="AgriPlantSample",
        payload={"measurements": {"nitrogen": 0.018}}, quality_flag="valid",
    )


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _Acquire(self._conn)


class FakeConn:
    """One compressed chunk covering LATE; inserts into it fail until it is decompressed."""

    def __init__(self, keep_failing=False, other_error=False, chunk_found=True):
        self.compressed = True
        self.keep_failing = keep_failing
        self.other_error = other_error
        self.chunk_found = chunk_found
        self.inserted, self.dlq, self.decompressed, self.lookups = [], [], [], []

    async def copy_records_to_table(self, table, records=None, columns=None):
        raise asyncpg.exceptions.FeatureNotSupportedError(COMPRESSED_MSG)

    async def fetch(self, sql, *args):
        assert "timescaledb_information.chunks" in sql
        assert "telemetry_events" in sql and "telemetry_measurements" in sql
        self.lookups.append(args)
        return [{"chunk": CHUNK}, {"chunk": MEASUREMENTS_CHUNK}] if self.chunk_found else []

    async def execute(self, sql, *args):
        if "decompress_chunk" in sql:
            self.decompressed.append(args)
            self.compressed = False
            return
        if "telemetry_events_dlq" in sql:
            self.dlq.append(args)
            return
        if self.other_error:
            raise RuntimeError("simulated constraint violation")
        if self.compressed or self.keep_failing:
            raise asyncpg.exceptions.FeatureNotSupportedError(COMPRESSED_MSG)
        self.inserted.append(args)


def _sink(conn):
    s = PostgreSQLSink(dsn="postgresql://unused")
    s._pool = _Pool(conn)
    return s


def test_write_decompresses_the_chunk_of_the_event_and_retries():
    conn = FakeConn()
    asyncio.run(_sink(conn).write(_event()))
    assert len(conn.inserted) == 1
    assert conn.decompressed == [(CHUNK,), (MEASUREMENTS_CHUNK,)]
    assert conn.lookups == [(LATE,)]
    assert conn.dlq == []


def test_batch_fallback_decompresses_once_and_keeps_every_record():
    conn = FakeConn()
    asyncio.run(_sink(conn).write_batch([_event("s1"), _event("s2")]))
    assert [r[2] for r in conn.inserted] == ["s1", "s2"]
    assert conn.decompressed == [(CHUNK,), (MEASUREMENTS_CHUNK,)]
    assert conn.dlq == []


def test_write_dead_letters_when_the_retry_still_fails():
    conn = FakeConn(keep_failing=True)
    asyncio.run(_sink(conn).write(_event()))  # must not raise
    assert conn.inserted == []
    assert len(conn.dlq) == 1 and "compressed chunk" in conn.dlq[0][-1]


def test_write_dead_letters_other_errors_without_decompressing():
    conn = FakeConn(other_error=True)
    conn.compressed = False
    asyncio.run(_sink(conn).write(_event()))
    assert conn.decompressed == [] and conn.lookups == []
    assert len(conn.dlq) == 1


def test_no_chunk_found_dead_letters():
    conn = FakeConn(chunk_found=False)
    asyncio.run(_sink(conn).write(_event()))
    assert conn.decompressed == [] and len(conn.dlq) == 1


def test_measurements_trigger_error_also_triggers_decompression():
    """The insert itself may pass and the trigger's ON CONFLICT insert fail instead."""
    conn = FakeConn()

    async def execute(sql, *args, _orig=conn.execute):
        if "decompress_chunk" not in sql and "telemetry_events_dlq" not in sql and conn.compressed:
            raise asyncpg.exceptions.FeatureNotSupportedError(ON_CONFLICT_MSG)
        return await _orig(sql, *args)
    conn.execute = execute
    asyncio.run(_sink(conn).write(_event()))
    assert len(conn.inserted) == 1 and conn.dlq == []
    assert (MEASUREMENTS_CHUNK,) in conn.decompressed

