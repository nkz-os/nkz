"""
Notification handler for Orion-LD subscriptions.

Receives NGSI-LD entity updates and persists to TimescaleDB
after applying Processing Profiles (throttle, filter, delta).
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, BackgroundTasks, Header, HTTPException
from nkz_platform_sdk.notify_auth import accept_master_from_env, verify_notification

from .config import Settings
from .event_sink import EventSink, TelemetryEvent, is_connection_error
from .health_checker import HealthChecker, _severity
from .profiles import ProfileService
from .calibration import CalibrationService
from .dedup import NotificationDedup
from .observations import reading_point, resolve_observation, units_compatible
from common.observation_context import entity_id_conforms
from .geometry_cache import upsert_geometry
from .device_measurement import (  # noqa: F401  (re-exported: the shape rule lives there)
    CONTEXT_MEASUREMENT_TYPES,
    CONTEXT_STATISTICS,
    NAME_KEYS,
    SDM_MEASUREMENT_TYPES,
    SKIP_NO_DEVICE_TYPE,
    SKIP_PROPERTY_MISMATCH,
    SKIP_STATISTIC,
    DeviceMeasurementShape,
    decide_observation_property,
    device_measurement_shape,
    non_empty_str as _non_empty_str,
    prop_value as _prop_value,
)
from .log_throttle import ThrottledWarnings

logger = logging.getLogger(__name__)

# "No observation" reasons that hold for every reading of a device (most devices have
# no deployment): logged once per (tenant, device, reason), then summarised per window.
_skip_warnings = ThrottledWarnings(logger)
# Deliberate, expected skips (spread-statistic channels never carry observation context): same
# throttling, at INFO so they never read as a fault.
_skip_info = ThrottledWarnings(logger, level=logging.INFO)

# `refDevice` accepted under the short names or either IRI (design A.5 R1). Precedence is the
# tuple order; after the R1 remap the old attribute compacts to `hasDevice`.
REF_DEVICE_KEYS = (
    "refDevice",
    "hasDevice",
    "https://smartdatamodels.org/dataModel.Device/hasDevice",
    "https://smartdatamodels.org/dataModel.Device/refDevice",
)

router = APIRouter()


def _reject_unauthenticated_notify(headers) -> Optional[HTTPException]:
    """401 unless the notification authenticates (flag-gated).

    Two-phase rollout: NOTIFY_REQUIRE_INTERNAL_SECRET stays off until subscription
    creators have converged to carry receiverInfo, then flips on with no code deploy.
    Accepts the token bound to this tenant and host (nkz_platform_sdk.notify_auth)
    and, until NKZ_NOTIFY_ACCEPT_MASTER is false, the master secret itself.
    """
    require = os.getenv("NOTIFY_REQUIRE_INTERNAL_SECRET", "").lower() in (
        "1", "true", "yes", "on"
    )
    if not require:
        return None
    if not verify_notification(
        headers,
        secret=os.getenv("INTERNAL_SERVICE_SECRET", ""),
        accept_master=accept_master_from_env(),
    ):
        return HTTPException(status_code=401, detail="missing or invalid notification credentials")
    return None

# Global instances (set via init_handler)
_settings: Optional[Settings] = None
_profile_service: Optional[ProfileService] = None
_event_sink: Optional[EventSink] = None
_health_checker: Optional[HealthChecker] = None
_calibration_service: Optional[CalibrationService] = None
_dedup: Optional[NotificationDedup] = None
_observation_lookup: Optional[Any] = None  # ObservationLookup; None = feature off
_geometry_pool: Optional[Any] = None  # asyncpg pool for observation_geometry_cache; None = off
_inbox: Optional[Any] = None  # NotificationInbox (enqueue); None = not wired, receivers answer 503


def init_handler(
    settings: Settings,
    profile_service: ProfileService,
    event_sink: EventSink,
    health_checker: Optional[HealthChecker] = None,
    calibration_service: Optional[CalibrationService] = None,
    dedup: Optional[NotificationDedup] = None,
    observation_lookup: Optional[Any] = None,
    geometry_pool: Optional[Any] = None,
    inbox: Optional[Any] = None,
) -> None:
    """Wire dependencies from app lifespan."""
    global _settings, _profile_service, _event_sink, _health_checker, _calibration_service, _dedup, _observation_lookup, _geometry_pool, _inbox
    _settings = settings
    _profile_service = profile_service
    _event_sink = event_sink
    _health_checker = health_checker
    _calibration_service = calibration_service
    _dedup = dedup
    _observation_lookup = observation_lookup
    _geometry_pool = geometry_pool
    _inbox = inbox
    _skip_warnings.reset()
    _skip_info.reset()


class TransientProcessingError(Exception):
    """Infrastructure failure or handler not ready (database unavailable, not wired): retry later.

    Raised by process_entities with the original error as __cause__. The failed attempt
    left no dedup key and no delta-filter state, so a retry stores the reading.
    """


@dataclass(frozen=True)
class _PreparedEvent:
    """An event plus what is recorded once the sink has taken it (see _after_write)."""

    event: TelemetryEvent
    device_id: str
    measurements: Dict[str, Any]  # calibrated, before filtering: the delta-filter state
    filtered: Dict[str, Any]  # what is stored: the dedup key


async def process_notification_task(
    data: Dict[str, Any],
    tenant_id: Optional[str] = None,
):
    """Process one Orion-LD notification body; never raises (kept for existing callers).

    Failures, transient ones included, are logged and the notification is dropped;
    the inbox consumer calls process_entities instead and retries transient ones.
    """
    try:
        entities = data.get("data", [])
        if not entities:
            logger.debug("Empty notification received")
            return
        await process_entities(entities, tenant_id)
    except Exception as e:
        logger.error(f"Error processing notification: {e}", exc_info=True)


async def process_entities(
    entities: List[Dict[str, Any]],
    tenant_id: Optional[str],
) -> None:
    """
    Process notified NGSI-LD entities and persist them to TimescaleDB.

    Flow:
    1. Extract entity info (id, type, attributes)
    2. Get processing profile for device type
    3. Apply throttle/delta checks and the read-only dedup check
    4. Filter attributes
    5. Persist to TimescaleDB via EventSink
    6. Only then record dedup keys, delta-filter last values and replay state

    Raises TransientProcessingError when the handler is not ready (not initialised, no
    event sink) and on a database connection failure (is_connection_error), from the sink
    or while preparing an entity; nothing has been recorded then, so a retry is safe. Any other error from one entity is logged and that entity skipped. Poison
    records are dead-lettered inside the EventSink (final, recorded like an insert).

    NOTE: Does NOT write back to Orion (data comes from Orion subscription)
    """
    if not entities:
        return
    # Not ready (startup, or wiring missing): a normal return would let the inbox delete
    # the rows, so the readings would be lost. Raise and let them be retried.
    if _settings is None or _profile_service is None or _event_sink is None:
        raise TransientProcessingError(
            f"handler not ready (settings, profile service or event sink missing): "
            f"tenant_id={tenant_id} entities={len(entities)}"
        )

    prepared: List[_PreparedEvent] = []
    keys_in_call: set = set()

    for entity in entities:
        try:
            item = await _prepare_entity(entity, tenant_id)
        except TransientProcessingError:
            raise
        except Exception as e:
            if is_connection_error(e):
                raise TransientProcessingError(
                    f"connection failure preparing entity for tenant={tenant_id}: {e}"
                ) from e
            entity_id = entity.get("id") if isinstance(entity, dict) else None
            logger.error(
                f"Entity skipped, processing failed: tenant_id={tenant_id} "
                f"entity_id={entity_id}: {e}",
                exc_info=True,
            )
            continue
        if item is None:
            continue
        # The dedup check is read-only now: an identical entity twice in one call would
        # pass it both times, so repeats inside the call are dropped here.
        key = _call_dedup_key(item)
        if key is not None:
            if key in keys_in_call:
                logger.debug(
                    f"Duplicate entity in one call skipped: entity={item.event.entity_id} "
                    f"observed_at={item.event.observed_at.isoformat()}"
                )
                continue
            keys_in_call.add(key)
        prepared.append(item)

    if not prepared:
        return

    events = [item.event for item in prepared]
    try:
        if len(events) == 1:
            inserted = events if await _event_sink.write(events[0]) is True else []
        else:
            inserted = await _event_sink.write_batch(events)
            if not isinstance(inserted, list):
                inserted = []
    except Exception as e:
        if is_connection_error(e):
            raise TransientProcessingError(
                f"connection failure writing {len(events)} events for tenant={tenant_id}: {e}"
            ) from e
        raise

    logger.info(
        f"Persisted {len(inserted)}/{len(events)} events for tenant={tenant_id}"
    )
    # Replay records only for events really inserted (never dead-lettered ones).
    await _record_replay_state(inserted)
    # Dedup and delta-filter state for every event the sink took (inserted or
    # dead-lettered, both final); never before, so a failed attempt can be retried.
    await _after_write(prepared)


def _call_dedup_key(item: _PreparedEvent) -> Optional[tuple]:
    """Same identity as the Redis dedup key; None when dedup is off (today's behaviour)."""
    if _dedup is None or not _dedup.enabled:
        return None
    event = item.event
    return (
        event.tenant_id,
        event.entity_id,
        event.observed_at.isoformat(),
        json.dumps(item.filtered, sort_keys=True, default=str),
    )


async def _after_write(prepared: List[_PreparedEvent]) -> None:
    """Record dedup keys and delta-filter last values once the sink has taken the events."""
    for item in prepared:
        event = item.event
        if _dedup is not None:
            await _dedup.mark(
                tenant_id=event.tenant_id,
                entity_id=event.entity_id,
                observed_at=event.observed_at,
                measurements=item.filtered,
            )
        if _profile_service is not None:
            _profile_service.update_last_values(item.device_id, item.measurements)


def _replay_fingerprint(entity: Dict[str, Any]) -> Optional[tuple]:
    """(dateObserved, value) of a DeviceMeasurement as notified (before calibration).

    None when either is missing or dateObserved is unparseable: observed_at would then fall
    back to now(), so no meaningful record can exist and the replay guard is bypassed.
    """
    at = _date_observed(entity)
    if at is None:
        return None
    for value_key in ("numValue", "textValue"):
        value_attr = entity.get(value_key)
        if isinstance(value_attr, dict) and "value" in value_attr:
            return at, value_attr["value"]
    return None


async def _record_replay_state(inserted: List[TelemetryEvent]) -> None:
    """After a successful insert, remember each DeviceMeasurement's last reading."""
    if _dedup is None:
        return
    for event in inserted:
        if event.entity_type != "DeviceMeasurement":
            continue
        raw = event.payload.get("raw") if isinstance(event.payload, dict) else None
        fingerprint = _replay_fingerprint(raw) if isinstance(raw, dict) else None
        if fingerprint is not None:
            await _dedup.record_processed(event.tenant_id, event.entity_id, *fingerprint)


async def _process_entity(
    entity: Dict[str, Any],
    tenant_id: Optional[str],
) -> Optional[TelemetryEvent]:
    """Process a single NGSI-LD entity. Returns event or None.

    Records no dedup key and no delta-filter state: process_entities does that after
    the write (see _after_write).
    """
    item = await _prepare_entity(entity, tenant_id)
    return item.event if item is not None else None


async def _prepare_entity(
    entity: Dict[str, Any],
    tenant_id: Optional[str],
) -> Optional[_PreparedEvent]:
    """Build the event of one entity, or None when it is skipped. Writes no state."""
    entity_id = entity.get("id", "")
    entity_type = entity.get("type", "")

    if not entity_id or not entity_type:
        logger.warning("Entity missing id or type, skipping")
        return None

    if entity_type == "DeviceMeasurement":
        # Inverted shape vs every other entity type (see module docstring on the helper):
        # the device is not the last segment of this entity's own id (that is the
        # channel name), and the reading's name/value are not plain attribute
        # keys either. A missing refDevice or reading means there is nothing safe to
        # persist, not a device_id of "" — bail out here rather than falling through.
        device_id, measurements = _extract_device_measurement(entity)
        shape = device_measurement_shape(entity)
        if shape.has_name and shape.channel is None:
            # A data problem, not a normal absence: a present but unusable `name` cannot
            # be keyed (controlledProperty may be the catalogue name, not the channel).
            _skip_warnings.warning(
                (tenant_id, entity_id, "device_measurement_name_unusable"),
                f"device_measurement_name_unusable: reading not stored: tenant_id={tenant_id} "
                f"entity_id={entity_id}",
            )
            return None
        if shape.ambiguous:
            # Stored under `name`; context follows the legacy profile rule for that channel.
            _skip_warnings.warning(
                (tenant_id, entity_id, "ambiguous_shape"),
                f"ambiguous_shape: name differs from controlledProperty and measurementType "
                f"is not in the closed vocabulary; keyed by name, legacy profile rule: "
                f"tenant_id={tenant_id} entity_id={entity_id} name={shape.channel!r} "
                f"controlledProperty={shape.controlled_property!r} "
                f"measurementType={shape.measurement_type!r}",
            )
        if device_id is None or not measurements:
            logger.debug(
                f"DeviceMeasurement {entity_id} missing refDevice or a reading, skipping"
            )
            return None
    else:
        # Extract device_id from entity_id (format: urn:ngsi-ld:Type:tenant:device)
        device_id = entity_id.split(":")[-1] if ":" in entity_id else entity_id
        measurements = _extract_measurements(entity)

    logger.debug(f"Processing entity: {entity_type}/{device_id}")

    if not _profile_service or not _settings:
        # Raised, not skipped: dropping the entity here would lose the reading.
        raise TransientProcessingError(
            f"handler not initialised: tenant_id={tenant_id} entity_id={entity_id}"
        )

    # Replay guard (design A.3): a one-off Orion write to a DM re-notifies its current
    # state long after the short dedup window below. Checked before calibration and the
    # health check (which writes back to Orion). Fail-open inside `is_replay`.
    if entity_type == "DeviceMeasurement" and _dedup is not None:
        fingerprint = _replay_fingerprint(entity)
        if fingerprint is not None and await _dedup.is_replay(
            tenant_id, entity_id, *fingerprint
        ):
            _skip_info.warning(
                (tenant_id, entity_id, "device_measurement_replay_skipped"),
                f"device_measurement_replay_skipped: identical to the last processed "
                f"reading, not stored: tenant_id={tenant_id} entity_id={entity_id} "
                f"device_id={device_id} date_observed={fingerprint[0].isoformat()} "
                f"value={fingerprint[1]!r}",
            )
            return None

    # Get processing profile
    profile = _profile_service.get_profile(
        device_type=entity_type,
        device_id=device_id,
        tenant_id=tenant_id,
    )

    if not measurements:
        logger.debug(f"No measurements in entity {entity_id}")
        return None

    # --- Calibration step: transform raw values ---
    raw_measurements: Dict[str, Any] = {}
    calibration_period_ids: Dict[str, str] = {}
    if _calibration_service and tenant_id:
        for var, val in list(measurements.items()):
            if isinstance(val, (int, float)):
                raw_measurements[var] = val
                cal = await _calibration_service.get_active_period(
                    entity_id, tenant_id, var
                )
                if cal:
                    calibrated = _calibration_service.apply_calibration(val, cal)
                    measurements[var] = calibrated
                    calibration_period_ids[var] = cal["id"]

    # --- Health check on extracted measurements ---
    quality_flag = "valid"
    health_flags: Dict[str, str] = {}
    if _health_checker and tenant_id:
        health_config = await _health_checker.get_health_config(
            entity_id, tenant_id
        )
        if health_config:
            worst_flag = "valid"
            for var, val in measurements.items():
                flag = _health_checker.evaluate_measurement(var, val, health_config)
                health_flags[var] = flag
                if _severity(flag) > _severity(worst_flag):
                    worst_flag = flag
            quality_flag = worst_flag

            # Update reliabilityStatus in Orion if degraded/error
            if quality_flag in ("out_of_bounds", "nan"):
                status = "error" if quality_flag == "out_of_bounds" else "degraded"
                await _health_checker.update_reliability_status(
                    entity_id, tenant_id, status
                )

    # Check if should persist (throttle + delta)
    if not _profile_service.should_persist(profile, device_id, measurements):
        logger.debug(f"Skipping persistence for {device_id} (throttle/delta)")
        return None

    # Filter attributes
    filtered = _profile_service.filter_attributes(profile, measurements)

    if not filtered:
        logger.debug(f"No attributes after filtering for {device_id}")
        return None

    # Extract observedAt timestamp. DeviceMeasurement carries its instant in dateObserved
    # (a plain Property value), not in per-attribute observedAt metadata — the generic
    # extractor below would never find it there and would silently fall back to utcnow().
    observed_at = (
        _extract_device_measurement_observed_at(entity)
        if entity_type == "DeviceMeasurement"
        else _extract_observed_at(entity)
    )

    # Deduplicate against Orion-LD notification redelivery (network retries,
    # subscription replay, inbox rows processed again). Per-event, read-only check:
    # the key is marked only after the write (_after_write), so a failed attempt is
    # retried, not skipped. Fail-open: if Redis is unavailable, seen() returns False.
    # The delta-filter last values are likewise updated only after the write.
    if _dedup is not None:
        is_dup = await _dedup.seen(
            tenant_id=tenant_id,
            entity_id=entity_id,
            observed_at=observed_at,
            measurements=filtered,
        )
        if is_dup:
            logger.debug(
                f"Duplicate notification skipped for entity={entity_id} "
                f"observed_at={observed_at.isoformat()}"
            )
            return None

    observation_payload: Dict[str, Any] = {}
    if entity_type == "DeviceMeasurement" and _observation_lookup is not None:
        observation_payload = await _resolve_observation_payload(
            entity, tenant_id, observed_at, filtered, health_flags
        )

    event = TelemetryEvent(
        tenant_id=tenant_id,
        observed_at=observed_at,
        device_id=device_id,
        entity_id=entity_id,
        entity_type=entity_type,
        payload={
            "measurements": filtered,
            "raw": entity,
            **({"raw_measurements": raw_measurements} if raw_measurements else {}),
            **({"calibration_period_ids": calibration_period_ids} if calibration_period_ids else {}),
            **observation_payload,
        },
        quality_flag=quality_flag,
    )
    return _PreparedEvent(
        event=event, device_id=device_id, measurements=measurements, filtered=filtered
    )


def _ref_device_urn(entity: Dict[str, Any]) -> Optional[str]:
    """The Device URN of a DeviceMeasurement: the first non-empty `object` in REF_DEVICE_KEYS order."""
    for key in REF_DEVICE_KEYS:
        rel = entity.get(key)
        if isinstance(rel, dict):
            target = rel.get("object")
            if isinstance(target, str) and target:
                return target
    return None


def _reading_point(entity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The DeviceMeasurement's own `location` GeoProperty Point, if well-formed."""
    return reading_point(_prop_value(entity, "location"))


async def _resolve_observation_payload(
    entity: Dict[str, Any],
    tenant_id: Optional[str],
    observed_at: datetime,
    filtered: Dict[str, Any],
    health_flags: Dict[str, str],
) -> Dict[str, Any]:
    """Observation keys for the event payload (contract 1.2); {} when nothing resolves.

    Never raises: any failure is logged and the event is written without observations.
    """
    device_urn = _ref_device_urn(entity)
    at_iso = observed_at.isoformat()
    log_ctx = f"tenant_id={tenant_id} device_urn={device_urn} observed_at={at_iso}"

    if not tenant_id:
        logger.warning(f"Observation skipped, tenant missing: {log_ctx}")
        return {}
    if not isinstance(device_urn, str) or not device_urn:
        logger.warning(f"Observation skipped, refDevice URN missing: {log_ctx}")
        return {}

    try:
        dep = await _observation_lookup.deployment_at(tenant_id, device_urn, observed_at)
        if dep is None:
            _skip_warnings.warning(
                (tenant_id, device_urn, "no_deployment"),
                f"Observation skipped, no deployment at observed_at: {log_ctx}",
            )
            return {}

        # ASSUMPTION: a DM `observedProperty` attribute is never read. The builder no longer
        # emits it and it was never deployed; the catalogue name comes only from
        # controlledProperty (SDM shape) or the profile entry (legacy shape).
        shape = device_measurement_shape(entity)
        profile_code = _prop_value(entity, "deviceType")
        entity_id = entity.get("id")
        # same short-id rule as _extract_device_measurement (the stored device_id)
        device_id = device_urn.split(":")[-1]
        observations: Dict[str, Any] = {}
        for attr, value in filtered.items():
            # The reader prunes compressed batches by this entity id rule (common
            # observation_context.entity_id_conforms, timeseries-reader sources._SERIES_PRUNE):
            # a row it would skip must not carry observation context at all.
            # ASSUMPTION: per-attribute reasons key on the attribute too, so each
            # misconfigured channel of a device is reported once, not only the first.
            if not entity_id_conforms(entity_id, device_id, attr):
                _skip_warnings.warning(
                    (tenant_id, device_urn, f"observation_entity_id_nonconforming:{attr}"),
                    f"observation_entity_id_nonconforming: no observation written: {log_ctx} "
                    f"entity_id={entity_id} attribute={attr}",
                )
                continue
            # The profile entry of the PUBLISHED channel (window statistics expanded): the
            # legacy shape's catalogue name, the new shape's opt-in check, the unit guard
            # and counterMax all read it.
            measurement_def: Optional[Dict[str, Any]] = None
            if isinstance(profile_code, str) and profile_code:
                measurement_def = (
                    await _observation_lookup.channel_def(tenant_id, profile_code, attr)
                ) or None
            decision = decide_observation_property(shape, profile_code, measurement_def)
            if decision.skip == SKIP_STATISTIC:
                # Deliberate gate, not a fault: a spread statistic (min/max/stddev) is not an
                # observation of the property itself. The reading is still stored.
                _skip_info.warning(
                    (tenant_id, device_urn, f"observation_skipped_statistic:{attr}"),
                    f"observation_skipped_statistic: no observation written: {log_ctx} "
                    f"entity_id={entity_id} attribute={attr} "
                    f"measurementType={shape.measurement_type!r} statistic={decision.statistic!r}",
                )
                continue
            if decision.skip == SKIP_PROPERTY_MISMATCH:
                _skip_warnings.warning(
                    (tenant_id, device_urn, f"observation_property_mismatch:{attr}"),
                    f"observation_property_mismatch: no observation written: {log_ctx} "
                    f"entity_id={entity_id} attribute={attr} "
                    f"controlledProperty={shape.controlled_property!r} "
                    f"profile_observedProperty={decision.def_property!r}",
                )
                continue
            if decision.skip == SKIP_NO_DEVICE_TYPE:
                _skip_warnings.warning(
                    (tenant_id, device_urn, f"no_device_type:{attr}"),
                    f"no_device_type: no observation written: {log_ctx} "
                    f"entity_id={entity_id} attribute={attr}",
                )
                continue
            observed_property = decision.observed_property
            if not observed_property:
                _skip_warnings.warning(
                    (tenant_id, device_urn, f"no_observed_property:{attr}"),
                    f"Observation skipped for {attr}, no observedProperty: {log_ctx}",
                )
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            prop = await _observation_lookup.property(tenant_id, observed_property)
            if prop is None:
                logger.warning(
                    f"Observation skipped for {attr}, observedProperty "
                    f"{observed_property!r} unknown or deprecated: {log_ctx}"
                )
                continue
            profile_unit = measurement_def.get("unit") if measurement_def else None
            if not units_compatible(profile_unit, prop.unit):
                _skip_warnings.warning(
                    (tenant_id, device_urn, f"observation_unit_mismatch:{attr}"),
                    f"observation_unit_mismatch: no observation written: {log_ctx} "
                    f"attribute={attr} profile_unit={profile_unit!r} catalogue_unit={prop.unit!r}",
                )
                continue
            observations[attr] = resolve_observation(
                attr, value, device_urn, dep, measurement_def, prop, health_flags.get(attr)
            )
        if not observations:
            return {}

        out: Dict[str, Any] = {
            "observations": observations,
            "deploymentId": dep.id,
            "sourceKind": "device",
        }
        time_source = _prop_value(entity, "timeSource")
        if time_source in ("device", "server"):
            out["timeSource"] = time_source
        if dep.mobility == "mobile":
            point = _reading_point(entity)
        elif dep.location is not None:
            point = {"type": "Point", "coordinates": [dep.location[0], dep.location[1]]}
        else:
            point = None
        if point is not None:
            out["location"] = point
        return out
    except Exception as e:
        logger.error(
            f"Observation resolution failed, writing event without observations: "
            f"{log_ctx}: {e}",
            exc_info=True,
        )
        return {}


_ENTITY_METADATA_KEYS = frozenset(
    {
        "id",
        "type",
        "@context",
        "location",
        # NGSI-LD system / metadata
        "name",
        "description",
        "dateCreated",
        "dateModified",
        "observedAt",
        "controlledProperty",
        "category",
        "source",
        "provider",
        "seeAlso",
        "ownedBy",
        "address",
        # Relationships (not measurements) — standard names + legacy ref<Type> aliases
        "hasDeviceProfile", "refDeviceProfile",
        "hasDevice", "refDevice",
        "hasAgriParcel", "refAgriParcel",
        "locatedAt", "refParcel",
        "observes", "refWeatherStation",
    }
)


def _extract_measurements(entity: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract measurement values from NGSI-LD entity attributes.

    Only extracts Property-type attributes with scalar values.
    Skips Relationships, GeoProperties, metadata keys, and non-scalar values.
    """
    measurements = {}

    for key, attr in entity.items():
        if key in _ENTITY_METADATA_KEYS:
            continue

        if isinstance(attr, dict):
            attr_type = attr.get("type")
            if attr_type == "Property":
                val = attr.get("value")
                if val is not None and not isinstance(val, (dict, list)):
                    measurements[key] = val
            # GeoProperty and Relationship: skip (not measurements)

    return measurements


def _extract_device_measurement(entity: Dict[str, Any]) -> tuple[Optional[str], Dict[str, Any]]:
    """Pull (device_id, {measurement_name: value}) out of a `DeviceMeasurement` entity.

    `DeviceMeasurement` inverts the shape every other entity type uses here: its own id
    ends in the CHANNEL name, not the device (see
    entity-manager/blueprints/measurements.py:build_measurements — the id scheme is
    `urn:ngsi-ld:DeviceMeasurement:{tenant}:{externalId}:{channel}`), so
    `entity_id.split(":")[-1]` — the extraction every other type uses — would yield the
    reading's name instead of the device. The device lives on the `refDevice`
    Relationship (any REF_DEVICE_KEYS form) instead; the reading's name is the channel
    (`name` in the SDM shape, `controlledProperty` in the legacy shape, see
    `device_measurement_shape`), not an attribute key, and its value is in `numValue` or
    `textValue` (never both). The channel is the storage key in both shapes.

    Returns (None, {}) when `refDevice` is missing and (device_id, {}) when the channel or a
    value is missing, so the caller can treat that as "nothing safe to persist" rather than
    writing a row with a null/guessed device_id or channel.
    """
    device_id = None
    target = _ref_device_urn(entity)
    if target is not None:
        # Same short-id convention as every other entity type here: last URN segment.
        device_id = target.split(":")[-1] if ":" in target else target

    measurements: Dict[str, Any] = {}
    channel = device_measurement_shape(entity).channel
    if channel:
        for value_key in ("numValue", "textValue"):
            value_attr = entity.get(value_key)
            if isinstance(value_attr, dict) and "value" in value_attr:
                measurements = {channel: value_attr["value"]}
                break

    return device_id, measurements


def _as_aware_utc(dt: datetime) -> datetime:
    """Naive timestamps are UTC by platform convention; the lookup rejects naive ones."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _extract_device_measurement_observed_at(entity: Dict[str, Any]) -> datetime:
    """`DeviceMeasurement` carries its instant in `dateObserved`, a plain Property value
    (see build_measurements), not in per-attribute `observedAt` metadata — the generic
    `_extract_observed_at` below would never find it there and would silently fall back
    to utcnow(). Same ISO-8601 parsing, dedicated lookup key.
    """
    at = _date_observed(entity)
    return at if at is not None else datetime.now(timezone.utc)


def _date_observed(entity: Dict[str, Any]) -> Optional[datetime]:
    """The parsed, timezone-aware `dateObserved` of a DeviceMeasurement, else None."""
    date_observed = entity.get("dateObserved")
    if isinstance(date_observed, dict):
        value = date_observed.get("value")
        if value:
            try:
                return _as_aware_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
            except (ValueError, TypeError):
                pass
    return None


def _extract_observed_at(entity: Dict[str, Any]) -> datetime:
    """Extract observedAt from any entity attribute, fallback to utcnow."""
    for attr in entity.values():
        if isinstance(attr, dict) and "observedAt" in attr:
            try:
                return _as_aware_utc(
                    datetime.fromisoformat(attr["observedAt"].replace("Z", "+00:00"))
                )
            except (ValueError, TypeError, AttributeError):
                pass
    return datetime.now(timezone.utc)


def _parse_notified_at(value: Any) -> Optional[datetime]:
    """The body's top-level `notifiedAt` as an aware datetime, None when absent or unparseable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return _as_aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


@router.post("/notify", status_code=204)
async def receive_notification(
    request: Request,
    ngsild_tenant: Optional[str] = Header(None, alias="NGSILD-Tenant"),
    fiware_service: Optional[str] = Header(None, alias="Fiware-Service"),
):
    """
    Endpoint for Orion-LD notifications.

    Stores every notified entity in the durable inbox before answering; the inbox
    consumer processes them. An inbox failure answers 503 so Orion counts it as a
    failed delivery. Never processes in memory.

    Supports both NGSI-LD (NGSILD-Tenant) and NGSIv2 (Fiware-Service) tenant headers.

    Answers 204 with no body: Orion-LD looks for a capitalised "Content-Length:"
    with a case-sensitive strstr and only skips that check on a 204. uvicorn emits
    the header lower-cased, so any other success status is counted as a failed
    notification and deactivates the subscription after 3 consecutive hits.
    """
    reject = _reject_unauthenticated_notify(request.headers)
    if reject:
        raise reject

    try:
        body = await request.json()
    except ValueError as e:
        # Malformed JSON body. Must surface as a 4xx: returning a dict here answered
        # 200 (FastAPI serialises the tuple), silently acknowledging a lost notification.
        logger.error(f"Malformed notification body: {e}")
        raise HTTPException(status_code=400, detail="malformed notification payload")
    if not isinstance(body, dict):
        logger.error("Malformed notification body: not a JSON object")
        raise HTTPException(status_code=400, detail="malformed notification payload")

    entities = body.get("data")
    if entities is None:
        entities = []
    if not isinstance(entities, list):
        logger.error("Malformed notification body: data is not a list")
        raise HTTPException(status_code=400, detail="malformed notification payload")

    # NGSI-LD uses NGSILD-Tenant header; NGSIv2 uses Fiware-Service
    tenant_id = ngsild_tenant or fiware_service

    logger.info(f"Notification received for tenant={tenant_id}, entities={len(entities)}")

    if not entities:
        return None
    if not tenant_id:
        # The inbox keys rows by tenant; without one nothing can be stored or processed.
        logger.error(f"Notification without tenant header rejected: entities={len(entities)}")
        raise HTTPException(status_code=400, detail="missing tenant header")
    if _inbox is None:
        logger.error(
            f"Notification not stored, inbox not wired: tenant_id={tenant_id} entities={len(entities)}"
        )
        raise HTTPException(status_code=503, detail="notification inbox unavailable")

    try:
        await _inbox.enqueue(tenant_id, entities, _parse_notified_at(body.get("notifiedAt")))
    except Exception as e:
        logger.error(
            f"Notification not stored: tenant_id={tenant_id} entities={len(entities)}: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="notification inbox unavailable")
    return None


async def process_geometry_notification_task(data: Dict[str, Any], tenant_id: Optional[str]):
    """Upsert the location of every notified entity into observation_geometry_cache.

    One bad entity never blocks the others; nothing is raised out of the task.
    """
    if _geometry_pool is None:
        logger.warning("geometry notification dropped, no pool wired: tenant_id=%s", tenant_id)
        return
    for entity in data.get("data", []) or []:
        try:
            await upsert_geometry(_geometry_pool, tenant_id, entity)
        except Exception as e:
            logger.error(
                "geometry upsert failed: tenant_id=%s entity_urn=%s error=%s",
                tenant_id, entity.get("id") if isinstance(entity, dict) else None, e,
                exc_info=True,
            )


@router.post("/notify/geometry", status_code=204)
async def receive_geometry_notification(
    request: Request,
    background_tasks: BackgroundTasks,
    ngsild_tenant: Optional[str] = Header(None, alias="NGSILD-Tenant"),
    fiware_service: Optional[str] = Header(None, alias="Fiware-Service"),
):
    """Orion-LD `location` notifications for the geometry cache (same auth and
    tenant headers as /notify; 204 for the reason documented there)."""
    reject = _reject_unauthenticated_notify(request.headers)
    if reject:
        raise reject
    try:
        body = await request.json()
    except ValueError as e:
        logger.error(f"Malformed geometry notification body: {e}")
        raise HTTPException(status_code=400, detail="malformed notification payload")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="malformed notification payload")
    tenant_id = ngsild_tenant or fiware_service
    background_tasks.add_task(process_geometry_notification_task, body, tenant_id)
    return None


@router.post("/v2/notify", status_code=204)
async def receive_notification_v2(
    request: Request,
    fiware_service: Optional[str] = Header(None, alias="Fiware-Service"),
):
    """
    Alternative endpoint for v2 format notifications.
    Maintains compatibility with older Orion subscriptions.
    """
    return await receive_notification(
        request,
        ngsild_tenant=None,
        fiware_service=fiware_service,
    )
