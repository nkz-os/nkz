#!/usr/bin/env python3
"""
Entities Blueprint - Extracted from entity_management_api.py
"""
import os
import sys
import json
import logging
import uuid
import re
from typing import Optional, Dict, Any
from datetime import datetime

from flask import Blueprint, request, jsonify, g
from psycopg2.extras import RealDictCursor
import requests

from common.auth_middleware import require_auth, inject_fiware_headers
from db_helper import get_db_connection_with_tenant, return_db_connection, get_db_connection_simple

# Import shared helpers
from helpers import (
    ORION_URL, CONTEXT_URL, _extract_number,
    get_limits_for_tenant, _count_all_entities, _count_entities_by_type,
    _check_entity_total_limit, _check_parcel_count_limit, _sum_parcel_area,
    MAX_ROBOTS, MAX_SENSORS, MAX_AREA_HECTARES,
    ROBOT_ENTITY_TYPES, SENSOR_ENTITY_TYPES, PARCEL_ENTITY_TYPES,
    log_entity_operation, require_entity_ownership
)

logger = logging.getLogger(__name__)

entities_bp = Blueprint('entities', __name__)


def get_entity_types():
    """Get available entity types from configuration"""
    default_types = {
        "robot_types": {
            "harvester_robot": {
                "name": "Harvester Robot",
                "description": "Robot for harvesting crops",
                "attributes": {
                    "status": {"type": "Text", "description": "Current status"},
                    "battery_level": {"type": "Number", "description": "Battery percentage"},
                    "current_task": {"type": "Text", "description": "Current task"},
                    "location": {"type": "geo:json", "description": "Robot location"},
                    "speed": {"type": "Number", "description": "Current speed"},
                    "payload": {"type": "Number", "description": "Current payload weight"}
                }
            },
            "sprayer_robot": {
                "name": "Sprayer Robot",
                "description": "Robot for spraying pesticides/herbicides",
                "attributes": {
                    "status": {"type": "Text", "description": "Current status"},
                    "tank_level": {"type": "Number", "description": "Tank level percentage"},
                    "spray_rate": {"type": "Number", "description": "Current spray rate"},
                    "location": {"type": "geo:json", "description": "Robot location"}
                }
            }
        },
        "sensor_types": {
            "soil_sensor": {
                "name": "Soil Sensor",
                "description": "Sensor for soil conditions",
                "attributes": {
                    "moisture": {"type": "Number", "description": "Soil moisture percentage"},
                    "ph": {"type": "Number", "description": "Soil pH level"},
                    "temperature": {"type": "Number", "description": "Soil temperature"},
                    "location": {"type": "geo:json", "description": "Sensor location"}
                }
            },
            "weather_sensor": {
                "name": "Weather Sensor",
                "description": "Environmental weather sensor",
                "attributes": {
                    "temperature": {"type": "Number", "description": "Air temperature"},
                    "humidity": {"type": "Number", "description": "Air humidity percentage"},
                    "pressure": {"type": "Number", "description": "Atmospheric pressure"},
                    "wind_speed": {"type": "Number", "description": "Wind speed"},
                    "location": {"type": "geo:json", "description": "Sensor location"}
                }
            }
        },
        "parcel_types": {
            "olive_grove": {
                "name": "Olive Grove",
                "description": "Olive tree plantation",
                "attributes": {
                    "area": {"type": "Number", "description": "Parcel area in hectares"},
                    "tree_count": {"type": "Number", "description": "Number of olive trees"},
                    "variety": {"type": "Text", "description": "Olive variety"},
                    "planting_date": {"type": "DateTime", "description": "Planting date"},
                    "location": {"type": "geo:json", "description": "Parcel boundaries"}
                }
            },
            "vineyard": {
                "name": "Vineyard",
                "description": "Grape vine plantation",
                "attributes": {
                    "area": {"type": "Number", "description": "Parcel area in hectares"},
                    "row_count": {"type": "Number", "description": "Number of vine rows"},
                    "variety": {"type": "Text", "description": "Grape variety"},
                    "planting_date": {"type": "DateTime", "description": "Planting date"},
                    "location": {"type": "geo:json", "description": "Parcel boundaries"}
                }
            }
        }
    }
    return default_types


# === Lines 540-589 from entity_management_api.py ===
@entities_bp.route('/api/entities/inventory', methods=['GET'])
@require_auth
def get_entity_inventory():
    """
    Get entity inventory for current tenant
    Returns list of entity types and counts for context-aware risk configuration
    """
    try:
        tenant = g.tenant
        headers = inject_fiware_headers({}, tenant)

        # Get all entities from Orion-LD
        orion_url = f"{ORION_URL}/ngsi-ld/v1/entities"
        params = {'limit': 1000}

        response = requests.get(orion_url, params=params, headers=headers, timeout=30)
        if response.status_code != 200:
            return jsonify({'error': 'Failed to get entities from Orion'}), 500

        entities = response.json()
        if not isinstance(entities, list):
            entities = []

        # Group by entity type
        inventory = {}
        for entity in entities:
            entity_type = entity.get('type', 'Unknown')
            if entity_type not in inventory:
                inventory[entity_type] = {
                    'type': entity_type,
                    'count': 0,
                    'entities': []
                }
            inventory[entity_type]['count'] += 1
            inventory[entity_type]['entities'].append({
                'id': entity.get('id'),
                'name': entity.get('name', {}).get('value', entity.get('id'))
            })

        # Convert to list format
        result = list(inventory.values())

        return jsonify({
            'inventory': result,
            'tenant': tenant
        }), 200

    except Exception as e:
        logger.error(f"Error getting entity inventory: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# === Lines 721-734 from entity_management_api.py ===
@entities_bp.route('/entity-types', methods=['GET'])
@require_auth
def list_entity_types():
    """List all available entity types"""
    try:
        entity_types = get_entity_types()
        return jsonify({
            'entity_types': entity_types,
            'count': sum(len(types) for types in entity_types.values()),
            'tenant': g.tenant
        })
    except Exception as e:
        logger.error(f"Error listing entity types: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# === Lines 736-758 from entity_management_api.py ===
@entities_bp.route('/entity-types/<category>/<type_name>', methods=['GET'])
@require_auth
def get_entity_type(category, type_name):
    """Get specific entity type definition"""
    try:
        entity_types = get_entity_types()

        if category not in entity_types:
            return jsonify({'error': 'Category not found'}), 404

        if type_name not in entity_types[category]:
            return jsonify({'error': 'Entity type not found'}), 404

        return jsonify({
            'category': category,
            'type_name': type_name,
            'definition': entity_types[category][type_name],
            'tenant': g.tenant
        })

    except Exception as e:
        logger.error(f"Error getting entity type: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# === Lines 760-797 from entity_management_api.py ===
@entities_bp.route('/entity-types/<category>/<type_name>', methods=['POST'])
@require_auth
def create_entity_type(category, type_name):
    """Create new entity type definition"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        # Validate required fields
        required_fields = ['name', 'description', 'attributes']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400

        # Store entity type definition (in production, this would be stored in database)
        entity_types = get_entity_types()

        if category not in entity_types:
            entity_types[category] = {}

        entity_types[category][type_name] = {
            'name': data['name'],
            'description': data['description'],
            'attributes': data['attributes'],
            'created_at': datetime.utcnow().isoformat()
        }

        return jsonify({
            'message': 'Entity type created successfully',
            'category': category,
            'type_name': type_name,
            'definition': entity_types[category][type_name]
        }), 201

    except Exception as e:
        logger.error(f"Error creating entity type: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# === Lines 799-820 from entity_management_api.py ===
@entities_bp.route('/entity-types/<category>/<type_name>', methods=['DELETE'])
@require_auth
def delete_entity_type(category, type_name):
    """Delete entity type definition"""
    try:
        entity_types = get_entity_types()

        if category not in entity_types or type_name not in entity_types[category]:
            return jsonify({'error': 'Entity type not found'}), 404

        del entity_types[category][type_name]

        return jsonify({
            'message': 'Entity type deleted successfully',
            'category': category,
            'type_name': type_name
        })

    except Exception as e:
        logger.error(f"Error deleting entity type: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# === Lines 822-855 from entity_management_api.py ===
@entities_bp.route('/instances/<entity_type>', methods=['GET'])
@require_auth
def list_instances(entity_type):
    """List instances of a specific entity type"""
    try:
        # Query Orion-LD for entities of this type
        orion_url = f"{ORION_URL}/ngsi-ld/v1/entities"
        params = {'type': entity_type}

        headers = {
            'Accept': 'application/ld+json'
        }
        headers = inject_fiware_headers(headers, g.tenant)

        response = requests.get(orion_url, params=params, headers=headers)
        if response.status_code != 200:
            return jsonify({'error': 'Failed to query Orion'}), 500

        entities = response.json()

        # Log the operation
        log_entity_operation('list', None, entity_type, g.tenant, g.farmer_id,
                           {'count': len(entities)})

        return jsonify({
            'entity_type': entity_type,
            'instances': entities,
            'count': len(entities),
            'tenant': g.tenant
        })

    except Exception as e:
        logger.error(f"Error listing instances: {e}")
        return jsonify({'error': 'Internal server error'}), 500


def _build_ngsild_urn(entity_type: str, tenant: str, custom_id: str | None = None) -> str:
    """Build NGSI-LD compliant URN: urn:ngsi-ld:<type>:<tenant>:<id>

    Per ETSI NGSI-LD spec, entity IDs MUST be URNs in the format:
    urn:ngsi-ld:<entity-type>:<remaining-parts>
    """
    _id = custom_id or str(uuid.uuid4())
    _id = _id.replace(':', '-').replace(' ', '_')
    return f"urn:ngsi-ld:{entity_type}:{tenant}:{_id}"


# === Lines 857-970 from entity_management_api.py ===
@entities_bp.route('/instances/<entity_type>', methods=['POST'])
@require_auth
def create_instance(entity_type):
    """Create new instance of entity type"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        # Add type and ID to entity — NGSI-LD URN format
        entity_id = data.get('id')
        if entity_id:
            if not entity_id.startswith('urn:ngsi-ld:'):
                entity_id = _build_ngsild_urn(entity_type, g.tenant, entity_id)
        else:
            entity_id = _build_ngsild_urn(entity_type, g.tenant)
        entity_data = {
            'id': entity_id,
            'type': entity_type,
            **data
        }

        # Enforcements por límites (por tipo)
        tenant = g.tenant
        # Cargar límites por tenant (override de env si existen en Orion)
        limits = get_limits_for_tenant(tenant) or {}
        max_robots = int(limits.get('maxRobots') or MAX_ROBOTS)
        max_sensors = int(limits.get('maxSensors') or MAX_SENSORS)
        max_area = float(limits.get('maxAreaHectares') or MAX_AREA_HECTARES)
        max_parcels = limits.get('maxParcels')  # None = unlimited
        max_entities_total = limits.get('maxEntitiesTotal')  # None = unlimited

        # Aggregate entity cap (Task 6: max_entities_total counts ALL entity types)
        if max_entities_total is not None:
            total_count = _count_all_entities(tenant)
            if not _check_entity_total_limit(total_count, max_entities_total):
                return jsonify({
                    'error': 'Entities total limit exceeded',
                    'error_en': 'Entities total limit exceeded',
                    'limit': int(max_entities_total),
                    'current': total_count,
                    'message': f'Tu plan permite un máximo de {max_entities_total} entidades. Actualiza a Pro para aumentar el límite.',
                    'message_en': f'Your plan allows up to {max_entities_total} entities. Upgrade to Pro to increase the limit.',
                }), 403

        # Límite de robots - contar todos los tipos de robots
        if entity_type in ROBOT_ENTITY_TYPES and max_robots < 999999:
            robots_total = 0
            for robot_type in ROBOT_ENTITY_TYPES:
                count = _count_entities_by_type(robot_type, tenant)
                if count is not None:
                    robots_total += count
            if robots_total >= max_robots:
                return jsonify({'error': 'Robot limit exceeded', 'limit': max_robots, 'current': robots_total}), 403
        # Límite de sensores - contar todos los tipos de sensores
        if entity_type in SENSOR_ENTITY_TYPES and max_sensors < 999999:
            sensors_total = 0
            for sensor_type in SENSOR_ENTITY_TYPES:
                count = _count_entities_by_type(sensor_type, tenant)
                if count is not None:
                    sensors_total += count
            if sensors_total >= max_sensors:
                return jsonify({'error': 'Sensor limit exceeded', 'limit': max_sensors, 'current': sensors_total}), 403
        # Parcel count limit (Task 5: max_parcels limits number of parcels regardless of area)
        if entity_type in PARCEL_ENTITY_TYPES and max_parcels is not None and int(max_parcels) >= 0:
            parcels_total = 0
            for ptype in PARCEL_ENTITY_TYPES:
                count = _count_entities_by_type(ptype, tenant)
                if count is not None:
                    parcels_total += count
            if not _check_parcel_count_limit(parcels_total, max_parcels):
                return jsonify({
                    'error': 'Parcel count limit exceeded',
                    'error_en': 'Parcel count limit exceeded',
                    'limit': int(max_parcels),
                    'current': parcels_total,
                }), 403

        # Límite de superficie (ha) para parcelas
        if entity_type in PARCEL_ENTITY_TYPES and max_area < 1000000000:
            new_area = _extract_number(entity_data.get('area'))
            if new_area is None:
                new_area = 0.0
            current_area = _sum_parcel_area(entity_type, tenant)
            if (current_area + new_area) > max_area:
                return jsonify({
                    'error': 'Parcel area limit exceeded',
                    'limit_hectares': max_area,
                    'current_hectares': current_area,
                    'requested_hectares': new_area
                }), 403

        # Inject @context for NGSI-LD compliance
        entity_data['@context'] = CONTEXT_URL

        # Send to Orion-LD
        orion_url = f"{ORION_URL}/ngsi-ld/v1/entities"
        headers = {
            'Content-Type': 'application/ld+json'
        }
        headers = inject_fiware_headers(headers, g.tenant)

        response = requests.post(orion_url, json=entity_data, headers=headers)
        if response.status_code in [200, 201]:
            # Log the operation
            log_entity_operation('create', entity_id, entity_type, g.tenant, g.farmer_id,
                               {'attributes': list(entity_data.keys())})

            return jsonify({
                'message': 'Entity instance created successfully',
                'entity': entity_data,
                'tenant': g.tenant
            }), 201
        else:
            return jsonify({'error': 'Failed to create entity in Orion'}), 500

    except Exception as e:
        logger.error(f"Error creating instance: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# === Lines 972-998 from entity_management_api.py ===
@entities_bp.route('/instances/<entity_type>/<entity_id>', methods=['GET'])
@require_auth
@require_entity_ownership
def get_instance(entity_type, entity_id):
    """Get specific entity instance"""
    try:
        orion_url = f"{ORION_URL}/ngsi-ld/v1/entities/{entity_id}"
        headers = {
            'Accept': 'application/ld+json'
        }
        headers = inject_fiware_headers(headers, g.tenant)

        response = requests.get(orion_url, headers=headers)
        if response.status_code == 200:
            entity = response.json()
            return jsonify({
                'entity': entity,
                'tenant': g.tenant
            })
        elif response.status_code == 404:
            return jsonify({'error': 'Entity not found'}), 404
        else:
            return jsonify({'error': 'Failed to get entity from Orion'}), 500

    except Exception as e:
        logger.error(f"Error getting instance: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# === Lines 1000-1038 from entity_management_api.py ===
@entities_bp.route('/instances/<entity_type>/<entity_id>', methods=['PATCH'])
@require_auth
@require_entity_ownership
def update_instance(entity_type, entity_id):
    """Update specific entity instance attributes"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        # Inject @context for NGSI-LD compliance
        data['@context'] = CONTEXT_URL

        orion_url = f"{ORION_URL}/ngsi-ld/v1/entities/{entity_id}/attrs"
        headers = {
            'Content-Type': 'application/ld+json'
        }
        headers = inject_fiware_headers(headers, g.tenant)

        response = requests.patch(orion_url, json=data, headers=headers)
        if response.status_code in [200, 204]:
            # Log the operation
            log_entity_operation('update', entity_id, entity_type, g.tenant, g.farmer_id,
                               {'updated_attributes': list(data.keys())})

            return jsonify({
                'message': 'Entity updated successfully',
                'entity_id': entity_id,
                'updated_attributes': list(data.keys()),
                'tenant': g.tenant
            })
        elif response.status_code == 404:
            return jsonify({'error': 'Entity not found'}), 404
        else:
            return jsonify({'error': 'Failed to update entity in Orion'}), 500

    except Exception as e:
        logger.error(f"Error updating instance: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# === Lines 1040-1067 from entity_management_api.py ===
@entities_bp.route('/instances/<entity_type>/<entity_id>', methods=['DELETE'])
@require_auth
@require_entity_ownership
def delete_instance(entity_type, entity_id):
    """Delete specific entity instance"""
    try:
        orion_url = f"{ORION_URL}/ngsi-ld/v1/entities/{entity_id}"
        headers = {}
        headers = inject_fiware_headers(headers, g.tenant)

        response = requests.delete(orion_url, headers=headers)
        if response.status_code in [200, 204]:
            # Log the operation
            log_entity_operation('delete', entity_id, entity_type, g.tenant, g.farmer_id)

            return jsonify({
                'message': 'Entity deleted successfully',
                'entity_id': entity_id,
                'tenant': g.tenant
            })
        elif response.status_code == 404:
            return jsonify({'error': 'Entity not found'}), 404
        else:
            return jsonify({'error': 'Failed to delete entity from Orion'}), 500

    except Exception as e:
        logger.error(f"Error deleting instance: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# === Lines 2044-2101 from entity_management_api.py ===
def _resolve_urn_to_timeseries_entity_id(tenant_id: str, entity_id: str) -> tuple:
    """
    Resolve an NGSI-LD URN to (timeseries_entity_id, source).
    Returns (id, source) for success, (None, 'not_found') for 404, (None, 'no_location') for 204.
    source: 'municipality' | 'station' | 'passthrough' | 'not_found' | 'no_location'
    """
    if not entity_id or not isinstance(entity_id, str):
        return (None, 'not_found')
    entity_id = entity_id.strip()
    # Passthrough: not a URN (no urn: prefix)
    if not entity_id.lower().startswith('urn:'):
        return (entity_id, 'passthrough')

    headers = {'Accept': 'application/ld+json'}
    headers = inject_fiware_headers(headers, tenant_id)

    # Fetch entity from Orion
    orion_url = f"{ORION_URL}/ngsi-ld/v1/entities/{entity_id}"
    try:
        resp = requests.get(orion_url, headers=headers, timeout=10)
    except Exception as e:
        logger.warning(f"Orion request failed for {entity_id}: {e}")
        return (None, 'not_found')
    if resp.status_code == 404:
        return (None, 'not_found')
    if resp.status_code != 200:
        logger.warning(f"Orion returned {resp.status_code} for {entity_id}")
        return (None, 'not_found')

    entity = resp.json()
    etype = (entity.get('type') or '').strip()

    # WeatherObserved: resolve refParcel -> parcel -> municipality_code
    if etype == 'WeatherObserved':
        ref_parcel = entity.get('refParcel')
        if not ref_parcel:
            return (None, 'no_location')
        parcel_urn = ref_parcel.get('object') if isinstance(ref_parcel, dict) else ref_parcel
        if not parcel_urn:
            return (None, 'no_location')
        parcel_urn = str(parcel_urn).strip()
        parcel_resp = requests.get(
            f"{ORION_URL}/ngsi-ld/v1/entities/{parcel_urn}",
            headers=headers,
            timeout=10,
        )
        if parcel_resp.status_code != 200:
            return (None, 'no_location')
        parcel_entity = parcel_resp.json()
        res = _parcel_urn_to_municipality_code(tenant_id, parcel_urn, parcel_entity)
        return (None, 'no_location') if res is None else res

    # AgriParcel / Parcel / parcel-like: resolve by cadastral_parcels or Orion municipality
    if etype in PARCEL_ENTITY_TYPES or 'parcel' in etype.lower():
        res = _parcel_urn_to_municipality_code(tenant_id, entity_id, entity)
        return (None, 'no_location') if res is None else res

    return (None, 'no_location')


# === Lines 2104-2201 from entity_management_api.py ===
def _parcel_urn_to_municipality_code(tenant_id: str, parcel_urn: str, parcel_entity: Optional[dict] = None) -> Optional[tuple]:
    """
    Resolve parcel URN to municipality_code (INE).
    Tries: cadastral_parcels.id (UUID from URN) -> weather_location_id -> municipality_code;
    else cadastral_parcels.municipality -> catalog_municipalities.ine_code.
    """
    uuid_candidate = None
    parts = parcel_urn.split(':')
    if parts:
        last = parts[-1].strip()
        if re.match(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$', last):
            uuid_candidate = last
        elif last.startswith('parcel-'):
            uuid_candidate = last[7:].strip()
            if not re.match(r'^[0-9a-fA-F-]{36}$', uuid_candidate):
                uuid_candidate = None
    if not uuid_candidate:
        # Fallback: try to get municipality from Orion entity (address.addressLocality, etc.)
        if parcel_entity:
            addr = parcel_entity.get('address')
            if isinstance(addr, dict) and 'value' in addr:
                addr = addr['value']
            if isinstance(addr, dict):
                loc = addr.get('addressLocality') or addr.get('addressRegion') or ''
                if isinstance(loc, str) and loc.strip():
                    with get_db_connection_with_tenant(tenant_id) as conn:
                        if conn:
                            try:
                                cur = conn.cursor(cursor_factory=RealDictCursor)
                                cur.execute("""
                                    SELECT ine_code FROM catalog_municipalities
                                    WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s))
                                    LIMIT 1
                                """, (loc.strip(),))
                                row = cur.fetchone()
                                cur.close()
                                if row:
                                    return (row['ine_code'], 'municipality')
                            except Exception as e:
                                logger.debug(f"Catalog lookup for municipality name failed: {e}")
        return None

    with get_db_connection_with_tenant(tenant_id) as conn:
        if not conn:
            return None
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            # Prefer weather_location_id -> tenant_weather_locations.municipality_code
            cur.execute("""
                SELECT twl.municipality_code
                FROM cadastral_parcels cp
                LEFT JOIN tenant_weather_locations twl ON twl.id = cp.weather_location_id
                WHERE cp.id = %s::uuid AND cp.tenant_id = %s
                LIMIT 1
            """, (uuid_candidate, tenant_id))
            row = cur.fetchone()
            if row and row.get('municipality_code'):
                cur.close()
                return (row['municipality_code'], 'municipality')
            # Fallback: parcel municipality (name) -> catalog_municipalities.ine_code
            cur.execute("""
                SELECT cm.ine_code
                FROM cadastral_parcels cp
                JOIN catalog_municipalities cm ON LOWER(TRIM(cm.name)) = LOWER(TRIM(cp.municipality))
                WHERE cp.id = %s::uuid AND cp.tenant_id = %s
                LIMIT 1
            """, (uuid_candidate, tenant_id))
            row = cur.fetchone()
            cur.close()
            if row and row.get('ine_code'):
                return (row['ine_code'], 'municipality')
        except Exception as e:
            logger.debug(f"cadastral_parcels lookup failed for {uuid_candidate}: {e}")
    # No cadastral row: resolve from Orion parcel address (matches timeseries-reader)
    if parcel_entity:
        addr = parcel_entity.get('address')
        if isinstance(addr, dict) and 'value' in addr:
            addr = addr['value']
        if isinstance(addr, dict):
            loc = addr.get('addressLocality') or addr.get('addressRegion') or ''
            if isinstance(loc, str) and loc.strip():
                with get_db_connection_with_tenant(tenant_id) as conn:
                    if conn:
                        try:
                            cur = conn.cursor(cursor_factory=RealDictCursor)
                            cur.execute("""
                                SELECT ine_code FROM catalog_municipalities
                                WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s))
                                LIMIT 1
                            """, (loc.strip(),))
                            row = cur.fetchone()
                            cur.close()
                            if row:
                                return (row['ine_code'], 'municipality')
                        except Exception as e:
                            logger.debug(f'Catalog lookup for municipality name failed: {e}')
    return None


# === Lines 2204-2228 from entity_management_api.py ===
@entities_bp.route('/api/entities/<path:entity_id>/timeseries-location', methods=['GET'])
@require_auth(require_hmac=False)
def get_entity_timeseries_location(entity_id):
    """
    [Deprecated] Resolve an NGSI-LD entity URN to the timeseries key for weather_observations.
    Prefer timeseries-reader GET /api/timeseries/v2/entities/<urn>/data (unified read path).
    Returns 200 + { timeseries_entity_id, source }, 204 when entity has no location, or 404 when not found.
    """
    if not entity_id or not entity_id.strip():
        return jsonify({'error': 'entity_id is required'}), 400

    tenant_id = g.tenant
    timeseries_entity_id, source = _resolve_urn_to_timeseries_entity_id(tenant_id, entity_id.strip())

    if timeseries_entity_id is None:
        if source == 'not_found':
            return jsonify({'error': 'Entity not found'}), 404
        return '', 204

    resp = jsonify({
        'timeseries_entity_id': timeseries_entity_id,
        'source': source,
    })
    resp.headers['Deprecation'] = 'true'
    return resp, 200


# === Lines 2251-2320 from entity_management_api.py ===
@entities_bp.route('/api/entities/parents', methods=['GET'])
@require_auth
def get_parent_entities():
    """
    Get entities that can be used as parents for hierarchical relationships

    Returns entities that have Polygon/MultiPolygon geometry and can contain
    child entities (subdivisions, zones, etc.)
    """
    try:
        entity_type = request.args.get('type')  # Optional filter by type

        # Types that can be parents (have area/geometry)
        parent_types = [
            'AgriParcel', 'Parcel', 'Vineyard', 'OliveGrove',
            'AgriBuilding', 'LivestockFarm'
        ]

        if entity_type:
            parent_types = [entity_type] if entity_type in parent_types else []

        all_parents = []
        tenant = g.tenant

        for parent_type in parent_types:
            try:
                # Query Orion-LD for entities of this type
                orion_url = f"{ORION_URL}/ngsi-ld/v1/entities"
                params = {'type': parent_type}

                headers = {
                    'Accept': 'application/ld+json'
                }
                headers = inject_fiware_headers(headers, tenant)

                response = requests.get(orion_url, params=params, headers=headers, timeout=10)

                if response.status_code == 200:
                    entities = response.json()
                    if not isinstance(entities, list):
                        entities = [entities]

                    # Filter entities that have Polygon/MultiPolygon geometry
                    for entity in entities:
                        location = entity.get('location', {})
                        if isinstance(location, dict):
                            value = location.get('value', {})
                            if isinstance(value, dict):
                                geom_type = value.get('type', '')
                                # Only include entities with area (Polygon/MultiPolygon)
                                if geom_type in ['Polygon', 'MultiPolygon']:
                                    all_parents.append({
                                        'id': entity.get('id'),
                                        'type': entity.get('type'),
                                        'name': entity.get('name', {}).get('value', 'Unnamed'),
                                        'geometry': value  # Full GeoJSON geometry
                                    })
            except Exception as e:
                logger.warning(f"Error fetching {parent_type} entities: {e}")
                continue

        return jsonify({
            'entities': all_parents,
            'count': len(all_parents),
            'tenant': tenant
        }), 200

    except Exception as e:
        logger.error(f"Error getting parent entities: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# === Lines 2329-2354 from entity_management_api.py ===
def _get_next_robot_index(tenant_id: str) -> int:
    """Get next sequential robot index for tenant"""
    try:
        conn = get_db_connection_with_tenant(tenant_id)
        if not conn:
            return 1  # Default to 1 if DB unavailable

        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            # Query Orion-LD to count existing robots for this tenant
            orion_url = f"{ORION_URL}/ngsi-ld/v1/entities"
            params = {'type': 'AgriculturalRobot', 'options': 'count'}
            headers = inject_fiware_headers({'Accept': 'application/ld+json'}, tenant_id)

            response = requests.get(orion_url, params=params, headers=headers, timeout=5)
            if response.status_code == 200:
                count = response.json()
                if isinstance(count, list):
                    return len(count) + 1
                elif isinstance(count, dict) and 'count' in count:
                    return count['count'] + 1
            return 1
        finally:
            return_db_connection(conn)
    except:
        return 1


# === Lines 2357-2432 from entity_management_api.py ===
@entities_bp.route('/api/robots/provision', methods=['POST'])
@require_auth
def provision_robot():
    """
    Provision a new robot: creates NGSI-LD entity with UUID and ROS namespace.
    Network access (Headscale SDN) is provisioned separately via nkz-module-vpn
    using a hardware Claim Code printed on the device chassis.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        tenant_id = g.tenant

        # 1. Generate persistent UUID
        robot_uuid = str(uuid.uuid4())

        # 2. Generate ROS_NAMESPACE
        robot_index = _get_next_robot_index(tenant_id)
        ros_namespace = f"/{tenant_id}/robot_{robot_index:03d}"

        # 3. Build robot entity for Orion-LD
        robot_name = data.get('name', 'Robot')
        robot_location = data.get('location', {})

        robot_entity = {
            'id': f"urn:ngsi-ld:AgriculturalRobot:{tenant_id}:{robot_uuid}",
            'type': 'AgriculturalRobot',
            'name': {'type': 'Property', 'value': robot_name},
            'status': {'type': 'Property', 'value': 'offline'},
            'robotUUID': {'type': 'Property', 'value': robot_uuid},
            'rosNamespace': {'type': 'Property', 'value': ros_namespace},
            '@context': [CONTEXT_URL]
        }

        if robot_location:
            robot_entity['location'] = robot_location

        for field in ('robotType', 'model', 'manufacturer', 'serialNumber', 'icon'):
            if data.get(field):
                robot_entity[field] = {'type': 'Property', 'value': data[field]}

        if data.get('ref3DModel'):
            robot_entity['ref3DModel'] = {'type': 'Property', 'value': data['ref3DModel']}
            for sub in ('modelScale', 'modelRotation'):
                if data.get(sub):
                    robot_entity[sub] = {'type': 'Property', 'value': data[sub]}

        # 4. Create in Orion-LD
        orion_url = f"{ORION_URL}/ngsi-ld/v1/entities"
        headers = inject_fiware_headers({'Content-Type': 'application/ld+json'}, tenant_id)
        response = requests.post(orion_url, json=robot_entity, headers=headers, timeout=10)

        if response.status_code not in [201, 409]:
            logger.error(f"Failed to create robot in Orion: {response.status_code} - {response.text}")
            return jsonify({'error': 'Failed to create robot in Orion-LD', 'details': response.text}), 500

        # 5. Log operation
        log_entity_operation('create', robot_entity['id'], 'AgriculturalRobot', tenant_id, g.farmer_id, {
            'robot_uuid': robot_uuid,
            'ros_namespace': ros_namespace
        })

        return jsonify({
            'robot': robot_entity,
            'credentials': {
                'robot_uuid': robot_uuid,
                'ros_namespace': ros_namespace,
            },
            'info': 'Network access (Headscale SDN) is provisioned via the Device Management module using the Claim Code on the device chassis.'
        }), 201

    except Exception as e:
        logger.error(f"Error provisioning robot: {e}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500
