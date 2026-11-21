"""Durable notification inbox (Postgres store).

A delivery buffer, not a time-series store: the receiver inserts one row per
notified entity before answering, a single consumer processes the rows in
per-entity `(observed_at, id)` order and deletes them. After MAX_ATTEMPTS
infrastructure failures a row moves to `notification_inbox_dead`, from where
`redrive` sends it back. Schema: migration 119.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
from prometheus_client import Counter

from telemetry_worker.notification_handler import _date_observed, _extract_observed_at

logger = logging.getLogger(__name__)

INBOX_ENQUEUED = Counter(
    "inbox_enqueued_total", "Rows inserted into the inbox.", ["queue"])
INBOX_ENQUEUE_FAILED = Counter(
    "inbox_enqueue_failed_total", "Enqueue calls that raised (receiver answers 503).", ["queue"])
INBOX_ACKED = Counter(
    "inbox_acked_total", "Rows processed and deleted from the inbox.", ["queue"])
INBOX_RETRIED = Counter(
    "inbox_retried_total", "Rows put back with backoff after an infrastructure failure.", ["queue"])
INBOX_DEAD = Counter(
    "inbox_dead_total", "Rows moved to the dead table (12th attempt or permanent failure).", ["queue"])

MAX_ATTEMPTS = 12
BACKOFF_CAP_SECONDS = 300

# A NUL escape in the serialized JSON (jsonb rejects it); an even run of
# backslashes before `u0000` is an escaped literal backslash, not a NUL.
_NUL_ESCAPE = re.compile(r"(?<!\\)(?:\\\\)*\\u0000")

_COLUMNS = (
    "id, queue, tenant_id, entity_id, entity_type, notified_at, received_at, "
    "observed_at, payload, attempts, available_at, last_error"
)


def _is_data_error(exc: BaseException) -> bool:
    """Data-caused failure (bad value), as opposed to connection/infrastructure."""
    if isinstance(exc, asyncpg.DataError):
        return True
    return isinstance(exc, asyncpg.PostgresError) and str(exc.sqlstate or "").startswith("22")


@dataclass(frozen=True)
class InboxRow:
    id: int
    tenant_id: str
    entity_id: str
    entity_type: str
    observed_at: datetime
    payload: dict
    attempts: int


def _has_attribute_observed_at(entity: Dict[str, Any]) -> bool:
    """True when some attribute carries a parseable `observedAt` instant."""
    for attr in entity.values():
        if isinstance(attr, dict) and isinstance(attr.get("observedAt"), str):
            try:
                datetime.fromisoformat(attr["observedAt"].replace("Z", "+00:00"))
                return True
            except ValueError:
                continue
    return False


def observed_at_for(
    entity: Dict[str, Any], notified_at: Optional[datetime], received_at: datetime
) -> datetime:
    """Ordering instant of an entity. Pure; never fails.

    DeviceMeasurement: `dateObserved`. Otherwise (and as fallback): an attribute
    `observedAt` if one parses, else the notification time, else receive time.
    """
    if entity.get("type") == "DeviceMeasurement":
        found = _date_observed(entity)
        if found is not None:
            return found
    if _has_attribute_observed_at(entity):
        return _extract_observed_at(entity)
    return notified_at if notified_at is not None else received_at


def _row(rec: asyncpg.Record) -> InboxRow:
    payload = rec["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return InboxRow(
        id=rec["id"],
        tenant_id=rec["tenant_id"],
        entity_id=rec["entity_id"],
        entity_type=rec["entity_type"],
        observed_at=rec["observed_at"],
        payload=payload,
        attempts=rec["attempts"],
    )


class NotificationInbox:
    def __init__(self, pool: asyncpg.Pool, queue: str = "telemetry"):
        self._pool = pool
        self._queue = queue

    @property
    def queue(self) -> str:
        return self._queue

    async def enqueue(
        self, tenant_id: str, entities: List[Dict[str, Any]], notified_at: Optional[datetime]
    ) -> int:
        """Insert one row per entity (see `_enqueue`); counts rows stored and failures."""
        try:
            stored = await self._enqueue(tenant_id, entities, notified_at)
        except Exception:
            INBOX_ENQUEUE_FAILED.labels(queue=self._queue).inc()
            raise
        if stored:
            INBOX_ENQUEUED.labels(queue=self._queue).inc(stored)
        return stored

    async def _enqueue(
        self, tenant_id: str, entities: List[Dict[str, Any]], notified_at: Optional[datetime]
    ) -> int:
        """Insert one row per entity in one transaction; raises on DB failure.

        Entities without a string id/type can never be processed: logged, dropped.
        """
        received_at = datetime.now(timezone.utc)
        ids: List[str] = []
        types: List[str] = []
        observed: List[datetime] = []
        payloads: List[str] = []
        for entity in entities:
            eid = entity.get("id") if isinstance(entity, dict) else None
            etype = entity.get("type") if isinstance(entity, dict) else None
            if not isinstance(eid, str) or not eid or not isinstance(etype, str) or not etype:
                logger.warning(
                    "inbox_enqueue_dropped_malformed",
                    extra={"tenant_id": tenant_id, "queue": self._queue, "has_id": bool(eid)},
                )
                continue
            try:
                text = json.dumps(entity, allow_nan=False)
            except (ValueError, TypeError):
                text = None
            if text is None or _NUL_ESCAPE.search(text):
                logger.warning(
                    "inbox_enqueue_dropped_unstorable",
                    extra={"tenant_id": tenant_id, "queue": self._queue, "entity_id": eid},
                )
                continue
            ids.append(eid)
            types.append(etype)
            observed.append(observed_at_for(entity, notified_at, received_at))
            payloads.append(text)
        if not ids:
            return 0
        async with self._pool.acquire() as conn:
            try:
                async with conn.transaction():
                    return await self._insert(
                        conn, tenant_id, notified_at, received_at, ids, types, observed, payloads)
            except Exception as exc:
                if not _is_data_error(exc):
                    raise
                logger.warning(
                    "inbox_enqueue_batch_data_error_isolating",
                    extra={"tenant_id": tenant_id, "queue": self._queue, "error": repr(exc)},
                )
            # Safety net: a value Postgres cannot store (e.g. a lone surrogate) must
            # not fail the other entities. Retry one by one, a savepoint each.
            stored = 0
            async with conn.transaction():
                for i, eid in enumerate(ids):
                    try:
                        async with conn.transaction():
                            stored += await self._insert(
                                conn, tenant_id, notified_at, received_at,
                                ids[i:i + 1], types[i:i + 1], observed[i:i + 1], payloads[i:i + 1])
                    except Exception as exc:
                        if not _is_data_error(exc):
                            raise
                        logger.warning(
                            "inbox_enqueue_dropped_unstorable",
                            extra={"tenant_id": tenant_id, "queue": self._queue,
                                   "entity_id": eid, "error": repr(exc)},
                        )
            return stored

    async def _insert(self, conn, tenant_id, notified_at, received_at, ids, types, observed, payloads) -> int:
        status = await conn.execute(
            """
            INSERT INTO notification_inbox
                (queue, tenant_id, entity_id, entity_type, notified_at,
                 received_at, observed_at, payload)
            SELECT $1, $2, u.entity_id, u.entity_type, $3, $4, u.observed_at, u.payload::jsonb
            FROM unnest($5::text[], $6::text[], $7::timestamptz[], $8::text[])
                AS u(entity_id, entity_type, observed_at, payload)
            """,
            self._queue, tenant_id, notified_at, received_at, ids, types, observed, payloads,
        )
        return int(status.split()[-1])

    async def claim_round(self, limit: int) -> List[List[InboxRow]]:
        """Ready rows as rounds: round k = the k-th row of each entity.

        A row is excluded while an older row of its entity (by observed_at, id)
        is not ready (backing off). Takes the first `limit` by (observed_at, id),
        so a cut never skips an entity's earlier row. Does not lock: a single
        consumer is guaranteed by an advisory lock outside this class.
        """
        records = await self._pool.fetch(
            """
            SELECT s.*, row_number() OVER (
                       PARTITION BY s.tenant_id, s.entity_id ORDER BY s.observed_at, s.id) AS rn
            FROM (
                SELECT i.id, i.tenant_id, i.entity_id, i.entity_type, i.observed_at,
                       i.payload, i.attempts
                FROM notification_inbox i
                WHERE i.queue = $1 AND i.available_at <= now()
                  AND NOT EXISTS (
                      SELECT 1 FROM notification_inbox o
                      WHERE o.queue = i.queue AND o.tenant_id = i.tenant_id
                        AND o.entity_id = i.entity_id
                        AND (o.observed_at, o.id) < (i.observed_at, i.id)
                        AND o.available_at > now())
                ORDER BY i.observed_at, i.id
                LIMIT $2
            ) s
            ORDER BY rn, s.observed_at, s.id
            """,
            self._queue, limit,
        )
        rounds: List[List[InboxRow]] = []
        for rec in records:
            while len(rounds) < rec["rn"]:
                rounds.append([])
            rounds[rec["rn"] - 1].append(_row(rec))
        return rounds

    async def ack(self, ids: List[int]) -> None:
        if not ids:
            return
        await self._pool.execute(
            "DELETE FROM notification_inbox WHERE queue = $1 AND id = ANY($2::bigint[])",
            self._queue, ids,
        )
        INBOX_ACKED.labels(queue=self._queue).inc(len(ids))

    async def retry(self, row: InboxRow, error: str) -> None:
        """attempts+1 and back off `min(2^attempts, 300)` s; at MAX_ATTEMPTS bury."""
        buried = False
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                attempts = await conn.fetchval(
                    "UPDATE notification_inbox SET attempts = attempts + 1, last_error = $3 "
                    "WHERE queue = $1 AND id = $2 RETURNING attempts",
                    self._queue, row.id, error,
                )
                if attempts is None:
                    return
                if attempts >= MAX_ATTEMPTS:
                    await self._bury(conn, row.id, error)
                    buried = True
                else:
                    delay = min(2 ** attempts, BACKOFF_CAP_SECONDS)
                    await conn.execute(
                        "UPDATE notification_inbox SET available_at = now() + make_interval(secs => $2) "
                        "WHERE id = $1",
                        row.id, float(delay),
                    )
        # Counted after the transaction commits; a buried row is dead, not retried.
        (INBOX_DEAD if buried else INBOX_RETRIED).labels(queue=self._queue).inc()

    async def _bury(self, conn: asyncpg.Connection, row_id: int, error: str) -> None:
        await conn.execute(
            f"""
            WITH moved AS (
                DELETE FROM notification_inbox WHERE queue = $1 AND id = $2 RETURNING {_COLUMNS}
            )
            INSERT INTO notification_inbox_dead ({_COLUMNS})
            SELECT id, queue, tenant_id, entity_id, entity_type, notified_at, received_at,
                   observed_at, payload, attempts, available_at, $3 FROM moved
            """,
            self._queue, row_id, error,
        )

    async def bury(self, row: InboxRow, error: str) -> None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await self._bury(conn, row.id, error)
        INBOX_DEAD.labels(queue=self._queue).inc()

    async def redrive(self, since: Optional[datetime], entity_id: Optional[str]) -> int:
        """Move dead rows (optionally died since / of one entity) back, attempts 0."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    f"""
                    WITH moved AS (
                        DELETE FROM notification_inbox_dead
                        WHERE queue = $1
                          AND ($2::timestamptz IS NULL OR died_at >= $2)
                          AND ($3::text IS NULL OR entity_id = $3)
                        RETURNING {_COLUMNS}
                    )
                    INSERT INTO notification_inbox ({_COLUMNS})
                    SELECT id, queue, tenant_id, entity_id, entity_type, notified_at, received_at,
                           observed_at, payload, 0, now(), last_error FROM moved
                    RETURNING id
                    """,
                    self._queue, since, entity_id,
                )
        return len(rows)

    async def stats(self) -> Tuple[int, float]:
        """(depth, age in seconds of the oldest row) over ALL rows of the queue, backed-off ones included; (0, 0.0) if empty."""
        rec = await self._pool.fetchrow(
            "SELECT count(*) AS depth, "
            "COALESCE(EXTRACT(EPOCH FROM now() - min(received_at)), 0)::float8 AS age "
            "FROM notification_inbox WHERE queue = $1",
            self._queue,
        )
        return int(rec["depth"]), float(rec["age"])
