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
from io import BytesIO

# Configuration - All environment variables are REQUIRED for security
POSTGRES_URL = os.getenv('POSTGRES_URL')
ORION_URL = os.getenv('ORION_URL')

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

# VisibilityRules type moved to blueprints/modules.py

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
# Import and register blueprints (at bottom to avoid circular imports)
from blueprints.weather import weather_bp
from blueprints.admin import admin_bp
from blueprints.assets import assets_bp
from blueprints.entities import entities_bp
from blueprints.sync import sync_bp
from blueprints.modules import modules_bp
app.register_blueprint(weather_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(assets_bp)
app.register_blueprint(entities_bp)
app.register_blueprint(sync_bp)
app.register_blueprint(modules_bp)

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = LOG_LEVEL == 'DEBUG'
    
    logger.info(f"Starting Entity Manager API on port {port}")
    app.run(host='0.0.0.0', port=port, debug=debug)

