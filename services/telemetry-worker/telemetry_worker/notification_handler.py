"""
Notification handler for Orion-LD subscriptions.

Receives NGSI-LD entity updates and persists to TimescaleDB
after applying Processing Profiles (throttle, filter, delta).
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, BackgroundTasks, Header

from .config import Settings
from .event_sink import EventSink, TelemetryEvent
from .health_checker import HealthChecker, _severity
from .profiles import ProfileService
from .calibration import CalibrationService

logger = logging.getLogger(__name__)

router = APIRouter()

# Global instances (set via init_handler)
_settings: Optional[Settings] = None
_profile_service: Optional[ProfileService] = None
_event_sink: Optional[EventSink] = None
_health_checker: Optional[HealthChecker] = None
_calibration_service: Optional[CalibrationService] = None


def init_handler(
    settings: Settings,
    profile_service: ProfileService,
    event_sink: EventSink,
    health_checker: Optional[HealthChecker] = None,
    calibration_service: Optional[CalibrationService] = None,
) -> None:
    """Wire dependencies from app lifespan."""
    global _settings, _profile_service, _event_sink, _health_checker, _calibration_service
    _settings = settings
    _profile_service = profile_service
    _event_sink = event_sink
    _health_checker = health_checker
    _calibration_service = calibration_service


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

    # Extract device_id from entity_id (format: urn:ngsi-ld:Type:tenant:device)
    device_id = entity_id.split(":")[-1] if ":" in entity_id else entity_id

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

    # Extract measurements from entity attributes
    measurements = _extract_measurements(entity)

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

    # Extract observedAt timestamp
    observed_at = _extract_observed_at(entity)

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


def _extract_observed_at(entity: Dict[str, Any]) -> datetime:
    """Extract observedAt from any entity attribute, fallback to utcnow."""
    for attr in entity.values():
        if isinstance(attr, dict) and "observedAt" in attr:
            try:
                return datetime.fromisoformat(attr["observedAt"].replace("Z", "+00:00"))
            except ValueError:
                pass
    return datetime.utcnow()


@router.post("/notify")
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

        return {"status": "received"}

    except Exception as e:
        logger.error(f"Invalid notification received: {e}")
        return {"error": "invalid payload"}, 400


@router.post("/v2/notify")
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
