"""Single consumer of the durable notification inbox.

One consumer per queue, guaranteed by a session-level Postgres advisory lock held
on a dedicated connection; the other replica waits for it. Each pass claims ready
rows in rounds (round k = the k-th row of each entity, see
NotificationInbox.claim_round) and runs them through the existing processing,
one entity per call (a multi-entity call that fails half-way would re-insert the
rows already written when retried).

Outcomes per row:
- success: deleted (`ack`, one call per round);
- TransientProcessingError (database unavailable, handler not ready): `retry`
  with backoff; `claim_round` then holds that entity's later rows back;
- any other exception (a bug): `bury` to the dead table, no retry.

A failed row (either kind) also skips that entity's rows in the later rounds of
the same claimed batch: `claim_round` only holds rows back between claims, and a
successor must never be processed before its predecessor's fate is recorded. A
buried row holds nothing back after that: it leaves the inbox, so its successors
are claimed in the next pass (a one-reading gap, visible in the dead table and
recoverable with redrive).

Delivery is at least once: a pass killed after processing and before `ack`
processes the rows again, and the handler's Redis dedup / replay guard keeps
TimescaleDB free of duplicates. `asyncio.CancelledError` always propagates: a
cancelled row is neither retried nor buried, it stays in the inbox.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Set, Tuple

from prometheus_client import Counter, Gauge

from telemetry_worker.inbox import InboxRow, NotificationInbox
from telemetry_worker.notification_handler import TransientProcessingError, process_entities

logger = logging.getLogger(__name__)

LOCK_RETRY_SECONDS = 5.0  # without the lock (or after losing it), try again after this
ERROR_SLEEP_SECONDS = 5.0  # after a failed pass (store unavailable)
LOCK_CHECK_SECONDS = 5.0  # ping the lock connection at most this often
LOCK_PING_TIMEOUT_SECONDS = 5.0
STATS_REFRESH_SECONDS = 15.0  # inbox_depth / inbox_oldest_age_seconds refresh period
STATS_TIMEOUT_SECONDS = 5.0

_LOCK_SQL = "SELECT pg_try_advisory_lock(hashtext('inbox:' || $1::text))"

CONSUMER_ACTIVE = Gauge(
    "consumer_active",
    "1 while this replica holds the inbox consumer lock for the queue, else 0.",
    ["queue"],
)

INBOX_DEPTH = Gauge(
    "inbox_depth", "Rows in the inbox, backed-off ones included.", ["queue"])
INBOX_OLDEST_AGE = Gauge(
    "inbox_oldest_age_seconds", "Age of the oldest row in the inbox (0 when empty).", ["queue"])

INBOX_STATS_FAILED = Counter(
    "inbox_stats_failed_total", "Failed inbox stats refreshes (gauges are NaN meanwhile).", ["queue"])

# process(entities, tenant_id, fallback_observed_at=...): see process_entities.
ProcessFn = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class _Outcome:
    ok: bool
    transient: bool = False
    error: str = ""


_OK = _Outcome(ok=True)


def _fmt(context: Dict[str, Any]) -> str:
    """key=value text: the log format does not render `extra`."""
    return " ".join(f"{k}={v}" for k, v in context.items())


class InboxConsumer:
    def __init__(
        self,
        inbox: NotificationInbox,
        lock_pool: Any,  # asyncpg.Pool; one connection is held for the lock tenure
        process: ProcessFn = process_entities,
        claim_limit: int = 500,
        concurrency: int = 8,  # the sink pool (max 20) also serves /notify
        idle_s: float = 0.5,
    ):
        self._inbox = inbox
        self._lock_pool = lock_pool
        self._process = process
        self._claim_limit = claim_limit
        self._semaphore = asyncio.Semaphore(concurrency)
        self._idle_s = idle_s
        self._queue: str = inbox.queue
        self._lock_retry_s = LOCK_RETRY_SECONDS
        self._error_sleep_s = ERROR_SLEEP_SECONDS
        self._lock_check_s = LOCK_CHECK_SECONDS
        self._stats_refresh_s = STATS_REFRESH_SECONDS
        self._holds_lock = False

    @property
    def holds_lock(self) -> bool:
        return self._holds_lock

    def _set_active(self, active: bool) -> None:
        self._holds_lock = active
        CONSUMER_ACTIVE.labels(queue=self._queue).set(1 if active else 0)
        if not active:
            self._clear_stats_gauges()

    def _clear_stats_gauges(self) -> None:
        """Without the lock this replica knows nothing about the queue: no series."""
        for gauge in (INBOX_DEPTH, INBOX_OLDEST_AGE):
            try:
                gauge.remove(self._queue)
            except KeyError:
                pass

    # --- one pass -----------------------------------------------------------------

    async def run_once(self) -> int:
        """Claim ready rows and process them round by round. Returns rows acked.

        A failure of the store itself (claim, ack, retry, bury) propagates and ends
        the pass; unacknowledged rows stay in the inbox and are claimed again.
        """
        rounds = await self._inbox.claim_round(self._claim_limit)
        blocked: Set[Tuple[str, str]] = set()
        acked = 0
        for round_rows in rounds:
            rows = [r for r in round_rows if (r.tenant_id, r.entity_id) not in blocked]
            if not rows:
                continue
            outcomes = await self._process_round(rows)
            failed = [(r, o) for r, o in zip(rows, outcomes) if not o.ok]
            blocked.update((r.tenant_id, r.entity_id) for r, _ in failed)
            ok_ids = [r.id for r, o in zip(rows, outcomes) if o.ok]
            if ok_ids:
                await self._inbox.ack(ok_ids)
                acked += len(ok_ids)
            for r, outcome in failed:
                if outcome.transient:
                    await self._inbox.retry(r, outcome.error)
                else:
                    await self._inbox.bury(r, outcome.error)
        return acked

    async def _process_round(self, rows: List[InboxRow]) -> List[_Outcome]:
        # One row per entity in a round: distinct entities, run concurrently. The
        # TaskGroup cancels and awaits every row if the pass is cancelled.
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(self._process_row(r)) for r in rows]
        return [t.result() for t in tasks]

    async def _process_row(self, row: InboxRow) -> _Outcome:
        context = {
            "queue": self._queue,
            "inbox_row_id": row.id,
            "tenant_id": row.tenant_id,
            "entity_id": row.entity_id,
            "entity_type": row.entity_type,
            "attempts": row.attempts,
        }
        async with self._semaphore:
            try:
                # The row's observed_at stands in for an entity without its own instant:
                # reprocessing (backoff, crash before the ack, redrive) keeps the same time.
                await self._process(
                    [row.payload], row.tenant_id, fallback_observed_at=row.observed_at)
            except TransientProcessingError as exc:
                logger.warning(
                    f"inbox_row_transient_failure {_fmt(context)} error={exc}",
                    extra={**context, "error": str(exc)},
                )
                return _Outcome(ok=False, transient=True, error=str(exc))
            except Exception as exc:  # CancelledError is not an Exception: it propagates
                logger.error(
                    f"inbox_row_permanent_failure {_fmt(context)} error={exc!r}",
                    extra={**context, "error": repr(exc)},
                    exc_info=True,
                )
                return _Outcome(ok=False, transient=False, error=repr(exc))
        return _OK

    # --- the loop -------------------------------------------------------------------

    async def run(self) -> None:
        """Hold the queue's advisory lock and drain the inbox; runs until cancelled."""
        self._set_active(False)
        try:
            while True:
                try:
                    await self._hold_lock_and_drain()
                except Exception as exc:
                    logger.error(
                        f"inbox_consumer_lock_error queue={self._queue} error={exc!r}",
                        extra={"queue": self._queue, "error": repr(exc)},
                    )
                await asyncio.sleep(self._lock_retry_s)
        finally:
            self._set_active(False)

    async def _hold_lock_and_drain(self) -> None:
        """Returns when the lock is not available or its connection is lost.

        The connection stays checked out for the whole tenure. A tenure always ends
        by terminating the connection: the session ends and the lock with it, and the
        pool slot is freed even when the server dropped the connection unnoticed (a
        plain release of such a connection leaves the slot checked out in asyncpg, and
        the next acquire on the one-connection pool would wait forever). Only a
        healthy connection that did not get the lock goes back to the pool as is.
        """
        conn = await self._lock_pool.acquire()
        healthy = False
        try:
            if not await conn.fetchval(_LOCK_SQL, self._queue):
                healthy = True
                logger.debug(
                    f"inbox_consumer_lock_busy queue={self._queue}", extra={"queue": self._queue})
                return
            self._set_active(True)
            logger.info(
                f"inbox_consumer_lock_acquired queue={self._queue}", extra={"queue": self._queue})
            try:
                await self._drain(conn)
            finally:
                self._set_active(False)
                logger.warning(
                    f"inbox_consumer_lock_released queue={self._queue}", extra={"queue": self._queue})
        finally:
            if not healthy:
                conn.terminate()
            try:
                await self._lock_pool.release(conn)
            except Exception as exc:  # already detached by terminate(): nothing to give back
                logger.debug(
                    f"inbox_consumer_lock_conn_release_skipped queue={self._queue} error={exc!r}",
                    extra={"queue": self._queue, "error": repr(exc)},
                )

    async def _refresh_stats(self) -> None:
        """Set the depth/age gauges; a failure is logged and never ends the loop."""
        try:
            depth, age = await asyncio.wait_for(self._inbox.stats(), STATS_TIMEOUT_SECONDS)
        except Exception as exc:  # CancelledError is not an Exception: it propagates
            logger.warning(
                f"inbox_stats_failed queue={self._queue} error={exc!r}",
                extra={"queue": self._queue, "error": repr(exc)},
            )
            INBOX_STATS_FAILED.labels(queue=self._queue).inc()
            INBOX_DEPTH.labels(queue=self._queue).set(math.nan)
            INBOX_OLDEST_AGE.labels(queue=self._queue).set(math.nan)
            return
        INBOX_DEPTH.labels(queue=self._queue).set(depth)
        INBOX_OLDEST_AGE.labels(queue=self._queue).set(age)

    async def _drain(self, conn: Any) -> None:
        last_check = float("-inf")
        last_stats = float("-inf")
        while True:
            if time.monotonic() - last_stats >= self._stats_refresh_s:
                last_stats = time.monotonic()
                await self._refresh_stats()
            if time.monotonic() - last_check >= self._lock_check_s:
                if not await self._lock_alive(conn):
                    return
                last_check = time.monotonic()
            try:
                acked = await self.run_once()
            except Exception as exc:
                logger.error(
                    f"inbox_consumer_pass_failed queue={self._queue} error={exc!r}",
                    extra={"queue": self._queue, "error": repr(exc)},
                    exc_info=True,
                )
                await asyncio.sleep(self._error_sleep_s)
                last_check = float("-inf")  # the store failed: check the lock before going on
                continue
            if acked == 0:
                await asyncio.sleep(self._idle_s)

    async def _lock_alive(self, conn: Any) -> bool:
        """The session-level lock lives as long as its connection does."""
        if conn.is_closed():
            logger.warning(
                f"inbox_consumer_lock_lost queue={self._queue} reason=closed",
                extra={"queue": self._queue, "reason": "closed"},
            )
            return False
        try:
            await asyncio.wait_for(conn.fetchval("SELECT 1"), LOCK_PING_TIMEOUT_SECONDS)
        except Exception as exc:
            logger.warning(
                f"inbox_consumer_lock_lost queue={self._queue} reason={exc!r}",
                extra={"queue": self._queue, "reason": repr(exc)},
            )
            return False
        return True
