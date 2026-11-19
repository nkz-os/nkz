"""Audited recompute of stored observation context (contract 8.6).

entity-manager enqueues a row in `observation_recompute_runs` whenever a
deployment is opened in the past, corrected, closed or deleted. This loop claims
those runs and re-applies the deployment-derived context to the
`telemetry_measurements` rows of that device in the run's range:

* every row of the device in [range_from, range_to) is re-resolved against the
  deployment valid at its own `observed_at` (not only rows carrying the run's
  deployment_id), so rows that had no deployment at ingest are covered too;
* a row without `observed_property` takes it from the raw DeviceMeasurement
  (`telemetry_events.payload->'raw'`), and a row without `quality_flag` gets one
  computed from the catalogue, so it becomes visible to the reader;
* `value`, `time_source` and an already-set `quality_flag` / `observed_property`
  / `source_kind` are never changed;
* only rows whose values differ are written, so a re-run reports 0 rows changed.

TimescaleDB 2.10 has no DML on compressed chunks: the compressed chunks that hold
rows to change are decompressed in the same transaction as the UPDATE (so the
compression policy cannot recompress them in between). They are not recompressed
here; the compression policy does that on its next run.

Tenant context: `observation_recompute_runs` and `device_deployments` are FORCE
RLS, so every access to them runs inside a transaction that first sets
`app.current_tenant`, and filters tenant_id explicitly as well.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

from common.observation_context import MEDIA

from .observation_lookup import _DEPLOYMENTS_SQL, _in_range, _to_deployment
from .observations import (
    Deployment,
    PropertyDef,
    counter_max_of,
    observation_context,
    quality_flag,
)

logger = logging.getLogger(__name__)

INTERVAL_ENV = "OBSERVATION_RECOMPUTE_INTERVAL_S"
DEFAULT_INTERVAL_S = 30.0
TENANTS_TTL_S = 600.0
PAGE_ROWS = 5000  # rows read per page (bounded memory on a whole-history catch-up)
UPDATE_BATCH_ROWS = 1000  # rows per UPDATE statement (contract: <= 1000)
MAX_RUNS_PER_TENANT = 20  # per run_once, so one busy tenant cannot starve the others
ERROR_MAX_CHARS = 500
# ASSUMPTION: decompressing one 7-day chunk of telemetry_measurements takes well
# under 10 minutes on production; the pool's command_timeout (30 s) is too short.
DECOMPRESS_TIMEOUT_S = 600.0
UPDATE_TIMEOUT_S = 300.0


class StoredRow(NamedTuple):
    """One telemetry_measurements row of the device, plus the raw event fields."""

    observed_at: datetime
    source_event_id: int
    attribute_name: str
    value: float
    observed_property: Optional[str]
    feature_of_interest: Optional[str]
    medium: Optional[str]
    depth_from_cm: Optional[float]
    depth_to_cm: Optional[float]
    height_m: Optional[float]
    organ: Optional[str]
    context_ref: Optional[str]
    deployment_id: Optional[str]
    source_kind: Optional[str]
    quality_flag: Optional[str]
    lon: Optional[float]
    lat: Optional[float]
    raw_observed_property: Optional[str]
    raw_profile_code: Optional[str]


class RowUpdate(NamedTuple):
    """Target values of one row. Field order = UPDATE_COLUMNS = VALUES order."""

    observed_at: datetime
    source_event_id: int
    attribute_name: str
    deployment_id: Optional[str]
    feature_of_interest: Optional[str]
    medium: Optional[str]
    depth_from_cm: Optional[float]
    depth_to_cm: Optional[float]
    height_m: Optional[float]
    organ: Optional[str]
    context_ref: Optional[str]
    keep_location: bool  # True: the row's own location stays (mobile reading)
    lon: Optional[float]
    lat: Optional[float]
    observed_property: Optional[str]  # applied only where the row has none
    quality_flag: Optional[str]  # applied only where the row has none
    source_kind: Optional[str]  # applied only where the row has none


UPDATE_COLUMNS: Tuple[str, ...] = RowUpdate._fields
_UPDATE_TYPES: Tuple[str, ...] = (
    "timestamptz", "bigint", "text", "uuid", "text", "text", "float8", "float8",
    "float8", "text", "text", "boolean", "float8", "float8", "text", "text", "text",
)
assert len(_UPDATE_TYPES) == len(UPDATE_COLUMNS)


# ---------------------------------------------------------------------------
# SQL (each statement carries a `-- recompute:<name>` tag for logs and tests)
# ---------------------------------------------------------------------------

TENANTS_SQL = """-- recompute:tenants
SELECT DISTINCT tenant_id FROM tenants WHERE tenant_id IS NOT NULL
"""

SET_TENANT_SQL = """-- recompute:set_tenant
SELECT set_config('app.current_tenant', $1, true)
"""

CLAIM_SQL = """-- recompute:claim
SELECT id, tenant_id, device_urn, deployment_id, range_from, range_to
FROM public.observation_recompute_runs
WHERE tenant_id = $1
  AND status = 'pending'
  AND created_at < now() - interval '90 seconds'
ORDER BY created_at
LIMIT 1
FOR UPDATE SKIP LOCKED
"""

MARK_RUNNING_SQL = """-- recompute:mark_running
UPDATE public.observation_recompute_runs
SET status = 'running', started_at = now()
WHERE tenant_id = $1 AND id = $2
"""

MARK_DONE_SQL = """-- recompute:mark_done
UPDATE public.observation_recompute_runs
SET status = 'done', rows_changed = $3, error = NULL, finished_at = now()
WHERE tenant_id = $1 AND id = $2
"""

MARK_FAILED_SQL = """-- recompute:mark_failed
UPDATE public.observation_recompute_runs
SET status = 'failed', rows_changed = $3, error = $4, finished_at = now()
WHERE tenant_id = $1 AND id = $2
"""

DEPLOYMENTS_SQL = "-- recompute:deployments\n" + _DEPLOYMENTS_SQL

# Keyset-paginated rows of one device in [lo, hi). $5..$7 = last key of the
# previous page (NULL on the first page). The raw event is joined on its full
# primary key; a missing event leaves the raw fields NULL.
ROWS_SQL = """-- recompute:rows
SELECT m.observed_at, m.source_event_id, m.attribute_name, m.value,
       m.observed_property, m.feature_of_interest, m.medium,
       m.depth_from_cm, m.depth_to_cm, m.height_m, m.organ, m.context_ref,
       m.deployment_id::text AS deployment_id, m.source_kind, m.quality_flag,
       ST_X(m.location::geometry) AS lon, ST_Y(m.location::geometry) AS lat,
       e.payload->'raw'->'observedProperty'->>'value' AS raw_observed_property,
       e.payload->'raw'->'profileCode'->>'value' AS raw_profile_code
FROM public.telemetry_measurements m
LEFT JOIN public.telemetry_events e
       ON e.tenant_id = m.tenant_id AND e.observed_at = m.observed_at
      AND e.id = m.source_event_id
WHERE m.tenant_id = $1
  AND m.entity_type = 'DeviceMeasurement'
  AND m.device_id = $2
  AND m.observed_at >= $3
  AND ($4::timestamptz IS NULL OR m.observed_at < $4)
  AND ($5::timestamptz IS NULL
       OR (m.observed_at, m.source_event_id, m.attribute_name) > ($5, $6::bigint, $7::text))
ORDER BY m.observed_at, m.source_event_id, m.attribute_name
LIMIT $8
"""

# Compressed chunks of telemetry_measurements overlapping [$1, $2] (by range;
# event_sink.CHUNKS_AT_SQL is the by-instant variant used for late inserts).
CHUNKS_IN_RANGE_SQL = """-- recompute:chunks
SELECT format('%I.%I', chunk_schema, chunk_name) AS chunk
FROM timescaledb_information.chunks
WHERE hypertable_schema = 'public'
  AND hypertable_name = 'telemetry_measurements'
  AND is_compressed
  AND range_start <= $2 AND range_end > $1
ORDER BY range_start
"""

DECOMPRESS_SQL = """-- recompute:decompress
SELECT decompress_chunk($1::regclass, if_compressed => true)
"""


def _update_sql(n_rows: int) -> str:
    """UPDATE ... FROM (VALUES ...) for n_rows rows; $1 tenant, $2/$3 time window.

    The constant observed_at window lets the planner exclude chunks outside the
    batch (all compressed chunks inside it were decompressed first). The
    IS DISTINCT FROM guard re-checks against the current values, so the returned
    count is exactly the rows changed.
    """
    width = len(UPDATE_COLUMNS)
    tuples = []
    for r in range(n_rows):
        base = 4 + r * width
        cells = [f"${base + c}::{_UPDATE_TYPES[c]}" for c in range(width)]
        tuples.append("(" + ", ".join(cells) + ")")
    return (
        "-- recompute:update_rows\n"
        "UPDATE public.telemetry_measurements AS t SET\n"
        "    deployment_id = v.deployment_id,\n"
        "    feature_of_interest = v.feature_of_interest,\n"
        "    medium = v.medium,\n"
        "    depth_from_cm = v.depth_from_cm,\n"
        "    depth_to_cm = v.depth_to_cm,\n"
        "    height_m = v.height_m,\n"
        "    organ = v.organ,\n"
        "    context_ref = v.context_ref,\n"
        "    location = CASE WHEN v.keep_location THEN t.location\n"
        "                    WHEN v.lon IS NULL OR v.lat IS NULL THEN NULL\n"
        "                    ELSE ST_SetSRID(ST_MakePoint(v.lon, v.lat), 4326)::geography END,\n"
        "    observed_property = COALESCE(t.observed_property, v.observed_property),\n"
        "    quality_flag = COALESCE(t.quality_flag, v.quality_flag),\n"
        "    source_kind = COALESCE(t.source_kind, v.source_kind)\n"
        "FROM (VALUES\n    " + ",\n    ".join(tuples) + "\n"
        ") AS v(" + ", ".join(UPDATE_COLUMNS) + ")\n"
        "WHERE t.tenant_id = $1\n"
        "  AND t.observed_at >= $2 AND t.observed_at <= $3\n"
        "  AND t.observed_at = v.observed_at\n"
        "  AND t.source_event_id = v.source_event_id\n"
        "  AND t.attribute_name = v.attribute_name\n"
        "  AND (t.deployment_id IS DISTINCT FROM v.deployment_id\n"
        "       OR t.feature_of_interest IS DISTINCT FROM v.feature_of_interest\n"
        "       OR t.medium IS DISTINCT FROM v.medium\n"
        "       OR t.depth_from_cm IS DISTINCT FROM v.depth_from_cm\n"
        "       OR t.depth_to_cm IS DISTINCT FROM v.depth_to_cm\n"
        "       OR t.height_m IS DISTINCT FROM v.height_m\n"
        "       OR t.organ IS DISTINCT FROM v.organ\n"
        "       OR t.context_ref IS DISTINCT FROM v.context_ref\n"
        "       OR (NOT v.keep_location AND (ST_X(t.location::geometry), ST_Y(t.location::geometry))\n"
        "           IS DISTINCT FROM (v.lon, v.lat))\n"
        "       OR (t.observed_property IS NULL AND v.observed_property IS NOT NULL)\n"
        "       OR (t.quality_flag IS NULL AND v.quality_flag IS NOT NULL)\n"
        "       OR (t.source_kind IS NULL AND v.source_kind IS NOT NULL))\n"
    )


# ---------------------------------------------------------------------------
# Pure planning
# ---------------------------------------------------------------------------


def _text_or_none(value: Any, field: str, log_ctx: str) -> Optional[str]:
    """Mirror of the 115 trigger's nkz_obs_text: a non-string becomes NULL + warning."""
    if value is None or isinstance(value, str):
        return value
    logger.warning(f"Recompute: {field} rejected (not a string): {log_ctx}")
    return None


def plan_row(
    row: StoredRow,
    dep: Optional[Deployment],
    prev_dep: Optional[Deployment],
    prop: Optional[PropertyDef],
    measurement_def: Optional[Dict[str, Any]],
    device_urn: str,
) -> Optional[RowUpdate]:
    """Target values for one row, or None when nothing would change.

    `dep` is the deployment valid at row.observed_at; `prev_dep` the deployment
    the row currently points at (None if it had none or it no longer exists);
    `prop` the catalogue entry of the row's effective observed property.
    """
    effective_property = row.observed_property or row.raw_observed_property or None
    # A row gets context only when ingest would have given it one: a valid
    # deployment and an observed property. ASSUMPTION: a property taken from the raw
    # event must be in the catalogue (a column defined as a catalogue name never
    # receives an unknown one); a row already carrying observed_property is
    # re-resolved regardless.
    has_context = dep is not None and (
        row.observed_property is not None
        or (effective_property is not None and prop is not None)
    )

    if has_context:
        log_ctx = (
            f"device_urn={device_urn} observed_at={row.observed_at.isoformat()} "
            f"attribute={row.attribute_name}"
        )
        ctx = observation_context(row.attribute_name, device_urn, dep)
        medium = ctx["medium"]
        if medium is not None and medium not in MEDIA:
            logger.warning(f"Recompute: medium {medium!r} not in vocabulary, stored NULL: {log_ctx}")
            medium = None
        if dep.mobility == "mobile":
            keep_location, lon, lat = True, row.lon, row.lat
        elif dep.location is not None:
            keep_location, (lon, lat) = False, dep.location
        else:
            keep_location, lon, lat = False, None, None
        observed_property = row.observed_property or effective_property
        flag = row.quality_flag
        if flag is None and prop is not None:
            flag = quality_flag(row.value, prop, counter_max_of(measurement_def), None)
        target = RowUpdate(
            observed_at=row.observed_at,
            source_event_id=row.source_event_id,
            attribute_name=row.attribute_name,
            deployment_id=dep.id,
            feature_of_interest=_text_or_none(ctx["featureOfInterest"], "featureOfInterest", log_ctx),
            medium=medium,
            depth_from_cm=ctx["depthFromCm"],
            depth_to_cm=ctx["depthToCm"],
            height_m=ctx["heightM"],
            organ=_text_or_none(ctx["organ"], "organ", log_ctx),
            context_ref=_text_or_none(ctx["contextRef"], "contextRef", log_ctx),
            keep_location=keep_location,
            lon=lon,
            lat=lat,
            observed_property=observed_property,
            quality_flag=flag,
            source_kind=row.source_kind or "device",
        )
    else:
        # No context: the deployment-derived columns go NULL; observed_property,
        # quality_flag and source_kind stay as they are. The location is the
        # reading's own only for a mobile deployment. ASSUMPTION: when the row's
        # stored deployment no longer exists (deleted), its mobility is unknown and
        # the location is cleared, as a fresh ingest without deployment would store.
        mobile = (dep is not None and dep.mobility == "mobile") or (
            dep is None and prev_dep is not None and prev_dep.mobility == "mobile"
        )
        target = RowUpdate(
            observed_at=row.observed_at,
            source_event_id=row.source_event_id,
            attribute_name=row.attribute_name,
            deployment_id=None,
            feature_of_interest=None,
            medium=None,
            depth_from_cm=None,
            depth_to_cm=None,
            height_m=None,
            organ=None,
            context_ref=None,
            keep_location=mobile,
            lon=row.lon if mobile else None,
            lat=row.lat if mobile else None,
            observed_property=row.observed_property,
            quality_flag=row.quality_flag,
            source_kind=row.source_kind,
        )

    current = (
        row.deployment_id, row.feature_of_interest, row.medium, row.depth_from_cm,
        row.depth_to_cm, row.height_m, row.organ, row.context_ref,
        row.observed_property, row.quality_flag, row.source_kind,
    )
    wanted = (
        target.deployment_id, target.feature_of_interest, target.medium,
        target.depth_from_cm, target.depth_to_cm, target.height_m, target.organ,
        target.context_ref, target.observed_property, target.quality_flag,
        target.source_kind,
    )
    location_differs = not target.keep_location and (row.lon, row.lat) != (target.lon, target.lat)
    if current == wanted and not location_differs:
        return None
    return target


def recompute_interval_from_env() -> float:
    """OBSERVATION_RECOMPUTE_INTERVAL_S (default 30); invalid values raise."""
    raw = os.getenv(INTERVAL_ENV)
    if raw is None or raw.strip() == "":
        return DEFAULT_INTERVAL_S
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{INTERVAL_ENV} must be a number of seconds, got {raw!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{INTERVAL_ENV} must be > 0, got {raw!r}")
    return value


def _error_text(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ")
    return text[:ERROR_MAX_CHARS]


class _Progress:
    __slots__ = ("rows_changed",)

    def __init__(self) -> None:
        self.rows_changed = 0


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------


class RecomputeLoop:
    def __init__(
        self,
        pool,
        lookup,
        interval_s: float = DEFAULT_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not interval_s > 0:
            raise ValueError(f"interval_s must be > 0, got {interval_s}")
        self._pool = pool
        self._lookup = lookup
        self._interval_s = interval_s
        self._clock = clock
        self._tenants: List[str] = []
        self._tenants_expires_at: Optional[float] = None

    # -- tenants ------------------------------------------------------------

    async def _active_tenants(self) -> List[str]:
        now = self._clock()
        if self._tenants_expires_at is not None and now < self._tenants_expires_at:
            return self._tenants
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(TENANTS_SQL)
        except Exception as exc:
            logger.error(f"Recompute: tenant list query failed: {_error_text(exc)}")
            return self._tenants  # last known list (empty on first failure)
        self._tenants = sorted(r["tenant_id"] for r in rows if r["tenant_id"])
        self._tenants_expires_at = self._clock() + TENANTS_TTL_S
        return self._tenants

    # -- claim --------------------------------------------------------------

    async def claim_next(self, conn, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Claim the oldest eligible pending run of `tenant_id` on `conn`.

        Must be called inside a transaction on `conn`; the caller commits. The row
        lock (FOR UPDATE SKIP LOCKED) keeps concurrent claimers off the same run.
        """
        await conn.execute(SET_TENANT_SQL, tenant_id)
        row = await conn.fetchrow(CLAIM_SQL, tenant_id)
        if row is None:
            return None
        await conn.execute(MARK_RUNNING_SQL, tenant_id, row["id"])
        return dict(row)

    async def _claim(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn, conn.transaction():
            return await self.claim_next(conn, tenant_id)

    # -- audit --------------------------------------------------------------

    async def _mark(self, sql: str, tenant_id: str, *args: Any) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(SET_TENANT_SQL, tenant_id)
            await conn.execute(sql, tenant_id, *args)

    # -- processing ---------------------------------------------------------

    async def _load_deployments(self, tenant_id: str, device_urn: str) -> List[Tuple[Any, Deployment]]:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(SET_TENANT_SQL, tenant_id)
            rows = await conn.fetch(DEPLOYMENTS_SQL, tenant_id, device_urn)
        return [(r["valid"], _to_deployment(r)) for r in rows]

    async def _fetch_page(
        self, tenant_id: str, device_id: str, lo: datetime, hi: Optional[datetime],
        after: Optional[Tuple[datetime, int, str]],
    ) -> List[StoredRow]:
        after_at, after_id, after_attr = after if after is not None else (None, None, None)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                ROWS_SQL, tenant_id, device_id, lo, hi, after_at, after_id, after_attr, PAGE_ROWS
            )
        return [StoredRow(**dict(r)) for r in rows]

    async def _apply(self, tenant_id: str, updates: List[RowUpdate], log_ctx: str) -> int:
        """Decompress the compressed chunks holding `updates`, then UPDATE in batches.

        One transaction: the chunk stays decompressed until the UPDATE commits.
        """
        updates = sorted(updates, key=lambda u: (u.observed_at, u.source_event_id, u.attribute_name))
        lo, hi = updates[0].observed_at, updates[-1].observed_at
        changed = 0
        async with self._pool.acquire() as conn, conn.transaction():
            chunks = [r["chunk"] for r in await conn.fetch(CHUNKS_IN_RANGE_SQL, lo, hi)]
            for chunk in chunks:
                logger.warning(f"Recompute: decompressing chunk {chunk}: {log_ctx}")
                await conn.execute(DECOMPRESS_SQL, chunk, timeout=DECOMPRESS_TIMEOUT_S)
            for start in range(0, len(updates), UPDATE_BATCH_ROWS):
                batch = updates[start:start + UPDATE_BATCH_ROWS]
                flat: List[Any] = []
                for u in batch:
                    flat.extend(u)
                status = await conn.execute(
                    _update_sql(len(batch)),
                    tenant_id, batch[0].observed_at, batch[-1].observed_at, *flat,
                    timeout=UPDATE_TIMEOUT_S,
                )
                changed += int(str(status).rsplit(" ", 1)[-1])
        return changed

    async def _process_run(self, run: Dict[str, Any], progress: _Progress) -> int:
        tenant_id = run["tenant_id"]
        device_urn = run["device_urn"]
        device_id = device_urn.rsplit(":", 1)[-1]
        log_ctx = (
            f"tenant_id={tenant_id} run_id={run['id']} device_urn={device_urn} "
            f"range=[{run['range_from'].isoformat()}, "
            f"{run['range_to'].isoformat() if run['range_to'] else 'open'})"
        )

        intervals = await self._load_deployments(tenant_id, device_urn)
        by_id = {dep.id: dep for _rng, dep in intervals}
        props: Dict[str, Optional[PropertyDef]] = {}
        mdefs: Dict[Tuple[str, str], Optional[dict]] = {}

        def deployment_at(at: datetime) -> Optional[Deployment]:
            for rng, dep in intervals:
                if _in_range(rng, at):
                    return dep
            return None

        after = None
        while True:
            page = await self._fetch_page(
                tenant_id, device_id, run["range_from"], run["range_to"], after
            )
            if not page:
                break
            updates: List[RowUpdate] = []
            for row in page:
                dep = deployment_at(row.observed_at)
                prev = by_id.get(row.deployment_id) if row.deployment_id else None
                name = row.observed_property or row.raw_observed_property
                prop = None
                if name and dep is not None:
                    if name not in props:
                        props[name] = await self._lookup.property(tenant_id, name)
                    prop = props[name]
                mdef = None
                if prop is not None and row.quality_flag is None and row.raw_profile_code:
                    key = (row.raw_profile_code, row.attribute_name)
                    if key not in mdefs:
                        mdefs[key] = await self._lookup.measurement_def(tenant_id, *key)
                    mdef = mdefs[key]
                target = plan_row(row, dep, prev, prop, mdef, device_urn)
                if target is not None:
                    updates.append(target)
            if updates:
                progress.rows_changed += await self._apply(tenant_id, updates, log_ctx)
            last = page[-1]
            after = (last.observed_at, last.source_event_id, last.attribute_name)
            if len(page) < PAGE_ROWS:
                break
        return progress.rows_changed

    async def _execute(self, run: Dict[str, Any]) -> None:
        """Process one claimed run and record the outcome. Never raises (but cancellation)."""
        tenant_id = run["tenant_id"]
        run_id = run["id"]
        progress = _Progress()
        try:
            changed = await self._process_run(run, progress)
            await self._mark(MARK_DONE_SQL, tenant_id, run_id, changed)
            logger.info(
                f"Recompute run done: tenant_id={tenant_id} run_id={run_id} "
                f"device_urn={run['device_urn']} rows_changed={changed}"
            )
        except asyncio.CancelledError:
            try:
                await self._mark(
                    MARK_FAILED_SQL, tenant_id, run_id, progress.rows_changed,
                    "cancelled (worker shutdown)",
                )
            except Exception as mark_exc:
                logger.error(
                    f"Recompute: could not mark cancelled run failed: tenant_id={tenant_id} "
                    f"run_id={run_id}: {_error_text(mark_exc)}"
                )
            raise
        except Exception as exc:
            error = _error_text(exc)
            logger.error(
                f"Recompute run failed: tenant_id={tenant_id} run_id={run_id} "
                f"device_urn={run['device_urn']} rows_changed={progress.rows_changed}: {error}",
                exc_info=True,
            )
            try:
                await self._mark(MARK_FAILED_SQL, tenant_id, run_id, progress.rows_changed, error)
            except Exception as mark_exc:
                logger.error(
                    f"Recompute: could not mark run failed (stays running): "
                    f"tenant_id={tenant_id} run_id={run_id}: {_error_text(mark_exc)}"
                )

    async def run_once(self) -> int:
        """Claim and process eligible pending runs of every tenant. Returns runs processed."""
        processed = 0
        for tenant_id in await self._active_tenants():
            for _ in range(MAX_RUNS_PER_TENANT):
                try:
                    run = await self._claim(tenant_id)
                except Exception as exc:
                    logger.error(f"Recompute: claim failed: tenant_id={tenant_id}: {_error_text(exc)}")
                    break
                if run is None:
                    break
                self._lookup.invalidate(tenant_id)
                await self._execute(run)
                processed += 1
        return processed

    async def run(self) -> None:
        """Run `run_once` every `interval_s` until cancelled."""
        logger.info(f"Observation recompute loop started (interval={self._interval_s}s)")
        try:
            while True:
                try:
                    await self.run_once()
                except Exception as exc:
                    logger.error(f"Recompute: run_once failed: {_error_text(exc)}", exc_info=True)
                await asyncio.sleep(self._interval_s)
        finally:
            logger.info("Observation recompute loop stopped")
