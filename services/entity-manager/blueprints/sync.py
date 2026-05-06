#!/usr/bin/env python3
"""
Sync Blueprint - Extracted from entity_management_api.py
"""
import os
import sys
import json
import logging
import time
from datetime import datetime
from typing import Optional, Dict, Any

from flask import Blueprint, request, jsonify, g
from psycopg2.extras import RealDictCursor
import requests
import redis

from common.auth_middleware import require_auth
from common import inject_fiware_headers
from db_helper import get_db_connection_with_tenant, return_db_connection

# Import shared helpers from main module
from entity_management_api import ORION_URL, _extract_number, log_entity_operation

logger = logging.getLogger(__name__)

sync_bp = Blueprint('sync', __name__)


_REDIS_URL = os.getenv('REDIS_URL', 'redis://:default@redis-service:6379/0')


def _calculate_centroid(geometry):
    """Calculate simple centroid from GeoJSON geometry"""
    try:
        if not geometry or 'coordinates' not in geometry:
            return None, None

        coords = geometry['coordinates']
        points = []
        if geometry['type'] == 'Polygon':
            points = coords[0]
        elif geometry['type'] == 'MultiPolygon':
            points = coords[0][0]  # First polygon representing outer boundary
        elif geometry['type'] == 'Point':
            return coords[1], coords[0]
        else:
            return None, None

        if not points:
            return None, None

        sum_lon = 0
        sum_lat = 0
        count = 0

        for p in points:
            if len(p) >= 2:
                # Correct order for GeoJSON is [lon, lat]
                sum_lon += p[0]
                sum_lat += p[1]
                count += 1

        if count == 0:
            return None, None

        return sum_lat / count, sum_lon / count
    except Exception:
        return None, None


def _sync_str_prop(props, key, default=''):
    """Extract string from NGSI-LD entity properties dict."""
    v = props.get(key)
    val = v.get('value') if isinstance(v, dict) else v
    return str(val) if val is not None else default


def _sync_num_prop(props, key, default=0.0):
    """Extract float from NGSI-LD entity properties dict."""
    v = props.get(key)
    if isinstance(v, dict):
        v = v.get('value', default)
    try:
        return float(v) if v is not None else default
    except (ValueError, TypeError):
        return default


def _map_entity_to_mobile(ent):
    """Map NGSI-LD entity to WatermelonDB Parcel schema"""
    props = ent.copy()
    remote_id = ent.get('id')

    # Extract name
    name = 'Unknown'
    if 'name' in props:
        val = props['name']
        name = val.get('value') if isinstance(val, dict) else val

    # Extract area
    area = 0.0
    if 'area' in props:
        area = _extract_number(props['area']) or 0.0

    # Extract crop_type
    crop_type = ''
    if 'cropType' in props:
        val = props['cropType']
        crop_type = val.get('value') if isinstance(val, dict) else val

    # Extract status
    status = 'synced'

    # Extract geometry
    geometry = None
    if 'location' in props:
        val = props['location']
        if isinstance(val, dict) and 'value' in val:
            geometry = val['value']
        elif isinstance(val, dict):
            geometry = val

    # Calculate centroid
    lat, lng = _calculate_centroid(geometry)

    # Timestamps
    created_at = 0
    updated_at = 0

    # helper for timestamp
    def parse_ts(ts_val):
        try:
            val = ts_val.get('value') if isinstance(ts_val, dict) else ts_val
            if not val: return 0
            if isinstance(val, str):
                if val.endswith('Z'):
                    dt = datetime.fromisoformat(val.replace('Z', '+00:00'))
                else:
                    dt = datetime.fromisoformat(val)
                return int(dt.timestamp() * 1000)
            return 0
        except:
            return 0

    if 'createdAt' in props:
        created_at = parse_ts(props['createdAt'])

    if 'modifiedAt' in props:
        updated_at = parse_ts(props['modifiedAt'])

    # If updated_at is 0, use current time or created_at
    if updated_at == 0:
        updated_at = created_at or int(time.time() * 1000)

    # Format geometry as string for WDB
    geojson_str = json.dumps(geometry) if geometry else '{}'

    return {
        'remote_id': remote_id,
        'name': str(name) if name else '',
        'geojson': geojson_str,
        'area': float(area),
        'crop_type': str(crop_type) if crop_type else '',
        'status': status,
        'created_at': created_at,
        'updated_at': updated_at,
        'centroid_lat': lat,
        'centroid_lng': lng
    }


def _ngsi_modified_at_ms(ent):
    """Best-effort modifiedAt -> epoch ms for sync filtering."""
    props = ent if isinstance(ent, dict) else {}
    mod = props.get('modifiedAt')
    if mod is None:
        return 0
    try:
        val = mod.get('value') if isinstance(mod, dict) else mod
        if not val:
            return 0
        if isinstance(val, str):
            if val.endswith('Z'):
                dt = datetime.fromisoformat(val.replace('Z', '+00:00'))
            else:
                dt = datetime.fromisoformat(val)
            return int(dt.timestamp() * 1000)
    except Exception:
        return 0
    return 0


def _parcel_record_for_watermelon(ent):
    """NGSI-LD parcel-like entity -> WatermelonDB raw row (requires sync ``id``)."""
    rec = _map_entity_to_mobile(ent)
    rid = rec.get('remote_id')
    if not rid:
        raise ValueError('Parcel entity missing id')
    rec['id'] = str(rid)
    return rec


def _routing_line_record_for_watermelon(ent, fallback_ts):
    """NGSI-LD routing line -> WatermelonDB-compatible row with sync id."""
    remote_id = ent.get('id')
    if not remote_id:
        raise ValueError('Routing line entity missing id')
    geometry = None
    if 'location' in ent and isinstance(ent['location'], dict) and 'value' in ent['location']:
        geometry = ent['location']['value']
    elif 'location' in ent:
        geometry = ent['location']
    if not geometry:
        raise ValueError('Routing line missing location')
    name_raw = ent.get('name')
    if isinstance(name_raw, dict):
        name = name_raw.get('value', 'Route')
    else:
        name = name_raw or 'Route'
    mod_ms = _ngsi_modified_at_ms(ent)
    created_ms = mod_ms or fallback_ts
    updated_ms = mod_ms or fallback_ts
    rid = str(remote_id)
    return {
        'id': rid,
        'remote_id': rid,
        'name': str(name),
        'geojson': json.dumps(geometry),
        'status': 'synced',
        'created_at': created_ms,
        'updated_at': updated_ms,
    }


def _equipment_record_for_watermelon(ent, fallback_ts):
    """NGSI-LD AgriEquipment -> WatermelonDB equipment row."""
    remote_id = ent.get('id')
    if not remote_id:
        raise ValueError('Equipment entity missing id')
    name_raw = ent.get('name')
    name = name_raw.get('value') if isinstance(name_raw, dict) else (name_raw or 'Unknown')
    props = ent if isinstance(ent, dict) else {}

    def _str_prop(key, default=''):
        return _sync_str_prop(props, key, default)

    def _num_prop(key, default=0.0):
        return _sync_num_prop(props, key, default)

    mod_ms = _ngsi_modified_at_ms(ent)
    created_ms = mod_ms or fallback_ts
    updated_ms = mod_ms or fallback_ts
    rid = str(remote_id)

    return {
        'id': rid,
        'remote_id': rid,
        'name': str(name),
        'equipment_type': _str_prop('equipmentType', 'tractor'),
        'implement_width': _num_prop('implementWidth', 3.0),
        'status': _str_prop('status', 'available'),
        'steering_type': _str_prop('steeringType', 'ackermann'),
        'steering_axles': _str_prop('steeringAxles', 'rear'),
        'track_width': _num_prop('trackWidth'),
        'wheelbase': _num_prop('wheelbase'),
        'gps_offset_x': _num_prop('gpsOffsetX'),
        'gps_offset_y': _num_prop('gpsOffsetY'),
        'gps_offset_z': _num_prop('gpsOffsetZ'),
        'hitch_type': _str_prop('hitchType', 'none'),
        'hitch_offset_x': _num_prop('hitchOffsetX'),
        'implement_length': _num_prop('implementLength'),
        'implement_offset_x': _num_prop('implementOffsetX'),
        'created_at': created_ms,
        'updated_at': updated_ms,
    }


def _operation_record_for_watermelon(ent, fallback_ts):
    """NGSI-LD AgriParcelOperation -> WatermelonDB operations row."""
    remote_id = ent.get('id')
    if not remote_id:
        raise ValueError('Operation entity missing id')
    props = ent if isinstance(ent, dict) else {}

    def _str_prop(key, default=''):
        return _sync_str_prop(props, key, default)

    def _num_prop(key, default=0.0):
        return _sync_num_prop(props, key, default)

    def _bool_prop(key, default=False):
        v = props.get(key)
        if isinstance(v, dict):
            v = v.get('value', default)
        if v is None:
            return default
        return bool(v)

    def _json_prop(key):
        v = props.get(key)
        if isinstance(v, dict):
            v = v.get('value')
        if v is None:
            return '{}'
        return json.dumps(v) if not isinstance(v, str) else v

    mod_ms = _ngsi_modified_at_ms(ent)
    created_ms = mod_ms or fallback_ts
    updated_ms = mod_ms or fallback_ts
    rid = str(remote_id)

    started_val = _num_prop('startedAt', None) or _num_prop('plannedStartDate', None)
    started_at = int(started_val) if started_val else None

    return {
        'id': rid,
        'remote_id': rid,
        'parcel_id': _str_prop('refParcel', ''),
        'equipment_id': _str_prop('refEquipment', ''),
        'tractor_id': _str_prop('refTractor', ''),
        'implement_id': _str_prop('refImplement', ''),
        'operation_type': _str_prop('operationType', 'spraying'),
        'ab_line_geojson': _json_prop('abLine'),
        'implement_width': _num_prop('implementWidth', 24.0),
        'status': _str_prop('status', 'planned'),
        'vra_enabled': _bool_prop('vraEnabled'),
        'prescription_map': _json_prop('prescriptionMap'),
        'base_rate': _num_prop('baseRate'),
        'rate_unit': _str_prop('rateUnit', ''),
        'coverage_geojson': _json_prop('coverage'),
        'area_covered_ha': _num_prop('areaCoveredHa'),
        'started_at': started_at,
        'completed_at': None,
        'created_at': created_ms,
        'updated_at': updated_ms,
    }


def _parse_vectorial_collections():
    """
    Query ``collections=parcels`` or ``collections=parcels,equipment,operations,routing_lines``.
    Empty / missing => all (backward compatible).
    """
    raw = (request.args.get('collections') or '').strip()
    allowed = frozenset({'parcels', 'routing_lines', 'equipment', 'operations'})
    if not raw:
        return allowed
    parts = {p.strip().lower() for p in raw.split(',') if p.strip()}
    picked = parts & allowed
    return picked if picked else allowed


# === Lines 3004-3023 from entity_management_api.py ===
@sync_bp.route('/api/core/sync/vectorial', methods=['GET', 'POST'])
@require_auth
def core_vector_sync():
    """
    Offline vector sync for WatermelonDB (pull GET, push POST).

    GET: WatermelonDB shape
    ``{ "changes": { "<table>": { "created"|"updated"|"deleted" } }, "timestamp": <ms> }``.
    Each created/updated row must include string ``id`` (here: NGSI-LD entity URN).

    Query params:
    - last_pulled_at: epoch ms; rows with updated_at < this are omitted from updated lists.
    - collections: comma list ``parcels``, ``routing_lines`` (default: both).

    POST: apply local parcel changes to Orion-LD via PATCH; optional
    ``experimentalRejectedIds`` for WatermelonDB.
    """
    if request.method == 'POST':
        return _core_vector_sync_push()
    return _core_vector_sync_pull()


def _get_redis():
    """Lazy Redis connection. Returns None if Redis is unavailable."""
    try:
        return redis.Redis.from_url(
            _REDIS_URL, socket_timeout=2, socket_connect_timeout=2, decode_responses=False
        )
    except Exception:
        return None


def _sync_cache_key(tenant, collections_str, last_pulled_at):
    return f"sync:pull:tenant:{tenant}:collections:{collections_str}:since:{last_pulled_at}"


_SYNC_CACHE_TTL = 30


def _core_vector_sync_pull():
    try:
        tenant = g.tenant
        last_pulled_at = request.args.get('last_pulled_at', type=int, default=0) or 0
        current_ts = int(time.time() * 1000)
        collections = _parse_vectorial_collections()
        collections_key = ','.join(sorted(collections))

        # Try Redis cache first
        r = _get_redis()
        cache_key = _sync_cache_key(tenant, collections_key, last_pulled_at)
        if r:
            try:
                cached = r.get(cache_key)
                if cached:
                    logger.debug("sync_pull cache hit for tenant=%s collections=%s", tenant, collections_key)
                    return jsonify(json.loads(cached))
            except Exception as e:
                logger.debug("Redis cache read skipped: %s", e)

        orion_url = f"{ORION_URL}/ngsi-ld/v1/entities"
        headers = inject_fiware_headers({'Accept': 'application/ld+json'}, tenant)

        changes = {}

        if 'parcels' in collections:
            params_parcels = {
                'type': 'AgriParcel,Parcel,OliveGrove,Vineyard',
                'limit': 1000,
            }
            resp_parcels = requests.get(orion_url, params=params_parcels, headers=headers)
            updated_parcels = []
            if resp_parcels.status_code == 200:
                for ent in resp_parcels.json():
                    try:
                        mobile_ent = _parcel_record_for_watermelon(ent)
                        if mobile_ent['updated_at'] >= last_pulled_at:
                            updated_parcels.append(mobile_ent)
                    except Exception as e:
                        logger.warning(f"Error mapping parcel {ent.get('id')}: {e}")
            changes['parcels'] = {
                'created': [],
                'updated': updated_parcels,
                'deleted': [],
            }

        if 'routing_lines' in collections:
            params_routes = {'type': 'RoutingLine,AgriNavigationLine', 'limit': 1000}
            resp_routes = requests.get(orion_url, params=params_routes, headers=headers)
            updated_routes = []
            if resp_routes.status_code == 200:
                for ent in resp_routes.json():
                    try:
                        route_rec = _routing_line_record_for_watermelon(ent, current_ts)
                        if route_rec['updated_at'] >= last_pulled_at:
                            updated_routes.append(route_rec)
                    except Exception as e:
                        logger.warning(f"Error mapping route {ent.get('id')}: {e}")
            changes['routing_lines'] = {
                'created': [],
                'updated': updated_routes,
                'deleted': [],
            }

        if 'equipment' in collections:
            params_eq = {
                'type': 'AgriEquipment',
                'limit': 1000,
            }
            resp_eq = requests.get(orion_url, params=params_eq, headers=headers)
            updated_eq = []
            if resp_eq.status_code == 200:
                for ent in resp_eq.json():
                    try:
                        eq_rec = _equipment_record_for_watermelon(ent, current_ts)
                        if eq_rec['updated_at'] >= last_pulled_at:
                            updated_eq.append(eq_rec)
                    except Exception as e:
                        logger.warning(f"Error mapping equipment {ent.get('id')}: {e}")
            changes['equipment'] = {
                'created': [],
                'updated': updated_eq,
                'deleted': [],
            }

        if 'operations' in collections:
            params_ops = {
                'type': 'AgriParcelOperation',
                'limit': 1000,
            }
            resp_ops = requests.get(orion_url, params=params_ops, headers=headers)
            updated_ops = []
            if resp_ops.status_code == 200:
                for ent in resp_ops.json():
                    try:
                        op_rec = _operation_record_for_watermelon(ent, current_ts)
                        if op_rec['updated_at'] >= last_pulled_at:
                            updated_ops.append(op_rec)
                    except Exception as e:
                        logger.warning(f"Error mapping operation {ent.get('id')}: {e}")
            changes['operations'] = {
                'created': [],
                'updated': updated_ops,
                'deleted': [],
            }

        response_data = {'changes': changes, 'timestamp': current_ts}

        if r:
            try:
                r.setex(cache_key, _SYNC_CACHE_TTL, json.dumps(response_data))
            except Exception as e:
                logger.debug("Redis cache write skipped: %s", e)

        return jsonify(response_data)
    except Exception as e:
        logger.error(f"Core Vector Sync pull error: {e}")
        return jsonify({'error': str(e)}), 500


def _core_vector_sync_push():
    """
    WatermelonDB push: PATCH parcel attributes on Orion-LD.
    Does not support creating new NGSI-LD entities from the client (SDM POST TBD).
    """
    try:
        data = request.get_json(silent=True) or {}
        if 'changes' not in data:
            return jsonify({'error': 'Invalid body: missing changes'}), 400

        changes = data['changes']
        last_pulled_at = data.get('last_pulled_at', 0)
        tenant = g.tenant
        current_ts = int(time.time() * 1000)

        # Conflict detection: reject if client cursor is behind server
        r = _get_redis()
        if r and last_pulled_at:
            try:
                cursor_key = f"sync:cursor:tenant:{tenant}"
                server_cursor = int(r.get(cursor_key) or 0)
                if last_pulled_at < server_cursor:
                    return jsonify({
                        'error': {
                            'code': 'CONFLICT',
                            'message': f'Client timestamp {last_pulled_at} is behind server cursor {server_cursor}. Pull first.',
                            'server_timestamp': server_cursor,
                        }
                    }), 409
            except Exception as e:
                logger.debug("Redis cursor check skipped: %s", e)

        headers = inject_fiware_headers({'Content-Type': 'application/ld+json'}, tenant)
        # WatermelonDB expects { tableName: [recordId, ...] } (local Watermelon record ids).
        rejected_by_table = {}

        if 'parcels' in changes:
            parcels = changes['parcels']
            parcel_rejected = []

            created = parcels.get('created') or []
            if created:
                for item in created:
                    wid = item.get('id')
                    if wid:
                        parcel_rejected.append(str(wid))
                logger.info(
                    'Vectorial sync push: parcel create not supported; rejecting %d record(s)',
                    len(created),
                )

            if 'updated' in parcels:
                for item in parcels['updated']:
                    sync_id = str(item.get('id') or '')
                    entity_id = item.get('remote_id') or item.get('id')
                    if not entity_id or not str(entity_id).startswith('urn:'):
                        if sync_id:
                            parcel_rejected.append(sync_id)
                        logger.warning('Push skip: non-URN parcel id %s', entity_id)
                        continue

                    attrs = {}
                    if 'name' in item and item['name'] is not None:
                        attrs['name'] = {'type': 'Property', 'value': item['name']}
                    if 'crop_type' in item and item['crop_type'] is not None:
                        attrs['cropType'] = {'type': 'Property', 'value': item['crop_type']}

                    if not attrs:
                        continue

                    try:
                        patch_url = f"{ORION_URL}/ngsi-ld/v1/entities/{entity_id}/attrs"
                        pr = requests.patch(patch_url, json=attrs, headers=headers, timeout=30)
                        if not pr.ok:
                            logger.error(
                                'Orion PATCH failed for %s: %s %s',
                                entity_id,
                                pr.status_code,
                                pr.text[:500],
                            )
                            parcel_rejected.append(sync_id or str(entity_id))
                    except Exception as up_err:
                        logger.error('Error pushing update for %s: %s', entity_id, up_err)
                        parcel_rejected.append(sync_id or str(entity_id))

            if parcel_rejected:
                rejected_by_table['parcels'] = parcel_rejected

        body = {'status': 'success'}
        if rejected_by_table:
            body['experimentalRejectedIds'] = rejected_by_table

        # Update Redis cursor after successful push
        if r:
            try:
                r.set(f"sync:cursor:tenant:{tenant}", current_ts)
            except Exception as e:
                logger.debug("Redis cursor update skipped: %s", e)

        return jsonify(body)
    except Exception as e:
        logger.error(f"Core Vector Sync push error: {e}")
        return jsonify({'error': str(e)}), 500
