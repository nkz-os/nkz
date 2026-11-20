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
* the location comes only from the deployment valid at `observed_at` (fixed) or
  the raw reading's own point (mobile), never from the row's current values, so
  repeated runs converge;
* `value`, `time_source` and an already-set `quality_flag` / `observed_property`
  / `source_kind` are never changed;
* only rows whose values differ are written, so a re-run reports 0 rows changed.

TimescaleDB 2.10 has no DML on compressed chunks. Only the compressed chunks that
hold rows to change are decompressed, one chunk per short transaction with a lock
timeout (the chunks are shared by every tenant); the UPDATE batches follow in
their own short transactions, split so that no batch's time window overlaps a
chunk left compressed (2.10 rejects such an UPDATE even without matching rows). If the compression policy recompresses a chunk in
between, the batch decompresses it again and retries once. Chunks are not
recompressed here; the compression policy does that on its next run.

Liveness: a run that fails is retried on a later tick (`attempts` + 1) up to
MAX_ATTEMPTS, then marked `failed`. A cancelled run (worker shutdown) goes back to
`pending`. A `running` run whose lease (`started_at`, renewed after every page) is
older than 15 minutes was abandoned by a dead worker and is claimed again.
`rows_changed` accumulates across attempts.

Tenant context: `observation_recompute_runs` and `device_deployments` are FORCE
RLS, so every access to them runs inside a transaction that first sets
`app.current_tenant`, and filters tenant_id explicitly as well.
"""

from __future__ import annotations

import asyncio
import bisect
import logging
import math
import os
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

import asyncpg

from common.observation_context import MEDIA, entity_id_conforms

from .device_measurement import (
    GATE_REASONS,
    SKIP_NAME_UNUSABLE,
    SKIP_PROPERTY_MISMATCH,
    SKIP_STATISTIC,
    ObservationDecision,
    decide_observation_property,
    device_measurement_shape,
)
from .log_throttle import ThrottledWarnings
from .observation_lookup import _DEPLOYMENTS_SQL, _in_range, _json, _to_deployment
from .observations import (
    Deployment,
    PropertyDef,
    units_compatible,
    counter_max_of,
    observation_context,
    quality_flag,
    reading_point,
)

logger = logging.getLogger(__name__)

# Same reasons as ingest repeat for every row of a device in a run: logged once per
# (tenant, device, reason), the rest counted and reported at the end of the run.
_skip_warnings = ThrottledWarnings(logger)

INTERVAL_ENV = "OBSERVATION_RECOMPUTE_INTERVAL_S"
DEFAULT_INTERVAL_S = 30.0
TENANTS_TTL_S = 600.0
PAGE_ROWS = 5000  # rows read per page (bounded memory on a whole-history catch-up)
UPDATE_BATCH_ROWS = 1000  # rows per UPDATE statement (contract: <= 1000)
MAX_RUNS_PER_TENANT = 20  # per run_once, so one busy tenant cannot starve the others
MAX_ATTEMPTS = 5  # failed attempts before a run is marked failed
ERROR_MAX_CHARS = 500
# ASSUMPTION: decompressing one 7-day chunk of telemetry_measurements takes well
# under 10 minutes on production; the pool's command_timeout (30 s) is too short.
DECOMPRESS_TIMEOUT_S = 600.0
UPDATE_TIMEOUT_S = 300.0
PAGE_TIMEOUT_S = 120.0
# Chunks are shared by all tenants: never queue behind (or in front of) ingest for long.
LOCK_TIMEOUT_SQL = """-- recompute:lock_timeout
SET LOCAL lock_timeout = '5s'
"""


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
    raw_profile_code: Optional[str]
    raw_location: Any  # the raw entity's location value (jsonb text or parsed)
    entity_id: Optional[str]  # the DeviceMeasurement id (entity id rule, see plan_row)
    # The raw entity's shape attributes (name in any NAME_KEYS form, controlledProperty,
    # measurementType) as a jsonb object, or NULL: the input of `device_measurement_shape`.
    raw_shape: Any = None


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
    lon: Optional[float]
    lat: Optional[float]
    observed_property: Optional[str]  # applied only where the row has none
    quality_flag: Optional[str]  # applied only where the row has none
    source_kind: Optional[str]  # applied only where the row has none


UPDATE_COLUMNS: Tuple[str, ...] = RowUpdate._fields
_UPDATE_TYPES: Tuple[str, ...] = (
    "timestamptz", "bigint", "text", "uuid", "text", "text", "float8", "float8",
    "float8", "text", "text", "float8", "float8", "text", "text", "text",
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

# Eligible: pending for more than 90 s (longer than the lookup cache TTL), or
# running with an expired lease (its worker died). $2 = runs already handled in
# this tick, so a run put back to pending is retried on a later tick, not at once.
CLAIM_SQL = """-- recompute:claim
SELECT id, tenant_id, device_urn, deployment_id, range_from, range_to
FROM public.observation_recompute_runs
WHERE tenant_id = $1
  AND NOT (id = ANY($2::uuid[]))
  AND ((status = 'pending' AND created_at < now() - interval '90 seconds')
       OR (status = 'running' AND started_at < now() - interval '15 minutes'))
ORDER BY created_at
LIMIT 1
FOR UPDATE SKIP LOCKED
"""

# Reclaiming an expired lease counts as a failed attempt (status is the old value).
MARK_RUNNING_SQL = """-- recompute:mark_running
UPDATE public.observation_recompute_runs
SET status = 'running',
    started_at = now(),
    attempts = attempts + CASE WHEN status = 'running' THEN 1 ELSE 0 END
WHERE tenant_id = $1 AND id = $2
RETURNING attempts
"""

RENEW_LEASE_SQL = """-- recompute:renew_lease
UPDATE public.observation_recompute_runs
SET started_at = now()
WHERE tenant_id = $1 AND id = $2 AND status = 'running'
"""

MARK_DONE_SQL = """-- recompute:mark_done
UPDATE public.observation_recompute_runs
SET status = 'done', rows_changed = COALESCE(rows_changed, 0) + $3, error = NULL,
    finished_at = now()
WHERE tenant_id = $1 AND id = $2
"""

# A failed attempt: back to pending until the MAX_ATTEMPTS-th ($5), then failed.
MARK_RETRY_SQL = """-- recompute:mark_retry
UPDATE public.observation_recompute_runs
SET attempts = attempts + 1,
    rows_changed = COALESCE(rows_changed, 0) + $3,
    error = $4,
    status = CASE WHEN attempts + 1 >= $5 THEN 'failed' ELSE 'pending' END,
    started_at = CASE WHEN attempts + 1 >= $5 THEN started_at ELSE NULL END,
    finished_at = CASE WHEN attempts + 1 >= $5 THEN now() ELSE NULL END
WHERE tenant_id = $1 AND id = $2
RETURNING status
"""

MARK_CANCELLED_SQL = """-- recompute:mark_cancelled
UPDATE public.observation_recompute_runs
SET status = 'pending', started_at = NULL, rows_changed = COALESCE(rows_changed, 0) + $3
WHERE tenant_id = $1 AND id = $2
"""

MARK_FAILED_SQL = """-- recompute:mark_failed
UPDATE public.observation_recompute_runs
SET status = 'failed', rows_changed = COALESCE(rows_changed, 0) + $3, error = $4,
    finished_at = now()
WHERE tenant_id = $1 AND id = $2
"""

DEPLOYMENTS_SQL = "-- recompute:deployments\n" + _DEPLOYMENTS_SQL

# Keyset-paginated rows of one device in [$3, $4). $3 is range_from on the first
# page and the previous page's last observed_at afterwards: a plain bound on the
# time column, so chunks before it are excluded. $5..$7 = last key of the previous
# page (NULL on the first). The raw event is joined on its full primary key; a
# missing event leaves the raw fields NULL.
ROWS_SQL = """-- recompute:rows
SELECT m.observed_at, m.source_event_id, m.attribute_name, m.value,
       m.observed_property, m.feature_of_interest, m.medium,
       m.depth_from_cm, m.depth_to_cm, m.height_m, m.organ, m.context_ref,
       m.deployment_id::text AS deployment_id, m.source_kind, m.quality_flag,
       ST_X(m.location::geometry) AS lon, ST_Y(m.location::geometry) AS lat,
       e.payload->'raw'->'deviceType'->>'value' AS raw_profile_code,
       e.payload->'raw'->'location'->'value' AS raw_location,
       m.entity_id,
       CASE WHEN jsonb_typeof(e.payload->'raw') = 'object' THEN
         (SELECT jsonb_object_agg(kv.key, kv.value)
            FROM jsonb_each(e.payload->'raw') AS kv
           WHERE kv.key IN ('name', 'https://schema.org/name',
                            'https://smartdatamodels.org/name',
                            'controlledProperty', 'measurementType'))
       END AS raw_shape
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
SELECT format('%I.%I', chunk_schema, chunk_name) AS chunk, range_start, range_end
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
    batch. The IS DISTINCT FROM guard re-checks against the current values, so the
    returned count is exactly the rows changed.
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
        "    location = CASE WHEN v.lon IS NULL OR v.lat IS NULL THEN NULL\n"
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
        "       OR (ST_X(t.location::geometry), ST_Y(t.location::geometry))\n"
        "           IS DISTINCT FROM (v.lon, v.lat)\n"
        "       OR (t.observed_property IS NULL AND v.observed_property IS NOT NULL)\n"
        "       OR (t.quality_flag IS NULL AND v.quality_flag IS NOT NULL)\n"
        "       OR (t.source_kind IS NULL AND v.source_kind IS NOT NULL))\n"
    )


# ---------------------------------------------------------------------------
# Pure planning
# ---------------------------------------------------------------------------


def _text_or_none(value: Any, field: str, log_ctx: str) -> Optional[str]:
    """Mirror of the 116 trigger's nkz_obs_text: a non-string becomes NULL + warning."""
    if value is None or isinstance(value, str):
        return value
    logger.warning(f"Recompute: {field} rejected (not a string): {log_ctx}")
    return None


def raw_point(raw_location: Any) -> Optional[Tuple[float, float]]:
    """(lon, lat) of the raw reading's own location, as ingest would store it.

    Same shape check as ingest (observations.reading_point) plus the 116 trigger's
    WGS84 bounds (nkz_obs_point stores NULL outside them).
    """
    try:
        value = _json(raw_location) if raw_location is not None else None
    except ValueError:
        return None
    point = reading_point(value)
    if point is None:
        return None
    lon, lat = float(point["coordinates"][0]), float(point["coordinates"][1])
    if not (math.isfinite(lon) and math.isfinite(lat)) or abs(lon) > 180 or abs(lat) > 90:
        return None
    return lon, lat


def decide_row(row: StoredRow, measurement_def: Optional[Dict[str, Any]]) -> ObservationDecision:
    """The ingest decision (`decide_observation_property`) applied to the stored raw payload.

    `measurement_def` is the profile entry of (raw `deviceType`, the row's channel) as
    `channel_def` returns it. The channel is the row's `attribute_name`: recompute never
    changes it. A raw payload with neither `deviceType` nor `name` (Phase 1) falls on the
    legacy rule without a profile and gets no property.
    """
    try:
        entity = _json(row.raw_shape) if row.raw_shape is not None else {}
    except (TypeError, ValueError):
        entity = {}
    shape = device_measurement_shape(entity if isinstance(entity, dict) else {})
    return decide_observation_property(shape, row.raw_profile_code, measurement_def)


def _log_gate(
    row: StoredRow, decision: ObservationDecision, device_urn: str, tenant_id: str
) -> None:
    reason = {
        SKIP_STATISTIC: "observation_skipped_statistic",
        SKIP_PROPERTY_MISMATCH: "observation_property_mismatch",
        SKIP_NAME_UNUSABLE: "device_measurement_name_unusable",
    }[decision.skip]
    _skip_warnings.warning(
        (tenant_id, device_urn, f"{reason}:{row.attribute_name}"),
        f"{reason}: context left NULL: tenant_id={tenant_id} device_urn={device_urn} "
        f"observed_at={row.observed_at.isoformat()} entity_id={row.entity_id} "
        f"attribute={row.attribute_name} statistic={decision.statistic!r} "
        f"profile_observedProperty={decision.def_property!r}",
    )


def plan_row(
    row: StoredRow,
    dep: Optional[Deployment],
    prop: Optional[PropertyDef],
    measurement_def: Optional[Dict[str, Any]],
    device_urn: str,
    *,
    tenant_id: str,
) -> Optional[RowUpdate]:
    """Target values for one row, or None when nothing would change.

    `dep` is the deployment valid at row.observed_at and `prop` the catalogue entry
    of the row's effective observed property. The target depends only on them and
    on the raw event, never on the row's current context, so runs converge.
    """
    decision = decide_row(row, measurement_def)
    effective_property = row.observed_property or decision.observed_property or None
    # A row gets context only when ingest would have given it one: a valid
    # deployment and an observed property. ASSUMPTION: a DeviceMeasurement `observedProperty`
    # in the raw payload is never read (ingest does not read it either); the property comes
    # from the shared decision. ASSUMPTION: a property taken from the decision must be in the
    # catalogue (a column defined as a catalogue name never receives an unknown one); a row
    # already carrying observed_property is re-resolved regardless, except when the decision
    # is a deliberate gate (statistic channel, property mismatch, unusable name): those never
    # carry context, so a stale one is cleared. The row's observed_property is left as it is.
    has_context = dep is not None and decision.skip not in GATE_REASONS and (
        row.observed_property is not None
        or (effective_property is not None and prop is not None)
    )
    if dep is not None and decision.skip in GATE_REASONS:
        _log_gate(row, decision, device_urn, tenant_id)
    # Entity id rule (same as ingest, common.observation_context.entity_id_conforms): the
    # reader prunes compressed batches by it, so a row it would skip never gets context.
    device_id = device_urn.rsplit(":", 1)[-1]
    if has_context and not entity_id_conforms(row.entity_id, device_id, row.attribute_name):
        has_context = False
        _skip_warnings.warning(
            (tenant_id, device_urn, f"observation_entity_id_nonconforming:{row.attribute_name}"),
            f"observation_entity_id_nonconforming: context left NULL: tenant_id={tenant_id} "
            f"device_urn={device_urn} observed_at={row.observed_at.isoformat()} "
            f"entity_id={row.entity_id} attribute={row.attribute_name}",
        )
    # Unit guard (same as ingest): a property is only filled in from the raw event when
    # the profile's unit matches the catalogue's; otherwise the row stays without one.
    if (
        has_context
        and row.observed_property is None
        and not units_compatible(
            (measurement_def or {}).get("unit"), prop.unit if prop is not None else None
        )
    ):
        has_context = False
        _skip_warnings.warning(
            (tenant_id, device_urn, f"observation_unit_mismatch:{row.attribute_name}"),
            f"observation_unit_mismatch: observed_property left NULL: tenant_id={tenant_id} "
            f"device_urn={device_urn} observed_at={row.observed_at.isoformat()} "
            f"attribute={row.attribute_name} "
            f"profile_unit={(measurement_def or {}).get('unit')!r} "
            f"catalogue_unit={prop.unit if prop is not None else None!r}",
        )

    if has_context:
        log_ctx = (
            f"tenant_id={tenant_id} device_urn={device_urn} "
            f"observed_at={row.observed_at.isoformat()} attribute={row.attribute_name}"
        )
        ctx = observation_context(row.attribute_name, device_urn, dep)
        medium = ctx["medium"]
        if medium is not None and medium not in MEDIA:
            logger.warning(f"Recompute: medium {medium!r} not in vocabulary, stored NULL: {log_ctx}")
            medium = None
        # Location: mobile -> the reading's own point (or NULL); fixed -> the
        # deployment's point (or NULL). Same as ingest (notification_handler).
        point = raw_point(row.raw_location) if dep.mobility == "mobile" else dep.location
        lon, lat = point if point is not None else (None, None)
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
            lon=lon,
            lat=lat,
            observed_property=row.observed_property or effective_property,
            quality_flag=flag,
            source_kind=row.source_kind or "device",
        )
    else:
        # No context: the deployment-derived columns and the location go NULL, as a
        # fresh ingest without deployment stores; observed_property, quality_flag
        # and source_kind stay as they are.
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
            lon=None,
            lat=None,
            observed_property=row.observed_property,
            quality_flag=row.quality_flag,
            source_kind=row.source_kind,
        )

    current = (
        row.deployment_id, row.feature_of_interest, row.medium, row.depth_from_cm,
        row.depth_to_cm, row.height_m, row.organ, row.context_ref, row.lon, row.lat,
        row.observed_property, row.quality_flag, row.source_kind,
    )
    wanted = (
        target.deployment_id, target.feature_of_interest, target.medium,
        target.depth_from_cm, target.depth_to_cm, target.height_m, target.organ,
        target.context_ref, target.lon, target.lat, target.observed_property,
        target.quality_flag, target.source_kind,
    )
    return None if current == wanted else target


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


def _is_compressed_chunk_error(exc: BaseException) -> bool:
    """TimescaleDB 2.10: 'cannot update/delete rows from chunk "…" as it is compressed'."""
    return isinstance(exc, asyncpg.PostgresError) and "as it is compressed" in str(exc)


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

    async def claim_next(
        self, conn, tenant_id: str, exclude: Sequence[Any] = ()
    ) -> Optional[Dict[str, Any]]:
        """Claim the oldest eligible run of `tenant_id` on `conn`.

        Must be called inside a transaction on `conn`; the caller commits. The row
        lock (FOR UPDATE SKIP LOCKED) keeps concurrent claimers off the same run.
        The returned dict carries `attempts` after the claim.
        """
        await conn.execute(SET_TENANT_SQL, tenant_id)
        row = await conn.fetchrow(CLAIM_SQL, tenant_id, list(exclude))
        if row is None:
            return None
        attempts = await conn.fetchval(MARK_RUNNING_SQL, tenant_id, row["id"])
        return {**dict(row), "attempts": attempts}

    async def _claim(self, tenant_id: str, exclude: Sequence[Any]) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn, conn.transaction():
            return await self.claim_next(conn, tenant_id, exclude)

    # -- audit --------------------------------------------------------------

    async def _mark(self, sql: str, tenant_id: str, *args: Any) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(SET_TENANT_SQL, tenant_id)
            await conn.execute(sql, tenant_id, *args)

    async def _mark_retry(self, tenant_id: str, run_id: Any, rows: int, error: str) -> str:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(SET_TENANT_SQL, tenant_id)
            return await conn.fetchval(
                MARK_RETRY_SQL, tenant_id, run_id, rows, error, MAX_ATTEMPTS
            )

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
        if after is not None:
            after_at, after_id, after_attr = after
            lo = after_at  # plain time bound: earlier chunks are excluded
        else:
            after_at, after_id, after_attr = None, None, None
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                ROWS_SQL, tenant_id, device_id, lo, hi, after_at, after_id, after_attr,
                PAGE_ROWS, timeout=PAGE_TIMEOUT_S,
            )
        return [StoredRow(**dict(r)) for r in rows]

    async def _decompress(self, chunk: str, log_ctx: str) -> None:
        """Decompress one chunk in its own short transaction (lock timeout 5 s)."""
        logger.warning(f"Recompute: decompressing chunk {chunk}: {log_ctx}")
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(LOCK_TIMEOUT_SQL)
            await conn.execute(DECOMPRESS_SQL, chunk, timeout=DECOMPRESS_TIMEOUT_S)

    async def _decompress_holding(
        self, updates: List[RowUpdate], log_ctx: str
    ) -> List[Tuple[datetime, datetime]]:
        """Decompress only the compressed chunks that contain rows of `updates`.

        Returns the [start, end) ranges of the compressed chunks left alone inside
        the updates' time span (they hold none of the rows).
        """
        times = [u.observed_at for u in updates]  # sorted
        async with self._pool.acquire() as conn:
            chunks = await conn.fetch(
                CHUNKS_IN_RANGE_SQL, times[0], times[-1], timeout=PAGE_TIMEOUT_S
            )
        skipped: List[Tuple[datetime, datetime]] = []
        for ch in chunks:
            i = bisect.bisect_left(times, ch["range_start"])
            if i < len(times) and times[i] < ch["range_end"]:
                await self._decompress(ch["chunk"], log_ctx)
            else:
                skipped.append((ch["range_start"], ch["range_end"]))
        return skipped

    @staticmethod
    def _split_batches(
        updates: List[RowUpdate], skipped: List[Tuple[datetime, datetime]]
    ) -> List[List[RowUpdate]]:
        """Batches of <= UPDATE_BATCH_ROWS whose observed_at window overlaps no
        compressed chunk: on TimescaleDB 2.10 the UPDATE fails when its window
        overlaps a compressed chunk, even one without matching rows (measured).
        """
        batches: List[List[RowUpdate]] = []
        current: List[RowUpdate] = []
        for u in updates:
            if current and (
                len(current) >= UPDATE_BATCH_ROWS
                or any(s <= u.observed_at and e > current[-1].observed_at for s, e in skipped)
            ):
                batches.append(current)
                current = []
            current.append(u)
        if current:
            batches.append(current)
        return batches

    async def _update_batch(self, tenant_id: str, batch: List[RowUpdate]) -> int:
        flat: List[Any] = []
        for u in batch:
            flat.extend(u)
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(LOCK_TIMEOUT_SQL)
            status = await conn.execute(
                _update_sql(len(batch)),
                tenant_id, batch[0].observed_at, batch[-1].observed_at, *flat,
                timeout=UPDATE_TIMEOUT_S,
            )
        return int(str(status).rsplit(" ", 1)[-1])

    async def _apply(
        self, tenant_id: str, updates: List[RowUpdate], progress: _Progress, log_ctx: str
    ) -> None:
        """Decompress the chunks holding `updates`, then UPDATE in batches (each commits)."""
        updates = sorted(updates, key=lambda u: (u.observed_at, u.source_event_id, u.attribute_name))
        skipped = await self._decompress_holding(updates, log_ctx)
        for batch in self._split_batches(updates, skipped):
            try:
                progress.rows_changed += await self._update_batch(tenant_id, batch)
            except Exception as exc:
                if not _is_compressed_chunk_error(exc):
                    raise
                # A chunk was (re)compressed in between (compression policy): once more.
                logger.warning(f"Recompute: chunk compressed before update, retrying: {log_ctx}")
                again = await self._decompress_holding(batch, log_ctx)
                for sub in self._split_batches(batch, again):
                    progress.rows_changed += await self._update_batch(tenant_id, sub)

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
                # The profile entry of the PUBLISHED channel (window statistics expanded),
                # as ingest reads it: the decision, the unit guard and counterMax need it.
                mdef = None
                if dep is not None and row.raw_profile_code:
                    key = (row.raw_profile_code, row.attribute_name)
                    if key not in mdefs:
                        mdefs[key] = await self._lookup.channel_def(tenant_id, *key)
                    mdef = mdefs[key]
                name = row.observed_property or decide_row(row, mdef).observed_property
                prop = None
                if name and dep is not None:
                    if name not in props:
                        props[name] = await self._lookup.property(tenant_id, name)
                    prop = props[name]
                target = plan_row(row, dep, prop, mdef, device_urn, tenant_id=tenant_id)
                if target is not None:
                    updates.append(target)
            if updates:
                await self._apply(tenant_id, updates, progress, log_ctx)
            # Renew the lease so a long catch-up is not reclaimed as abandoned.
            await self._mark(RENEW_LEASE_SQL, tenant_id, run["id"])
            last = page[-1]
            after = (last.observed_at, last.source_event_id, last.attribute_name)
            if len(page) < PAGE_ROWS:
                break
        return progress.rows_changed

    async def _execute(self, run: Dict[str, Any]) -> None:
        """Process one claimed run and record the outcome. Never raises (but cancellation)."""
        tenant_id = run["tenant_id"]
        run_id = run["id"]
        ctx = f"tenant_id={tenant_id} run_id={run_id} device_urn={run['device_urn']}"
        if (run.get("attempts") or 0) >= MAX_ATTEMPTS:
            # Its lease expired MAX_ATTEMPTS times: the run kills its worker (e.g. OOM).
            error = f"abandoned: lease expired {run['attempts']} times"
            logger.error(f"Recompute run failed: {ctx}: {error}")
            try:
                await self._mark(MARK_FAILED_SQL, tenant_id, run_id, 0, error)
            except Exception as mark_exc:
                logger.error(f"Recompute: could not mark run failed: {ctx}: {_error_text(mark_exc)}")
            return
        progress = _Progress()
        try:
            changed = await self._process_run(run, progress)
            await self._mark(MARK_DONE_SQL, tenant_id, run_id, changed)
            logger.info(f"Recompute run done: {ctx} rows_changed={changed}")
        except asyncio.CancelledError:
            try:
                await self._mark(MARK_CANCELLED_SQL, tenant_id, run_id, progress.rows_changed)
                logger.warning(
                    f"Recompute run cancelled, back to pending: {ctx} "
                    f"rows_changed={progress.rows_changed}"
                )
            except Exception as mark_exc:
                logger.error(
                    f"Recompute: could not return cancelled run to pending (lease will "
                    f"expire): {ctx}: {_error_text(mark_exc)}"
                )
            raise
        except Exception as exc:
            error = _error_text(exc)
            try:
                status = await self._mark_retry(tenant_id, run_id, progress.rows_changed, error)
                logger.error(
                    f"Recompute attempt failed, run now {status}: {ctx} "
                    f"rows_changed={progress.rows_changed}: {error}",
                    exc_info=True,
                )
            except Exception as mark_exc:
                logger.error(
                    f"Recompute attempt failed and could not be recorded (lease will "
                    f"expire): {ctx}: {error}; {_error_text(mark_exc)}"
                )
        finally:
            _skip_warnings.flush()  # the run's suppressed counts, whatever its outcome

    async def run_once(self) -> int:
        """Claim and process eligible runs of every tenant. Returns runs processed."""
        processed = 0
        for tenant_id in await self._active_tenants():
            handled: Set[Any] = set()
            for _ in range(MAX_RUNS_PER_TENANT):
                try:
                    run = await self._claim(tenant_id, sorted(handled, key=str))
                except Exception as exc:
                    logger.error(f"Recompute: claim failed: tenant_id={tenant_id}: {_error_text(exc)}")
                    break
                if run is None:
                    break
                handled.add(run["id"])
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
