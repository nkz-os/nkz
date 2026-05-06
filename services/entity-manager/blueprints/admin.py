#!/usr/bin/env python3
"""
Admin Blueprint - Extracted from entity_management_api.py
"""
import os
import sys
import json
import logging
import time
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify, g, Response
from psycopg2.extras import RealDictCursor
import requests

from common.auth_middleware import require_auth
from common import inject_fiware_headers
from db_helper import get_db_connection_with_tenant, get_db_connection_simple, return_db_connection, set_platform_admin_context

logger = logging.getLogger(__name__)

# Import shared helpers from main module (imported at bottom to avoid circular imports)
from entity_management_api import get_limits_for_tenant, _gather_usage_for_tenant, _count_all_entities, upsert_limits_in_orion
from entity_management_api import _limits_cache, _limits_cache_ts, _extract_number

ORION_URL = os.getenv('ORION_URL')
POSTGRES_URL = os.getenv('POSTGRES_URL')

# Import parcel sync
try:
    from parcel_sync import parcel_sync
    PARCEL_SYNC_AVAILABLE = True
except ImportError:
    PARCEL_SYNC_AVAILABLE = False

# Import audit_log with fallback
try:
    from audit_logger import audit_log, log_module_toggle, log_module_job_create, log_error
    AUDIT_LOGGER_AVAILABLE = True
except ImportError:
    AUDIT_LOGGER_AVAILABLE = False
    def audit_log(*args, **kwargs):
        pass

# Replicated from entity_management_api - shared helper used by admin routes
def _get_user_roles():
    """Get user roles from Flask g (set by auth middleware)"""
    roles = g.get('roles', [])
    if not roles:
        payload = g.get('current_user', {})
        if payload:
            roles = payload.get('realm_access', {}).get('roles', [])
    return roles

admin_bp = Blueprint('admin', __name__)

# === Lines 1067-1092 from entity_management_api.py ===
@admin_bp.route('/api/admin/tenant-limits', methods=['GET'])
@require_auth
def api_get_tenant_limits():
    """Devuelve límites efectivos del tenant actual (dinámicos si existen en Orion)."""
    try:
        tenant = getattr(g, 'tenant', 'master')
        limits = get_limits_for_tenant(tenant) or {}
        result = {
            'planType': limits.get('planType'),
            'maxUsers': int(limits.get('maxUsers') or 0) if limits.get('maxUsers') is not None else None,
            'maxRobots': int(limits.get('maxRobots') or 0) if limits.get('maxRobots') is not None else None,
            'maxSensors': int(limits.get('maxSensors') or 0) if limits.get('maxSensors') is not None else None,
            'maxAreaHectares': float(limits.get('maxAreaHectares') or 0.0) if limits.get('maxAreaHectares') is not None else None,
            'maxParcels': int(limits.get('maxParcels') or 0) if limits.get('maxParcels') is not None else None,
            'maxEntitiesTotal': int(limits.get('maxEntitiesTotal') or 0) if limits.get('maxEntitiesTotal') is not None else None,
            'defaults': {
                'maxUsers': None,
                'maxRobots': int(os.getenv('MAX_ROBOTS', '999999')),
                'maxSensors': int(os.getenv('MAX_SENSORS', '999999')),
                'maxAreaHectares': float(os.getenv('MAX_AREA_HECTARES', '1000000000'))
            }
        }
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error getting tenant limits: {e}")
        return jsonify({'error': 'Internal server error'}), 500

# === Lines 1094-1158 from entity_management_api.py ===
@admin_bp.route('/api/admin/tenant-usage', methods=['GET'])
@require_auth
def api_get_tenant_usage():
    tenant = request.headers.get('X-Tenant-Id') or request.args.get('tenant') or getattr(g, 'current_tenant', None) or getattr(g, 'tenant', None)
    if not tenant:
        return jsonify({'error': 'Tenant context required'}), 400
    try:
        usage = _gather_usage_for_tenant(tenant)
        limits_raw = get_limits_for_tenant(tenant) or {}

        def _safe_int(value):
            try:
                return int(value) if value is not None else None
            except Exception:
                return None

        def _safe_float(value):
            try:
                return float(value) if value is not None else None
            except Exception:
                return None

        limits_payload = {
            'planType': limits_raw.get('planType'),
            'maxUsers': _safe_int(limits_raw.get('maxUsers')),
            'maxRobots': _safe_int(limits_raw.get('maxRobots')),
            'maxSensors': _safe_int(limits_raw.get('maxSensors')),
            'maxAreaHectares': _safe_float(limits_raw.get('maxAreaHectares')),
            'maxParcels': _safe_int(limits_raw.get('maxParcels')),
            'maxEntitiesTotal': _safe_int(limits_raw.get('maxEntitiesTotal')),
        }

        percentages = {}
        robots_limit = limits_payload.get('maxRobots') or 0
        sensors_limit = limits_payload.get('maxSensors') or 0
        area_limit = limits_payload.get('maxAreaHectares') or 0.0
        parcels_limit = limits_payload.get('maxParcels') or 0
        entities_limit = limits_payload.get('maxEntitiesTotal') or 0

        if robots_limit > 0:
            percentages['robots'] = min(100.0, (usage['robots'] / robots_limit) * 100)
        if sensors_limit > 0:
            percentages['sensors'] = min(100.0, (usage['sensors'] / sensors_limit) * 100)
        if area_limit > 0:
            percentages['areaHectares'] = min(100.0, (usage['areaHectares'] / area_limit) * 100)
        if parcels_limit > 0:
            percentages['parcels'] = min(100.0, (usage['parcels'] / parcels_limit) * 100)
        if entities_limit > 0:
            total_entities = _count_all_entities(tenant)
            if total_entities is not None:
                percentages['entities'] = min(100.0, (total_entities / entities_limit) * 100)

        # Compute total entities for usage
        usage['totalEntities'] = _count_all_entities(tenant)

        return jsonify({
            'tenant': tenant,
            'usage': usage,
            'limits': limits_payload,
            'percentages': percentages,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
        })
    except Exception as exc:
        logger.exception("Error computing tenant usage: %s", exc)
        return jsonify({'error': 'Failed to compute tenant usage'}), 500

# === Lines 1160-1194 from entity_management_api.py ===
@admin_bp.route('/api/admin/tenant-limits', methods=['PATCH'])
@require_auth
def api_update_tenant_limits():
    """Update tenant limits in PostgreSQL (admin_platform.tenant_limits)."""
    try:
        tenant = getattr(g, 'tenant', 'master')
        data = request.get_json() or {}
        # Mapear claves esperadas
        allowed = {
            'planType': data.get('planType'),
            'maxUsers': data.get('maxUsers'),
            'maxRobots': data.get('maxRobots'),
            'maxSensors': data.get('maxSensors'),
            'maxAreaHectares': data.get('maxAreaHectares')
        }
        # Limpiar None
        update = {k: v for k, v in allowed.items() if v is not None}
        if not update:
            return jsonify({'error': 'No limits provided'}), 400
        ok = upsert_limits_in_orion(tenant, update)
        if not ok:
            return jsonify({'error': 'Failed to update tenant limits'}), 500
        # invalidar cache
        _limits_cache.pop(tenant, None)
        _limits_cache_ts.pop(tenant, None)
        audit_log(
            action='admin.tenant_limits.update',
            resource_type='tenant_limits',
            resource_id=tenant,
            metadata=update,
        )
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error updating tenant limits: {e}")
        return jsonify({'error': 'Internal server error'}), 500

# === Lines 2572-2602 from entity_management_api.py ===
@admin_bp.route('/api/admin/terms/<language>', methods=['GET'])
def get_terms(language):
    """Get terms and conditions for a specific language (public endpoint for registration). Returns 200 with empty content on DB error or missing table."""
    try:
        conn = get_db_connection_simple()
        if not conn:
            return jsonify({'content': '', 'last_updated': None, 'language': language}), 200

        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT content, last_updated, language
                FROM terms_and_conditions
                WHERE language = %s
                ORDER BY last_updated DESC
                LIMIT 1
            """, (language,))
            result = cur.fetchone()
            cur.close()
            if result:
                return jsonify({
                    'content': result['content'],
                    'last_updated': result['last_updated'].isoformat() if result['last_updated'] else None,
                    'language': result['language']
                }), 200
            return jsonify({'content': '', 'last_updated': None, 'language': language}), 200
        finally:
            return_db_connection(conn)
    except Exception as e:
        logger.warning(f"Error getting terms (returning empty): {e}")
        return jsonify({'content': '', 'last_updated': None, 'language': language}), 200

# === Lines 2605-2680 from entity_management_api.py ===
@admin_bp.route('/api/admin/terms/<language>', methods=['POST'])
@require_auth
def save_terms(language):
    """Save or update terms and conditions for a specific language (admin only)"""
    try:
        # Verify user is PlatformAdmin
        user_roles = _get_user_roles()
        if 'PlatformAdmin' not in user_roles:
            return jsonify({'error': 'Unauthorized. Only PlatformAdmin can manage terms.'}), 403
        
        data = request.get_json()
        content = data.get('content', '').strip()
        
        if not content:
            return jsonify({'error': 'Content is required'}), 400
        
        # Validate language
        supported_languages = ['es', 'en', 'ca', 'eu', 'fr', 'pt']
        if language not in supported_languages:
            return jsonify({'error': f'Unsupported language: {language}'}), 400
        
        conn = get_db_connection_simple()
        if not conn:
            return jsonify({'error': 'Database connection failed'}), 500
        
        try:
            set_platform_admin_context(conn)
            cur = conn.cursor(cursor_factory=RealDictCursor)
            
            # Check if terms exist for this language
            cur.execute("""
                SELECT id FROM terms_and_conditions 
                WHERE language = %s 
                ORDER BY last_updated DESC 
                LIMIT 1
            """, (language,))
            
            existing = cur.fetchone()
            
            if existing:
                # Update existing
                cur.execute("""
                    UPDATE terms_and_conditions 
                    SET content = %s, last_updated = NOW() 
                    WHERE id = %s
                """, (content, existing['id']))
            else:
                # Insert new
                cur.execute("""
                    INSERT INTO terms_and_conditions (language, content, last_updated)
                    VALUES (%s, %s, NOW())
                """, (language, content))
            
            conn.commit()
            cur.close()

            audit_log(
                action='admin.terms.update',
                resource_type='terms_and_conditions',
                resource_id=language,
            )

            return jsonify({
                'success': True,
                'message': 'Terms saved successfully'
            }), 200
        finally:
            return_db_connection(conn)

    except Exception as e:
        logger.error(f"Error saving terms: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# Parent Entities (for hierarchy)

# === Lines 4105-4124 from entity_management_api.py ===
@admin_bp.route('/api/admin/parcels/sync', methods=['POST'])
@require_auth(require_hmac=False)
def admin_sync_parcels():
    """Trigger parcel synchronization for a tenant (PlatformAdmin only)"""
    user_roles = _get_user_roles()
    if 'PlatformAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions. PlatformAdmin required.'}), 403
    
    tenant_id = request.args.get('tenant_id')
    if not tenant_id:
        return jsonify({'error': 'tenant_id query parameter is required'}), 400
        
    if not PARCEL_SYNC_AVAILABLE:
        return jsonify({'error': 'Parcel sync service is not available'}), 503
        
    success = parcel_sync.sync_all_tenant_parcels(tenant_id)
    if success:
        return jsonify({'message': f'Sync triggered for tenant {tenant_id}'}), 200
    else:
        return jsonify({'error': f'Sync failed for tenant {tenant_id}'}), 500

# === Lines 4126-4148 from entity_management_api.py ===
@admin_bp.route('/api/admin/tenants', methods=['GET'])
@require_auth(require_hmac=False)
def admin_list_tenants():
    """List all tenants in the system with their plan details (PlatformAdmin only)"""
    user_roles = _get_user_roles()
    if 'PlatformAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions. PlatformAdmin required.'}), 403
    
    try:
        conn = get_db_connection_simple()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT tenant_id, tenant_name, plan_type, plan_level, status, created_at, updated_at
            FROM tenants
            ORDER BY created_at DESC
        """)
        tenants = cur.fetchall()
        cur.close()
        return_db_connection(conn)
        return jsonify(tenants), 200
    except Exception as e:
        logger.error(f"Error listing tenants for admin: {e}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

# === Lines 4150-4172 from entity_management_api.py ===
@admin_bp.route('/api/admin/activations', methods=['GET'])
@require_auth(require_hmac=False)
def admin_list_activations():
    """List all activation codes (PlatformAdmin only)"""
    user_roles = _get_user_roles()
    if 'PlatformAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions. PlatformAdmin required.'}), 403
    
    try:
        conn = get_db_connection_simple()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, code, email, plan, plan_level, status, expires_at, created_at, tenant_id
            FROM activation_codes
            ORDER BY created_at DESC
        """)
        activations = cur.fetchall()
        cur.close()
        return_db_connection(conn)
        return jsonify(activations), 200
    except Exception as e:
        logger.error(f"Error listing activations for admin: {e}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

# === Lines 4174-4265 from entity_management_api.py ===
@admin_bp.route('/api/admin/tenants/<tenant_id>/purge', methods=['DELETE'])
@require_auth(require_hmac=False)
def admin_purge_tenant(tenant_id):
    """
    Nuclear purge of a tenant: PostgreSQL, Orion-LD entities, and Kubernetes Namespace.
    (PlatformAdmin only)
    """
    user_roles = _get_user_roles()
    if 'PlatformAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions. PlatformAdmin required.'}), 403
    
    if tenant_id == 'platform':
        return jsonify({'error': 'The platform tenant cannot be purged.'}), 400

    logger.info(f"☢️ NUCLEAR PURGE initiated for tenant: {tenant_id}")
    errors = []
    
    # 1. PostgreSQL Purge
    try:
        conn = get_db_connection_simple()
        cur = conn.cursor()
        # Delete from all known tables with tenant_id
        tables = ['cadastral_parcels', 'parcel_ndvi_history', 'parcel_sensors', 
                  'tenant_installed_modules', 'weather_observations', 'tenants']
        for table in tables:
            try:
                cur.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
            except Exception as te:
                errors.append(f"DB Error ({table}): {str(te)}")
        conn.commit()
        cur.close()
        return_db_connection(conn)
        logger.info(f"PostgreSQL purge completed for {tenant_id}")
    except Exception as e:
        errors.append(f"PostgreSQL global error: {str(e)}")

    # 2. Orion-LD Purge (Entities)
    try:
        orion_url = os.getenv('ORION_URL', 'http://orion-ld-service:1026')
        # We can only delete entities if we have their IDs, but we can try to query all
        types = ['AgriParcel', 'AgriSensor', 'Device']
        for t in types:
            try:
                resp = requests.get(f"{orion_url}/ngsi-ld/v1/entities?type={t}", 
                                   headers={'Fiware-Service': tenant_id})
                if resp.status_code == 200:
                    entities = resp.json()
                    for entity in entities:
                        requests.delete(f"{orion_url}/ngsi-ld/v1/entities/{entity['id']}",
                                       headers={'Fiware-Service': tenant_id})
            except Exception as oe:
                errors.append(f"Orion Purge Error ({t}): {str(oe)}")
        logger.info(f"Orion-LD purge attempted for {tenant_id}")
    except Exception as e:
        errors.append(f"Orion-LD global error: {str(e)}")

    # 3. Kubernetes Namespace Purge
    # Forward the request to the tenant-webhook which has K8s privileges
    try:
        webhook_url = os.getenv('TENANT_WEBHOOK_URL', 'http://tenant-webhook-service:5000')
        # We use a special internal token or HMAC if required, for now try direct if permitted
        resp = requests.delete(f"{webhook_url}/webhook/namespace/{tenant_id}", timeout=30)
        if resp.status_code not in [200, 204, 404]:
            errors.append(f"K8s Namespace error: {resp.status_code} - {resp.text}")
    except Exception as e:
        errors.append(f"K8s Webhook communication error: {str(e)}")

    if errors:
        return jsonify({
            'status': 'partial_success',
            'message': f'Tenant {tenant_id} purged with errors',
            'errors': errors
        }), 207
    
    return jsonify({
        'status': 'success',
        'message': f'Tenant {tenant_id} successfully purged from all systems.'
    }), 200

# =============================================================================
# Module Upload Endpoint
# =============================================================================

try:
    from module_upload_service import ModuleUploadService
    MODULE_UPLOAD_SERVICE_AVAILABLE = True
    # Import K8S namespace from module_upload_service
    from module_upload_service import K8S_NAMESPACE
except ImportError as e:
    logger.warning(f"ModuleUploadService not available: {e}")
    MODULE_UPLOAD_SERVICE_AVAILABLE = False
    K8S_NAMESPACE = os.getenv('K8S_NAMESPACE', 'nekazari')

# === Lines 4783-4792 from entity_management_api.py ===
def _ensure_platform_settings_table(cur):
    """Create the platform settings table if needed."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS platform_settings (
            key TEXT PRIMARY KEY,
            value_json JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by TEXT
        )
    """)

# === Lines 4795-4831 from entity_management_api.py ===
@admin_bp.route('/api/public/platform-settings', methods=['GET'])
def get_public_platform_settings():
    """
    Public read endpoint for non-sensitive platform settings used by frontend boot.
    Returns landing_mode: "standard" | "commercial".
    """
    default_mode = os.getenv('VITE_NKZ_EDITION', '').strip().lower()
    default_mode = 'commercial' if default_mode == 'commercial' else 'standard'

    conn = None
    try:
        conn = get_db_connection_simple()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT value_json FROM platform_settings WHERE key = %s",
            ('landing_mode',),
        )
        row = cur.fetchone()
        mode = default_mode
        if row and isinstance(row.get('value_json'), dict):
            configured = str(row['value_json'].get('value', '')).strip().lower()
            if configured in ('standard', 'commercial'):
                mode = configured

        cur.close()
        return_db_connection(conn)
        conn = None
        return jsonify({'landing_mode': mode}), 200
    except Exception as e:
        logger.error(f"Error reading public platform settings: {e}")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
            return_db_connection(conn)
        return jsonify({'landing_mode': default_mode}), 200

# === Lines 4834-4896 from entity_management_api.py ===
@admin_bp.route('/api/admin/platform-settings/landing-mode', methods=['PUT'])
@require_auth
def update_platform_landing_mode():
    """
    Update global landing mode.
    PlatformAdmin only.
    """
    user_roles = g.roles or []
    if 'PlatformAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions. PlatformAdmin required.'}), 403

    data = request.json or {}
    mode = str(data.get('landing_mode', '')).strip().lower()
    if mode not in ('standard', 'commercial'):
        return jsonify({'error': 'Invalid landing_mode. Use standard or commercial.'}), 400

    payload = getattr(g, 'current_user', {}) or {}
    updated_by = payload.get('preferred_username') or payload.get('email') or payload.get('sub') or 'unknown'

    conn = None
    try:
        conn = get_db_connection_simple()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_platform_settings_table(cur)
        cur.execute(
            """
            INSERT INTO platform_settings (key, value_json, updated_by)
            VALUES (%s, %s::jsonb, %s)
            ON CONFLICT (key)
            DO UPDATE SET value_json = EXCLUDED.value_json, updated_by = EXCLUDED.updated_by, updated_at = NOW()
            RETURNING key, value_json, updated_at, updated_by
            """,
            ('landing_mode', json.dumps({'value': mode}), updated_by),
        )
        updated = cur.fetchone()
        conn.commit()
        cur.close()
        return_db_connection(conn)
        conn = None

        audit_log(
            action='admin.platform_settings.update',
            resource_type='platform_settings',
            resource_id='landing_mode',
            metadata={'value': mode, 'updated_by': updated_by},
        )

        return jsonify({
            'key': updated['key'],
            'landing_mode': (updated.get('value_json') or {}).get('value', mode),
            'updated_at': updated['updated_at'].isoformat() if updated.get('updated_at') else None,
            'updated_by': updated.get('updated_by'),
        }), 200
    except Exception as e:
        logger.error(f"Error updating platform landing mode: {e}")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
            return_db_connection(conn)
        return jsonify({'error': 'Internal server error'}), 500


# === Lines 4898-4967 from entity_management_api.py ===
@admin_bp.route('/api/admin/tenants/<tenant_id>/governance', methods=['GET'])
@require_auth
def get_tenant_governance(tenant_id):
    """
    Get tenant governance configuration (administrative fields).
    Only PlatformAdmin can view.
    """
    user_roles = g.roles or []
    
    # Check permissions
    if 'PlatformAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions. PlatformAdmin required.'}), 403
    
    try:
        conn = get_db_connection_simple()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get tenant governance data
        cur.execute("""
            SELECT tenant_id, tenant_name, plan_type, plan_level, status, contract_end_date,
                   billing_email, notes, sales_contact, support_level,
                   max_area_hectares, max_users, max_sensors, max_robots,
                   created_at, updated_at, expires_at, email
            FROM tenants
            WHERE tenant_id = %s
        """, (tenant_id,))
        tenant = cur.fetchone()
        
        if not tenant:
            cur.close()
            return_db_connection(conn)
            return jsonify({'error': 'Tenant not found'}), 404
        
        # Get limits from Orion-LD (as fallback/secondary)
        limits = get_limits_for_tenant(tenant_id) or {}
        
        cur.close()
        return_db_connection(conn)
        
        return jsonify({
            'tenant_id': tenant['tenant_id'],
            'tenant_name': tenant['tenant_name'],
            'plan_level': tenant.get('plan_level', 0),
            'governance': {
                'plan_type': tenant['plan_type'],
                'plan_level': tenant.get('plan_level', 0),
                'contract_end_date': tenant['contract_end_date'].isoformat() if tenant['contract_end_date'] else None,
                'billing_email': tenant['billing_email'],
                'notes': tenant['notes'],
                'sales_contact': tenant['sales_contact'],
                'support_level': tenant['support_level'],
                'status': tenant['status'],
                'email': tenant['email'],
                'expires_at': tenant['expires_at'].isoformat() if tenant['expires_at'] else None,
            },
            'limits': {
                'maxUsers': int(tenant.get('max_users')) if tenant.get('max_users') is not None else int(limits.get('maxUsers') or 0) if limits.get('maxUsers') is not None else None,
                'maxRobots': int(tenant.get('max_robots')) if tenant.get('max_robots') is not None else int(limits.get('maxRobots') or 0) if limits.get('maxRobots') is not None else None,
                'maxSensors': int(tenant.get('max_sensors')) if tenant.get('max_sensors') is not None else int(limits.get('maxSensors') or 0) if limits.get('maxSensors') is not None else None,
                'maxAreaHectares': float(tenant.get('max_area_hectares')) if tenant.get('max_area_hectares') is not None else float(limits.get('maxAreaHectares') or 0.0) if limits.get('maxAreaHectares') is not None else None,
            },
            'plan_type': tenant['plan_type'] or limits.get('planType')
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting tenant governance: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500


# === Lines 4969-5109 from entity_management_api.py ===
@admin_bp.route('/api/admin/tenants/<tenant_id>/governance', methods=['PUT'])
@require_auth
def update_tenant_governance(tenant_id):
    """
    Update tenant governance configuration (administrative fields).
    Only PlatformAdmin can modify.
    
    Updates: plan_type, plan_level, contract_end_date, billing_email, notes, sales_contact, support_level,
             max_area_hectares, max_users, max_sensors, max_robots
    Note: Limits are now primarily stored in PostgreSQL but synced to Orion-LD for compatibility.
    """
    user_roles = g.roles or []
    
    # Check permissions
    if 'PlatformAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions. PlatformAdmin required.'}), 403
    
    try:
        data = request.json or {}
        
        # Validate tenant exists
        conn = get_db_connection_simple()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT tenant_id, plan_type, plan_level FROM tenants WHERE tenant_id = %s", (tenant_id,))
        tenant = cur.fetchone()
        
        if not tenant:
            cur.close()
            return_db_connection(conn)
            return jsonify({'error': 'Tenant not found'}), 404
        
        # Build update query dynamically
        updates = []
        values = []
        old_values = dict(tenant)
        
        # Plan mapping logic
        plan_type = data.get('plan_type')
        plan_level = data.get('plan_level')
        
        from common.tier_quotas import PLAN_LEVELS as plan_hierarchy, LEVEL_TO_TIER
        if plan_type and plan_level is None:
            plan_level = plan_hierarchy.get(plan_type, 0)
        elif plan_level is not None and not plan_type:
            plan_type = LEVEL_TO_TIER.get(plan_level, 'basic')

        # Allowed fields to update
        allowed_fields = {
            'plan_type': plan_type,
            'plan_level': plan_level,
            'contract_end_date': data.get('contract_end_date'),
            'billing_email': data.get('billing_email'),
            'notes': data.get('notes'),
            'sales_contact': data.get('sales_contact'),
            'support_level': data.get('support_level'),
            'max_area_hectares': data.get('max_area_hectares'),
            'max_users': data.get('max_users'),
            'max_sensors': data.get('max_sensors'),
            'max_robots': data.get('max_robots')
        }
        
        # Validate plan_type if provided
        if allowed_fields['plan_type']:
            plan = allowed_fields['plan_type']
            if plan not in ('basic', 'premium', 'pro', 'enterprise'):
                cur.close()
                return_db_connection(conn)
                return jsonify({'error': f'Invalid plan_type: {plan}.'}), 400
        
        # Build update statement
        for field, value in allowed_fields.items():
            if value is not None:
                updates.append(f"{field} = %s")
                values.append(value)
        
        if not updates:
            cur.close()
            return_db_connection(conn)
            return jsonify({'error': 'No fields to update'}), 400
        
        # Add updated_at and WHERE clause ID
        updates.append("updated_at = NOW()")
        values.append(tenant_id)
        
        # Execute update
        query = f"UPDATE tenants SET {', '.join(updates)} WHERE tenant_id = %s RETURNING *"
        cur.execute(query, values)
        updated_tenant = cur.fetchone()
        
        # Sync with Orion-LD for backwards compatibility
        limits_update = {}
        if allowed_fields['plan_type']:
            limits_update['planType'] = allowed_fields['plan_type']
        if allowed_fields['max_users'] is not None:
            limits_update['maxUsers'] = allowed_fields['max_users']
        if allowed_fields['max_robots'] is not None:
            limits_update['maxRobots'] = allowed_fields['max_robots']
        if allowed_fields['max_sensors'] is not None:
            limits_update['maxSensors'] = allowed_fields['max_sensors']
        if allowed_fields['max_area_hectares'] is not None:
            limits_update['maxAreaHectares'] = allowed_fields['max_area_hectares']
            
        if limits_update:
            upsert_limits_in_orion(tenant_id, limits_update)
            # Invalidate cache
            _limits_cache.pop(tenant_id, None)
            _limits_cache_ts.pop(tenant_id, None)
        
        # Log audit trail
        try:
            cur.execute("""
                INSERT INTO tenant_governance_audit 
                (tenant_id, changed_by, change_type, old_values, new_values, notes)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                tenant_id,
                getattr(g, 'user', None) or 'unknown',
                'governance_update',
                json.dumps(old_values),
                json.dumps(dict(updated_tenant), default=str),
                data.get('audit_notes')
            ))
        except Exception as audit_err:
            logger.warning(f"Failed to write audit log: {audit_err}")
        
        conn.commit()
        cur.close()
        return_db_connection(conn)
        
        return jsonify({
            'message': 'Tenant governance updated successfully',
            'tenant_id': tenant_id,
            'tenant': dict(updated_tenant)
        }), 200
        
    except Exception as e:
        logger.error(f"Error updating tenant governance: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500


# === Lines 5218-5698 from entity_management_api.py ===
@admin_bp.route('/api/admin/audit-logs', methods=['GET'])
@require_auth(require_hmac=False)
def get_audit_logs():
    """
    Get audit logs with filtering and pagination.
    Only accessible to PlatformAdmin.
    """
    tenant_id = getattr(g, 'tenant_id', None) or getattr(g, 'tenant', None)
    user_roles = _get_user_roles()
    
    # Check permissions - only PlatformAdmin
    if 'PlatformAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions. PlatformAdmin required.'}), 403
    
    if not POSTGRES_URL:
        return jsonify({'error': 'Database not configured'}), 503
    
    # Parse query parameters
    filter_tenant = request.args.get('tenant_id')
    filter_module = request.args.get('module_id')
    filter_user = request.args.get('user_id')
    filter_action = request.args.get('action')
    filter_event_type = request.args.get('event_type')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    
    # Pagination
    page = int(request.args.get('page', 1))
    per_page = min(int(request.args.get('per_page', 50)), 500)  # Max 500 per page
    offset = (page - 1) * per_page
    
    try:
        with get_db_connection_with_tenant(tenant_id or filter_tenant or 'bootstrap') as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Check if sys_audit_logs table exists
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public'
                    AND table_name = 'sys_audit_logs'
                )
            """)
            table_exists = cursor.fetchone()['exists']
            
            if not table_exists:
                logger.warning("sys_audit_logs table does not exist, returning empty audit logs")
                cursor.close()
                return jsonify({
                    'logs': [],
                    'pagination': {
                        'page': page,
                        'per_page': per_page,
                        'total': 0,
                        'pages': 0,
                    },
                    'filters': {
                        'tenant_id': filter_tenant,
                        'module_id': filter_module,
                        'user_id': filter_user,
                        'action': filter_action,
                        'event_type': filter_event_type,
                        'date_from': date_from,
                        'date_to': date_to,
                    },
                    '_meta': {'table_exists': False},
                }), 200
            
            # Build WHERE clause
            where_conditions = []
            params = []
            
            if filter_tenant:
                where_conditions.append("tenant_id = %s")
                params.append(filter_tenant)
            
            if filter_module:
                where_conditions.append("module_id = %s")
                params.append(filter_module)
            
            if filter_user:
                where_conditions.append("user_id = %s")
                params.append(filter_user)
            
            if filter_action:
                where_conditions.append("action = %s")
                params.append(filter_action)
            
            if filter_event_type:
                where_conditions.append("event_type = %s")
                params.append(filter_event_type)
            
            if date_from:
                where_conditions.append("created_at >= %s")
                params.append(date_from)
            
            if date_to:
                where_conditions.append("created_at <= %s")
                params.append(date_to)
            
            where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""
            
            # Count total (for pagination)
            count_query = f"SELECT COUNT(*) as total FROM sys_audit_logs {where_clause}"
            cursor.execute(count_query, params)
            total = cursor.fetchone()['total']
            
            # Get logs
            query = f"""
                SELECT 
                    id, tenant_id, user_id, username, module_id,
                    event_type, action, resource_type, resource_id,
                    success, error, ip_address, user_agent,
                    metadata, created_at
                FROM sys_audit_logs
                {where_clause}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """
            params.extend([per_page, offset])
            cursor.execute(query, params)
            rows = cursor.fetchall()
            cursor.close()
        
        # Format results
        logs = []
        for row in rows:
            log = dict(row)
            log['createdAt'] = log['created_at'].isoformat() if log.get('created_at') else None
            log.pop('created_at', None)
            logs.append(log)
        
        return jsonify({
            'logs': logs,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': (total + per_page - 1) // per_page,
            },
            'filters': {
                'tenant_id': filter_tenant,
                'module_id': filter_module,
                'user_id': filter_user,
                'action': filter_action,
                'event_type': filter_event_type,
                'date_from': date_from,
                'date_to': date_to,
            },
            '_meta': {'table_exists': True},
        }), 200

    except Exception as e:
        logger.error(f"Error fetching audit logs: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Failed to fetch audit logs', 'details': str(e)}), 500


# =============================================================================
# Mobile Offline Sync API
# =============================================================================

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
            points = coords[0][0] # First polygon representing outer boundary
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
        except: return 0

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


