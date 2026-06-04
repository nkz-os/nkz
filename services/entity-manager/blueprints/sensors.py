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

from flask import Blueprint, request, jsonify, g
from psycopg2.extras import RealDictCursor
import psycopg2
import requests
import paho.mqtt.client as mqtt

from common.auth_middleware import require_auth, inject_fiware_headers
from db_helper import get_db_connection_with_tenant, get_db_connection_simple

# Import shared config
from helpers import ORION_URL, CONTEXT_URL

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


# =============================================================================
# Sensor Registration
# =============================================================================


@sensors_bp.route('/api/sensors/register', methods=['POST'])
@require_auth
def register_sensor():
    """
    Register a sensor - creates Orion-LD entity first, then records in sensors table

    Request body:
    {
        "external_id": "BP_Vaso_PAR_1",
        "name": "BP Vaso PAR 1",
        "profile": "par_photon_flux",
        "location": {"lat": 42.57, "lon": -2.02},
        "station_id": "BP_Vaso",
        "is_under_canopy": true
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        tenant_id = g.tenant
        if not tenant_id:
            return jsonify({'error': 'Tenant not found in token'}), 401

        external_id = data.get('external_id')
        name = data.get('name')
        profile_code = data.get('profile')

        if not external_id or not name or not profile_code:
            return jsonify({
                'error': 'Missing required fields: external_id, name, profile'
            }), 400

        location = data.get('location', {})
        lat = location.get('lat')
        lon = location.get('lon')

        if not lat or not lon:
            return jsonify({
                'error': 'Location (lat, lon) is required'
            }), 400

        conn = get_db_connection_with_tenant(tenant_id)
        if not conn:
            return jsonify({'error': 'Database connection error'}), 500

        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)

            # Check if profile exists and get SDM mapping
            cur.execute("""
                SELECT id, sdm_entity_type, mapping
                FROM sensor_profiles
                WHERE code = %s AND (tenant_id IS NULL OR tenant_id = %s)
                ORDER BY tenant_id NULLS LAST
                LIMIT 1
            """, (profile_code, tenant_id))

            profile_row = cur.fetchone()
            if not profile_row:
                cur.close()
                conn.close()
                return jsonify({
                    'error': f'Profile "{profile_code}" not found'
                }), 404

            profile_id = profile_row['id']
            sdm_entity_type = profile_row.get('sdm_entity_type') or 'AgriSensor'
            profile_mapping = profile_row.get('mapping') or {}

            cur.close()
            conn.close()

            # ── Dedup check via Orion-LD ──────────────────────────────────
            orion_query_headers = {
                'Accept': 'application/ld+json',
                'Fiware-Service': tenant_id,
                'Fiware-ServicePath': '/'
            }
            orion_check_url = (
                f"{ORION_URL}/ngsi-ld/v1/entities"
                f"?type={sdm_entity_type}"
                f'&q=externalId=="{external_id}"'
            )
            orion_check = requests.get(
                orion_check_url, headers=orion_query_headers, timeout=10
            )
            if orion_check.status_code == 200:
                existing_entities = orion_check.json()
                if existing_entities:
                    existing_entity = existing_entities[0]
                    return jsonify({
                        'error': f'Sensor with external_id "{external_id}" already exists',
                        'sensor': {
                            'id': existing_entity.get('id'),
                            'external_id': _extract_prop_value(
                                existing_entity, 'externalId'
                            ),
                            'name': _extract_prop_value(
                                existing_entity, 'name'
                            ),
                        }
                    }), 409

            # Prepare metadata
            metadata = data.get('metadata', {})
            if data.get('station_id'):
                metadata['group'] = data['station_id']
                metadata['station_id'] = data['station_id']

            import json

            # =============================================================================
            # STEP 1: Create NGSI-LD entity in Orion-LD (sole source of truth)
            # =============================================================================
            orion_entity_id = (
                f"urn:ngsi-ld:{sdm_entity_type}:{tenant_id}:{external_id}"
            )

            orion_entity = {
                '@context': [CONTEXT_URL],
                'id': orion_entity_id,
                'type': sdm_entity_type,
                'name': {'type': 'Property', 'value': name},
                'location': {
                    'type': 'GeoProperty',
                    'value': {
                        'type': 'Point',
                        'coordinates': [lon, lat],
                    },
                },
                'externalId': {'type': 'Property', 'value': external_id},
                'sensorType': {'type': 'Property', 'value': profile_code},
                'profileCode': {'type': 'Property', 'value': profile_code},
                'installedAt': {
                    'type': 'Property',
                    'value': datetime.utcnow().isoformat(),
                },
                'status': {'type': 'Property', 'value': 'active'},
            }

            if metadata:
                orion_entity['metadata'] = {
                    'type': 'Property',
                    'value': metadata,
                }
            if data.get('is_under_canopy'):
                orion_entity['isUnderCanopy'] = {
                    'type': 'Property',
                    'value': True,
                }
            if data.get('station_id'):
                orion_entity['stationId'] = {
                    'type': 'Property',
                    'value': data['station_id'],
                }
            if data.get('altitude_meters'):
                orion_entity['altitudeMeters'] = {
                    'type': 'Property',
                    'value': data['altitude_meters'],
                }
            if data.get('parcel_id'):
                orion_entity['parcelId'] = {
                    'type': 'Property',
                    'value': data['parcel_id'],
                }

            orion_headers = {
                'Content-Type': 'application/ld+json',
                'Fiware-Service': tenant_id,
                'Fiware-ServicePath': '/',
            }
            orion_url = f"{ORION_URL}/ngsi-ld/v1/entities"

            orion_entity_created = False
            orion_response = requests.post(
                orion_url,
                json=orion_entity,
                headers=orion_headers,
                timeout=10,
            )
            if orion_response.status_code in [200, 201]:
                orion_entity_created = True
                logger.info(
                    "Created Orion-LD entity %s for sensor %s",
                    orion_entity_id,
                    external_id,
                )
            elif orion_response.status_code == 409:
                orion_entity_created = True
                logger.info(
                    "Orion-LD entity %s already exists for sensor %s",
                    orion_entity_id,
                    external_id,
                )
            else:
                logger.error(
                    "Failed to create Orion-LD entity for sensor %s: %s - %s",
                    external_id,
                    orion_response.status_code,
                    orion_response.text,
                )
                return jsonify({
                    'error': 'Failed to create sensor entity in context broker'
                }), 502

            # =============================================================================
            # STEP 2: Create MQTT credentials for the device
            # =============================================================================
            mqtt_credentials = None
            mqtt_credentials_created = False
            try:
                mqtt_service_url = os.getenv('MQTT_CREDENTIALS_SERVICE_URL', 'http://mqtt-credentials-manager-service:5000')
                mqtt_response = requests.post(
                    f'{mqtt_service_url}/api/mqtt/credentials/create',
                    json={
                        'tenant_id': tenant_id,
                        'device_id': external_id
                    },
                    timeout=10
                )

                if mqtt_response.status_code == 201:
                    mqtt_credentials = mqtt_response.json()
                    mqtt_credentials_created = True
                    logger.info(f"Created MQTT credentials for device {external_id}")
                else:
                    logger.warning(f"Failed to create MQTT credentials: {mqtt_response.status_code} - {mqtt_response.text}")
            except Exception as mqtt_error:
                logger.error(f"Error creating MQTT credentials: {mqtt_error}")
                # Don't fail the whole request, but log it

            # =============================================================================
            # STEP 3: Configure IoT Agent for this device
            # =============================================================================
            iot_agent_configured = False
            try:
                if profile_mapping and mqtt_credentials_created:
                    iot_agent_url = os.getenv('IOT_AGENT_URL', 'http://iot-agent-json-service:4041')

                    # Build IoT Agent device configuration
                    # Topic pattern: {tenant_id}/{device_id}/data
                    device_config = {
                        'devices': [{
                            'device_id': external_id,
                            'entity_name': orion_entity_id,
                            'entity_type': sdm_entity_type,
                            'protocol': 'MQTT',
                            'transport': 'MQTT',
                            'timezone': 'Europe/Madrid',
                            'attributes': []
                        }]
                    }

                    # Add attributes from profile mapping
                    mapping_data = profile_mapping if isinstance(profile_mapping, dict) else {}
                    measurements = mapping_data.get('measurements', [])

                    for measurement in measurements:
                        attr_config = {
                            'name': measurement.get('sdmAttribute', measurement.get('type')),
                            'type': 'Number' if measurement.get('unit') else 'Text'
                        }
                        if measurement.get('unit'):
                            attr_config['unit'] = measurement.get('unit')
                        device_config['devices'][0]['attributes'].append(attr_config)

                    # Register device in IoT Agent
                    iot_headers = {
                        'Content-Type': 'application/json',
                        'Fiware-Service': tenant_id,
                        'Fiware-ServicePath': '/'
                    }

                    iot_response = requests.post(
                        f'{iot_agent_url}/iot/devices',
                        json=device_config,
                        headers=iot_headers,
                        timeout=10
                    )

                    if iot_response.status_code in [200, 201]:
                        iot_agent_configured = True
                        logger.info(f"Configured IoT Agent for device {external_id}")
                    else:
                        logger.warning(f"Failed to configure IoT Agent: {iot_response.status_code} - {iot_response.text}")

            except Exception as iot_error:
                logger.error(f"Error configuring IoT Agent: {iot_error}")
                # Don't fail the whole request, but log it

            logger.info(f"Registered sensor {external_id} for tenant {tenant_id} (Orion-LD: {'created' if orion_entity_created else 'skipped'}, MQTT: {'created' if mqtt_credentials_created else 'skipped'}, IoT Agent: {'configured' if iot_agent_configured else 'skipped'})")

            response_data = {
                'success': True,
                'sensor': {
                    'id': orion_entity_id,
                    'external_id': external_id,
                    'name': name,
                    'profile': profile_code,
                    'tenant_id': tenant_id,
                    'created_at': datetime.utcnow().isoformat(),
                },
                'message': 'Sensor registered successfully',
            }

            # Add Orion-LD entity info if created
            if orion_entity_created and orion_entity_id:
                response_data['orion_entity'] = {
                    'id': orion_entity_id,
                    'type': sdm_entity_type,
                    'created': True
                }

            # Add MQTT credentials if created (ONLY returned on creation)
            if mqtt_credentials_created and mqtt_credentials:
                response_data['mqtt'] = {
                    'username': mqtt_credentials.get('username'),
                    'password': mqtt_credentials.get('password'),
                    'host': mqtt_credentials.get('mqtt_host', os.getenv('MQTT_HOST', 'mosquitto-service')),
                    'port': mqtt_credentials.get('mqtt_port', 1883),
                    'topics': mqtt_credentials.get('topics', {
                        'data': f'{tenant_id}/{external_id}/data',
                        'commands': f'{tenant_id}/{external_id}/cmd'
                    }),
                    'warning': 'Save these credentials securely. Password cannot be retrieved later.'
                }

            # Add IoT Agent status
            response_data['iot_agent'] = {
                'configured': iot_agent_configured,
                'status': 'ready' if iot_agent_configured else 'pending'
            }

            return jsonify(response_data), 201

        except Exception as e:
            logger.error("Error registering sensor: %s", e)
            # Best-effort cleanup of Orion-LD entity if it was created
            try:
                _cleanup_needed = orion_entity_created and orion_entity_id
            except NameError:
                _cleanup_needed = False
            if _cleanup_needed:
                try:
                    cleanup_headers = {
                        'Fiware-Service': tenant_id,
                        'Fiware-ServicePath': '/',
                    }
                    requests.delete(
                        f"{ORION_URL}/ngsi-ld/v1/entities/{orion_entity_id}",
                        headers=cleanup_headers,
                        timeout=5,
                    )
                    logger.info(
                        "Cleaned up Orion-LD entity %s after failure",
                        orion_entity_id,
                    )
                except Exception as cleanup_error:
                    logger.critical(
                        "INCONSISTENCY: Orion-LD entity %s exists but cleanup "
                        "failed: %s",
                        orion_entity_id,
                        cleanup_error,
                    )
            return jsonify({'error': f'Registration error: {str(e)}'}), 500

    except Exception as e:
        logger.error(f"Error in register_sensor: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# =============================================================================
# Sensor Profiles
# =============================================================================


@sensors_bp.route('/api/sensors/profiles', methods=['GET'])
@require_auth
def list_sensor_profiles():
    """List available sensor profiles"""
    try:
        tenant_id = g.tenant
        conn = get_db_connection_with_tenant(tenant_id)
        if not conn:
            return jsonify({'error': 'Database connection error'}), 500

        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT code, name, description, sdm_entity_type, sdm_category, mapping, metadata
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
            conn.close()

            return jsonify({
                'profiles': profiles
            }), 200

        except Exception as e:
            conn.close()
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
        conn = get_db_connection_with_tenant(tenant_id)
        if not conn:
            return jsonify({'error': 'Database connection error'}), 500

        try:
            cur = conn.cursor()

            # Count global profiles (tenant_id IS NULL)
            cur.execute("SELECT COUNT(*) FROM sensor_profiles WHERE tenant_id IS NULL")
            global_count = cur.fetchone()[0] or 0

            # Count tenant-specific profiles
            cur.execute("SELECT COUNT(*) FROM sensor_profiles WHERE tenant_id = %s", (tenant_id,))
            tenant_count = cur.fetchone()[0] or 0

            cur.close()
            conn.close()

            return jsonify({
                'initialized': global_count > 0,
                'global_profiles': global_count,
                'tenant_profiles': tenant_count,
                'total': global_count + tenant_count
            }), 200

        except Exception as e:
            conn.close()
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
        conn = get_db_connection_with_tenant(tenant_id)
        if not conn:
            return jsonify({'error': 'Database connection error'}), 500

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
            conn.close()

            return jsonify({
                'sensors': sensors
            }), 200

        except Exception as e:
            conn.close()
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
        conn = get_db_connection_with_tenant(tenant_id)
        if not conn:
            return jsonify({'error': 'Database connection error'}), 500

        # Get query parameters
        start_time = request.args.get('start_time')
        end_time = request.args.get('end_time')
        limit = int(request.args.get('limit', 1000))

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
            conn.close()

            return jsonify({
                'device_id': device_id,
                'telemetry': telemetry,
                'count': len(telemetry)
            }), 200

        except Exception as e:
            conn.close()
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
        conn = get_db_connection_with_tenant(tenant_id)
        if not conn:
            return jsonify({'error': 'Database connection error'}), 500

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
            conn.close()

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
            conn.close()
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
        conn = get_db_connection_with_tenant(tenant_id)
        if not conn:
            return jsonify({'error': 'Database connection error'}), 500

        # Get query parameters
        start_time = request.args.get('start_time')
        end_time = request.args.get('end_time')

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
            conn.close()

            return jsonify({
                'device_id': device_id,
                'stats': {
                    'total_records': row['total_records'],
                    'first_record': row['first_record'].isoformat() if row['first_record'] else None,
                    'last_record': row['last_record'].isoformat() if row['last_record'] else None
                }
            }), 200

        except Exception as e:
            conn.close()
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
        conn = get_db_connection_with_tenant(tenant_id)
        if not conn:
            return jsonify({'error': 'Database connection error'}), 500

        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT external_id
                FROM sensors
                WHERE tenant_id = %s AND external_id = %s
                LIMIT 1
            """, (tenant_id, device_id))

            device = cur.fetchone()
            cur.close()
            conn.close()

            if not device:
                return jsonify({'error': 'Device not found'}), 404

            # Determine MQTT topic for commands
            # Pattern: {tenant_id}/{device_id}/cmd
            mqtt_topic = f"{tenant_id}/{device_id}/cmd"

            # Create command record in database
            command_id = str(uuid.uuid4())
            conn = get_db_connection_with_tenant(tenant_id)
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                INSERT INTO commands (id, tenant_id, device_id, command_type, payload, status, sent_at)
                VALUES (%s, %s, %s, %s, %s, 'pending', NOW())
                RETURNING id, sent_at
            """, (command_id, tenant_id, device_id, command_type, json.dumps(payload)))

            command_record = cur.fetchone()
            conn.commit()
            cur.close()
            conn.close()

            # Publish command to MQTT
            mqtt_client = get_mqtt_client()
            if not mqtt_client:
                # Update command status to failed
                conn = get_db_connection_with_tenant(tenant_id)
                cur = conn.cursor()
                cur.execute("""
                    UPDATE commands SET status = 'failed', response = %s
                    WHERE id = %s
                """, (json.dumps({'error': 'MQTT client not available'}), command_id))
                conn.commit()
                cur.close()
                conn.close()
                return jsonify({'error': 'MQTT service unavailable'}), 503

            # Publish command
            command_message = {
                'command_id': command_id,
                'command_type': command_type,
                'payload': payload,
                'timestamp': datetime.utcnow().isoformat()
            }

            try:
                result = mqtt_client.publish(mqtt_topic, json.dumps(command_message), qos=1)

                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    # Update command status to sent
                    conn = get_db_connection_with_tenant(tenant_id)
                    cur = conn.cursor()
                    cur.execute("""
                        UPDATE commands SET status = 'sent'
                        WHERE id = %s
                    """, (command_id,))
                    conn.commit()
                    cur.close()
                    conn.close()

                    return jsonify({
                        'success': True,
                        'command_id': command_id,
                        'mqtt_topic': mqtt_topic,
                        'status': 'sent',
                        'sent_at': command_record['sent_at'].isoformat()
                    }), 201
                else:
                    # Update command status to failed
                    conn = get_db_connection_with_tenant(tenant_id)
                    cur = conn.cursor()
                    cur.execute("""
                        UPDATE commands SET status = 'failed', response = %s
                        WHERE id = %s
                    """, (json.dumps({'error': f'MQTT publish failed with code {result.rc}'}), command_id))
                    conn.commit()
                    cur.close()
                    conn.close()

                    return jsonify({'error': f'Failed to publish command: MQTT error {result.rc}'}), 500

            except Exception as mqtt_error:
                logger.error(f"MQTT publish error: {mqtt_error}")
                # Update command status to failed
                conn = get_db_connection_with_tenant(tenant_id)
                cur = conn.cursor()
                cur.execute("""
                    UPDATE commands SET status = 'failed', response = %s
                    WHERE id = %s
                """, (json.dumps({'error': str(mqtt_error)}), command_id))
                conn.commit()
                cur.close()
                conn.close()

                return jsonify({'error': f'Failed to publish command: {str(mqtt_error)}'}), 500

        except Exception as e:
            if conn:
                conn.close()
            logger.error(f"Error sending command: {e}")
            return jsonify({'error': 'Database error'}), 500

    except Exception as e:
        logger.error(f"Error in send_device_command: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@sensors_bp.route('/api/devices/<device_id>/commands', methods=['GET'])
@require_auth
def get_device_commands(device_id):
    """Get command history for a device"""
    try:
        tenant_id = g.tenant
        conn = get_db_connection_with_tenant(tenant_id)
        if not conn:
            return jsonify({'error': 'Database connection error'}), 500

        # Get query parameters
        limit = int(request.args.get('limit', 50))
        status = request.args.get('status')  # optional filter

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
            conn.close()

            return jsonify({
                'device_id': device_id,
                'commands': commands,
                'count': len(commands)
            }), 200

        except Exception as e:
            conn.close()
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
            postgres_url = os.getenv('DATABASE_URL') or os.getenv('POSTGRES_URL')
            if not postgres_url:
                return jsonify({'error': 'Database not configured'}), 503

            conn = psycopg2.connect(postgres_url)
            cur = conn.cursor(cursor_factory=RealDictCursor)

            # Check telemetry_events table for any data from this device
            cur.execute("""
                SELECT
                    MIN(observed_at) as first_seen,
                    MAX(observed_at) as last_seen,
                    COUNT(*) as event_count
                FROM telemetry_events
                WHERE tenant_id = %s
                  AND (device_id = %s OR device_id LIKE %s)
                LIMIT 1
            """, (tenant_id, device_id, f'%{device_id}%'))

            row = cur.fetchone()
            cur.close()
            conn.close()

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
            if conn:
                conn.close()
            return jsonify({'connected': False}), 200

    except Exception as e:
        logger.error(f"Error checking heartbeat: {e}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500
