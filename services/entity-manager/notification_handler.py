"""
Notification handler for Orion-LD subscriptions (Flask blueprint).

Receives NGSI-LD entity notifications from Orion-LD subscriptions
and persists to the appropriate database tables:
  - AgriSensor -> sensors table
  - RiskAssessment -> risk_daily_states table (TimescaleDB)
"""

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Optional

import psycopg2
from flask import Blueprint, request, jsonify

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
        if "AgriSensor" in by_type:
            total += _handle_agrisensor(tenant_id, by_type["AgriSensor"])
        if "RiskAssessment" in by_type:
            total += _handle_risk_assessment(
                tenant_id, by_type["RiskAssessment"]
            )
        if "DeviceCommand" in by_type:
            total += _handle_device_command(
                tenant_id, by_type["DeviceCommand"]
            )

        logger.info(
            "Persisted %d entities for tenant=%s", total, tenant_id
        )
        return jsonify({"status": "ok", "persisted": total})

    except Exception as e:
        logger.error(
            "Error processing notification: %s", e, exc_info=True
        )
        return jsonify({"status": "error", "detail": str(e)}), 500


def _handle_agrisensor(tenant_id: str, entities: list) -> int:
    """Persist AgriSensor entities to sensors table."""
    persisted = 0
    conn = _get_conn()
    try:
        _set_tenant_context(conn, tenant_id)
        with conn.cursor() as cur:
            for entity in entities:
                external_id = _extract_prop(entity, "externalId")
                name = _extract_prop(entity, "name")
                profile_code = _extract_prop(entity, "profileCode")
                location = entity.get("location", {})
                is_under_canopy = _extract_prop(entity, "isUnderCanopy")
                metadata_val = _extract_prop(entity, "metadata")
                altitude = _extract_prop(entity, "altitudeMeters")
                parcel_id = _extract_prop(entity, "parcelId")
                installed_at = _extract_prop(entity, "installedAt")
                status = _extract_prop(entity, "status") or "active"

                if not external_id or not name:
                    continue

                # Resolve profile_id from profileCode
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


def _handle_risk_assessment(tenant_id: str, entities: list) -> int:
    """Persist RiskAssessment entities to risk_daily_states table."""
    persisted = 0
    conn = _get_conn()
    try:
        _set_tenant_context(conn, tenant_id)
        with conn.cursor() as cur:
            for entity in entities:
                risk_code = _extract_prop(entity, "riskCode")
                probability_score = _extract_prop(entity, "probabilityScore")
                target_entity_id = _extract_prop(entity, "targetEntityId")
                target_entity_type = _extract_prop(
                    entity, "targetEntityType"
                )
                evaluation_data = _extract_prop(entity, "evaluationData")
                evaluated_by = _extract_prop(entity, "evaluatedBy")
                evaluation_version = _extract_prop(
                    entity, "evaluationVersion"
                )
                severity = _extract_prop(entity, "severity")
                timestamp_val = _extract_prop(entity, "timestamp")

                if not risk_code or probability_score is None:
                    continue

                ts = datetime.utcnow()
                if timestamp_val:
                    try:
                        ts = datetime.fromisoformat(
                            str(timestamp_val).replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        pass

                cur.execute(
                    """
                    INSERT INTO risk_daily_states (
                        tenant_id, entity_id, entity_type, risk_code,
                        probability_score, severity,
                        evaluation_data, evaluation_timestamp,
                        timestamp, evaluated_by, evaluation_version
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s,
                        %s, %s,
                        %s, %s, %s
                    )
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        tenant_id,
                        target_entity_id or entity.get("id", ""),
                        target_entity_type or entity.get("type", ""),
                        risk_code,
                        float(probability_score),
                        severity
                        or _compute_severity(float(probability_score)),
                        json.dumps(evaluation_data or {}),
                        ts,
                        ts,
                        evaluated_by or "risk-worker",
                        evaluation_version or "1.0.0",
                    ),
                )
                persisted += 1
        conn.commit()
        return persisted
    except Exception as e:
        logger.error(
            "Error in _handle_risk_assessment: %s", e, exc_info=True
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
