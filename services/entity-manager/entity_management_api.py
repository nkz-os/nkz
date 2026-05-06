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
from blueprints.sensors import sensors_bp
app.register_blueprint(weather_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(assets_bp)
app.register_blueprint(entities_bp)
app.register_blueprint(sync_bp)
app.register_blueprint(modules_bp)
app.register_blueprint(sensors_bp)

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = LOG_LEVEL == 'DEBUG'
    
    logger.info(f"Starting Entity Manager API on port {port}")
    app.run(host='0.0.0.0', port=port, debug=debug)

