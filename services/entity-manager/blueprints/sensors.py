#!/usr/bin/env python3
"""
Sensors Blueprint - Extracted from entity_management_api.py
"""
import os
import sys
import json
import uuid
import logging
import secrets
from datetime import datetime
from functools import wraps
from typing import Optional

from flask import Blueprint, request, jsonify, g
from psycopg2.extras import RealDictCursor
import psycopg2
import requests
import paho.mqtt.client as mqtt

from common.auth_middleware import require_auth
from common.api_errors import internal_error
from common.ngsi_headers import inject_fiware_headers
from db_helper import get_db_connection_with_tenant, get_db_connection_simple, return_db_connection

# Import shared config
from helpers import ORION_URL, CONTEXT_URL
import dataloggers

logger = logging.getLogger(__name__)

# MQTT Configuration for device commands
MQTT_HOST = os.getenv('MQTT_HOST', 'mosquitto-service')
MQTT_PORT = int(os.getenv('MQTT_PORT', '1883'))
MQTT_USERNAME = os.getenv('MQTT_USERNAME', '')
MQTT_PASSWORD = os.getenv('MQTT_PASSWORD', '')

sensors_bp = Blueprint('sensors', __name__)


# =============================================================================
# Helper: normalize device ID
# =============================================================================

def _normalize_device_id(device_id: str) -> str:
    """Extract short device ID from NGSI-LD URN if needed.

    'urn:ngsi-ld:AgriSensor:abc123' -> 'abc123'
    'abc123' -> 'abc123'
    """
    if device_id and ':' in device_id:
        return device_id.rsplit(':', 1)[-1]
    return device_id


def _extract_prop_value(entity: dict, key: str):
    """Extract Property value from NGSI-LD entity dict."""
    prop = entity.get(key, {})
    if isinstance(prop, dict) and 'value' in prop:
        return prop['value']
    return prop


def _patch_command_status(
    tenant_id: str,
    entity_id: str,
    status: str,
    response_data: dict | None = None,
) -> bool:
    """PATCH a DeviceCommand entity status in Orion-LD."""
    try:
        patch_body = {
            'status': {
                'type': 'Property',
                'value': status,
            },
        }
        if status == 'sent':
            patch_body['executedAt'] = {
                'type': 'Property',
                'value': datetime.utcnow().isoformat(),
            }
        if response_data:
            patch_body['response'] = {
                'type': 'Property',
                'value': response_data,
            }

        headers = inject_fiware_headers(
            {'Content-Type': 'application/json'}, tenant=tenant_id, has_context_in_body=False
        )
        resp = requests.patch(
            f"{ORION_URL}/ngsi-ld/v1/entities/{entity_id}/attrs",
            json=patch_body,
            headers=headers,
            timeout=10,
        )
        return resp.status_code in (200, 204)
    except Exception as e:
        logger.error(
            "Failed to patch DeviceCommand %s: %s", entity_id, e
        )
        return False


_HEALTH_ATTRIBUTES = frozenset({
    "healthConfig", "reliabilityStatus", "isSilenced", "calibrationConfig",
})


def _extract_health_property(entity: dict, key: str) -> Optional[dict]:
    """Extract a health-related Property (which may contain nested objects)."""
    prop = entity.get(key)
    if isinstance(prop, dict) and prop.get("type") == "Property":
        return prop
    return None


# =============================================================================
# Sensor Registration
# =============================================================================


def _guard(fn):
    """Answer dataloggers.ApiError with its status and body; anything else is a 500."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except dataloggers.ApiError as e:
            return jsonify(e.body()), e.status
        except Exception as e:
            logger.error("Error in %s: %s", fn.__name__, type(e).__name__)
            return internal_error(e, fn.__name__, user_message='Request failed')
    return wrapper


def _tenant() -> str:
    tenant = g.tenant
    if not tenant:
        raise dataloggers.ApiError(401, 'Tenant not found in token')
    return tenant


def _body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise dataloggers.ApiError(400, 'No data provided')
    return data


def _parcel_links(value) -> list:
    """Ordered, de-duplicated parcel URNs from a string, {'object': ...} or a list of those."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    urns = []
    for item in items:
        if isinstance(item, dict):
            urns.extend(_parcel_links(item.get('object')))
        elif isinstance(item, str) and item:
            urns.append(item)
    return list(dict.fromkeys(urns))


def _coordinate(value, name: str) -> float:
    if value is None or isinstance(value, bool):
        raise dataloggers.ApiError(400, 'Location (lat, lon) is required')
    try:
        return float(value)
    except (TypeError, ValueError):
        raise dataloggers.ApiError(400, f'Invalid {name}')


def _ensure_bridge_subscription(tenant: str) -> None:
    """Device -> DeviceMeasurement subscription for this tenant.

    A failure is logged and does not fail the registration: the subscription
    is also ensured for every tenant at startup.
    """
    try:
        from blueprints.notifications import ensure_device_measurement_subscriptions
        ensure_device_measurement_subscriptions(tenants=[tenant])
    except Exception as exc:
        logger.error("bridge subscription not ensured for tenant %s: %s", tenant, type(exc).__name__)


@sensors_bp.route('/api/sensors/register', methods=['POST'])
@require_auth
@_guard
def register_sensor():
    """
    Register a sensor on a datalogger.

    Steps, each undone in reverse order if a later one fails (502 with the step):
    Orion entity, IoT Agent device, datalogger link row, broker ACL, then the
    Device -> DeviceMeasurement subscription (logged only if it fails).

    Request body:
    {
        "external_id": "BP_Vaso_PAR_1",
        "name": "BP Vaso PAR 1",
        "profile": "par_photon_flux",
        "location": {"lat": 42.57, "lon": -2.02},
        "controlledAsset": ["urn:ngsi-ld:AgriParcel:<id>", ...],   # owner first, >= 1 parcel
        "datalogger_id": "<uuid>"
    }
    """
    tenant_id = _tenant()
    data = _body()

    external_id = data.get('external_id')
    name = data.get('name')
    profile_code = data.get('profile')
    if not external_id or not name or not profile_code or not data.get('datalogger_id'):
        raise dataloggers.ApiError(
            400, 'Missing required fields: external_id, name, profile, datalogger_id')
    if not isinstance(external_id, str) or not dataloggers.EXTERNAL_ID_RE.match(external_id):
        raise dataloggers.ApiError(400, 'external_id must match [A-Za-z0-9_.-]{1,64}')

    location = data.get('location') or {}
    lat = _coordinate(location.get('lat'), 'lat')
    lon = _coordinate(location.get('lon'), 'lon')
    parcels = _parcel_links(data.get('controlledAsset'))
    if not parcels:
        raise dataloggers.ApiError(400, 'Device requires at least one parcel in controlledAsset.')

    # Reads and validations first: nothing is written until all of them pass.
    datalogger = dataloggers.require_usable_datalogger(tenant_id, data['datalogger_id'])

    with get_db_connection_with_tenant(tenant_id) as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, sdm_entity_type, sdm_device_category, mapping
            FROM sensor_profiles
            WHERE code = %s AND (tenant_id IS NULL OR tenant_id = %s)
            ORDER BY tenant_id NULLS LAST
            LIMIT 1
        """, (profile_code, tenant_id))
        profile_row = cur.fetchone()
        cur.close()
    if not profile_row:
        raise dataloggers.ApiError(404, f'Profile "{profile_code}" not found')

    sdm_entity_type = profile_row.get('sdm_entity_type') or 'Device'
    # tractor / implement / robot / harvester for machines, NULL for devices: the
    # one attribute that tells machines of the same type apart.
    device_category = profile_row.get('sdm_device_category')
    profile_mapping = profile_row.get('mapping')
    measurements = (profile_mapping.get('measurements') or []) if isinstance(profile_mapping, dict) else []
    try:
        dataloggers.iot_agent.build_iota_attributes(measurements)
    except (KeyError, TypeError, AttributeError):
        raise dataloggers.ApiError(400, 'Profile measurements need type and sdmAttribute')

    orion_entity_id = f"urn:ngsi-ld:{sdm_entity_type}:{tenant_id}:{external_id}"
    if dataloggers.device_link(tenant_id, orion_entity_id):
        raise dataloggers.ApiError(409, 'Device is already on a datalogger')

    orion_check = requests.get(
        f"{ORION_URL}/ngsi-ld/v1/entities?type={sdm_entity_type}"
        f'&q=externalId=="{external_id}"',
        headers=inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False),
        timeout=10)
    if orion_check.status_code == 200 and orion_check.json():
        existing = orion_check.json()[0]
        return jsonify({
            'error': f'Sensor with external_id "{external_id}" already exists',
            'sensor': {'id': existing.get('id'),
                       'external_id': _extract_prop_value(existing, 'externalId'),
                       'name': _extract_prop_value(existing, 'name')},
        }), 409

    metadata = data.get('metadata', {})
    if data.get('station_id'):
        metadata['group'] = data['station_id']
        metadata['station_id'] = data['station_id']

    orion_entity = {
        '@context': [CONTEXT_URL],
        'id': orion_entity_id,
        'type': sdm_entity_type,
        'name': {'type': 'Property', 'value': name},
        'location': {'type': 'GeoProperty',
                     'value': {'type': 'Point', 'coordinates': [lon, lat]}},
        'externalId': {'type': 'Property', 'value': external_id},
        'sensorType': {'type': 'Property', 'value': profile_code},
        'profileCode': {'type': 'Property', 'value': profile_code},
        'installedAt': {'type': 'Property', 'value': datetime.utcnow().isoformat()},
        'status': {'type': 'Property', 'value': 'active'},
        # controlledAsset is the SDM device->asset relationship; first entry is the owner.
        'controlledAsset': {'type': 'Relationship', 'object': parcels},
    }
    if device_category:
        orion_entity['category'] = {'type': 'Property', 'value': device_category}
    if metadata:
        orion_entity['metadata'] = {'type': 'Property', 'value': metadata}
    if data.get('is_under_canopy'):
        orion_entity['isUnderCanopy'] = {'type': 'Property', 'value': True}
    if data.get('station_id'):
        orion_entity['stationId'] = {'type': 'Property', 'value': data['station_id']}
    if data.get('altitude_meters'):
        orion_entity['altitudeMeters'] = {'type': 'Property', 'value': data['altitude_meters']}
    for attr in _HEALTH_ATTRIBUTES:
        if attr in data and data[attr] is not None:
            orion_entity[attr] = {'type': 'Property', 'value': data[attr]}

    apikey = dataloggers.get_apikey(tenant_id)

    undo = dataloggers.UndoStack()
    try:
        orion_response = requests.post(
            f"{ORION_URL}/ngsi-ld/v1/entities", json=orion_entity,
            headers=inject_fiware_headers({'Content-Type': 'application/ld+json'},
                                          tenant=tenant_id, has_context_in_body=True),
            timeout=10)
        if orion_response.status_code == 409:
            # Not ours: never compensate (delete) an entity this request did not create.
            raise dataloggers.ApiError(409, f'Sensor "{external_id}" already exists')
        if orion_response.status_code not in (200, 201):
            logger.error("Orion rejected sensor %s: %s", external_id, orion_response.status_code)
            raise dataloggers.ApiError(502, 'Failed to create sensor entity in context broker',
                                       'orion_entity')
        undo.push('orion_entity', lambda: dataloggers.delete_orion_entity(tenant_id, orion_entity_id))

        dataloggers.provision_iota_device(tenant_id, apikey, external_id, orion_entity_id,
                                          sdm_entity_type, measurements)
        undo.push('iota_device', lambda: dataloggers.delete_iota_device(tenant_id, external_id))

        dataloggers.link_device(tenant_id, datalogger['id'], orion_entity_id, external_id)
        undo.push('link', lambda: dataloggers.unlink_and_reconcile(tenant_id, orion_entity_id))

        dataloggers.reconcile_datalogger_acl(tenant_id, datalogger['id'])
    except Exception:
        undo.unwind()
        raise

    _ensure_bridge_subscription(tenant_id)
    logger.info("Registered sensor %s for tenant %s on datalogger %s",
                external_id, tenant_id, datalogger['id'])
    return jsonify({
        'entity_id': orion_entity_id,
        'topic': dataloggers.topic_for(apikey, external_id),
        'datalogger_id': datalogger['id'],
    }), 201


# =============================================================================
# Dataloggers
# =============================================================================


@sensors_bp.route('/api/sensors/dataloggers', methods=['POST'])
@require_auth
@_guard
def create_datalogger():
    tenant = _tenant()
    data = _body()
    name = data.get('name')
    kind = data.get('kind')
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise dataloggers.ApiError(400, 'name is required')
    if kind not in dataloggers.KINDS:
        raise dataloggers.ApiError(400, f'kind must be one of {", ".join(dataloggers.KINDS)}')
    created = dataloggers.create_datalogger(tenant, name.strip(), kind)
    return jsonify({'id': created['id'], 'mqtt': created['mqtt'], 'devices': []}), 201


@sensors_bp.route('/api/sensors/dataloggers', methods=['GET'])
@require_auth
@_guard
def list_dataloggers():
    return jsonify(dataloggers.list_dataloggers(_tenant())), 200


@sensors_bp.route('/api/sensors/dataloggers/<datalogger_id>/config', methods=['GET'])
@require_auth
@_guard
def datalogger_config(datalogger_id):
    return jsonify(dataloggers.get_datalogger_config(_tenant(), datalogger_id)), 200


@sensors_bp.route('/api/sensors/dataloggers/<datalogger_id>/rotate', methods=['POST'])
@require_auth
@_guard
def rotate_datalogger(datalogger_id):
    return jsonify(dataloggers.rotate_datalogger(_tenant(), datalogger_id)), 200


@sensors_bp.route('/api/sensors/dataloggers/<datalogger_id>/revoke', methods=['POST'])
@require_auth
@_guard
def revoke_datalogger(datalogger_id):
    dataloggers.set_datalogger_status(_tenant(), datalogger_id, revoke=True)
    return jsonify({'status': 'revoked'}), 200


@sensors_bp.route('/api/sensors/dataloggers/<datalogger_id>/enable', methods=['POST'])
@require_auth
@_guard
def enable_datalogger(datalogger_id):
    dataloggers.set_datalogger_status(_tenant(), datalogger_id, revoke=False)
    return jsonify({'status': 'active'}), 200


@sensors_bp.route('/api/sensors/dataloggers/<datalogger_id>', methods=['DELETE'])
@require_auth
@_guard
def delete_datalogger(datalogger_id):
    dataloggers.delete_datalogger(_tenant(), datalogger_id)
    return '', 204


@sensors_bp.route('/api/sensors/devices/<path:device_urn>/datalogger', methods=['PUT'])
@require_auth
@_guard
def move_device(device_urn):
    dataloggers.move_device(_tenant(), device_urn, _body().get('datalogger_id'))
    return jsonify({'entity_id': device_urn, 'datalogger_id': str(_body().get('datalogger_id'))}), 200


@sensors_bp.route('/api/sensors/devices/<path:device_urn>/profile', methods=['PUT'])
@require_auth
@_guard
def change_device_profile(device_urn):
    code = _body().get('profile')
    if not isinstance(code, str) or not code:
        raise dataloggers.ApiError(400, 'profile is required')
    dataloggers.change_device_profile(_tenant(), device_urn, code)
    return jsonify({'entity_id': device_urn, 'profile': code}), 200


@sensors_bp.route('/api/sensors/devices/<path:device_urn>', methods=['DELETE'])
@require_auth
@_guard
def delete_sensor_device(device_urn):
    dataloggers.delete_device(_tenant(), device_urn)
    return '', 204


# =============================================================================
# Sensor Profiles
# =============================================================================


@sensors_bp.route('/api/sensors/profiles', methods=['GET'])
@require_auth
def list_sensor_profiles():
    """List available sensor profiles"""
    try:
        tenant_id = g.tenant

        with get_db_connection_with_tenant(tenant_id) as conn:
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute("""
                    SELECT code, name, description, sdm_entity_type, sdm_category, mapping, metadata, health_defaults
                    FROM sensor_profiles
                    WHERE tenant_id IS NULL OR tenant_id = %s
                    ORDER BY code
                """, (tenant_id,))

                profiles = []
                for row in cur.fetchall():
                    profile_data = {
                        'code': row['code'],
                        'name': row['name'],
                        'description': row['description'],
                        'sdm_entity_type': row['sdm_entity_type'],
                        'sdm_category': row['sdm_category']
                    }

                    # Include metadata if available
                    if row.get('metadata'):
                        profile_data['metadata'] = row['metadata']

                    # Include health_defaults if available
                    if row.get('health_defaults'):
                        profile_data['health_defaults'] = row['health_defaults']

                    # Include mapping info for frontend hints
                    if row.get('mapping'):
                        mapping = row['mapping']
                        if isinstance(mapping, dict):
                            measurements = mapping.get('measurements', [])
                            if measurements:
                                # Extract SDM attributes for hints
                                sdm_attributes = [m.get('sdmAttribute') for m in measurements if m.get('sdmAttribute')]
                                if sdm_attributes:
                                    profile_data['sdm_attributes'] = sdm_attributes

                    profiles.append(profile_data)

                cur.close()

                return jsonify({
                    'profiles': profiles
                }), 200

            except Exception as e:
                logger.error(f"Error listing profiles: {e}")
                return jsonify({'error': 'Database error'}), 500

    except Exception as e:
        logger.error(f"Error in list_sensor_profiles: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@sensors_bp.route('/api/sensors/profiles/status', methods=['GET'])
@require_auth
def sensor_profiles_status():
    """Check if sensor profiles are initialized"""
    try:
        tenant_id = g.tenant

        with get_db_connection_with_tenant(tenant_id) as conn:
            try:
                cur = conn.cursor()

                # Count global profiles (tenant_id IS NULL)
                cur.execute("SELECT COUNT(*) FROM sensor_profiles WHERE tenant_id IS NULL")
                global_count = cur.fetchone()[0] or 0

                # Count tenant-specific profiles
                cur.execute("SELECT COUNT(*) FROM sensor_profiles WHERE tenant_id = %s", (tenant_id,))
                tenant_count = cur.fetchone()[0] or 0

                cur.close()

                return jsonify({
                    'initialized': global_count > 0,
                    'global_profiles': global_count,
                    'tenant_profiles': tenant_count,
                    'total': global_count + tenant_count
                }), 200

            except Exception as e:
                logger.error(f"Error checking profiles status: {e}")
                return jsonify({'error': 'Database error'}), 500

    except Exception as e:
        logger.error(f"Error in sensor_profiles_status: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# =============================================================================
# Sensor List
# =============================================================================


@sensors_bp.route('/api/sensors', methods=['GET'])
@require_auth
def list_tenant_sensors():
    """List sensors for the current tenant"""
    try:
        tenant_id = g.tenant

        with get_db_connection_with_tenant(tenant_id) as conn:
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute("""
                    SELECT
                        s.id,
                        s.external_id,
                        s.name,
                        sp.code as profile_code,
                        sp.name as profile_name,
                        s.is_under_canopy,
                        s.metadata,
                        s.created_at,
                        ST_X(s.installation_location::geometry) as lon,
                        ST_Y(s.installation_location::geometry) as lat,
                        (SELECT MAX(observed_at) FROM telemetry_events
                         WHERE tenant_id = %s AND device_id = s.external_id) as last_telemetry
                    FROM sensors s
                    JOIN sensor_profiles sp ON s.profile_id = sp.id
                    WHERE s.tenant_id = %s
                    ORDER BY s.created_at DESC
                """, (tenant_id, tenant_id))

                sensors = []
                for row in cur.fetchall():
                    sensor_data = {
                        'id': str(row['id']),
                        'external_id': row['external_id'],
                        'name': row['name'],
                        'profile': {
                            'code': row['profile_code'],
                            'name': row['profile_name']
                        },
                        'is_under_canopy': row['is_under_canopy'],
                        'metadata': row['metadata'],
                        'created_at': row['created_at'].isoformat(),
                        'last_telemetry': row['last_telemetry'].isoformat() if row['last_telemetry'] else None
                    }

                    # Add location if coordinates are available
                    if row['lon'] is not None and row['lat'] is not None:
                        sensor_data['installation_location'] = {
                            'lon': float(row['lon']),
                            'lat': float(row['lat'])
                        }

                    sensors.append(sensor_data)

                cur.close()

                return jsonify({
                    'sensors': sensors
                }), 200

            except Exception as e:
                logger.error(f"Error listing sensors: {e}")
                return jsonify({'error': 'Database error'}), 500

    except Exception as e:
        logger.error(f"Error in list_tenant_sensors: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# =============================================================================
# Device Telemetry Endpoints
# =============================================================================


@sensors_bp.route('/api/devices/<device_id>/telemetry', methods=['GET'])
@require_auth
def get_device_telemetry(device_id):
    """Get telemetry history for a device"""
    try:
        device_id = _normalize_device_id(device_id)
        tenant_id = g.tenant

        # Get query parameters
        start_time = request.args.get('start_time')
        end_time = request.args.get('end_time')
        limit = int(request.args.get('limit', 1000))

        with get_db_connection_with_tenant(tenant_id) as conn:
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)

                query = """
                    SELECT
                        observed_at,
                        payload,
                        metadata
                    FROM telemetry_events
                    WHERE tenant_id = %s AND device_id = %s
                """
                params = [tenant_id, device_id]

                if start_time:
                    query += " AND observed_at >= %s"
                    params.append(start_time)
                if end_time:
                    query += " AND observed_at <= %s"
                    params.append(end_time)

                query += " ORDER BY observed_at DESC LIMIT %s"
                params.append(limit)

                cur.execute(query, params)

                telemetry = []
                for row in cur.fetchall():
                    telemetry.append({
                        'observed_at': row['observed_at'].isoformat(),
                        'payload': row['payload'],
                        'metadata': row['metadata']
                    })

                cur.close()

                return jsonify({
                    'device_id': device_id,
                    'telemetry': telemetry,
                    'count': len(telemetry)
                }), 200

            except Exception as e:
                logger.error(f"Error getting telemetry: {e}")
                return jsonify({'error': 'Database error'}), 500

    except Exception as e:
        logger.error(f"Error in get_device_telemetry: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@sensors_bp.route('/api/devices/<device_id>/telemetry/latest', methods=['GET'])
@require_auth
def get_device_latest_telemetry(device_id):
    """Get latest telemetry value for a device"""
    try:
        device_id = _normalize_device_id(device_id)
        tenant_id = g.tenant

        with get_db_connection_with_tenant(tenant_id) as conn:
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute("""
                    SELECT
                        observed_at,
                        payload,
                        metadata
                    FROM telemetry_events
                    WHERE tenant_id = %s AND device_id = %s
                    ORDER BY observed_at DESC
                    LIMIT 1
                """, (tenant_id, device_id))

                row = cur.fetchone()
                cur.close()

                if row:
                    return jsonify({
                        'device_id': device_id,
                        'observed_at': row['observed_at'].isoformat(),
                        'payload': row['payload'],
                        'metadata': row['metadata']
                    }), 200
                else:
                    return jsonify({
                        'device_id': device_id,
                        'message': 'No telemetry data available'
                    }), 404

            except Exception as e:
                logger.error(f"Error getting latest telemetry: {e}")
                return jsonify({'error': 'Database error'}), 500

    except Exception as e:
        logger.error(f"Error in get_device_latest_telemetry: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@sensors_bp.route('/api/devices/<device_id>/telemetry/stats', methods=['GET'])
@require_auth
def get_device_telemetry_stats(device_id):
    """Get aggregated statistics for device telemetry"""
    try:
        device_id = _normalize_device_id(device_id)
        tenant_id = g.tenant

        # Get query parameters
        start_time = request.args.get('start_time')
        end_time = request.args.get('end_time')

        with get_db_connection_with_tenant(tenant_id) as conn:
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)

                query = """
                    SELECT
                        COUNT(*) as total_records,
                        MIN(observed_at) as first_record,
                        MAX(observed_at) as last_record
                    FROM telemetry_events
                    WHERE tenant_id = %s AND device_id = %s
                """
                params = [tenant_id, device_id]

                if start_time:
                    query += " AND observed_at >= %s"
                    params.append(start_time)
                if end_time:
                    query += " AND observed_at <= %s"
                    params.append(end_time)

                cur.execute(query, params)
                row = cur.fetchone()

                cur.close()

                return jsonify({
                    'device_id': device_id,
                    'stats': {
                        'total_records': row['total_records'],
                        'first_record': row['first_record'].isoformat() if row['first_record'] else None,
                        'last_record': row['last_record'].isoformat() if row['last_record'] else None
                    }
                }), 200

            except Exception as e:
                logger.error(f"Error getting telemetry stats: {e}")
                return jsonify({'error': 'Database error'}), 500

    except Exception as e:
        logger.error(f"Error in get_device_telemetry_stats: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# =============================================================================
# Device Commands Endpoints
# =============================================================================


def get_mqtt_client():
    """Get or create MQTT client (thread-safe)"""
    if not hasattr(get_mqtt_client, '_client') or get_mqtt_client._client is None:
        client = mqtt.Client(client_id=f"entity-manager-{secrets.token_hex(8)}")
        if MQTT_USERNAME and MQTT_PASSWORD:
            client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                logger.info("MQTT client connected successfully")
            else:
                logger.error(f"MQTT connection failed with code {rc}")

        def on_disconnect(client, userdata, rc):
            logger.warning(f"MQTT client disconnected (rc={rc})")

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect

        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            client.loop_start()
            get_mqtt_client._client = client
        except Exception as e:
            logger.error(f"Failed to connect MQTT client: {e}")
            get_mqtt_client._client = None

    return get_mqtt_client._client


get_mqtt_client._client = None


@sensors_bp.route('/api/devices/<device_id>/commands', methods=['POST'])
@require_auth
def send_device_command(device_id):
    """Send a command to a device via MQTT"""
    try:
        tenant_id = g.tenant
        data = request.get_json()

        if not data:
            return jsonify({'error': 'Request body is required'}), 400

        command_type = data.get('command_type', 'custom')
        payload = data.get('payload', {})

        if not isinstance(payload, dict):
            return jsonify({'error': 'Payload must be a JSON object'}), 400

        # Get device info to determine MQTT topic
        try:
            with get_db_connection_with_tenant(tenant_id) as conn:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute("""
                    SELECT external_id
                    FROM sensors
                    WHERE tenant_id = %s AND external_id = %s
                    LIMIT 1
                """, (tenant_id, device_id))
                device = cur.fetchone()
                cur.close()

            if not device:
                return jsonify({'error': 'Device not found'}), 404

            # Determine MQTT topic for commands
            mqtt_topic = f"{tenant_id}/{device_id}/cmd"

            # ── Create DeviceCommand entity in Orion-LD ──────────────────
            command_id = str(uuid.uuid4())
            command_entity_id = (
                f"urn:ngsi-ld:DeviceCommand:{tenant_id}:{command_id}"
            )
            now_iso = datetime.utcnow().isoformat()

            command_entity = {
                '@context': [CONTEXT_URL],
                'id': command_entity_id,
                'type': 'DeviceCommand',
                'commandId': {'type': 'Property', 'value': command_id},
                'commandType': {
                    'type': 'Property',
                    'value': command_type,
                },
                'targetDeviceId': {
                    'type': 'Property',
                    'value': device_id,
                },
                'mqttTopic': {
                    'type': 'Property',
                    'value': mqtt_topic,
                },
                'payload': {
                    'type': 'Property',
                    'value': payload,
                },
                'status': {'type': 'Property', 'value': 'pending'},
                'sentAt': {'type': 'Property', 'value': now_iso},
            }

            orion_headers = inject_fiware_headers(
                {'Content-Type': 'application/ld+json'}, tenant=tenant_id, has_context_in_body=True
            )
            orion_resp = requests.post(
                f"{ORION_URL}/ngsi-ld/v1/entities",
                json=command_entity,
                headers=orion_headers,
                timeout=10,
            )
            if orion_resp.status_code not in (200, 201):
                logger.error(
                    "Failed to create DeviceCommand in Orion-LD: %s",
                    orion_resp.text,
                )
                return jsonify({
                    'error': 'Failed to create command record'
                }), 502

            # Publish command to MQTT
            mqtt_client = get_mqtt_client()
            if not mqtt_client:
                # ── Update DeviceCommand status to failed via Orion-LD ──
                _patch_command_status(
                    tenant_id,
                    command_entity_id,
                    'failed',
                    {'error': 'MQTT client not available'},
                )
                return jsonify({'error': 'MQTT service unavailable'}), 503

            # Publish command
            command_message = {
                'command_id': command_id,
                'command_type': command_type,
                'payload': payload,
                'timestamp': now_iso,
            }

            try:
                result = mqtt_client.publish(
                    mqtt_topic,
                    json.dumps(command_message),
                    qos=1,
                )

                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    # ── Update DeviceCommand status to sent via Orion-LD ──
                    _patch_command_status(
                        tenant_id, command_entity_id, 'sent'
                    )

                    return jsonify({
                        'success': True,
                        'command_id': command_id,
                        'mqtt_topic': mqtt_topic,
                        'status': 'sent',
                        'sent_at': now_iso,
                    }), 201
                else:
                    _patch_command_status(
                        tenant_id,
                        command_entity_id,
                        'failed',
                        {
                            'error': (
                                f'MQTT publish failed with code '
                                f'{result.rc}'
                            )
                        },
                    )
                    return jsonify({
                        'error': (
                            f'Failed to publish command: '
                            f'MQTT error {result.rc}'
                        )
                    }), 500

            except Exception as mqtt_error:
                logger.error("MQTT publish error: %s", mqtt_error)
                _patch_command_status(
                    tenant_id,
                    command_entity_id,
                    'failed',
                    {'error': str(mqtt_error)},
                )
                return jsonify({
                    'error': f'Failed to publish command: {str(mqtt_error)}'
                }), 500

        except Exception as e:
            logger.error("Error sending command: %s", e)
            return jsonify({'error': 'Internal server error'}), 500

    except Exception as e:
        logger.error(f"Error in send_device_command: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@sensors_bp.route('/api/devices/<device_id>/commands', methods=['GET'])
@require_auth
def get_device_commands(device_id):
    """Get command history for a device"""
    try:
        tenant_id = g.tenant

        # Get query parameters
        limit = int(request.args.get('limit', 50))
        status = request.args.get('status')  # optional filter

        with get_db_connection_with_tenant(tenant_id) as conn:
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)

                query = """
                    SELECT
                        id,
                        command_type,
                        payload,
                        status,
                        sent_at,
                        executed_at,
                        response
                    FROM commands
                    WHERE tenant_id = %s AND device_id = %s
                """
                params = [tenant_id, device_id]

                if status:
                    query += " AND status = %s"
                    params.append(status)

                query += " ORDER BY sent_at DESC LIMIT %s"
                params.append(limit)

                cur.execute(query, params)

                commands = []
                for row in cur.fetchall():
                    commands.append({
                        'id': str(row['id']),
                        'command_type': row['command_type'],
                        'payload': row['payload'] if isinstance(row['payload'], dict) else json.loads(row['payload']) if row['payload'] else {},
                        'status': row['status'],
                        'sent_at': row['sent_at'].isoformat() if row['sent_at'] else None,
                        'executed_at': row['executed_at'].isoformat() if row['executed_at'] else None,
                        'response': row['response'] if isinstance(row['response'], dict) else json.loads(row['response']) if row['response'] else None
                    })

                cur.close()

                return jsonify({
                    'device_id': device_id,
                    'commands': commands,
                    'count': len(commands)
                }), 200

            except Exception as e:
                logger.error(f"Error getting commands: {e}")
                return jsonify({'error': 'Database error'}), 500

    except Exception as e:
        logger.error(f"Error in get_device_commands: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# =============================================================================
# Heartbeat / Connection Status Check
# =============================================================================


@sensors_bp.route('/api/heartbeat/check', methods=['GET'])
@require_auth
def check_entity_heartbeat():
    """
    Check if an entity (sensor, robot, device) has connected and sent data.

    Query params:
      - entity_id: The device/sensor external ID or entity URN
      - entity_type: 'sensor', 'robot', or 'device'

    Returns:
      - connected: boolean indicating if data has been received
      - last_seen: ISO timestamp of last data received
      - first_seen: ISO timestamp of first data received
    """
    try:
        tenant_id = g.tenant
        if not tenant_id:
            return jsonify({'error': 'Tenant not found'}), 401

        entity_id = request.args.get('entity_id')
        entity_type = request.args.get('entity_type', 'sensor')

        if not entity_id:
            return jsonify({'error': 'entity_id is required'}), 400

        # Extract device_id from URN if provided
        device_id = entity_id
        if entity_id.startswith('urn:ngsi-ld:'):
            # Format: urn:ngsi-ld:Type:tenant:device_id
            parts = entity_id.split(':')
            if len(parts) >= 5:
                device_id = parts[-1]

        conn = None
        try:
            conn = get_db_connection_simple()
            if not conn:
                return jsonify({'error': 'Database not configured'}), 503

            cur = conn.cursor(cursor_factory=RealDictCursor)

            # Check telemetry_events table for any data from this device
            cur.execute("""
                SELECT
                    MIN(observed_at) as first_seen,
                    MAX(observed_at) as last_seen,
                    COUNT(*) as event_count
                FROM telemetry_events
                WHERE tenant_id = %s AND device_id = %s
            """, (tenant_id, device_id))

            row = cur.fetchone()
            cur.close()

            if row and row['event_count'] and row['event_count'] > 0:
                return jsonify({
                    'connected': True,
                    'first_seen': row['first_seen'].isoformat() if row['first_seen'] else None,
                    'last_seen': row['last_seen'].isoformat() if row['last_seen'] else None,
                    'event_count': row['event_count']
                }), 200
            else:
                # No events found - check if we can query Orion-LD for entity status
                return jsonify({
                    'connected': False,
                    'first_seen': None,
                    'last_seen': None
                }), 200

        except Exception as db_error:
            logger.error(f"Database error checking heartbeat: {db_error}")
            return jsonify({'connected': False}), 200
        finally:
            # A pooled connection goes back to the pool; closing it leaks the slot.
            return_db_connection(conn)

    except Exception as e:
        logger.error(f"Error checking heartbeat: {e}")
        return internal_error(e, 'sensors_endpoint')
