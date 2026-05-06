#!/usr/bin/env python3
# =============================================================================
# Entity Management API - Production Service
# =============================================================================

import os
import sys
import uuid
import json
import logging
import time
import secrets
from math import cos, radians
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Mapping
from urllib.parse import quote

from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify, g, Response, send_file
from flask_cors import CORS
import requests
import paho.mqtt.client as mqtt
import threading
import psycopg2
import redis
from io import BytesIO

# Configuration - All environment variables are REQUIRED for security
POSTGRES_URL = os.getenv('POSTGRES_URL')
ORION_URL = os.getenv('ORION_URL')
_REDIS_URL = os.getenv('REDIS_URL', 'redis://:default@redis-service:6379/0')

# Add common directory to path for imports
# Try multiple paths for compatibility (local dev vs container)
common_paths = [
    os.path.join(os.path.dirname(__file__), '..', 'common'),
    '/app/common',
    '/common',
    os.path.join(os.path.dirname(__file__), 'common')
]

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import audit logger
try:
    from audit_logger import audit_log, log_module_toggle, log_module_job_create, log_error
    AUDIT_LOGGER_AVAILABLE = True
except ImportError:
    AUDIT_LOGGER_AVAILABLE = False
    # Fallback functions
    def audit_log(*args, **kwargs):
        pass
    def log_module_toggle(*args, **kwargs):
        pass
    def log_module_job_create(*args, **kwargs):
        pass
    def log_error(*args, **kwargs):
        pass

# Import module health checks
# Try importing from local directory first, then from common
try:
    # Try local import first (module_health.py in same directory)
    from module_health import get_module_health
    MODULE_HEALTH_AVAILABLE = True
except ImportError:
    try:
        # Try from common directory
        from common.module_health import get_module_health
        MODULE_HEALTH_AVAILABLE = True
    except ImportError:
        MODULE_HEALTH_AVAILABLE = False
        def get_module_health(*args, **kwargs):
            return {'error': 'Module health checks not available'}

# Import audit middleware
try:
    from audit_middleware import setup_audit_middleware
    AUDIT_MIDDLEWARE_AVAILABLE = True
except ImportError:
    AUDIT_MIDDLEWARE_AVAILABLE = False
    def setup_audit_middleware(*args, **kwargs):
        pass

# Import rate limiter
try:
    from rate_limiter import rate_limit_module
    RATE_LIMITER_AVAILABLE = True
except ImportError:
    RATE_LIMITER_AVAILABLE = False
    def rate_limit_module(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

# Import parcel sync service
try:
    from parcel_sync import parcel_sync
    PARCEL_SYNC_AVAILABLE = True
except ImportError:
    PARCEL_SYNC_AVAILABLE = False
    logger.warning("Parcel sync service not available (parcel_sync.py missing)")

# Import module metrics
try:
    from module_metrics import record_module_usage, record_module_latency, record_module_error, metrics_decorator
    MODULE_METRICS_AVAILABLE = True
except ImportError:
    MODULE_METRICS_AVAILABLE = False
    def record_module_usage(*args, **kwargs):
        pass
    def record_module_latency(*args, **kwargs):
        pass
    def record_module_error(*args, **kwargs):
        pass
    def metrics_decorator(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

# Import module upload service
try:
    from module_upload_service import ModuleUploadService
    MODULE_UPLOAD_SERVICE_AVAILABLE = True
    # Import K8S namespace from module_upload_service
    from module_upload_service import K8S_NAMESPACE
except ImportError as e:
    logger.warning(f"ModuleUploadService not available: {e}")
    MODULE_UPLOAD_SERVICE_AVAILABLE = False
task_queue_paths = [
    os.path.join(os.path.dirname(__file__), '..', 'task-queue'),
    '/app/task-queue',
    '/task-queue',
    os.path.join(os.path.dirname(__file__), 'task-queue')
]

for path in common_paths:
    if os.path.exists(path):
        sys.path.insert(0, path)
        break

for path in task_queue_paths:
    if os.path.exists(path):
        sys.path.insert(0, path)
        break

# Import from common/auth_middleware (not local auth_middleware.py)
from common.auth_middleware import require_auth, inject_fiware_headers
# Import entity-specific functions from local auth_middleware if they exist
try:
    from auth_middleware import log_entity_operation, require_entity_ownership
except ImportError:
    # Fallback if local auth_middleware doesn't have these functions
    def log_entity_operation(*args, **kwargs):
        pass
    def require_entity_ownership(*args, **kwargs):
        def decorator(f):
            return f
        return decorator
from db_helper import get_db_connection_with_tenant, get_db_connection_simple, return_db_connection, set_platform_admin_context
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST


# =============================================================================
# Helper types
# =============================================================================

VisibilityRules = Mapping[str, Dict[str, List[str]]]

app = Flask(__name__)

# Setup audit middleware (automatic request logging)
if AUDIT_MIDDLEWARE_AVAILABLE:
    setup_audit_middleware(app, postgres_url=POSTGRES_URL)

# Configure CORS to allow requests from frontend
# CORS must be configured before routes to handle OPTIONS preflight
_cors_env = os.getenv('CORS_ORIGINS', 'http://localhost:3000,http://localhost:5173')
ALLOWED_ORIGINS = {o.strip() for o in _cors_env.split(',') if o.strip()}


def _get_user_roles():
    """Get user roles from Flask g (set by auth middleware)"""
    roles = g.get('roles', [])
    if not roles:
        payload = g.get('current_user', {})
        if payload:
            roles = payload.get('realm_access', {}).get('roles', [])
    return roles



CORS(
    app,
    resources={
        # Exclude /api/weather/* from Flask-CORS - we handle it manually in blueprints/weather.py
        # Only configure CORS for non-weather API routes
        r"/api/entities/*": {
            "origins": list(ALLOWED_ORIGINS),
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
            "allow_headers": ["Content-Type", "Authorization", "X-Tenant-ID", "x-tenant-id", "X-Auth-Signature"],
            "expose_headers": ["Content-Type", "Authorization", "X-Tenant-ID"],
            "supports_credentials": True,
        },
        r"/api/parcels/*": {
            "origins": list(ALLOWED_ORIGINS),
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
            "allow_headers": ["Content-Type", "Authorization", "X-Tenant-ID", "x-tenant-id", "X-Auth-Signature"],
            "expose_headers": ["Content-Type", "Authorization", "X-Tenant-ID"],
            "supports_credentials": True,
        },
        r"/api/vegetation/*": {
            "origins": list(ALLOWED_ORIGINS),
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
            "allow_headers": ["Content-Type", "Authorization", "X-Tenant-ID", "x-tenant-id", "X-Auth-Signature", "X-Source-Module"],
            "expose_headers": ["Content-Type", "Authorization", "X-Tenant-ID"],
            "supports_credentials": True,
        },
        # NOTE: We do NOT configure /api/* here because it would catch /api/weather/*
        # which we handle manually with weather_cors_preflight handler
        r"/*": {
            "origins": list(ALLOWED_ORIGINS),
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
            "allow_headers": ["Content-Type", "Authorization", "X-Tenant-ID", "x-tenant-id", "X-Auth-Signature"],
            "expose_headers": ["Content-Type", "Authorization", "X-Tenant-ID"],
            "supports_credentials": True,
        },
    },
    # Disable automatic OPTIONS handling - we handle it manually for /api/weather/*
    automatic_options=False,
)


@app.before_request
def handle_preflight():
    """Log incoming requests for debugging"""
    # Don't handle OPTIONS here - let Flask-CORS handle it
    # Log all incoming requests for debugging
    if '/api/weather' in request.path:
        logger.info(f"[before_request] Incoming request: {request.method} {request.path}, origin={request.headers.get('Origin')}, has_auth={bool(request.headers.get('Authorization'))}")


@app.before_request
def _start_timer():
    g._request_start_time = time.perf_counter()


@app.after_request
def _record_metrics(response):
    start_time = getattr(g, '_request_start_time', None)
    if start_time is not None:
        elapsed = time.perf_counter() - start_time
        endpoint = request.endpoint or request.path or 'unknown'
        REQUEST_LATENCY.labels(request.method, endpoint).observe(elapsed)
        REQUEST_COUNT.labels(request.method, endpoint, response.status_code).inc()
    
    # Ensure CORS headers for allowed origins (especially for OPTIONS requests)
    origin = request.headers.get('Origin')
    if request.method == 'OPTIONS' and '/api/weather' in request.path:
        logger.info(f"[after_request] OPTIONS request for {request.path}, endpoint={request.endpoint}, origin={origin}")
        if origin and origin in ALLOWED_ORIGINS:
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Vary'] = 'Origin'
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Tenant-ID, x-tenant-id, X-Auth-Signature'
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS, PATCH'
            response.headers['Access-Control-Max-Age'] = '86400'
            logger.info(f"[after_request] CORS headers set: {dict(response.headers)}")
        else:
            logger.warning(f"[after_request] Origin {origin} not in ALLOWED_ORIGINS")
    elif origin and origin in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Vary'] = 'Origin'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Tenant-ID, x-tenant-id, X-Auth-Signature'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS, PATCH'
    
    return response


@app.route('/metrics', methods=['GET'])
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

# Configuration
# Límites y tipos sujetos a control (valores por defecto seguros, configurables por env)
MAX_ROBOTS = int(os.getenv('MAX_ROBOTS', '999999'))
MAX_SENSORS = int(os.getenv('MAX_SENSORS', '999999'))
MAX_AREA_HECTARES = float(os.getenv('MAX_AREA_HECTARES', '1000000000'))
ROBOT_ENTITY_TYPES = set([t.strip() for t in os.getenv('ROBOT_ENTITY_TYPES', 'AgriculturalRobot').split(',') if t.strip()])
SENSOR_ENTITY_TYPES = set([t.strip() for t in os.getenv('SENSOR_ENTITY_TYPES', 'AgriSensor').split(',') if t.strip()])
PARCEL_ENTITY_TYPES = set([t.strip() for t in os.getenv('PARCEL_ENTITY_TYPES', 'AgriParcel,Parcel,Vineyard,OliveGrove,vineyard,olive_grove').split(',') if t.strip()])
ENTITY_BASE_PATH = os.getenv('ENTITY_BASE_PATH', '/app/config/entities')
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
# Get URLs from config manager or construct from PRODUCTION_DOMAIN
try:
    from common.config_manager import ConfigManager
    KEYCLOAK_PUBLIC_URL = ConfigManager.get_keycloak_public_url()
    CONTEXT_URL = os.getenv('CONTEXT_URL', '')
    if not CONTEXT_URL:
        domain = ConfigManager.get_production_domain()
        CONTEXT_URL = f'https://{domain}/ngsi-ld-context.json'
except ImportError:
    # Fallback if config_manager not available
    PRODUCTION_DOMAIN = os.getenv('PRODUCTION_DOMAIN', '')
    KEYCLOAK_PUBLIC_URL = os.getenv('KEYCLOAK_PUBLIC_URL', f'https://{PRODUCTION_DOMAIN}/auth' if PRODUCTION_DOMAIN else '').rstrip('/')
    CONTEXT_URL = os.getenv('CONTEXT_URL', f'https://{PRODUCTION_DOMAIN}/ngsi-ld-context.json' if PRODUCTION_DOMAIN else '')
KEYCLOAK_REALM = os.getenv('KEYCLOAK_REALM', 'nekazari')
# MQTT Configuration for device commands
MQTT_HOST = os.getenv('MQTT_HOST', 'mosquitto-service')
MQTT_PORT = int(os.getenv('MQTT_PORT', '1883'))
MQTT_USERNAME = os.getenv('MQTT_USERNAME', '')
MQTT_PASSWORD = os.getenv('MQTT_PASSWORD', '')

REQUEST_LATENCY = Histogram(
    'entity_manager_request_latency_seconds',
    'Latencia de las peticiones HTTP en entity-manager',
    ['method', 'endpoint']
)
REQUEST_COUNT = Counter(
    'entity_manager_requests_total',
    'Total de peticiones HTTP en entity-manager',
    ['method', 'endpoint', 'http_status']
)

# Set logging level
logging.getLogger().setLevel(getattr(logging, LOG_LEVEL))

# Cache sencillo de límites por tenant
_limits_cache = {}
_limits_cache_ts = {}
_LIMITS_TTL_SECONDS = 60

def _ensure_tenant_limits_table():
    """Create tenant_limits table if it does not exist (PostgreSQL, not Orion-LD)."""
    conn = get_db_connection_simple()
    if not conn:
        return
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_platform.tenant_limits (
                tenant_id VARCHAR(128) PRIMARY KEY,
                plan_type VARCHAR(64),
                max_users INTEGER,
                max_robots INTEGER,
                max_sensors INTEGER,
                max_area_hectares REAL,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
        cursor.close()
    except Exception as e:
        logging.getLogger(__name__).warning(f"tenant_limits table init: {e}")
    finally:
        return_db_connection(conn)

_ensure_tenant_limits_table()


def _get_limits_from_db(tenant: str):
    """Read tenant limits from PostgreSQL (admin_platform.tenant_limits + tenants)."""
    conn = get_db_connection_simple()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT tl.plan_type, tl.max_users, tl.max_robots, tl.max_sensors,
                   tl.max_area_hectares, t.max_parcels, t.max_entities_total
            FROM admin_platform.tenant_limits tl
            LEFT JOIN tenants t ON t.tenant_id = tl.tenant_id
            WHERE tl.tenant_id = %s
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
    """Upsert tenant limits in PostgreSQL (name kept for backward compatibility with callers)."""
    conn = get_db_connection_simple()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO admin_platform.tenant_limits (tenant_id, plan_type, max_users, max_robots, max_sensors, max_area_hectares, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (tenant_id) DO UPDATE SET
                plan_type = COALESCE(EXCLUDED.plan_type, admin_platform.tenant_limits.plan_type),
                max_users = COALESCE(EXCLUDED.max_users, admin_platform.tenant_limits.max_users),
                max_robots = COALESCE(EXCLUDED.max_robots, admin_platform.tenant_limits.max_robots),
                max_sensors = COALESCE(EXCLUDED.max_sensors, admin_platform.tenant_limits.max_sensors),
                max_area_hectares = COALESCE(EXCLUDED.max_area_hectares, admin_platform.tenant_limits.max_area_hectares),
                updated_at = NOW()
        """, (
            tenant,
            limits.get('planType'),
            limits.get('maxUsers'),
            limits.get('maxRobots'),
            limits.get('maxSensors'),
            limits.get('maxAreaHectares'),
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
    """Extrae un número desde payload NGSI-LD (Property) o valor simple."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        # NGSI-LD Property {"type":"Property","value": X}
        inner = value.get('value')
        if isinstance(inner, (int, float)):
            return float(inner)
        # También aceptar cadenas numéricas
        try:
            return float(inner)
        except Exception:
            return None
    try:
        return float(value)
    except Exception:
        return None


def _count_entities_by_type(entity_type, tenant):
    """Cuenta entidades de un tipo para un tenant vía Orion-LD."""
    orion_url = f"{ORION_URL}/ngsi-ld/v1/entities"
    params = {'type': entity_type, 'limit': 1, 'count': 'true'}
    headers = {'Accept': 'application/ld+json'}
    headers = inject_fiware_headers(headers, tenant)
    # Solicitar cabecera Ngsild-Results-Count si Orion la soporta
    resp = requests.get(orion_url, params=params, headers=headers)
    if resp.status_code != 200:
        return None
    # Orion-LD suele devolver Link/Content-Range; intentamos con Content-Range
    count_header = resp.headers.get('Ngsild-Results-Count') or resp.headers.get('Content-Range')
    if count_header and '/' in count_header:
        try:
            total = count_header.split('/')[-1]
            return int(total)
        except Exception:
            pass
    # Fallback: contar elementos del body (si no hay muchísimos)
    try:
        data = resp.json()
        if isinstance(data, list):
            return len(data)
    except Exception:
        pass
    return None

def _count_all_entities(tenant: str) -> Optional[int]:
    """Return total entity count for a tenant via Orion-LD count header. Returns None on failure."""
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
        return True  # Can't verify count, allow
    return current_count < int(max_total)


def _check_parcel_count_limit(current_count, max_parcels):
    """Return True if creating a parcel is allowed (within parcel count limit), False if denied."""
    if max_parcels is None or int(max_parcels) < 0:
        return True
    return current_count < int(max_parcels)


def _sum_parcel_area(entity_type, tenant):
    """Suma el área (hectáreas) de todas las parcelas de un tipo para un tenant."""
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

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'service': 'entity-manager'
    })

@app.route('/api/sensors/register', methods=['POST'])
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

            # Check if sensor already exists
            cur.execute("""
                SELECT id, external_id, name FROM sensors
                WHERE tenant_id = %s AND external_id = %s
            """, (tenant_id, external_id))

            existing = cur.fetchone()
            if existing:
                cur.close()
                conn.close()
                return jsonify({
                    'error': f'Sensor with external_id "{external_id}" already exists',
                    'sensor': {
                        'id': str(existing['id']),
                        'external_id': existing['external_id'],
                        'name': existing['name']
                    }
                }), 409

            # Prepare metadata
            metadata = data.get('metadata', {})
            if data.get('station_id'):
                metadata['group'] = data['station_id']
                metadata['station_id'] = data['station_id']

            import json
            metadata_json = json.dumps(metadata)

            # =============================================================================
            # STEP 1: Create NGSI-LD entity in Orion-LD (FIRST - before Postgres INSERT)
            # =============================================================================
            orion_entity_id = f"urn:ngsi-ld:{sdm_entity_type}:{tenant_id}:{external_id}"

            orion_entity = {
                '@context': [CONTEXT_URL],
                'id': orion_entity_id,
                'type': sdm_entity_type,
                'name': {'type': 'Property', 'value': name},
                'location': {
                    'type': 'GeoProperty',
                    'value': {'type': 'Point', 'coordinates': [lon, lat]}
                },
                'externalId': {'type': 'Property', 'value': external_id},
                'sensorType': {'type': 'Property', 'value': profile_code}
            }

            if metadata:
                orion_entity['metadata'] = {'type': 'Property', 'value': metadata}
            if data.get('is_under_canopy'):
                orion_entity['isUnderCanopy'] = {'type': 'Property', 'value': True}
            if data.get('station_id'):
                orion_entity['stationId'] = {'type': 'Property', 'value': data['station_id']}

            orion_headers = {
                'Content-Type': 'application/ld+json',
                'Fiware-Service': tenant_id,
                'Fiware-ServicePath': '/'
            }
            orion_url = f"{ORION_URL}/ngsi-ld/v1/entities"

            orion_entity_created = False
            orion_response = requests.post(orion_url, json=orion_entity, headers=orion_headers, timeout=10)
            if orion_response.status_code in [200, 201]:
                orion_entity_created = True
                logger.info(f"Created Orion-LD entity {orion_entity_id} for sensor {external_id}")
            elif orion_response.status_code == 409:
                orion_entity_created = True
                logger.info(f"Orion-LD entity {orion_entity_id} already exists for sensor {external_id}")
            else:
                cur.close()
                conn.close()
                logger.error(f"Failed to create Orion-LD entity for sensor {external_id}: {orion_response.status_code} - {orion_response.text}")
                return jsonify({'error': 'Failed to create sensor entity in context broker'}), 502

            # =============================================================================
            # STEP 2: INSERT sensor into Postgres (SECOND - after Orion-LD success)
            # =============================================================================
            cur.execute("""
                INSERT INTO sensors (
                    tenant_id, external_id, profile_id, name,
                    installation_location, is_under_canopy, metadata
                )
                VALUES (
                    %s, %s, %s, %s,
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                    %s, %s::jsonb
                )
                RETURNING id, external_id, name, created_at
            """, (
                tenant_id, external_id, profile_id, name,
                lon, lat,
                data.get('is_under_canopy', False),
                metadata_json
            ))

            sensor_row = cur.fetchone()
            conn.commit()
            cur.close()

            conn.close()

            # =============================================================================
            # STEP 3: Create MQTT credentials for the device
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
            # STEP 4: Configure IoT Agent for this device
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
                    'id': str(sensor_row['id']),
                    'external_id': sensor_row['external_id'],
                    'name': sensor_row['name'],
                    'profile': profile_code,
                    'tenant_id': tenant_id,
                    'created_at': sensor_row['created_at'].isoformat()
                },
                'message': 'Sensor registered successfully'
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
            conn.rollback()
            conn.close()
            logger.error(f"Error registering sensor: {e}")
            # Best-effort cleanup of Orion-LD entity if it was created
            if orion_entity_created and orion_entity_id:
                try:
                    requests.delete(
                        f"{ORION_URL}/ngsi-ld/v1/entities/{orion_entity_id}",
                        headers=orion_headers, timeout=5
                    )
                    logger.info(f"Cleaned up Orion-LD entity {orion_entity_id} after Postgres failure")
                except Exception as cleanup_error:
                    logger.critical(
                        f"INCONSISTENCY: Orion-LD entity {orion_entity_id} exists "
                        f"but Postgres operation failed and cleanup also failed: {cleanup_error}"
                    )
            return jsonify({
                'error': f'Database error: {str(e)}'
            }), 500
    
    except Exception as e:
        logger.error(f"Error in register_sensor: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/sensors/profiles', methods=['GET'])
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


@app.route('/api/sensors/profiles/status', methods=['GET'])
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


@app.route('/api/sensors', methods=['GET'])
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


def _normalize_device_id(device_id: str) -> str:
    """Extract short device ID from NGSI-LD URN if needed.

    'urn:ngsi-ld:AgriSensor:abc123' -> 'abc123'
    'abc123' -> 'abc123'
    """
    if device_id and ':' in device_id:
        return device_id.rsplit(':', 1)[-1]
    return device_id


@app.route('/api/devices/<device_id>/telemetry', methods=['GET'])
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


@app.route('/api/devices/<device_id>/telemetry/latest', methods=['GET'])
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


@app.route('/api/devices/<device_id>/telemetry/stats', methods=['GET'])
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


@app.route('/api/devices/<device_id>/commands', methods=['POST'])
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


@app.route('/api/devices/<device_id>/commands', methods=['GET'])
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


# NOTE: WireGuard VPN endpoints removed (2026-02-21).
# Network provisioning is now handled by nkz-network-controller (nkz-module-vpn).
# Use POST /api/vpn/devices/claim for ZTP via Claim Code.



@app.route('/version', methods=['GET'])
def version():
    """Get service version"""
    return jsonify({
        'service': 'entity-manager',
        'version': '1.0.0',
        'timestamp': datetime.utcnow().isoformat()
    })


# =============================================================================
# Weather Data Endpoints
# =============================================================================






















# =============================================================================
# Terms and Conditions Management Endpoints



# =============================================================================
# Heartbeat / Connection Status Check
# =============================================================================

@app.route('/api/heartbeat/check', methods=['GET'])
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


# =============================================================================
# Module Federation Registry Endpoints
# =============================================================================

@app.route('/api/modules/me', methods=['GET'])
@require_auth(require_hmac=False)  # Frontend endpoint, no HMAC required
def get_tenant_modules():
    """
    Get active modules for the current tenant.
    Returns list of modules with remote entry URLs and federation configuration.
    """
    tenant_id = getattr(g, 'tenant_id', None) or getattr(g, 'tenant', None)
    # Extract roles from multiple possible sources
    user_roles = _get_user_roles()
    
    logger.info(f"[get_tenant_modules] tenant_id={tenant_id}, user_roles={user_roles}")
    
    try:
        with get_db_connection_with_tenant(tenant_id) as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            
            # Query: Get enabled modules for tenant, filtered by user roles
            # Includes new columns route_path, label, is_local with fallback to metadata
            # PlatformAdmin can see all modules regardless of required_roles
            is_platform_admin = 'PlatformAdmin' in user_roles
            
            if is_platform_admin:
                # PlatformAdmin sees all installed modules
                query = """
                    SELECT DISTINCT
                        mm.id,
                        mm.name,
                        mm.display_name,
                        mm.remote_entry_url as "remoteEntry",
                        mm.scope,
                        mm.exposed_module as "module",
                        mm.version,
                        mm.icon_url,
                        mm.route_path,
                        mm.label,
                        COALESCE(mm.is_local, false) as is_local,
                        mm.metadata,
                        tim.is_enabled,
                        tim.configuration as tenant_config
                    FROM marketplace_modules mm
                    INNER JOIN tenant_installed_modules tim ON mm.id = tim.module_id
                    WHERE tim.tenant_id = %s
                        AND tim.is_enabled = true
                        AND mm.is_active = true
                    ORDER BY mm.display_name
                """
                cur.execute(query, (tenant_id,))
            else:
                # Regular users see modules filtered by required_roles
                query = """
                    SELECT DISTINCT
                        mm.id,
                        mm.name,
                        mm.display_name,
                        mm.remote_entry_url as "remoteEntry",
                        mm.scope,
                        mm.exposed_module as "module",
                        mm.version,
                        mm.icon_url,
                        mm.route_path,
                        mm.label,
                        COALESCE(mm.is_local, false) as is_local,
                        mm.metadata,
                        tim.is_enabled,
                        tim.configuration as tenant_config
                    FROM marketplace_modules mm
                    INNER JOIN tenant_installed_modules tim ON mm.id = tim.module_id
                    WHERE tim.tenant_id = %s
                        AND tim.is_enabled = true
                        AND mm.is_active = true
                        AND (
                            mm.required_roles IS NULL
                            OR mm.required_roles = '{}'::text[]
                            OR mm.required_roles && %s::text[]
                        )
                    ORDER BY mm.display_name
                """
                cur.execute(query, (tenant_id, user_roles))
            rows = cur.fetchall()
            
            # Transform to expected format
            modules = []
            for row in rows:
                metadata = row.get('metadata') or {}
                tenant_config = row.get('tenant_config') or {}
                
                # Use explicit columns with fallback to metadata for backwards compatibility
                route_path = row.get('route_path') or metadata.get('routePath') or tenant_config.get('routePath') or f"/{row['name']}"
                label = row.get('label') or metadata.get('label') or row['display_name']
                icon = metadata.get('icon') or row.get('icon_url')
                
                module_data = {
                    'id': row['id'],
                    'name': row['name'],
                    'displayName': row['display_name'],
                    'isLocal': row.get('is_local', False),
                    'remoteEntry': row.get('remoteEntry') or None,
                    'scope': row.get('scope') or None,
                    'module': row.get('module') or None,
                    'version': row.get('version') or '1.0.0',
                    'routePath': route_path,
                    'label': label,
                    'icon': icon,
                    'moduleType': row.get('module_type', 'ADDON_FREE'),
                    'metadata': metadata,
                    'tenantConfig': tenant_config
                }
                
                # Add navigation items if present in metadata
                if 'navigationItems' in metadata:
                    module_data['navigationItems'] = metadata['navigationItems']
                
                modules.append(module_data)
            
            cur.close()
        
        logger.info(f"Returning {len(modules)} modules for tenant {tenant_id}")
        return jsonify(modules), 200
        
    except Exception as e:
        logger.error(f"Error fetching tenant modules: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500


def _dispatch_module_lifecycle_webhook_if_configured(module_id, tenant_id, enabled, user_email=None):
    """Fire-and-forget: POST lifecycle event to a module's webhook if configured.

    The webhook URL is read from marketplace_modules.metadata.
    The HMAC secret comes from an env var (never stored in the DB).
    Follows the same HMAC-SHA256 pattern used by risk-orchestrator.
    """
    import hmac
    import hashlib

    try:
        with get_db_connection_with_tenant(tenant_id) as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT metadata->>'lifecycle_webhook_url' AS webhook_url
                FROM marketplace_modules WHERE id = %s
            """, (module_id,))
            row = cur.fetchone()
            cur.close()

        if not row or not row.get('webhook_url'):
            return

        url = row['webhook_url']
        secret = os.environ.get('MODULE_LIFECYCLE_WEBHOOK_SECRET', '')

        payload = json.dumps({
            'event': 'module.enabled' if enabled else 'module.disabled',
            'tenant_id': tenant_id,
            'module_id': module_id,
            'user_email': user_email,
            'timestamp': datetime.utcnow().isoformat(),
        })

        headers = {'Content-Type': 'application/json'}
        if secret:
            sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
            headers['X-Nekazari-Signature'] = f'sha256={sig}'

        resp = requests.post(url, data=payload, headers=headers, timeout=10)
        logger.info(f"[lifecycle_webhook] POST {url} for module={module_id} tenant={tenant_id} -> {resp.status_code}")

    except Exception as exc:
        logger.warning(f"[lifecycle_webhook] Failed for module={module_id} tenant={tenant_id}: {exc}")


@app.route('/api/modules/<module_id>/toggle', methods=['POST'])
@require_auth(require_hmac=False)  # Frontend endpoint, no HMAC required
def toggle_module(module_id):
    """
    Toggle module installation for current tenant.
    Only TenantAdmin and PlatformAdmin can manage modules.
    """
    tenant_id = getattr(g, 'tenant_id', None) or getattr(g, 'tenant', None)
    user_roles = _get_user_roles()
    
    # Log user roles for debugging
    logger.info(f"[toggle_module] Initial check - tenant_id={tenant_id}, user_roles={user_roles}, has_PlatformAdmin={'PlatformAdmin' in user_roles}")
    
    # Check permissions
    if 'TenantAdmin' not in user_roles and 'PlatformAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions. TenantAdmin or PlatformAdmin required.'}), 403
    
    try:
        data = request.json or {}
        is_enabled = data.get('enabled', True)
        username = getattr(g, 'user', None) or getattr(g, 'current_user', {}).get('preferred_username', 'unknown')
        
        with get_db_connection_with_tenant(tenant_id) as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            
            # Get module details with governance fields
            cur.execute("""
                SELECT id, name, display_name, required_plan_level, is_active
                FROM marketplace_modules
                WHERE id = %s
            """, (module_id,))
            module = cur.fetchone()
            
            if not module:
                cur.close()
                return jsonify({'error': 'Module not found'}), 404
            
            # Check if module is active
            if not module['is_active']:
                cur.close()
                return jsonify({'error': 'Module is not active in marketplace'}), 403
            
            # Validate tenant can install this module (if installing, not uninstalling)
            if is_enabled:
                is_platform_admin = 'PlatformAdmin' in user_roles
                logger.info(f"[toggle_module] module_id={module_id}, user_roles={user_roles}, "
                            f"is_platform_admin={is_platform_admin}, "
                            f"required_plan_level={module.get('required_plan_level')}")

                if not is_platform_admin:
                    cur.execute("SELECT plan_level FROM tenants WHERE tenant_id = %s", (tenant_id,))
                    tenant_row = cur.fetchone()
                    tenant_level = (tenant_row or {}).get('plan_level', 0) or 0
                    required_level = module.get('required_plan_level') or 0

                    from entity_manager_gating import can_tenant_install_module
                    if not can_tenant_install_module(tenant_level, required_level):
                        cur.close()
                        from common.tier_quotas import LEVEL_TO_TIER
                        tenant_tier = LEVEL_TO_TIER.get(tenant_level, 'basic')
                        required_tier = LEVEL_TO_TIER.get(required_level, 'basic')
                        return jsonify({
                            'error': 'Plan insuficiente para instalar este módulo',
                            'error_en': 'Insufficient plan to install this module',
                            'reason': f'Este módulo requiere plan {required_tier}. Tu plan actual: {tenant_tier}.',
                            'reason_en': f'This module requires {required_tier} plan. Your current plan: {tenant_tier}.',
                            'required_plan': required_tier,
                            'current_plan': tenant_tier,
                            'action_required': 'upgrade_plan',
                        }), 403
            
            # Check if installation exists
            cur.execute("""
                SELECT id, is_enabled FROM tenant_installed_modules
                WHERE tenant_id = %s AND module_id = %s
            """, (tenant_id, module_id))
            installation = cur.fetchone()
            
            if installation:
                # Update existing installation
                cur.execute("""
                    UPDATE tenant_installed_modules
                    SET is_enabled = %s, updated_at = NOW()
                    WHERE tenant_id = %s AND module_id = %s
                    RETURNING id
                """, (is_enabled, tenant_id, module_id))
            else:
                # Create new installation
                cur.execute("""
                    INSERT INTO tenant_installed_modules (tenant_id, module_id, is_enabled, installed_by)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                """, (tenant_id, module_id, is_enabled, username))
            
            conn.commit()
            cur.close()
        
        # Audit log
        if AUDIT_LOGGER_AVAILABLE:
            try:
                # Get tenant_plan_type if available (only set when enabling)
                plan_type = None
                if is_enabled:
                    try:
                        limits = get_limits_for_tenant(tenant_id) or {}
                        plan_type = limits.get('planType') or 'basic'
                    except:
                        pass
                
                log_module_toggle(
                    module_id=module_id,
                    enabled=is_enabled,
                    tenant_plan_type=plan_type,
                )
            except Exception as audit_err:
                logger.warning(f"Failed to log audit event: {audit_err}")
        
        action = 'enabled' if is_enabled else 'disabled'
        logger.info(f"Module {module_id} {action} for tenant {tenant_id} by {username}")
        
        # Dispatch lifecycle webhook if module has one configured
        user_email = None
        try:
            payload = getattr(g, 'current_user', {}) or {}
            user_email = payload.get('email') or payload.get('preferred_username')
        except Exception:
            pass
        _dispatch_module_lifecycle_webhook_if_configured(
            module_id=module_id,
            tenant_id=tenant_id,
            enabled=is_enabled,
            user_email=user_email,
        )
        
        return jsonify({
            'message': f'Module {action} successfully',
            'moduleId': module_id,
            'enabled': is_enabled
        }), 200
        
    except Exception as e:
        logger.error(f"Error toggling module: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500


@app.route('/api/modules/marketplace', methods=['GET'])
@require_auth(require_hmac=False)  # Frontend endpoint, no HMAC required
def get_marketplace_modules():
    """
    Get all available modules from marketplace.
    PlatformAdmin can see all, others see only active modules.
    """
    user_roles = _get_user_roles()
    is_platform_admin = 'PlatformAdmin' in user_roles
    
    logger.info(f"[get_marketplace_modules] user_roles={user_roles}, is_platform_admin={is_platform_admin}")
    
    try:
        conn = get_db_connection_simple()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get tenant plan level
        tenant_id = getattr(g, 'tenant', None)
        plan_level = 0
        if tenant_id and not is_platform_admin:
            cur.execute("SELECT plan_level FROM tenants WHERE tenant_id = %s", (tenant_id,))
            tenant_row = cur.fetchone()
            if tenant_row:
                plan_level = tenant_row['plan_level']

        # PlatformAdmin sees all, others see only active modules
        query = """
            SELECT id, name, display_name, description, version, author,
                   category, icon_url, is_active, required_roles, metadata,
                   required_plan_level, created_at, updated_at
            FROM marketplace_modules
        """
        if not is_platform_admin:
            query += " WHERE is_active = true "
        query += " ORDER BY display_name"
        cur.execute(query)
        
        modules = cur.fetchall()
        cur.close()
        return_db_connection(conn)
        
        return jsonify([dict(m) for m in modules]), 200
        
    except Exception as e:
        logger.error(f"Error fetching marketplace modules: {e}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500


@app.route('/api/modules/<module_id>/activate', methods=['POST'])
@require_auth(require_hmac=False)  # Frontend endpoint, no HMAC required
def activate_marketplace_module(module_id):
    """
    Activate or deactivate a module in the marketplace.
    Only PlatformAdmin can activate/deactivate modules globally.
    This controls module visibility for all tenants.
    """
    user_roles = _get_user_roles()
    
    # Check permissions - only PlatformAdmin
    if 'PlatformAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions. PlatformAdmin required.'}), 403
    
    try:
        data = request.json or {}
        is_active = data.get('active', True)
        
        conn = get_db_connection_simple()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Check if module exists
        cur.execute("""
            SELECT id, name, display_name, is_active FROM marketplace_modules 
            WHERE id = %s
        """, (module_id,))
        module = cur.fetchone()
        
        if not module:
            cur.close()
            return_db_connection(conn)
            return jsonify({'error': 'Module not found'}), 404
        
        # Update is_active status
        cur.execute("""
            UPDATE marketplace_modules
            SET is_active = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING id, name, display_name, is_active
        """, (is_active, module_id))
        
        updated_module = cur.fetchone()
        conn.commit()
        cur.close()
        return_db_connection(conn)
        
        action = 'activated' if is_active else 'deactivated'
        username = getattr(g, 'current_user', {}).get('preferred_username', 'unknown') if hasattr(g, 'current_user') else 'unknown'
        logger.info(f"Module {module_id} ({updated_module['display_name']}) {action} in marketplace by {username}")
        
        return jsonify({
            'message': f'Module {action} successfully',
            'moduleId': module_id,
            'active': is_active,
            'module': dict(updated_module)
        }), 200
        
    except Exception as e:
        logger.error(f"Error activating/deactivating marketplace module: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500


@app.route('/api/modules/<module_id>/can-install', methods=['GET'])
@require_auth(require_hmac=False)  # Frontend endpoint, no HMAC required
def can_install_module(module_id):
    """
    Check if current tenant can install a module.
    Validates:
    1. Module exists and is active
    2. Tenant plan_type meets required_plan_type
    3. Module type restrictions (CORE always available, etc.)
    
    Returns: {can_install: bool, reason: str, module: {...}}
    """
    tenant_id = getattr(g, 'tenant_id', None) or getattr(g, 'tenant', None)
    user_roles = _get_user_roles()
    
    try:
        # Get tenant plan_type from Orion-LD (source of truth for limits)
        limits = get_limits_for_tenant(tenant_id) or {}
        tenant_plan_type = limits.get('planType') or 'basic'  # Default to basic if not set
        
        # Get tenant plan_type and plan_level from PostgreSQL
        conn = get_db_connection_simple()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT plan_type, plan_level FROM tenants WHERE tenant_id = %s", (tenant_id,))
        tenant_row = cur.fetchone()
        if tenant_row:
            if tenant_row.get('plan_type'):
                tenant_plan_type = tenant_row['plan_type']
            tenant_level_db = tenant_row.get('plan_level') or 0
        else:
            tenant_level_db = 0

        # Get module details
        cur.execute("""
            SELECT id, name, display_name, required_plan_level, is_active, category
            FROM marketplace_modules
            WHERE id = %s
        """, (module_id,))
        module = cur.fetchone()
        cur.close()
        return_db_connection(conn)
        
        if not module:
            return jsonify({
                'can_install': False,
                'reason': 'Module not found',
                'module': None
            }), 404
        
        # Module is not active
        if not module['is_active']:
            return jsonify({
                'can_install': False,
                'reason': 'Module is not active in marketplace',
                'module': dict(module)
            }), 200
        
        user_roles = _get_user_roles()
        is_platform_admin = 'PlatformAdmin' in user_roles

        # PlatformAdmin can install any module regardless of plan requirements
        if is_platform_admin:
            return jsonify({
                'can_install': True,
                'reason': 'PlatformAdmin - can install any module',
                'module': dict(module),
                'tenant_plan': tenant_plan_type
            }), 200

        # Check required_plan_level using canonical gating
        required_level = module.get('required_plan_level') or 0
        from entity_manager_gating import can_tenant_install_module
        from common.tier_quotas import LEVEL_TO_TIER

        if not can_tenant_install_module(tenant_level_db, required_level):
            required_tier = LEVEL_TO_TIER.get(required_level, 'basic')
            current_tier = LEVEL_TO_TIER.get(tenant_level_db, 'basic')
            return jsonify({
                'can_install': False,
                'reason': f'El módulo requiere plan {required_tier}, el tenant tiene plan {current_tier}',
                'reason_en': f'Module requires {required_tier} plan, tenant has {current_tier} plan',
                'message': f'Para instalar este módulo necesitas actualizar tu plan de {current_tier} a {required_tier}. Contacta con el administrador de la plataforma.',
                'message_en': f'To install this module you need to upgrade your plan from {current_tier} to {required_tier}. Contact the platform administrator.',
                'module': dict(module),
                'tenant_plan': current_tier,
                'required_plan': required_tier,
                'action_required': 'upgrade_plan'
            }), 200

        # All checks passed
        return jsonify({
            'can_install': True,
            'reason': 'Module can be installed',
            'module': dict(module),
            'tenant_plan': tenant_plan_type
        }), 200
        
    except Exception as e:
        logger.error(f"Error checking module installation eligibility: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500


# =============================================================================
# Tenant module visibility (UI-only, per-tenant)
# =============================================================================


def _get_tenant_module_visibility(tenant_id: str) -> Dict[str, Dict[str, List[str]]]:
    """Return visibility rules for a tenant.

    Structure:
      { "<module_id>": { "hiddenRoles": ["Farmer", ...] } }

    If the auxiliary table doesn't exist yet, returns an empty mapping so that
    the feature is effectively disabled without breaking the service.
    """
    if not tenant_id:
        return {}

    try:
        with get_db_connection_with_tenant(tenant_id) as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """
                SELECT module_id, hidden_roles
                FROM tenant_module_visibility
                WHERE tenant_id = %s
                """,
                (tenant_id,),
            )
            rows = cur.fetchall()
            cur.close()
    except Exception as exc:
        # Fail-safe when the table is not present yet (no migration applied)
        msg = str(exc)
        if 'tenant_module_visibility' in msg or 'relation "tenant_module_visibility"' in msg:
            logger.warning(
                "[tenant_visibility] tenant_module_visibility table not found, "
                "returning empty visibility rules"
            )
            return {}
        logger.error(f"[tenant_visibility] Unexpected error fetching visibility rules: {exc}")
        return {}

    rules: Dict[str, Dict[str, List[str]]] = {}
    for row in rows:
        module_id = row.get('module_id')
        if not module_id:
            continue
        hidden_roles = row.get('hidden_roles') or []
        # Normalise to list of strings
        if not isinstance(hidden_roles, list):
            hidden_roles = list(hidden_roles)
        rules[str(module_id)] = {'hiddenRoles': [str(r) for r in hidden_roles]}
    return rules


@app.route('/api/modules/visibility', methods=['GET'])
@require_auth(require_hmac=False)
def get_modules_visibility():
    """Get UI visibility rules for modules in the current tenant.

    Only TenantAdmin and PlatformAdmin can manage visibility rules. For now this
    is a purely UI-level feature: backend access remains governed by required_roles.
    """
    tenant_id = getattr(g, 'tenant_id', None) or getattr(g, 'tenant', None)
    user_roles = _get_user_roles() or []

    if 'TenantAdmin' not in user_roles and 'PlatformAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions. TenantAdmin or PlatformAdmin required.'}), 403

    rules = _get_tenant_module_visibility(tenant_id)
    return jsonify(rules), 200


@app.route('/api/modules/visibility', methods=['PUT'])
@require_auth(require_hmac=False)
def put_modules_visibility():
    """Replace UI visibility rules for modules in the current tenant.

    Body format (either top-level map or nested under "rules"):
      {
        "<module_id>": { "hiddenRoles": ["Farmer", "TechnicalConsultant"] },
        ...
      }
    """
    tenant_id = getattr(g, 'tenant_id', None) or getattr(g, 'tenant', None)
    user_roles = _get_user_roles() or []

    if 'TenantAdmin' not in user_roles and 'PlatformAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions. TenantAdmin or PlatformAdmin required.'}), 403

    try:
        data = request.json or {}
        raw_rules = data.get('rules') or data
        if not isinstance(raw_rules, dict):
            return jsonify({'error': 'Invalid payload. Expected object mapping moduleId -> { hiddenRoles: [...] }'}), 400

        # Normalise payload
        normalised: Dict[str, List[str]] = {}
        for module_id, cfg in raw_rules.items():
            if not module_id:
                continue
            if not isinstance(cfg, dict):
                continue
            hidden_roles = cfg.get('hiddenRoles') or cfg.get('hidden_roles') or []
            if not isinstance(hidden_roles, list):
                continue
            normalised[str(module_id)] = [str(r) for r in hidden_roles if isinstance(r, str)]

        with get_db_connection_with_tenant(tenant_id) as conn:
            cur = conn.cursor()
            # Best-effort: if table doesn't exist, swallow and log
            try:
                # Replace existing rules for this tenant
                cur.execute(
                    "DELETE FROM tenant_module_visibility WHERE tenant_id = %s",
                    (tenant_id,),
                )
                for module_id, hidden_roles in normalised.items():
                    cur.execute(
                        """
                        INSERT INTO tenant_module_visibility (tenant_id, module_id, hidden_roles)
                        VALUES (%s, %s, %s)
                        """,
                        (tenant_id, module_id, hidden_roles),
                    )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                msg = str(exc)
                if 'tenant_module_visibility' in msg or 'relation \"tenant_module_visibility\"' in msg:
                    logger.warning(
                        "[tenant_visibility] tenant_module_visibility table not found, "
                        "ignoring PUT /api/modules/visibility (no rules persisted)"
                    )
                    # Behave as no-op, but respond OK so UI doesn't break
                    return jsonify({'message': 'Visibility table not available; rules not persisted yet.'}), 200
                logger.error(f"[tenant_visibility] Error updating visibility rules: {exc}")
                import traceback
                logger.error(traceback.format_exc())
                return jsonify({'error': 'Failed to update visibility rules', 'details': str(exc)}), 500

        return jsonify({'message': 'Visibility rules updated', 'rules': normalised}), 200

    except Exception as exc:
        logger.error(f"[tenant_visibility] Unexpected error in PUT /api/modules/visibility: {exc}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Internal server error', 'details': str(exc)}), 500


# =============================================================================
# Administrative Endpoints (Nekazari Control Center)
# =============================================================================





@app.route('/api/modules/upload', methods=['POST'])
@require_auth(require_hmac=False)  # Frontend endpoint, no HMAC required
def upload_module():
    """
    Upload a module ZIP file for validation and registration.
    
    Only PlatformAdmin can upload modules.
    
    Request:
        - Content-Type: multipart/form-data
        - Body: ZIP file with key 'file'
        
    Returns:
        {
            'upload_id': str,
            'status': 'pending',
            'message': str,
            'module_id': str,
            'version': str
        }
    """
    user_roles = _get_user_roles()
    
    # Check permissions - only PlatformAdmin
    if 'PlatformAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions. PlatformAdmin required.'}), 403
    
    if not MODULE_UPLOAD_SERVICE_AVAILABLE:
        return jsonify({
            'error': 'Module upload service not available',
            'message': 'ModuleUploadService could not be initialized'
        }), 503
    
    try:
        # Check if file was uploaded
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided', 'message': 'ZIP file is required'}), 400
        
        file = request.files['file']
        
        # Check if file was selected
        if file.filename == '':
            return jsonify({'error': 'No file selected', 'message': 'Please select a ZIP file'}), 400
        
        # Validate file extension
        if not file.filename.lower().endswith('.zip'):
            return jsonify({
                'error': 'Invalid file type',
                'message': 'Only ZIP files are allowed'
            }), 400
        
        # Validate file size (max 50MB)
        MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
        file.seek(0, 2)  # Seek to end
        file_size = file.tell()
        file.seek(0)  # Reset to beginning
        
        if file_size > MAX_FILE_SIZE:
            return jsonify({
                'error': 'File too large',
                'message': f'Maximum file size is {MAX_FILE_SIZE / (1024*1024):.0f}MB'
            }), 400
        
        # Initialize upload service
        try:
            upload_service = ModuleUploadService()
        except Exception as e:
            logger.error(f"Failed to initialize ModuleUploadService: {e}")
            return jsonify({
                'error': 'Service initialization failed',
                'message': 'Could not initialize upload service'
            }), 500
        
        # Generate unique upload ID
        upload_id = str(uuid.uuid4())
        
        # Get user info
        username = getattr(g, 'user', None) or getattr(g, 'current_user', {}).get('preferred_username', 'unknown')
        
        # Record upload in tracking table
        try:
            conn = get_db_connection_simple()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO module_uploads (upload_id, status, uploaded_by, metadata)
                VALUES (%s, 'pending', %s, %s::jsonb)
            """, (upload_id, username, json.dumps({'filename': file.filename, 'size': file_size})))
            conn.commit()
            cur.close()
            return_db_connection(conn)
        except Exception as e:
            logger.warning(f"Failed to record upload in tracking table: {e}")
            # Don't fail the upload if tracking fails
        
        # Read file into BytesIO
        file_content = BytesIO(file.read())
        
        # Extract and validate ZIP structure and manifest
        manifest_data, error, error_message = upload_service.extract_and_validate_zip(file_content)
        
        if error or not manifest_data:
            return jsonify({
                'error': 'Validation failed',
                'message': error_message or 'Unknown validation error'
            }), 400
        
        # Get module info from manifest
        module_id = manifest_data['id']
        module_version = manifest_data['version']
        
        # Check if module with same ID already exists
        conn = get_db_connection_simple()
        cur = conn.cursor()
        cur.execute("SELECT id FROM marketplace_modules WHERE id = %s", (module_id,))
        existing = cur.fetchone()
        cur.close()
        return_db_connection(conn)
        
        if existing:
            logger.info(f"Module {module_id} already exists, will be updated after validation")
        
        # Upload ZIP to MinIO
        try:
            file_content.seek(0)  # Reset file pointer
            minio_object_name = upload_service.upload_to_minio(file_content, upload_id)
            logger.info(f"Uploaded module ZIP to MinIO: {minio_object_name}")
        except Exception as e:
            logger.error(f"Failed to upload to MinIO: {e}")
            return jsonify({
                'error': 'Upload failed',
                'message': f'Failed to upload file to storage: {str(e)}'
            }), 500
        
        # Create validation job in Kubernetes
        try:
            job_created = upload_service.create_validation_job(upload_id, module_id, module_version)
            if not job_created:
                return jsonify({
                    'error': 'Validation job creation failed',
                    'message': 'Could not create validation job'
                }), 500
        except Exception as e:
            logger.error(f"Failed to create validation job: {e}")
            return jsonify({
                'error': 'Validation job creation failed',
                'message': f'Could not create validation job: {str(e)}'
            }), 500
        
        # Store upload metadata temporarily (could use Redis in the future)
        # For now, the validation job will call a webhook endpoint when complete
        
        logger.info(f"Module upload initiated: {module_id} v{module_version}, upload_id={upload_id}")
        
        return jsonify({
            'upload_id': upload_id,
            'status': 'pending',
            'message': 'Module uploaded successfully. Validation in progress.',
            'module_id': module_id,
            'version': module_version
        }), 200
        
    except Exception as e:
        logger.error(f"Error uploading module: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({
            'error': 'Internal server error',
            'message': str(e),
            'details': 'See server logs for more information'
        }), 500


@app.route('/api/modules/<upload_id>/validation-status', methods=['GET'])
@require_auth(require_hmac=False)  # Frontend endpoint, no HMAC required
def get_validation_status(upload_id):
    """
    Get validation status for an uploaded module.
    
    Only PlatformAdmin can check status.
    """
    user_roles = _get_user_roles()
    
    # Check permissions - only PlatformAdmin
    if 'PlatformAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions. PlatformAdmin required.'}), 403
    
    try:
        if not MODULE_UPLOAD_SERVICE_AVAILABLE:
            return jsonify({
                'error': 'Module upload service not available'
            }), 503
        
        upload_service = ModuleUploadService()
        
        # Check Kubernetes job status
        job_name = f"module-validation-{upload_id[:8]}"
        try:
            from kubernetes.client.rest import ApiException
            job = upload_service.k8s_batch_api.read_namespaced_job(
                name=job_name,
                namespace=K8S_NAMESPACE
            )
            
            # Determine status from job conditions
            if job.status.succeeded:
                status = 'completed'
                message = 'Validation completed successfully'
            elif job.status.failed:
                status = 'failed'
                message = 'Validation failed. Check job logs for details.'
            elif job.status.active:
                status = 'running'
                message = 'Validation in progress...'
            else:
                status = 'pending'
                message = 'Validation job created, waiting to start...'
            
            return jsonify({
                'upload_id': upload_id,
                'status': status,
                'message': message,
                'job_name': job_name
            }), 200
            
        except Exception as e:
            # Check if it's a 404 ApiException
            error_str = str(e)
            if '404' in error_str or 'Not Found' in error_str:
                return jsonify({
                    'upload_id': upload_id,
                    'status': 'not_found',
                    'message': 'Validation job not found'
                }), 404
            else:
                logger.error(f"Error checking job status: {e}")
                raise
        
    except Exception as e:
        logger.error(f"Error checking validation status: {e}")
        return jsonify({
            'error': 'Internal server error',
            'message': str(e)
        }), 500


@app.route('/api/internal/modules/register-validated', methods=['POST'])
def register_validated_module():
    """
    Internal endpoint for validation jobs to register validated modules.
    
    Authenticated via INTERNAL_SERVICE_SECRET header (shared secret for internal services).
    """
    # Internal service authentication
    internal_secret = request.headers.get('X-Internal-Service-Secret')
    expected_secret = os.getenv('INTERNAL_SERVICE_SECRET', '')
    
    if not expected_secret or internal_secret != expected_secret:
        logger.warning(f"Invalid internal service secret from {request.remote_addr}")
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        upload_id = data.get('upload_id')
        manifest_data = data.get('manifest_data')
        
        if not upload_id or not manifest_data:
            return jsonify({
                'error': 'Missing required fields',
                'message': 'upload_id and manifest_data are required'
            }), 400
        
        if not MODULE_UPLOAD_SERVICE_AVAILABLE:
            return jsonify({
                'error': 'Module upload service not available'
            }), 503
        
        # Get database connection
        conn = get_db_connection_simple()
        
        try:
            upload_service = ModuleUploadService()
            success = upload_service.register_module_in_database(
                manifest_data,
                upload_id,
                conn
            )
            
            if success:
                module_id = manifest_data.get('id')
                logger.info(f"Module {module_id} registered successfully after validation")
                
                # Update tracking status to completed
                try:
                    upload_id = data.get('upload_id')
                    cur = conn.cursor()
                    cur.execute("""
                        UPDATE module_uploads 
                        SET status = 'completed', validated_at = NOW(), updated_at = NOW()
                        WHERE upload_id = %s
                    """, (upload_id,))
                    conn.commit()
                    cur.close()
                except Exception as e:
                    logger.warning(f"Failed to update upload tracking to completed: {e}")
                
                return jsonify({
                    'success': True,
                    'message': 'Module registered successfully',
                    'module_id': module_id
                }), 200
            else:
                return jsonify({
                    'error': 'Registration failed',
                    'message': 'Failed to register module in database'
                }), 500
                
        finally:
            return_db_connection(conn)
        
    except Exception as e:
        logger.error(f"Error in register_validated_module: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({
            'error': 'Internal server error',
            'message': str(e)
        }), 500


@app.route('/api/modules/<module_id>/deploy', methods=['POST'])
@require_auth(require_hmac=False)  # Frontend endpoint, no HMAC required
def deploy_module(module_id):
    """
    Deploy module assets to modules-server.
    
    Only PlatformAdmin can deploy modules.
    """
    user_roles = _get_user_roles()
    
    # Check permissions - only PlatformAdmin
    if 'PlatformAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions. PlatformAdmin required.'}), 403
    
    if not MODULE_UPLOAD_SERVICE_AVAILABLE:
        return jsonify({
            'error': 'Module upload service not available'
        }), 503
    
    try:
        data = request.json or {}
        upload_id = data.get('upload_id')
        
        if not upload_id:
            return jsonify({'error': 'upload_id is required'}), 400
        
        upload_service = ModuleUploadService()
        success, message = upload_service.deploy_module_assets_to_server(upload_id, module_id)
        
        if success:
            return jsonify({
                'success': True,
                'message': message,
                'module_id': module_id
            }), 200
        else:
            return jsonify({
                'error': 'Deployment failed',
                'message': message
            }), 500
        
    except Exception as e:
        logger.error(f"Error deploying module: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({
            'error': 'Internal server error',
            'message': str(e)
        }), 500


@app.route('/api/modules/<upload_id>/logs', methods=['GET'])
@require_auth(require_hmac=False)  # Frontend endpoint, no HMAC required
def get_validation_logs(upload_id):
    """
    Get logs from validation job.
    
    Only PlatformAdmin can view logs.
    """
    user_roles = _get_user_roles()
    
    # Check permissions - only PlatformAdmin
    if 'PlatformAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions. PlatformAdmin required.'}), 403
    
    try:
        if not MODULE_UPLOAD_SERVICE_AVAILABLE:
            return jsonify({
                'error': 'Module upload service not available'
            }), 503
        
        upload_service = ModuleUploadService()
        
        # Find job and pod
        job_name = f"module-validation-{upload_id[:8]}"
        try:
            from kubernetes.client.rest import ApiException
            job = upload_service.k8s_batch_api.read_namespaced_job(
                name=job_name,
                namespace=K8S_NAMESPACE
            )
            
            # Get pods for this job
            pods = upload_service.k8s_core_api.list_namespaced_pod(
                namespace=K8S_NAMESPACE,
                label_selector=f"job-name={job_name}"
            )
            
            if not pods.items:
                return jsonify({
                    'upload_id': upload_id,
                    'job_name': job_name,
                    'logs': [],
                    'message': 'No pods found for validation job'
                }), 404
            
            # Get logs from first pod
            pod_name = pods.items[0].metadata.name
            logs = upload_service.k8s_core_api.read_namespaced_pod_log(
                name=pod_name,
                namespace=K8S_NAMESPACE,
                tail_lines=500  # Last 500 lines
            )
            
            return jsonify({
                'upload_id': upload_id,
                'job_name': job_name,
                'pod_name': pod_name,
                'logs': logs.split('\n') if logs else []
            }), 200
            
        except ApiException as e:
            if e.status == 404:
                return jsonify({
                    'upload_id': upload_id,
                    'error': 'Validation job not found'
                }), 404
            else:
                raise
        
    except Exception as e:
        logger.error(f"Error getting validation logs: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({
            'error': 'Internal server error',
            'message': str(e)
        }), 500


@app.route('/api/modules/uploads', methods=['GET'])
@require_auth(require_hmac=False)  # Frontend endpoint, no HMAC required
def get_module_uploads():
    """
    Get list of module uploads with their status.
    
    Only PlatformAdmin can view uploads.
    """
    user_roles = _get_user_roles()
    
    # Check permissions - only PlatformAdmin
    if 'PlatformAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions. PlatformAdmin required.'}), 403
    
    try:
        conn = get_db_connection_simple()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get uploads, optionally filtered by status
        status_filter = request.args.get('status')
        if status_filter:
            cur.execute("""
                SELECT upload_id, module_id, version, status, uploaded_by, 
                       uploaded_at, validated_at, error_message, metadata, updated_at
                FROM module_uploads
                WHERE status = %s
                ORDER BY uploaded_at DESC
                LIMIT 100
            """, (status_filter,))
        else:
            cur.execute("""
                SELECT upload_id, module_id, version, status, uploaded_by, 
                       uploaded_at, validated_at, error_message, metadata, updated_at
                FROM module_uploads
                ORDER BY uploaded_at DESC
                LIMIT 100
            """)
        
        uploads = cur.fetchall()
        cur.close()
        return_db_connection(conn)
        
        return jsonify([dict(upload) for upload in uploads]), 200
        
    except Exception as e:
        logger.error(f"Error fetching module uploads: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({
            'error': 'Internal server error',
            'message': str(e)
        }), 500









@app.route('/api/tenants/me/limits', methods=['GET'])
@require_auth
def get_tenant_limits_with_usage():
    """
    Get tenant limits and current usage.
    Returns limits from Orion-LD (source of truth) and current consumption.
    """
    tenant_id = getattr(g, 'tenant_id', None) or getattr(g, 'tenant', None)
    
    try:
        # Get limits from Orion-LD
        limits = get_limits_for_tenant(tenant_id) or {}
        
        # Get current usage
        usage = _gather_usage_for_tenant(tenant_id)
        
        # Get plan_type from PostgreSQL as fallback
        conn = get_db_connection_simple()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT plan_type FROM tenants WHERE tenant_id = %s", (tenant_id,))
        tenant_row = cur.fetchone()
        cur.close()
        return_db_connection(conn)
        
        plan_type = limits.get('planType') or (tenant_row['plan_type'] if tenant_row else 'basic')
        
        # Build response with limits and usage
        result = {
            'tenant_id': tenant_id,
            'plan_type': plan_type,
            'limits': {
                'maxUsers': int(limits.get('maxUsers') or 0) if limits.get('maxUsers') is not None else None,
                'maxRobots': int(limits.get('maxRobots') or 0) if limits.get('maxRobots') is not None else None,
                'maxSensors': int(limits.get('maxSensors') or 0) if limits.get('maxSensors') is not None else None,
                'maxAreaHectares': float(limits.get('maxAreaHectares') or 0.0) if limits.get('maxAreaHectares') is not None else None,
            },
            'usage': usage,
            'percentages': {}
        }
        
        # Calculate percentages
        max_robots = result['limits']['maxRobots'] or 0
        max_sensors = result['limits']['maxSensors'] or 0
        max_area = result['limits']['maxAreaHectares'] or 0.0
        
        if max_robots > 0:
            result['percentages']['robots'] = min(100.0, (usage.get('robots', 0) / max_robots) * 100)
        if max_sensors > 0:
            result['percentages']['sensors'] = min(100.0, (usage.get('sensors', 0) / max_sensors) * 100)
        if max_area > 0:
            result['percentages']['areaHectares'] = min(100.0, (usage.get('areaHectares', 0) / max_area) * 100)
        
        return jsonify(result), 200
        
    except Exception as e:
        logger.error(f"Error getting tenant limits with usage: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500


# =============================================================================
# Module Health Check Endpoints
# =============================================================================

@app.route('/api/modules/<module_id>/health', methods=['GET'])
@require_auth(require_hmac=False)
def module_health_check(module_id):
    """
    Health check endpoint for a specific module.
    Returns health status including database tables, endpoints, and dependencies.
    """
    if not MODULE_HEALTH_AVAILABLE:
        return jsonify({
            'module_id': module_id,
            'status': 'unknown',
            'error': 'Module health checks not available',
            'timestamp': datetime.utcnow().isoformat() + 'Z',
        }), 503
    
    if not POSTGRES_URL:
        return jsonify({
            'module_id': module_id,
            'status': 'unhealthy',
            'error': 'Database not configured',
            'timestamp': datetime.utcnow().isoformat() + 'Z',
        }), 503
    
    try:
        tenant_id = getattr(g, 'tenant_id', None) or getattr(g, 'tenant', None)
        health_status = get_module_health(module_id, tenant_id, POSTGRES_URL)
        status_code = 200 if health_status['status'] == 'healthy' else 503
        return jsonify(health_status), status_code
    except Exception as e:
        logger.error(f"Error checking module health for {module_id}: {e}")
        return jsonify({
            'module_id': module_id,
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.utcnow().isoformat() + 'Z',
        }), 500


# =============================================================================
# Admin Audit Logs Endpoint
# =============================================================================


@app.route('/api/core/sync/vectorial', methods=['GET', 'POST'])
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


# Import and register blueprints (at bottom to avoid circular imports)
from blueprints.weather import weather_bp
from blueprints.admin import admin_bp
from blueprints.assets import assets_bp
from blueprints.entities import entities_bp
app.register_blueprint(weather_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(assets_bp)
app.register_blueprint(entities_bp)

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = LOG_LEVEL == 'DEBUG'
    
    logger.info(f"Starting Entity Manager API on port {port}")
    app.run(host='0.0.0.0', port=port, debug=debug)

