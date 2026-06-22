#!/usr/bin/env python3
# =============================================================================
# Entity Management API - Production Service
# =============================================================================

import os
import sys
import json
import logging
import time
from datetime import datetime
from typing import Dict, Any

import requests
import threading

from flask import Flask, request, jsonify, g, Response
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

# Add common directory to path for imports
common_paths = [
    os.path.join(os.path.dirname(__file__), '..', 'common'),
    '/app/common',
    '/common',
    os.path.join(os.path.dirname(__file__), 'common'),
]
for path in common_paths:
    if os.path.exists(path):
        sys.path.insert(0, path)
        break

from common.auth_middleware import require_auth
from db_helper import get_db_connection_simple, get_db_connection_with_tenant, return_db_connection

# Import optional dependencies with fallbacks
try:
    from audit_logger import audit_log, log_module_toggle, log_module_job_create, log_error
    AUDIT_LOGGER_AVAILABLE = True
except ImportError:
    AUDIT_LOGGER_AVAILABLE = False
    def audit_log(*args, **kwargs): pass
    def log_module_toggle(*args, **kwargs): pass
    def log_module_job_create(*args, **kwargs): pass
    def log_error(*args, **kwargs): pass

try:
    from module_health import get_module_health
    MODULE_HEALTH_AVAILABLE = True
except ImportError:
    try:
        from common.module_health import get_module_health
        MODULE_HEALTH_AVAILABLE = True
    except ImportError:
        MODULE_HEALTH_AVAILABLE = False
        def get_module_health(*args, **kwargs):
            return {'error': 'Module health checks not available'}

try:
    from audit_middleware import setup_audit_middleware
    AUDIT_MIDDLEWARE_AVAILABLE = True
except ImportError:
    AUDIT_MIDDLEWARE_AVAILABLE = False
    def setup_audit_middleware(*args, **kwargs): pass

try:
    from rate_limiter import rate_limit_module
    RATE_LIMITER_AVAILABLE = True
except ImportError:
    RATE_LIMITER_AVAILABLE = False
    def rate_limit_module(*args, **kwargs):
        def decorator(func): return func
        return decorator

try:
    from module_metrics import record_module_usage, record_module_latency, record_module_error, metrics_decorator
    MODULE_METRICS_AVAILABLE = True
except ImportError:
    MODULE_METRICS_AVAILABLE = False
    def record_module_usage(*args, **kwargs): pass
    def record_module_latency(*args, **kwargs): pass
    def record_module_error(*args, **kwargs): pass
    def metrics_decorator(*args, **kwargs):
        def decorator(func): return func
        return decorator

try:
    from module_upload_service import ModuleUploadService, K8S_NAMESPACE
    MODULE_UPLOAD_SERVICE_AVAILABLE = True
except ImportError as e:
    logging.getLogger(__name__).warning(f"ModuleUploadService not available: {e}")
    MODULE_UPLOAD_SERVICE_AVAILABLE = False

from helpers import (
    _extract_number, _count_all_entities, _gather_usage_for_tenant,
    get_limits_for_tenant,
    ORION_URL, CONTEXT_URL, LOG_LEVEL,
    MAX_ROBOTS, MAX_SENSORS, MAX_AREA_HECTARES,
    ROBOT_ENTITY_TYPES, SENSOR_ENTITY_TYPES, PARCEL_ENTITY_TYPES,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

POSTGRES_URL = os.getenv('POSTGRES_URL')
# Also store POSTGRES_URL on module for blueprints that import it
entity_management_api = sys.modules[__name__]

app = Flask(__name__)

if AUDIT_MIDDLEWARE_AVAILABLE:
    setup_audit_middleware(app, postgres_url=POSTGRES_URL)

_cors_env = os.getenv('CORS_ORIGINS', 'http://localhost:3000,http://localhost:5173')
ALLOWED_ORIGINS = {o.strip() for o in _cors_env.split(',') if o.strip()}

CORS(app, resources={
    r"/api/entities/*": {"origins": list(ALLOWED_ORIGINS), "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
                          "allow_headers": ["Content-Type", "Authorization", "X-Tenant-ID", "x-tenant-id", "X-Auth-Signature"],
                          "expose_headers": ["Content-Type", "Authorization", "X-Tenant-ID"], "supports_credentials": True},
    r"/api/parcels/*": {"origins": list(ALLOWED_ORIGINS), "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
                         "allow_headers": ["Content-Type", "Authorization", "X-Tenant-ID", "x-tenant-id", "X-Auth-Signature"],
                         "expose_headers": ["Content-Type", "Authorization", "X-Tenant-ID"], "supports_credentials": True},
    r"/api/vegetation/*": {"origins": list(ALLOWED_ORIGINS), "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
                            "allow_headers": ["Content-Type", "Authorization", "X-Tenant-ID", "x-tenant-id", "X-Auth-Signature", "X-Source-Module"],
                            "expose_headers": ["Content-Type", "Authorization", "X-Tenant-ID"], "supports_credentials": True},
    r"/*": {"origins": list(ALLOWED_ORIGINS), "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
            "allow_headers": ["Content-Type", "Authorization", "X-Tenant-ID", "x-tenant-id", "X-Auth-Signature"],
            "expose_headers": ["Content-Type", "Authorization", "X-Tenant-ID"], "supports_credentials": True},
}, automatic_options=False)

REQUEST_LATENCY = Histogram('entity_manager_request_latency_seconds', 'Latencia de las peticiones HTTP en entity-manager', ['method', 'endpoint'])
REQUEST_COUNT = Counter('entity_manager_requests_total', 'Total de peticiones HTTP en entity-manager', ['method', 'endpoint', 'http_status'])

logging.getLogger().setLevel(getattr(logging, LOG_LEVEL))


# ---------------------------------------------------------------------------
# Before / After request handlers
# ---------------------------------------------------------------------------

@app.before_request
def _start_timer():
    g._request_start_time = time.perf_counter()

@app.after_request
def _record_metrics(response):
    start_time = getattr(g, '_request_start_time', None)
    if start_time is not None:
        elapsed = time.perf_counter() - start_time
        REQUEST_LATENCY.labels(request.method, request.endpoint or request.path or 'unknown').observe(elapsed)
        REQUEST_COUNT.labels(request.method, request.endpoint or request.path or 'unknown', response.status_code).inc()
    origin = request.headers.get('Origin')
    if origin and origin in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Vary'] = 'Origin'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Tenant-ID, x-tenant-id, X-Auth-Signature'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS, PATCH'
    return response


# ---------------------------------------------------------------------------
# Health / Metrics / Version
# ---------------------------------------------------------------------------

@app.route('/metrics', methods=['GET'])
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'timestamp': datetime.utcnow().isoformat(), 'service': 'entity-manager'})

@app.route('/version', methods=['GET'])
def version():
    return jsonify({'service': 'entity-manager', 'version': '1.0.0', 'timestamp': datetime.utcnow().isoformat()})


# ---------------------------------------------------------------------------
# Tenant Limits
# ---------------------------------------------------------------------------

@app.route('/api/tenants/me/limits', methods=['GET'])
@require_auth
def get_tenant_limits_with_usage():
    """Get tenant limits and current usage."""
    tenant_id = getattr(g, 'tenant_id', None) or getattr(g, 'tenant', None)
    try:
        limits = get_limits_for_tenant(tenant_id) or {}
        usage = _gather_usage_for_tenant(tenant_id)
        conn = get_db_connection_simple()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT plan_type FROM tenants WHERE tenant_id = %s", (tenant_id,))
        tenant_row = cur.fetchone()
        cur.close()
        return_db_connection(conn)
        plan_type = limits.get('planType') or (tenant_row['plan_type'] if tenant_row else 'basic')
        result = {
            'tenant_id': tenant_id, 'plan_type': plan_type,
            'limits': {
                'maxUsers': int(limits.get('maxUsers') or 0) if limits.get('maxUsers') is not None else None,
                'maxRobots': int(limits.get('maxRobots') or 0) if limits.get('maxRobots') is not None else None,
                'maxSensors': int(limits.get('maxSensors') or 0) if limits.get('maxSensors') is not None else None,
                'maxAreaHectares': float(limits.get('maxAreaHectares') or 0.0) if limits.get('maxAreaHectares') is not None else None,
            },
            'usage': usage,
            'percentages': {},
        }
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
        import traceback; logger.error(traceback.format_exc())
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500


# ---------------------------------------------------------------------------
# Blueprint registrations (at bottom to avoid circular imports)
# ---------------------------------------------------------------------------

# weather routes extracted to standalone weather-api service
from blueprints.admin import admin_bp
from blueprints.assets import assets_bp
from blueprints.entities import entities_bp
from blueprints.sync import sync_bp
from blueprints.modules import modules_bp
from blueprints.sensors import sensors_bp
from blueprints.calibration import calibration_bp
from notification_handler import notify_bp
from blueprints.notifications import notifications_bp, ensure_subscriptions_for_all_tenants

# weather_bp removed — routes now served by standalone weather-api service
app.register_blueprint(admin_bp)
app.register_blueprint(assets_bp)
app.register_blueprint(entities_bp)
app.register_blueprint(sync_bp)
app.register_blueprint(modules_bp)
app.register_blueprint(sensors_bp)
app.register_blueprint(calibration_bp)
app.register_blueprint(notify_bp)
app.register_blueprint(notifications_bp)

# ---------------------------------------------------------------------------
# NGSI-LD subscription bootstrap at startup
# ---------------------------------------------------------------------------



def _bootstrap_subscriptions_at_startup():
    """Ensure NGSI-LD subscriptions exist for all active tenants on startup.

    Runs in a background thread so it doesn't block the HTTP server startup.
    Subscriptions are created asynchronously — if this fails, they can be
    created lazily on first use via check_or_create_subscription pattern.
    """
    logger.info("Bootstrapping NGSI-LD subscriptions for all active tenants...")
    try:
        from subscription_manager import ensure_subscriptions_for_all_tenants
        ensure_subscriptions_for_all_tenants()
        logger.info("Subscription bootstrap complete")
    except Exception as e:
        logger.error("Subscription bootstrap failed (non-fatal): %s", e, exc_info=True)

    # Also bootstrap notification subscriptions (Alert entities)
    try:
        ensure_subscriptions_for_all_tenants()
        logger.info("Notification subscription bootstrap complete")
    except Exception as e:
        logger.error("Notification subscription bootstrap failed (non-fatal): %s", e, exc_info=True)


# Start bootstrap in background thread to not block startup
_bootstrap_t = threading.Thread(
    target=_bootstrap_subscriptions_at_startup,
    daemon=True,
    name="subscription-bootstrap"
)
_bootstrap_t.start()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = LOG_LEVEL == 'DEBUG'
    logger.info(f"Starting Entity Manager API on port {port}")
    app.run(host='0.0.0.0', port=port, debug=debug)
