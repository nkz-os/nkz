"""
Notification handler for Orion-LD subscriptions.

Receives NGSI-LD entity updates and persists to TimescaleDB
after applying Processing Profiles (throttle, filter, delta).
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, BackgroundTasks, Header, HTTPException

from .config import Settings
from .event_sink import EventSink, TelemetryEvent
from .health_checker import HealthChecker, _severity
from .profiles import ProfileService
from .calibration import CalibrationService
from .dedup import NotificationDedup

logger = logging.getLogger(__name__)

router = APIRouter()

# Global instances (set via init_handler)
_settings: Optional[Settings] = None
_profile_service: Optional[ProfileService] = None
_event_sink: Optional[EventSink] = None
_health_checker: Optional[HealthChecker] = None
_calibration_service: Optional[CalibrationService] = None
_dedup: Optional[NotificationDedup] = None


def init_handler(
    settings: Settings,
    profile_service: ProfileService,
    event_sink: EventSink,
    health_checker: Optional[HealthChecker] = None,
    calibration_service: Optional[CalibrationService] = None,
    dedup: Optional[NotificationDedup] = None,
) -> None:
    """Wire dependencies from app lifespan."""
    global _settings, _profile_service, _event_sink, _health_checker, _calibration_service, _dedup
    _settings = settings
    _profile_service = profile_service
    _event_sink = event_sink
    _health_checker = health_checker
    _calibration_service = calibration_service
    _dedup = dedup


async def process_notification_task(
    data: Dict[str, Any],
    tenant_id: Optional[str] = None,
):
    """
    Background task to process Orion-LD notification.

    Flow:
    1. Extract entity info (id, type, attributes)
    2. Get processing profile for device type
    3. Apply throttle/delta checks
    4. Filter attributes
    5. Persist to TimescaleDB via EventSink

    NOTE: Does NOT write back to Orion (data comes from Orion subscription)
    """
    try:
        entities = data.get("data", [])
        if not entities:
            logger.debug("Empty notification received")
            return

        # Collect events for batch insert
        events: List[TelemetryEvent] = []

        for entity in entities:
            event = await _process_entity(entity, tenant_id)
            if event:
                events.append(event)

        # Batch persist all events from this notification
        if events and _event_sink:
            if len(events) == 1:
                await _event_sink.write(events[0])
            else:
                await _event_sink.write_batch(events)

            logger.info(f"Persisted {len(events)} events for tenant={tenant_id}")

    except Exception as e:
        logger.error(f"Error processing notification: {e}", exc_info=True)


async def _process_entity(
    entity: Dict[str, Any],
    tenant_id: Optional[str],
) -> Optional[TelemetryEvent]:
    """Process a single NGSI-LD entity. Returns event or None."""
    entity_id = entity.get("id", "")
    entity_type = entity.get("type", "")

    if not entity_id or not entity_type:
        logger.warning("Entity missing id or type, skipping")
        return None

    if entity_type == "DeviceMeasurement":
        # Inverted shape vs every other entity type (see module docstring on the helper):
        # the device is not the last segment of this entity's own id (that is the
        # controlledProperty name), and the reading's name/value are not plain attribute
        # keys either. A missing refDevice or reading means there is nothing safe to
        # persist, not a device_id of "" — bail out here rather than falling through.
        device_id, measurements = _extract_device_measurement(entity)
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
        logger.error("Handler not initialized")
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
    if _health_checker and tenant_id:
        health_config = await _health_checker.get_health_config(
            entity_id, tenant_id
        )
        if health_config:
            worst_flag = "valid"
            for var, val in measurements.items():
                flag = _health_checker.evaluate_measurement(var, val, health_config)
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
    # subscription replay). Per-event check so a batch with some new +
    # some duplicate events keeps only the new ones. Fail-open: if Redis is
    # unavailable, _dedup.is_duplicate() returns False and the write proceeds.
    if _dedup is not None:
        is_dup = await _dedup.is_duplicate(
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

    # Update last values cache for future delta checks
    _profile_service.update_last_values(device_id, measurements)

    return TelemetryEvent(
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
        },
        quality_flag=quality_flag,
    )


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
    ends in the controlledProperty name, not the device (see
    entity-manager/blueprints/measurements.py:build_measurements — the id scheme is
    `urn:ngsi-ld:DeviceMeasurement:{tenant}:{externalId}:{controlledProperty}`), so
    `entity_id.split(":")[-1]` — the extraction every other type uses — would yield the
    reading's name instead of the device. The device lives on the `refDevice`
    Relationship instead; the reading's name is the VALUE of `controlledProperty`, not an
    attribute key, and its value is in `numValue` or `textValue` (never both).

    Returns (None, {}) when `refDevice` or a value are missing so the caller can treat
    that as "nothing safe to persist" rather than writing a row with a null/guessed
    device_id.
    """
    device_id = None
    ref_device = entity.get("refDevice")
    if isinstance(ref_device, dict):
        target = ref_device.get("object")
        if isinstance(target, str) and target:
            # Same short-id convention as every other entity type here: last URN segment.
            device_id = target.split(":")[-1] if ":" in target else target

    measurements: Dict[str, Any] = {}
    controlled_property = entity.get("controlledProperty")
    if isinstance(controlled_property, dict):
        name = controlled_property.get("value")
        if name:
            for value_key in ("numValue", "textValue"):
                value_attr = entity.get(value_key)
                if isinstance(value_attr, dict) and "value" in value_attr:
                    measurements = {name: value_attr["value"]}
                    break

    return device_id, measurements


def _extract_device_measurement_observed_at(entity: Dict[str, Any]) -> datetime:
    """`DeviceMeasurement` carries its instant in `dateObserved`, a plain Property value
    (see build_measurements), not in per-attribute `observedAt` metadata — the generic
    `_extract_observed_at` below would never find it there and would silently fall back
    to utcnow(). Same ISO-8601 parsing, dedicated lookup key.
    """
    date_observed = entity.get("dateObserved")
    if isinstance(date_observed, dict):
        value = date_observed.get("value")
        if value:
            try:
                return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
    return datetime.utcnow()


def _extract_observed_at(entity: Dict[str, Any]) -> datetime:
    """Extract observedAt from any entity attribute, fallback to utcnow."""
    for attr in entity.values():
        if isinstance(attr, dict) and "observedAt" in attr:
            try:
                return datetime.fromisoformat(attr["observedAt"].replace("Z", "+00:00"))
            except ValueError:
                pass
    return datetime.utcnow()


@router.post("/notify", status_code=204)
async def receive_notification(
    request: Request,
    background_tasks: BackgroundTasks,
    ngsild_tenant: Optional[str] = Header(None, alias="NGSILD-Tenant"),
    fiware_service: Optional[str] = Header(None, alias="Fiware-Service"),
):
    """
    Endpoint for Orion-LD notifications.

    Accepts NGSI-LD subscription notifications and processes them
    in the background for fast acknowledgment.

    Supports both NGSI-LD (NGSILD-Tenant) and NGSIv2 (Fiware-Service) tenant headers.

    Answers 204 with no body: Orion-LD looks for a capitalised "Content-Length:"
    with a case-sensitive strstr and only skips that check on a 204. uvicorn emits
    the header lower-cased, so any other success status is counted as a failed
    notification and deactivates the subscription after 3 consecutive hits.
    """
    try:
        body = await request.json()

        # NGSI-LD uses NGSILD-Tenant header; NGSIv2 uses Fiware-Service
        tenant_id = ngsild_tenant or fiware_service

        logger.info(
            f"Notification received for tenant={tenant_id}, "
            f"entities={len(body.get('data', []))}"
        )

        # Fast response - process in background
        background_tasks.add_task(process_notification_task, body, tenant_id)

        return None

    except ValueError as e:
        # Malformed JSON body. Must surface as a 4xx: returning a dict here answered
        # 200 (FastAPI serialises the tuple), silently acknowledging a lost notification.
        logger.error(f"Malformed notification body: {e}")
        raise HTTPException(status_code=400, detail="malformed notification payload")
    except Exception as e:
        logger.error(f"Failed to accept notification: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="notification processing error")


@router.post("/v2/notify", status_code=204)
async def receive_notification_v2(
    request: Request,
    background_tasks: BackgroundTasks,
    fiware_service: Optional[str] = Header(None, alias="Fiware-Service"),
):
    """
    Alternative endpoint for v2 format notifications.
    Maintains compatibility with older Orion subscriptions.
    """
    return await receive_notification(request, background_tasks, fiware_service)
