"""
Notification handler for AgriSensor subscription (Flask blueprint).

Receives NGSI-LD entity notifications from Orion-LD subscription
and persists to sensors table.
"""

import json
import logging
import os
from typing import Any

import psycopg2
from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

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


@notify_bp.route("/notify", methods=["POST"])
def handle_notification():
    """Receive Orion-LD subscription notification for AgriSensor entities.

    Persists to sensors table. Tenant extracted from headers.
    """
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

                    # Resolve parcel_id UUID from parcelId string
                    parcel_uuid = None
                    if parcel_id:
                        parcel_ref = parcel_id
                        if parcel_ref.startswith("urn:"):
                            parcel_ref = parcel_ref.rsplit(":", 1)[-1]
                        cur.execute(
                            "SELECT id FROM cadastral_parcels "
                            "WHERE tenant_id = %s "
                            "AND external_id = %s LIMIT 1",
                            (tenant_id, parcel_ref),
                        )
                        parcel_row = cur.fetchone()
                        if parcel_row:
                            parcel_uuid = parcel_row[0]

                    # Extract coordinates
                    lon = None
                    lat = None
                    if isinstance(location, dict) and location.get("value"):
                        loc_val = location["value"]
                        if isinstance(loc_val, dict) and "coordinates" in loc_val:
                            coords = loc_val["coordinates"]
                            if len(coords) >= 2:
                                lon, lat = coords[0], coords[1]

                    # UPSERT into sensors table
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
        finally:
            conn.close()

        logger.info("Persisted %d sensors for tenant=%s", persisted, tenant_id)
        return jsonify({"status": "ok", "persisted": persisted})

    except Exception as e:
        logger.error("Error processing sensor notification: %s", e, exc_info=True)
        return jsonify({"status": "error", "detail": str(e)}), 500
