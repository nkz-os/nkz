"""
Notification handler for Orion-LD subscriptions (Flask blueprint).

Receives NGSI-LD entity notifications from Orion-LD subscriptions
and persists to the appropriate database tables:
  - Device (and legacy AgriSensor) -> sensors table
  - DeviceCommand -> commands table
"""

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Optional

import psycopg2
from flask import Blueprint, request, jsonify

from common.api_errors import internal_error
from device_profile import entity_profile_code, first_time
from device_urn import external_id_of, is_device_type
from nkz_platform_sdk.notify_auth import accept_master_from_env, verify_notification

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _parcel_uuid_from_urn(parcel_id: Optional[str]) -> Optional[str]:
    """Derive the parcel UUID from its Orion URN (uniform writes).

    The parcel URN is ``urn:ngsi-ld:AgriParcel:<uuid4>`` and that uuid IS the
    parcel identity — used directly as ``sensors.parcel_id`` (the cadastral_parcels
    mirror is retired). Returns None for non-UUID identifiers.
    """
    if not parcel_id or not isinstance(parcel_id, str):
        return None
    candidate = (parcel_id.rsplit(":", 1)[-1] if parcel_id.startswith("urn:") else parcel_id).strip()
    return candidate if _UUID_RE.match(candidate) else None

notify_bp = Blueprint("sensor_notifications", __name__)


def _notify_unauthorized() -> bool:
    """Flag-gated auth for notification receivers (two-phase rollout).

    Accepts the token bound to this tenant and host (nkz_platform_sdk.notify_auth)
    and, until NKZ_NOTIFY_ACCEPT_MASTER is false, the master secret itself.
    """
    require = os.getenv("NOTIFY_REQUIRE_INTERNAL_SECRET", "").lower() in (
        "1", "true", "yes", "on"
    )
    if not require:
        return False
    return not verify_notification(
        request.headers,
        secret=os.getenv("INTERNAL_SERVICE_SECRET", ""),
        accept_master=accept_master_from_env(),
    )

POSTGRES_URL = os.getenv("POSTGRES_URL", "")


def _get_conn():
    if not POSTGRES_URL:
        raise RuntimeError("POSTGRES_URL not set")
    return psycopg2.connect(POSTGRES_URL)


def _set_tenant_context(conn, tenant_id: str) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.current_tenant', %s, true)",
                (tenant_id,),
            )
    except Exception:
        pass


def _extract_prop(entity: dict, key: str) -> Any:
    prop = entity.get(key, {})
    if isinstance(prop, dict):
        return prop.get("value")
    return prop


def _first_urn(value: Any, prefix: str = "") -> Optional[str]:
    """First usable URN (starting with `prefix`) in a string | {object: ...} | list of those."""
    if isinstance(value, str):
        if value and value != "urn:ngsi-ld:null" and value.startswith(prefix):
            return value
        return None
    if isinstance(value, dict):
        return _first_urn(value.get("object"), prefix)
    if isinstance(value, list):
        for item in value:
            urn = _first_urn(item, prefix)
            if urn:
                return urn
    return None


def _parcel_ref(entity: dict) -> Optional[str]:
    """Primary parcel URN of a device: first parcel URN of `controlledAsset`, else legacy `parcelId`.

    The list may start with a non-parcel owner (e.g. a solar park); those are skipped.
    """
    return _first_urn(entity.get("controlledAsset"), "urn:ngsi-ld:AgriParcel:") or _extract_prop(entity, "parcelId") or None


_DEVICE_IRI = "https://smartdatamodels.org/dataModel.Device/"


def _first_present(entity: dict, *keys: str) -> Any:
    """The value of the first of `keys` the entity carries, even when that value is empty."""
    return next((_extract_prop(entity, key) for key in keys if key in entity), None)


def _date_text(tenant_id: str, entity: dict, *keys: str) -> Optional[str]:
    """The text of the first of `keys` the entity carries, a DateTime literal unwrapped.

    Anything else cannot be bound to the timestamp column and would roll the whole batch back, so it
    counts as undeclared, with one log per Device.
    """
    key = next((k for k in keys if k in entity), None)
    value = _extract_prop(entity, key) if key else None
    if value is None:
        return None
    if isinstance(value, dict) and value.get("@type") == "DateTime":
        value = value.get("@value")
    if isinstance(value, str):
        return value
    if first_time("date_not_text", tenant_id, entity.get("id")):
        logger.warning(
            "installation date ignored, its value is not text: tenant=%s entity_id=%s "
            "attribute=%s type=%s", tenant_id, entity.get("id"), key, type(value).__name__,
        )
    return None


def _registered_device(tenant_id: str, entity: dict) -> Optional[dict]:
    """Identity, profile, installation and state of a registered Device.

    None for any other Device-typed twin: its id is not the one registration writes, or it is
    on no profile.
    """
    urn = entity.get("id")
    external_id = (external_id_of(tenant_id, urn)
                   if isinstance(urn, str) and is_device_type(urn) else None)
    profile_code = entity_profile_code(tenant_id, entity) if external_id else None
    if not external_id:
        logger.debug("device notification ignored: tenant=%s entity_id=%s reason=%s",
                     tenant_id, urn, "not the id of a registered device")
        return None
    if not profile_code:
        # Somebody registered this one: without a profile it silently stops being updated here.
        if first_time("no_profile", tenant_id, urn):
            logger.info("device notification ignored: tenant=%s entity_id=%s reason=%s",
                        tenant_id, urn, "no profile")
        return None
    return {
        "external_id": external_id,
        "profile_code": profile_code,
        # ASSUMPTION: a `dateInstalled` that is not text gives way to `installedAt`, as an empty one
        # does; NULL when neither is text.
        "installed_at": (_date_text(tenant_id, entity, "dateInstalled", _DEVICE_IRI + "dateInstalled")
                         or _date_text(tenant_id, entity, "installedAt")),
        # A profile may declare `status` as a reading (an inverter's code), so a Device that has
        # `deviceState` never takes its state from `status`, not even when `deviceState` is empty.
        "status": _first_present(entity, "deviceState", _DEVICE_IRI + "deviceState", "status"),
    }


def _legacy_sensor(entity: dict) -> dict:
    """The same of a legacy AgriSensor."""
    return {
        "external_id": _extract_prop(entity, "externalId"),
        "profile_code": _extract_prop(entity, "profileCode"),
        "installed_at": _extract_prop(entity, "installedAt"),
        "status": _extract_prop(entity, "status"),
    }


def _compute_severity(probability_score: float) -> str:
    if probability_score >= 95:
        return "critical"
    if probability_score >= 80:
        return "high"
    if probability_score >= 60:
        return "medium"
    return "low"


@notify_bp.route("/notify", methods=["POST"])
def handle_notification():
    """Receive Orion-LD subscription notification, dispatch by entity type."""
    if _notify_unauthorized():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        tenant_id = (
            request.headers.get("NGSILD-Tenant")
            or request.headers.get("Fiware-Service")
            or "unknown"
        )
        body = request.get_json(force=True, silent=True)
        if not body:
            return jsonify({"status": "ok", "persisted": 0})

        entities = body.get("data", [])
        if not entities:
            return jsonify({"status": "ok", "persisted": 0})

        # Group by entity type for batch processing
        by_type: dict[str, list] = {}
        for e in entities:
            etype = e.get("type", "unknown")
            by_type.setdefault(etype, []).append(e)

        total = 0
        for device_type in ("Device", "AgriSensor"):
            if device_type in by_type:
                total += _handle_agrisensor(tenant_id, by_type[device_type])
        if "DeviceCommand" in by_type:
            total += _handle_device_command(
                tenant_id, by_type["DeviceCommand"]
            )

        logger.info(
            "Persisted %d entities for tenant=%s", total, tenant_id
        )
        return jsonify({"status": "ok", "persisted": total})

    except Exception as e:
        return internal_error(e, 'notification_handler')


def _handle_agrisensor(tenant_id: str, entities: list) -> int:
    """Persist Device (or legacy AgriSensor) entities to the sensors table."""
    persisted = 0
    conn = _get_conn()
    try:
        _set_tenant_context(conn, tenant_id)
        with conn.cursor() as cur:
            for entity in entities:
                if entity.get("type") == "Device":
                    sensor = _registered_device(tenant_id, entity)
                    if sensor is None:
                        continue
                else:
                    sensor = _legacy_sensor(entity)
                external_id = sensor["external_id"]
                name = _extract_prop(entity, "name")
                profile_code = sensor["profile_code"]
                location = entity.get("location", {})
                is_under_canopy = _extract_prop(entity, "isUnderCanopy")
                metadata_val = _extract_prop(entity, "metadata")
                altitude = _extract_prop(entity, "altitudeMeters")
                parcel_id = _parcel_ref(entity)
                installed_at = sensor["installed_at"]
                status = sensor["status"] or "active"

                if not external_id or not name:
                    continue

                # Resolve profile_id from the profile code
                profile_id = None
                if profile_code:
                    cur.execute(
                        "SELECT id FROM sensor_profiles "
                        "WHERE code = %s "
                        "AND (tenant_id IS NULL OR tenant_id = %s) "
                        "ORDER BY tenant_id NULLS LAST LIMIT 1",
                        (profile_code, tenant_id),
                    )
                    profile_row = cur.fetchone()
                    if profile_row:
                        profile_id = profile_row[0]

                # Derive parcel_id UUID from the parcel URN (uniform writes:
                # urn:ngsi-ld:AgriParcel:<uuid4>). The cadastral_parcels mirror is retired.
                parcel_uuid = _parcel_uuid_from_urn(parcel_id)

                # Extract coordinates
                lon = None
                lat = None
                if isinstance(location, dict) and location.get("value"):
                    loc_val = location["value"]
                    if (
                        isinstance(loc_val, dict)
                        and "coordinates" in loc_val
                    ):
                        coords = loc_val["coordinates"]
                        if len(coords) >= 2:
                            lon, lat = coords[0], coords[1]

                cur.execute(
                    """
                    INSERT INTO sensors (
                        tenant_id, external_id, profile_id, name,
                        installation_location, altitude_meters,
                        is_under_canopy, parcel_id, metadata,
                        status, installed_at
                    ) VALUES (
                        %s, %s, %s, %s,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                        %s, %s, %s, %s::jsonb, %s, %s::timestamptz
                    )
                    ON CONFLICT (tenant_id, external_id)
                    DO UPDATE SET
                        name = EXCLUDED.name,
                        profile_id = EXCLUDED.profile_id,
                        installation_location = EXCLUDED.installation_location,
                        altitude_meters = EXCLUDED.altitude_meters,
                        is_under_canopy = EXCLUDED.is_under_canopy,
                        parcel_id = EXCLUDED.parcel_id,
                        metadata = EXCLUDED.metadata,
                        status = EXCLUDED.status,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        tenant_id,
                        external_id,
                        profile_id,
                        name,
                        lon,
                        lat,
                        altitude,
                        is_under_canopy if is_under_canopy else False,
                        parcel_uuid,
                        json.dumps(metadata_val or {}),
                        status,
                        installed_at,
                    ),
                )
                persisted += 1
        conn.commit()
        return persisted
    except Exception as e:
        logger.error(
            "Error in _handle_agrisensor: %s", e, exc_info=True
        )
        conn.rollback()
        return persisted
    finally:
        conn.close()


def _handle_device_command(tenant_id: str, entities: list) -> int:
    """Persist DeviceCommand entities to commands table."""
    persisted = 0
    conn = _get_conn()
    try:
        _set_tenant_context(conn, tenant_id)
        with conn.cursor() as cur:
            for entity in entities:
                command_id = _extract_prop(entity, "commandId")
                command_type = _extract_prop(entity, "commandType")
                device_id = _extract_prop(entity, "targetDeviceId")
                payload_val = _extract_prop(entity, "payload")
                status_val = _extract_prop(entity, "status") or "pending"
                sent_at = _extract_prop(entity, "sentAt")
                executed_at = _extract_prop(entity, "executedAt")
                response_val = _extract_prop(entity, "response")

                if not command_id or not device_id:
                    continue

                cur.execute(
                    """
                    INSERT INTO commands (
                        id, tenant_id, device_id, command_type,
                        payload, status, sent_at, executed_at, response
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s::timestamptz, %s::timestamptz, %s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        status = EXCLUDED.status,
                        executed_at = COALESCE(
                            EXCLUDED.executed_at, commands.executed_at
                        ),
                        response = COALESCE(
                            EXCLUDED.response, commands.response
                        )
                    """,
                    (
                        command_id,
                        tenant_id,
                        device_id,
                        command_type or "custom",
                        json.dumps(payload_val or {}),
                        status_val,
                        sent_at,
                        executed_at,
                        json.dumps(response_val) if response_val else None,
                    ),
                )
                persisted += 1
        conn.commit()
        return persisted
    except Exception as e:
        logger.error(
            "Error in _handle_device_command: %s", e, exc_info=True
        )
        conn.rollback()
        return persisted
    finally:
        conn.close()
