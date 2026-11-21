"""Versioned device deployments (`public.device_deployments`).

A deployment says where a device is and what it observes over a period. An open
deployment has an unbounded upper bound (NULL in the range). Writes are one
transaction each and leave a row in `device_deployment_changes`; the exclusion
constraint on the table is the final guard against overlapping periods.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg2.errors
from psycopg2.extras import Json, RealDictCursor

import dataloggers
from dataloggers import ApiError
from db_helper import get_db_connection_with_tenant

logger = logging.getLogger(__name__)

MOBILITIES = ('fixed', 'mobile')
_REVERT_REASON = 'rollback: orion mirror failed'
_OVERLAP = 'Deployment periods would overlap'

_SELECT = (
    "SELECT id::text AS id, device_urn, lower(valid) AS valid_from, upper(valid) AS valid_to, "
    "observes_feature, observation_context, channel_overrides, mobility, "
    "ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lon "
    "FROM device_deployments"
)
_CORRECTABLE = ('valid_from', 'valid_to', 'observesFeature', 'observation_context',
                'channel_overrides', 'location')


@dataclass(frozen=True)
class DeploymentSpec:
    observes_feature: Optional[str]
    observation_context: dict = field(default_factory=dict)
    channel_overrides: dict = field(default_factory=dict)
    mobility: str = 'fixed'
    location: Optional[tuple] = None          # (lon, lat)


# ---- helpers -----------------------------------------------------------------

def _require_aware(value, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ApiError(400, f'{name} must be a timezone-aware datetime')
    return value


def _require_reason(reason) -> str:
    if not isinstance(reason, str) or not reason.strip():
        raise ApiError(400, 'reason is required')
    return reason.strip()


def _normalise_location(loc) -> Optional[tuple]:
    """(lon, lat) tuple, {lat, lon} dict or None -> validated (lon, lat) or None."""
    if loc is None:
        return None
    try:
        if isinstance(loc, dict):
            lon, lat = float(loc['lon']), float(loc['lat'])
        else:
            lon, lat = float(loc[0]), float(loc[1])
    except (KeyError, IndexError, TypeError, ValueError):
        raise ApiError(400, 'location must be (lon, lat) or {lat, lon}') from None
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        raise ApiError(400, 'location out of range')
    return (lon, lat)


def _to_dict(row: dict) -> dict:
    lat, lon = row.get('lat'), row.get('lon')
    return {
        'id': row['id'],
        'device_urn': row['device_urn'],
        'valid_from': row['valid_from'],
        'valid_to': row['valid_to'],
        'observesFeature': row['observes_feature'],
        'observationContext': row['observation_context'],
        'channelOverrides': row['channel_overrides'],
        'mobility': row['mobility'],
        'location': None if lat is None or lon is None else {'lat': lat, 'lon': lon},
    }


def _audit(dep: Optional[dict]) -> Optional[Json]:
    """JSON-safe copy of a deployment dict for the change log."""
    if dep is None:
        return None
    out = dict(dep)
    for key in ('valid_from', 'valid_to'):
        if isinstance(out.get(key), datetime):
            out[key] = out[key].isoformat()
    return Json(out)


def _fetch(cur, tenant_id: str, deployment_id: str, lock: bool = False) -> Optional[dict]:
    cur.execute(f"{_SELECT} WHERE id = %s AND tenant_id = %s" + (" FOR UPDATE" if lock else ""),
                (deployment_id, tenant_id))
    row = cur.fetchone()
    return _to_dict(row) if row else None


def _log_change(cur, tenant_id, deployment_id, actor, reason, before, after) -> None:
    cur.execute(
        "INSERT INTO device_deployment_changes "
        "(tenant_id, deployment_id, changed_by, reason, before, after) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (tenant_id, deployment_id, actor, reason, _audit(before), _audit(after)))


def _enqueue_recompute(cur, tenant_id, device_urn, deployment_id, range_from, range_to,
                       actor, reason) -> None:
    """Queue a recompute of stored observations (contract 8.6) in the caller's transaction.

    `range_to` None means open-ended. Same cursor as the change itself, so tenant RLS
    applies and a rolled-back change leaves no run behind.
    """
    cur.execute(
        "INSERT INTO observation_recompute_runs "
        "(tenant_id, device_urn, deployment_id, range_from, range_to, requested_by, reason) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (tenant_id, device_urn, deployment_id, range_from, range_to, actor, reason))


# ---- writes ------------------------------------------------------------------

@dataloggers.in_step('db', 'Database error')
def open_deployment(tenant_id: str, device_urn: str, spec: DeploymentSpec,
                    valid_from: datetime, actor: str, reason: str, first: bool = False) -> dict:
    """Close the device's open deployment at `valid_from` and open a new one, atomically.

    A per-device advisory lock serialises every writer of one device's deployments.
    With `first=True` (registration) the device must have no open deployment, and
    `valid_from` must come after the end of any closed history.
    """
    _require_aware(valid_from, 'valid_from')
    reason = _require_reason(reason)
    if spec.mobility not in MOBILITIES:
        raise ApiError(400, "mobility must be 'fixed' or 'mobile'")
    location = _normalise_location(spec.location)

    with get_db_connection_with_tenant(tenant_id) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            # Held until commit/rollback: the open-row check and the insert are atomic,
            # also when there is no row yet to lock (concurrent first registrations).
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (f"{tenant_id}|{device_urn}",))
            # Lock the open row so two concurrent moves serialise on it.
            cur.execute(f"{_SELECT} WHERE tenant_id = %s AND device_urn = %s "
                        "AND upper_inf(valid) FOR UPDATE", (tenant_id, device_urn))
            row = cur.fetchone()
            previous = _to_dict(row) if row else None
            if first:
                if previous is not None:
                    raise ApiError(409, 'Device already has an open deployment')
                cur.execute("SELECT max(upper(valid)) AS last_end FROM device_deployments "
                            "WHERE tenant_id = %s AND device_urn = %s", (tenant_id, device_urn))
                last = cur.fetchone()
                last_end = last['last_end'] if last else None
                if last_end is not None and valid_from <= last_end:
                    raise ApiError(409, 'validFrom must be after the end of the previous '
                                        'deployment history of this device')
            if previous is not None:
                if valid_from <= previous['valid_from']:
                    raise ApiError(409, 'valid_from must be after the start of the current deployment')
                cur.execute("UPDATE device_deployments SET valid = tstzrange(lower(valid), %s, '[)') "
                            "WHERE id = %s AND tenant_id = %s",
                            (valid_from, previous['id'], tenant_id))

            point = "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography" if location else "NULL"
            loc_params = location if location else ()
            cur.execute(
                "INSERT INTO device_deployments "
                "(tenant_id, device_urn, valid, observes_feature, observation_context, "
                " channel_overrides, mobility, location, created_by) "
                f"VALUES (%s, %s, tstzrange(%s, NULL, '[)'), %s, %s, %s, %s, {point}, %s) "
                "RETURNING id::text AS id",
                (tenant_id, device_urn, valid_from, spec.observes_feature,
                 Json(spec.observation_context), Json(spec.channel_overrides),
                 spec.mobility, *loc_params, actor))
            new_id = cur.fetchone()['id']

            if previous is not None:
                _log_change(cur, tenant_id, previous['id'], actor, reason,
                            previous, _fetch(cur, tenant_id, previous['id']))
            created = _fetch(cur, tenant_id, new_id)
            _log_change(cur, tenant_id, new_id, actor, reason, None, created)
            # Always: readings around a registration or move may carry missing/stale context.
            _enqueue_recompute(cur, tenant_id, device_urn, new_id, valid_from, None, actor, reason)
            conn.commit()
        except psycopg2.errors.ExclusionViolation:
            raise ApiError(409, _OVERLAP) from None
        finally:
            cur.close()
    logger.info("deployment opened tenant=%s device=%s id=%s", tenant_id, device_urn, new_id)
    return created


@dataloggers.in_step('db', 'Database error')
def correct_deployment(tenant_id: str, deployment_id: str, changes: dict,
                       actor: str, reason: str) -> dict:
    """Correct a deployment in place (history fix); records before and after."""
    reason = _require_reason(reason)
    if not isinstance(changes, dict) or not changes:
        raise ApiError(400, 'changes must not be empty')
    unknown = sorted(set(changes) - set(_CORRECTABLE))
    if unknown:
        raise ApiError(400, f"cannot correct: {', '.join(unknown)}")
    if 'valid_from' in changes:
        _require_aware(changes['valid_from'], 'valid_from')
    if changes.get('valid_to') is not None:
        _require_aware(changes['valid_to'], 'valid_to')
    location = _normalise_location(changes['location']) if 'location' in changes else None

    with get_db_connection_with_tenant(tenant_id) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            before = _fetch(cur, tenant_id, deployment_id, lock=True)
            if before is None:
                raise ApiError(404, 'Deployment not found')

            sets, params = [], []
            new_from = changes.get('valid_from', before['valid_from'])
            new_to = changes['valid_to'] if 'valid_to' in changes else before['valid_to']
            if 'valid_from' in changes or 'valid_to' in changes:
                if new_to is not None and new_to <= new_from:
                    raise ApiError(400, 'valid_to must be after valid_from')
                sets.append("valid = tstzrange(%s, %s, '[)')")
                params += [new_from, new_to]
            if 'observesFeature' in changes:
                sets.append("observes_feature = %s")
                params.append(changes['observesFeature'])
            if 'observation_context' in changes:
                sets.append("observation_context = %s")
                params.append(Json(changes['observation_context']))
            if 'channel_overrides' in changes:
                sets.append("channel_overrides = %s")
                params.append(Json(changes['channel_overrides']))
            if 'location' in changes:
                if location:
                    sets.append("location = ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography")
                    params += list(location)
                else:
                    sets.append("location = NULL")
            cur.execute(f"UPDATE device_deployments SET {', '.join(sets)} "
                        "WHERE id = %s AND tenant_id = %s",
                        (*params, deployment_id, tenant_id))
            after = _fetch(cur, tenant_id, deployment_id)
            _log_change(cur, tenant_id, deployment_id, actor, reason, before, after)
            # Union of old and new interval; open if either bound is open.
            run_from = min(before['valid_from'], new_from)
            run_to = (None if before['valid_to'] is None or new_to is None
                      else max(before['valid_to'], new_to))
            _enqueue_recompute(cur, tenant_id, before['device_urn'], deployment_id,
                               run_from, run_to, actor, reason)
            conn.commit()
        except psycopg2.errors.ExclusionViolation:
            raise ApiError(409, _OVERLAP) from None
        finally:
            cur.close()
    logger.info("deployment corrected tenant=%s id=%s by=%s", tenant_id, deployment_id, actor)
    return after


@dataloggers.in_step('db', 'Database error')
def revert_open(tenant_id: str, new_deployment_id: str, actor: str) -> None:
    """Undo exactly one `open_deployment`: drop the new row and re-open the one it closed."""
    with get_db_connection_with_tenant(tenant_id) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            new = _fetch(cur, tenant_id, new_deployment_id, lock=True)
            if new is None or new['valid_to'] is not None:
                raise ApiError(409, 'Not the current open deployment')
            cur.execute(f"{_SELECT} WHERE tenant_id = %s AND device_urn = %s "
                        "AND upper(valid) = %s FOR UPDATE",
                        (tenant_id, new['device_urn'], new['valid_from']))
            row = cur.fetchone()
            previous = _to_dict(row) if row else None

            cur.execute("DELETE FROM device_deployments WHERE id = %s AND tenant_id = %s",
                        (new_deployment_id, tenant_id))
            _log_change(cur, tenant_id, new_deployment_id, actor, _REVERT_REASON,
                        new, {'deleted': True})
            if previous is not None:
                cur.execute("UPDATE device_deployments SET valid = tstzrange(lower(valid), NULL, '[)') "
                            "WHERE id = %s AND tenant_id = %s", (previous['id'], tenant_id))
                _log_change(cur, tenant_id, previous['id'], actor, _REVERT_REASON,
                            previous, _fetch(cur, tenant_id, previous['id']))
            conn.commit()
        finally:
            cur.close()
    logger.info("deployment open reverted tenant=%s id=%s by=%s", tenant_id, new_deployment_id, actor)


@dataloggers.in_step('db', 'Database error')
def delete_deployment(tenant_id: str, deployment_id: str, actor: str = 'system') -> None:
    """Remove one deployment (and its change rows); registration undo only.

    Queues a recompute of its whole interval, so its observations are re-resolved
    against whatever deployment is valid (or get NULL context).
    """
    with get_db_connection_with_tenant(tenant_id) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            doomed = _fetch(cur, tenant_id, deployment_id, lock=True)
            cur.execute("DELETE FROM device_deployment_changes WHERE tenant_id = %s "
                        "AND deployment_id = %s", (tenant_id, deployment_id))
            cur.execute("DELETE FROM device_deployments WHERE tenant_id = %s AND id = %s",
                        (tenant_id, deployment_id))
            if doomed is not None:
                _enqueue_recompute(cur, tenant_id, doomed['device_urn'], deployment_id,
                                   doomed['valid_from'], doomed['valid_to'],
                                   actor, 'deployment deleted')
            conn.commit()
        finally:
            cur.close()


@dataloggers.in_step('db', 'Database error')
def close_open_deployment(tenant_id: str, device_urn: str, actor: str, reason: str) -> Optional[dict]:
    """End the device's open deployment now, keeping it as history; None if none is open."""
    reason = _require_reason(reason)
    now = datetime.now(timezone.utc)
    with get_db_connection_with_tenant(tenant_id) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (f"{tenant_id}|{device_urn}",))
            cur.execute(f"{_SELECT} WHERE tenant_id = %s AND device_urn = %s "
                        "AND upper_inf(valid) FOR UPDATE", (tenant_id, device_urn))
            row = cur.fetchone()
            if row is None:
                conn.commit()
                return None
            before = _to_dict(row)
            # The range needs upper > lower; the clock can equal the start in tests.
            end = max(now, before['valid_from'] + timedelta(microseconds=1))
            cur.execute("UPDATE device_deployments SET valid = tstzrange(lower(valid), %s, '[)') "
                        "WHERE id = %s AND tenant_id = %s", (end, before['id'], tenant_id))
            after = _fetch(cur, tenant_id, before['id'])
            _log_change(cur, tenant_id, before['id'], actor, reason, before, after)
            _enqueue_recompute(cur, tenant_id, device_urn, before['id'], end, None, actor, reason)
            conn.commit()
        finally:
            cur.close()
    logger.info("deployment closed tenant=%s device=%s id=%s", tenant_id, device_urn, before['id'])
    return after


# ---- reads -------------------------------------------------------------------

@dataloggers.in_step('db', 'Database error')
def list_deployments(tenant_id: str, device_urn: str) -> list:
    with get_db_connection_with_tenant(tenant_id) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(f"{_SELECT} WHERE tenant_id = %s AND device_urn = %s "
                    "ORDER BY lower(valid) DESC", (tenant_id, device_urn))
        rows = cur.fetchall()
        cur.close()
    return [_to_dict(r) for r in rows]


@dataloggers.in_step('db', 'Database error')
def current_deployment(tenant_id: str, device_urn: str) -> Optional[dict]:
    with get_db_connection_with_tenant(tenant_id) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(f"{_SELECT} WHERE tenant_id = %s AND device_urn = %s "
                    "AND valid @> now() LIMIT 1", (tenant_id, device_urn))
        row = cur.fetchone()
        cur.close()
    return _to_dict(row) if row else None


@dataloggers.in_step('db', 'Database error')
def get_deployment(tenant_id: str, deployment_id: str) -> Optional[dict]:
    with get_db_connection_with_tenant(tenant_id) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        found = _fetch(cur, tenant_id, deployment_id)
        cur.close()
    return found
