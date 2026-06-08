"""
Tenant limits, entity counting, and usage tracking helpers.
Shared by entity-manager main module and blueprints.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

import requests
from common.auth_middleware import inject_fiware_headers
from db_helper import get_db_connection_simple, return_db_connection

from helpers.constants import (
    CONTEXT_URL,
    ORION_URL,
    PARCEL_ENTITY_TYPES,
    ROBOT_ENTITY_TYPES,
    SENSOR_ENTITY_TYPES,
)

logger = logging.getLogger(__name__)

# Simple per-tenant limits cache
_limits_cache: dict = {}
_limits_cache_ts: dict = {}
_LIMITS_TTL_SECONDS = 60


def _get_limits_from_db(tenant: str):
    """Read tenant limits from PostgreSQL (tenants table — single source of truth)."""
    conn = get_db_connection_simple()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT plan_type, max_users, max_robots, max_sensors,
                   max_area_hectares, max_parcels, max_entities_total
            FROM tenants
            WHERE tenant_id = %s
        """, (tenant,))
        row = cursor.fetchone()
        cursor.close()
        if not row:
            return None
        return {
            'planType': row[0],
            'maxUsers': row[1],
            'maxRobots': row[2],
            'maxSensors': row[3],
            'maxAreaHectares': row[4],
            'maxParcels': row[5],
            'maxEntitiesTotal': row[6],
        }
    except Exception:
        return None
    finally:
        return_db_connection(conn)


def get_limits_for_tenant(tenant: str):
    now = datetime.utcnow().timestamp()
    if tenant in _limits_cache and (now - _limits_cache_ts.get(tenant, 0)) < _LIMITS_TTL_SECONDS:
        return _limits_cache[tenant]
    limits = _get_limits_from_db(tenant)
    if limits:
        _limits_cache[tenant] = limits
        _limits_cache_ts[tenant] = now
    return limits


def upsert_limits_in_orion(tenant: str, limits: dict):
    """Upsert tenant limits in PostgreSQL (tenants table — single source of truth).

    Function name kept for backward compatibility with callers.
    """
    conn = get_db_connection_simple()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE tenants SET
                plan_type = COALESCE(%s, plan_type),
                max_users = COALESCE(%s, max_users),
                max_robots = COALESCE(%s, max_robots),
                max_sensors = COALESCE(%s, max_sensors),
                max_area_hectares = COALESCE(%s, max_area_hectares),
                updated_at = NOW()
            WHERE tenant_id = %s
        """, (
            limits.get('planType'),
            limits.get('maxUsers'),
            limits.get('maxRobots'),
            limits.get('maxSensors'),
            limits.get('maxAreaHectares'),
            tenant,
        ))
        conn.commit()
        cursor.close()
        return True
    except Exception as e:
        logging.getLogger(__name__).error(f"Failed to upsert tenant limits: {e}")
        return False
    finally:
        return_db_connection(conn)


def _extract_number(value):
    """Extract a number from NGSI-LD Property payload or simple value."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        inner = value.get('value')
        if isinstance(inner, (int, float)):
            return float(inner)
        try:
            return float(inner)
        except Exception:
            return None
    try:
        return float(value)
    except Exception:
        return None


def _count_entities_by_type(entity_type, tenant):
    """Count entities of a type for a tenant via Orion-LD."""
    orion_url = f"{ORION_URL}/ngsi-ld/v1/entities"
    params = {'type': entity_type, 'limit': 1, 'count': 'true'}
    headers = {'Accept': 'application/ld+json'}
    headers = inject_fiware_headers(headers, tenant)
    resp = requests.get(orion_url, params=params, headers=headers)
    if resp.status_code != 200:
        return None
    count_header = resp.headers.get('Ngsild-Results-Count') or resp.headers.get('Content-Range')
    if count_header and '/' in count_header:
        try:
            total = count_header.split('/')[-1]
            return int(total)
        except Exception:
            pass
    try:
        data = resp.json()
        if isinstance(data, list):
            return len(data)
    except Exception:
        pass
    return None


def _count_all_entities(tenant: str) -> Optional[int]:
    """Return total entity count for a tenant via Orion-LD count header."""
    try:
        url = f"{ORION_URL}/ngsi-ld/v1/entities?limit=0&count=true"
        headers = {'Accept': 'application/json'}
        headers = inject_fiware_headers(headers, tenant)
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code not in (200, 204):
            logger.warning(f"_count_all_entities: Orion returned {resp.status_code}")
            return None
        total_str = resp.headers.get('Fiware-Total-Count', '0')
        return int(total_str)
    except Exception as e:
        logger.error(f"_count_all_entities failed: {e}")
        return None


def _check_entity_total_limit(current_count, max_total):
    """Return True if creating an entity is allowed (within aggregate cap), False if denied."""
    if max_total is None or int(max_total) < 0:
        return True
    if current_count is None:
        return True
    return current_count < int(max_total)


def _check_parcel_count_limit(current_count, max_parcels):
    """Return True if creating a parcel is allowed (within parcel count limit), False if denied."""
    if max_parcels is None or int(max_parcels) < 0:
        return True
    return current_count < int(max_parcels)


def _sum_parcel_area(entity_type, tenant):
    """Sum the area (hectares) of all parcels of a type for a tenant."""
    orion_url = f"{ORION_URL}/ngsi-ld/v1/entities"
    params = {'type': entity_type, 'limit': 1000}
    headers = {'Accept': 'application/ld+json'}
    headers = inject_fiware_headers(headers, tenant)
    total = 0.0
    page = 0
    while True:
        p = dict(params)
        p['offset'] = page * 1000
        resp = requests.get(orion_url, params=p, headers=headers)
        if resp.status_code != 200:
            break
        try:
            items = resp.json()
        except Exception:
            break
        if not items:
            break
        for ent in items:
            area_val = _extract_number(ent.get('area'))
            if area_val is not None:
                total += area_val
        if len(items) < 1000:
            break
        page += 1
    return total


def _gather_usage_for_tenant(tenant: str) -> Dict[str, Any]:
    """Compute aggregated usage statistics for a tenant."""
    robots_total = 0
    sensors_total = 0
    parcels_total = 0
    total_area = 0.0

    for entity_type in ROBOT_ENTITY_TYPES:
        count = _count_entities_by_type(entity_type, tenant)
        if isinstance(count, int) and count > 0:
            robots_total += count

    for entity_type in SENSOR_ENTITY_TYPES:
        count = _count_entities_by_type(entity_type, tenant)
        if isinstance(count, int) and count > 0:
            sensors_total += count

    for entity_type in PARCEL_ENTITY_TYPES:
        count = _count_entities_by_type(entity_type, tenant)
        if isinstance(count, int) and count > 0:
            parcels_total += count
        area_val = _sum_parcel_area(entity_type, tenant)
        if isinstance(area_val, (int, float)):
            total_area += area_val

    return {
        'robots': robots_total,
        'sensors': sensors_total,
        'parcels': parcels_total,
        'areaHectares': total_area,
    }
