"""
EventSink abstraction for telemetry persistence.

Current implementation: PostgreSQL/TimescaleDB via asyncpg connection pool.
Future: swap in KafkaSink when scale demands it (>2000 devices).
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

import asyncpg
from prometheus_client import Counter

logger = logging.getLogger(__name__)

# Poison telemetry records isolated into telemetry_events_dlq instead of
# failing the whole batch (see write_batch()). Labelled by tenant/entity_type
# so a spike in one tenant/device-type is visible without reading logs.
TELEMETRY_DLQ_TOTAL = Counter(
    "telemetry_dlq_total",
    "Total telemetry records dead-lettered after failing individual insert",
    ["tenant_id", "entity_type"],
)


class TelemetryEvent:
    """Single telemetry event ready for persistence."""

    __slots__ = (
        "tenant_id",
        "observed_at",
        "device_id",
        "entity_id",
        "entity_type",
        "payload",
        "quality_flag",  # NEW
    )

    def __init__(
        self,
        tenant_id: Optional[str],
        observed_at: datetime,
        device_id: str,
        entity_id: str,
        entity_type: str,
        payload: Dict[str, Any],
        quality_flag: Optional[str] = None,  # NEW
    ):
        self.tenant_id = tenant_id
        self.observed_at = observed_at
        self.device_id = device_id
        self.entity_id = entity_id
        self.entity_type = entity_type
        self.payload = payload
        self.quality_flag = quality_flag  # NEW

    def as_tuple(self) -> tuple:
        """Return values as a tuple for batch insertion."""
        return (
            self.tenant_id,
            self.observed_at,
            self.device_id,
            self.entity_id,
            self.entity_type,
            json.dumps(self.payload),
            self.quality_flag,  # NEW
        )


class EventSink(ABC):
    """Abstract interface for telemetry event persistence."""

    @abstractmethod
    async def start(self) -> None:
        """Initialize resources (connection pools, etc.)."""

    @abstractmethod
    async def stop(self) -> None:
        """Release resources."""

    @abstractmethod
    async def write(self, event: TelemetryEvent) -> None:
        """Persist a single event."""

    @abstractmethod
    async def write_batch(self, events: List[TelemetryEvent]) -> None:
        """Persist a batch of events."""


class PostgreSQLSink(EventSink):
    """
    Async PostgreSQL/TimescaleDB sink using asyncpg connection pool.

    - Pool: 5-20 connections (configurable)
    - Batch: uses copy_records_to_table for high throughput
    - Single events: prepared statement for low latency
    """

    INSERT_SQL = """
        INSERT INTO telemetry_events (
            tenant_id, observed_at, device_id,
            entity_id, entity_type, payload,
            quality_flag
        )
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
    """

    # Dead-letter queue for records that fail their individual insert during
    # the write_batch() COPY fallback. Same column shape as INSERT_SQL plus
    # error_message. See migration 098_telemetry_events_dlq.sql.
    INSERT_DLQ_SQL = """
        INSERT INTO telemetry_events_dlq (
            tenant_id, observed_at, device_id,
            entity_id, entity_type, payload,
            quality_flag, error_message
        )
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8)
    """

    # Chunk of telemetry_events that covers an instant (for late data, see _insert_one).
    CHUNK_AT_SQL = """
        SELECT format('%I.%I', chunk_schema, chunk_name)
        FROM timescaledb_information.chunks
        WHERE hypertable_name = 'telemetry_events'
          AND is_compressed
          AND range_start <= $1 AND range_end > $1
        LIMIT 1
    """
    DECOMPRESS_SQL = "SELECT decompress_chunk($1::regclass, if_compressed => true)"

    COLUMNS = [
        "tenant_id",
        "observed_at",
        "device_id",
        "entity_id",
        "entity_type",
        "payload",
        "quality_flag",
    ]

    def __init__(
        self,
        dsn: str,
        min_pool: int = 5,
        max_pool: int = 20,
    ):
        self._dsn = dsn
        self._min_pool = min_pool
        self._max_pool = max_pool
        self._pool: Optional[asyncpg.Pool] = None

    async def start(self) -> None:
        """Create the connection pool."""
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=self._min_pool,
            max_size=self._max_pool,
            command_timeout=30,
        )
        logger.info(
            f"PostgreSQLSink pool started (min={self._min_pool}, max={self._max_pool})"
        )

    async def stop(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            logger.info("PostgreSQLSink pool closed")

    async def write(self, event: TelemetryEvent) -> None:
        """Persist a single event; a record that cannot be stored is dead-lettered."""
        if not self._pool:
            raise RuntimeError("PostgreSQLSink not started")

        record = event.as_tuple()
        async with self._pool.acquire() as conn:
            try:
                await self._insert_one(conn, record, event)
            except Exception as insert_err:
                logger.warning(
                    "Telemetry record not stored (entity_id=%s, device_id=%s, tenant_id=%s): %s - dead-lettering",
                    event.entity_id, event.device_id, event.tenant_id, insert_err,
                )
                await self._dead_letter(conn, record, str(insert_err), event)

    async def _insert_one(self, conn: asyncpg.Connection, record: tuple, event: TelemetryEvent) -> None:
        """
        INSERT one record. Late data (a lab sample dated weeks ago) can fall in a chunk
        the compression policy already compressed; before TimescaleDB 2.11 inserting
        there fails on hypertables with a primary key. Then the chunk covering the
        event time is decompressed and the insert retried once; the compression
        policy recompresses the chunk on its next run. Anything else raises.
        """
        try:
            await conn.execute(self.INSERT_SQL, *record)
            return
        except asyncpg.exceptions.FeatureNotSupportedError as err:
            if "compressed chunk" not in str(err):
                raise
            chunk = await conn.fetchval(self.CHUNK_AT_SQL, event.observed_at)
            if not chunk:
                raise
            logger.warning(
                "Decompressing chunk %s for late telemetry (entity_id=%s, tenant_id=%s, observed_at=%s)",
                chunk, event.entity_id, event.tenant_id, event.observed_at.isoformat(),
            )
            await conn.execute(self.DECOMPRESS_SQL, chunk)
        await conn.execute(self.INSERT_SQL, *record)

    async def write_batch(self, events: List[TelemetryEvent]) -> None:
        """
        Persist a batch of events using copy_records_to_table for maximum throughput.

        Falls back to per-record inserts if COPY fails (e.g., schema mismatch).
        Each fallback insert is its own error boundary (no wrapping
        conn.transaction()): a poison record — bad data, constraint
        violation — must not roll back the other, valid records in the same
        batch. A record whose individual insert still fails is dead-lettered
        into telemetry_events_dlq (see _dead_letter()) and processing
        continues with the rest of the batch.
        """
        if not self._pool:
            raise RuntimeError("PostgreSQLSink not started")

        if not events:
            return

        records = [e.as_tuple() for e in events]

        async with self._pool.acquire() as conn:
            try:
                await conn.copy_records_to_table(
                    "telemetry_events",
                    records=records,
                    columns=self.COLUMNS,
                )
            except Exception as e:
                logger.warning(f"COPY failed ({e}), falling back to per-record inserts")
                dlq_count = 0
                for event, record in zip(events, records):
                    try:
                        await self._insert_one(conn, record, event)
                    except Exception as insert_err:
                        logger.warning(
                            "Poison telemetry record (entity_id=%s, device_id=%s, "
                            "tenant_id=%s): %s - dead-lettering",
                            event.entity_id,
                            event.device_id,
                            event.tenant_id,
                            insert_err,
                        )
                        await self._dead_letter(conn, record, str(insert_err), event)
                        dlq_count += 1

                if dlq_count:
                    logger.warning(
                        "Batch fallback complete: %d/%d persisted, %d dead-lettered",
                        len(events) - dlq_count,
                        len(events),
                        dlq_count,
                    )

        logger.debug(f"Batch persisted {len(events)} events")

    async def _dead_letter(
        self,
        conn: asyncpg.Connection,
        record: tuple,
        error_message: str,
        event: TelemetryEvent,
    ) -> None:
        """
        Insert a single poison record into telemetry_events_dlq.

        Last-resort path: this record already failed to persist into
        telemetry_events. Never raises — a failure here must not take down
        the rest of the batch. Handles the pre-migration case where
        telemetry_events_dlq doesn't exist yet (deploy landed before
        migration 098) by logging a clear warning instead of crashing
        ingestion.
        """
        try:
            await conn.execute(self.INSERT_DLQ_SQL, *record, error_message)
            TELEMETRY_DLQ_TOTAL.labels(
                tenant_id=event.tenant_id or "unknown",
                entity_type=event.entity_type or "unknown",
            ).inc()
        except asyncpg.exceptions.UndefinedTableError:
            logger.warning(
                "telemetry_events_dlq table does not exist yet (pending migration "
                "098_telemetry_events_dlq.sql) - poison record for entity_id=%s "
                "dropped (original error: %s)",
                event.entity_id,
                error_message,
            )
        except Exception as dlq_err:
            logger.error(
                "Failed to dead-letter poison record (entity_id=%s, device_id=%s): "
                "%s (original error: %s)",
                event.entity_id,
                event.device_id,
                dlq_err,
                error_message,
            )
