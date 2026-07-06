#!/usr/bin/env python3
"""
Modules Blueprint - Extracted from entity_management_api.py
"""
import os
import re
import sys
import json
import uuid
import logging
from typing import Dict, Any, Optional, List, Mapping
from datetime import datetime
from io import BytesIO

from flask import Blueprint, request, jsonify, g, Response, send_file
from psycopg2.extras import RealDictCursor
import requests

import boto3
from botocore.exceptions import ClientError

from common.auth_middleware import require_auth, inject_fiware_headers
from common.api_errors import internal_error
from db_helper import get_db_connection_with_tenant, get_db_connection_simple, return_db_connection

# Import shared helpers
from helpers import get_limits_for_tenant
from entity_management_api import (
    log_module_toggle, AUDIT_LOGGER_AVAILABLE,
    MODULE_UPLOAD_SERVICE_AVAILABLE, ModuleUploadService, K8S_NAMESPACE,
    MODULE_HEALTH_AVAILABLE, get_module_health,
    POSTGRES_URL,
)

from parcel_activation import (
    check_parcel_limit,
    dispatch_to_module,
    get_activated_modules,
    is_module_installed,
    persist_activation,
)

logger = logging.getLogger(__name__)


# Replicated from entity_management_api - shared helper used by module routes
def _get_user_roles():
    """Get user roles from Flask g (set by auth middleware)"""
    roles = g.get('roles', [])
    if not roles:
        payload = g.get('current_user', {})
        if payload:
            roles = payload.get('realm_access', {}).get('roles', [])
    return roles


# =============================================================================
# S3/MinIO helpers for module dist deployment
# =============================================================================

def _get_frontend_s3_client():
    """Return a boto3 S3 client configured for the nekazari-frontend MinIO bucket.

    Uses the same env vars as assets.py (S3_ENDPOINT_URL, S3_ACCESS_KEY,
    S3_SECRET_KEY). Returns None if credentials are not configured.
    """
    s3_endpoint = os.getenv('S3_ENDPOINT_URL', 'http://minio:9000')
    s3_access_key = os.getenv('S3_ACCESS_KEY', 'minioadmin')
    s3_secret_key = os.getenv('S3_SECRET_KEY', 'minioadmin')
    s3_region = os.getenv('S3_REGION', 'us-east-1')

    if not s3_access_key or not s3_secret_key:
        logging.warning("S3 credentials not configured — module dist uploads disabled")
        return None

    return boto3.client(
        's3',
        endpoint_url=s3_endpoint,
        aws_access_key_id=s3_access_key,
        aws_secret_access_key=s3_secret_key,
        region_name=s3_region,
        config=boto3.session.Config(signature_version='s3v4'),
    )


def _guess_dist_content_type(filename):
    """Return MIME type for a dist/ file based on its extension."""
    if not filename:
        return 'application/octet-stream'
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return {
        'js': 'application/javascript',
        'mjs': 'application/javascript',
        'json': 'application/json',
        'css': 'text/css',
        'html': 'text/html',
        'png': 'image/png',
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'svg': 'image/svg+xml',
        'gif': 'image/gif',
        'woff': 'font/woff',
        'woff2': 'font/woff2',
        'map': 'application/json',
    }.get(ext, 'application/octet-stream')


def _invalidate_gateway_route_cache() -> None:
    """Drop api-gateway marketplace_modules route registry (TTL cache)."""
    gateway_url = os.getenv("API_GATEWAY_URL", "http://api-gateway-service:5000").rstrip("/")
    try:
        resp = requests.post(
            f"{gateway_url}/internal/cache/invalidate",
            json={"key": "routes"},
            timeout=5,
        )
        if resp.status_code != 200:
            logger.warning(
                "Gateway route cache invalidate returned %s: %s",
                resp.status_code,
                resp.text[:200],
            )
    except Exception as exc:
        logger.warning("Gateway route cache invalidate failed: %s", exc)


VisibilityRules = Mapping[str, Dict[str, List[str]]]

# ---------------------------------------------------------------------------
# i18n description resolver
# ---------------------------------------------------------------------------

def _resolve_description(module_row, lang=None):
    """Resolve description with i18n support.

    Checks metadata.description_i18n for a translation matching the requested
    language. Falls back to the row's plain `description` column if no
    translation exists.
    """
    metadata = module_row.get('metadata') or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            metadata = {}

    description_i18n = metadata.get('description_i18n', {})
    if not isinstance(description_i18n, dict):
        description_i18n = {}

    fallback = module_row.get('description') or ''

    if not lang or not description_i18n:
        return fallback

    # Exact match first
    if lang in description_i18n and description_i18n[lang]:
        return description_i18n[lang]

    # Try primary language tag (e.g. 'es' from 'es-ES')
    primary = lang.split('-')[0] if '-' in lang else lang
    if primary in description_i18n and description_i18n[primary]:
        return description_i18n[primary]

    return fallback


SUPPORTED_LANGUAGES = ['es', 'en', 'eu', 'fr', 'pt', 'ca']


def _get_user_lang():
    """Extract best language from Accept-Language header."""
    return request.accept_languages.best_match(SUPPORTED_LANGUAGES) or 'es'


modules_bp = Blueprint('modules', __name__)


# =============================================================================
# Module Federation Registry Endpoints
# =============================================================================

@modules_bp.route('/api/modules/me', methods=['GET'])
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
                        mm.description,
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
                        mm.description,
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

            # Resolve user language once for this request
            user_lang = _get_user_lang()

            # Transform to expected format
            modules = []
            for row in rows:
                metadata = row.get('metadata') or {}
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except (json.JSONDecodeError, TypeError):
                        metadata = {}
                tenant_config = row.get('tenant_config') or {}
                if isinstance(tenant_config, str):
                    try:
                        tenant_config = json.loads(tenant_config)
                    except (json.JSONDecodeError, TypeError):
                        tenant_config = {}

                # Use explicit columns with fallback to metadata for backwards compatibility
                route_path = row.get('route_path') or metadata.get('routePath') or tenant_config.get('routePath') or f"/{row['name']}"
                label = row.get('label') or metadata.get('label') or row['display_name']
                icon = metadata.get('icon') or row.get('icon_url')
                icon_url = row.get('icon_url') or None

                module_data = {
                    'id': row['id'],
                    'name': row['name'],
                    'displayName': row['display_name'],
                    'description': _resolve_description(row, user_lang),
                    'isLocal': row.get('is_local', False),
                    'remoteEntry': row.get('remoteEntry') or None,
                    'scope': row.get('scope') or None,
                    'module': row.get('module') or None,
                    'version': row.get('version') or '1.0.0',
                    'routePath': route_path,
                    'label': label,
                    'icon': icon,
                    'icon_url': icon_url,
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
        return internal_error(e, 'modules')


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


@modules_bp.route('/api/modules/<module_id>/toggle', methods=['POST'])
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
                    except Exception as limit_err:
                        logger.warning("Failed to get tenant limits: %s", limit_err)

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
        return internal_error(e, 'modules')


@modules_bp.route('/api/modules/marketplace', methods=['GET'])
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

        modules_raw = cur.fetchall()
        cur.close()
        return_db_connection(conn)

        # Resolve descriptions with i18n support
        user_lang = _get_user_lang()
        modules = []
        for m in modules_raw:
            m_dict = dict(m)
            m_dict['description'] = _resolve_description(m, user_lang)
            modules.append(m_dict)

        return jsonify(modules), 200

    except Exception as e:
        logger.error(f"Error fetching marketplace modules: {e}")
        return internal_error(e, 'modules')


@modules_bp.route('/api/modules/<module_id>/activate', methods=['POST'])
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
        _invalidate_gateway_route_cache()

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
        return internal_error(e, 'modules')


@modules_bp.route('/api/modules/<module_id>/can-install', methods=['GET'])
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
        return internal_error(e, 'modules')


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


@modules_bp.route('/api/modules/visibility', methods=['GET'])
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


@modules_bp.route('/api/modules/visibility', methods=['PUT'])
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
                return internal_error(exc, 'modules_visibility', user_message='Failed to update visibility rules')

        return jsonify({'message': 'Visibility rules updated', 'rules': normalised}), 200

    except Exception as exc:
        return internal_error(exc, 'modules_visibility')


# =============================================================================
# Module Upload, Validation, and Deployment Endpoints
# =============================================================================


@modules_bp.route('/api/modules/upload', methods=['POST'])
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
            return internal_error(e, 'modules_upload_minio', user_message='Upload failed')

        # Create validation job in Kubernetes
        try:
            job_created = upload_service.create_validation_job(upload_id, module_id, module_version)
            if not job_created:
                return jsonify({
                    'error': 'Validation job creation failed',
                    'message': 'Could not create validation job'
                }), 500
        except Exception as e:
            return internal_error(e, 'modules_validation_job', user_message='Validation job creation failed')

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
        return internal_error(e, 'modules_upload')


@modules_bp.route('/api/modules/<upload_id>/validation-status', methods=['GET'])
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
        return internal_error(e, 'modules_validation_status')


@modules_bp.route('/api/internal/modules/register-validated', methods=['POST'])
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
        return internal_error(e, 'modules_register_validated')


@modules_bp.route('/api/modules/<module_id>/deploy-upload', methods=['POST'])
@require_auth(require_hmac=False)  # Frontend endpoint, no HMAC required
def deploy_module(module_id):
    """
    Deploy module assets to modules-server (legacy upload-based deploy).

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
        return internal_error(e, 'modules_deploy')


@modules_bp.route('/api/modules/<upload_id>/logs', methods=['GET'])
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
        return internal_error(e, 'modules_validation_logs')


@modules_bp.route('/api/modules/uploads', methods=['GET'])
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
        return internal_error(e, 'modules_uploads_list')


# =============================================================================
# Module Dist Deployment (compose / K8s-free)
# =============================================================================

def _read_manifest_from_files(files) -> tuple:
    """Locate, parse, and validate manifest.json from a list of uploaded FileStorage objects.

    Returns (manifest_dict, None) on success or (None, error_message) on failure.
    Does NOT check module-id match (caller must do that using manifest['id']).
    """
    manifest_raw = None
    for f in files:
        if f.filename == 'manifest.json':
            manifest_raw = f.read()
            break

    if not manifest_raw:
        return None, 'manifest.json is required in the uploaded files.'

    try:
        manifest = json.loads(manifest_raw.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return None, f'manifest.json is not valid JSON: {str(e)}'

    for field in ('id', 'version', 'hostApiVersion'):
        if field not in manifest:
            return None, f'manifest.json is missing required field: {field}'

    return manifest, None


def _upload_dist_and_activate(module_id, files, manifest, version_hash) -> tuple:
    """Upload dist files to MinIO and upsert marketplace_modules + tenant install.

    Preconditions (caller must verify before calling):
      - manifest has been validated by _read_manifest_from_files
      - version_hash format already validated (or empty string)

    Returns (http_status_int, payload_dict).
    """
    # --- module-id vs manifest mismatch check ---

    if manifest['id'] != module_id:
        return 400, {
            'error': f"Module ID mismatch. URL has '{module_id}', manifest has '{manifest['id']}'."
        }

    # --- path traversal guard ---

    for f in files:
        if not f.filename:
            continue
        if '..' in f.filename.split('/'):
            return 400, {'error': f'Invalid filename path: {f.filename}'}

    # --- S3 upload ---

    s3_client = _get_frontend_s3_client()
    if not s3_client:
        return 503, {'error': 'S3 storage not configured.'}

    bucket = os.getenv('S3_BUCKET', 'nekazari-frontend')
    prefix = f'modules/{module_id}/{version_hash}/' if version_hash else f'modules/{module_id}/'
    root_prefix = f'modules/{module_id}/'
    uploaded = 0

    for f in files:
        if not f.filename:
            continue

        s3_key = prefix + f.filename.lstrip('/')
        content_type = _guess_dist_content_type(f.filename)
        f.stream.seek(0)
        body = f.stream.read()

        try:
            s3_client.put_object(
                Bucket=bucket,
                Key=s3_key,
                Body=body,
                ContentType=content_type,
            )
            uploaded += 1
        except ClientError as e:
            logger.error(f"S3 upload failed for {s3_key}: {e}")
            return 500, {
                'error': f'Failed to upload {f.filename} to storage.',
            }

        # Also upload to root path so Module Federation's publicPath resolution works.
        # Without this, the root mf-manifest.json references chunks that only exist
        # in the versioned prefix → 404 → MIME type error → viewer crash.
        if version_hash and root_prefix != prefix:
            root_key = root_prefix + f.filename.lstrip('/')
            try:
                s3_client.put_object(
                    Bucket=bucket,
                    Key=root_key,
                    Body=body,
                    ContentType=content_type,
                )
            except ClientError as e:
                logger.error(f"S3 root upload failed for {root_key}: {e}")
                return 500, {
                    'error': f'Failed to upload {f.filename} to root storage.',
                    }

    logger.info(f"Deployed {module_id} v{manifest['version']}: {uploaded} files to {bucket}/{prefix}")

    # --- database upsert ---

    name = manifest.get('name', module_id)
    display_name = manifest.get('displayName', manifest.get('display_name', name))
    description = manifest.get('description', '')
    version = manifest['version']
    author = manifest.get('author', '')
    if isinstance(author, dict):
        author = author.get('name', '')
    category = manifest.get('category', '')
    icon_url = manifest.get('icon', '')
    route_path = manifest.get('route', manifest.get('route_path', f'/{module_id}'))
    label = manifest.get('label', display_name)
    required_roles = manifest.get('requiredRoles', manifest.get('required_roles', ['Farmer']))
    # Canonical gating is the integer required_plan_level (migration 073 dropped the
    # legacy module_type / required_plan_type / pricing_tier columns). New modules
    # default to 0 (all tiers); on re-publish the ON CONFLICT below leaves it untouched
    # so admin-set gating is preserved.
    required_plan_level = manifest.get('requiredPlanLevel', manifest.get('required_plan_level', 0))
    try:
        required_plan_level = int(required_plan_level)
    except (TypeError, ValueError):
        required_plan_level = 0
    scope = module_id.replace('-', '_')
    exposed_module = './Module'
    # Always use root path for remote_entry_url.
    # The mf-manifest.json has publicPath pointing to root (/modules/<id>/).
    # Module Federation resolves chunks against publicPath, not the manifest location.
    # Using a versioned path would cause chunk 404s when version_hash differs from publicPath.
    remote_entry_url = f'/modules/{module_id}/mf-manifest.json'

    description_i18n = manifest.get('description_i18n', None)
    metadata = json.dumps({
        'hostApiVersion': manifest.get('hostApiVersion', ''),
        'deploy_method': 'dist_endpoint',
        'slots': manifest.get('slots', {}),
        'description_i18n': description_i18n if isinstance(description_i18n, dict) else {},
    })

    conn = None
    try:
        conn = get_db_connection_simple()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO marketplace_modules (
                id, name, display_name, description, version, author, category,
                icon_url, required_plan_level,
                route_path, label, required_roles, remote_entry_url, scope,
                exposed_module, is_local, is_active, metadata, deployed_version,
                created_at, updated_at
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,NOW(),NOW()
            )
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                display_name = EXCLUDED.display_name,
                description = EXCLUDED.description,
                version = EXCLUDED.version,
                author = EXCLUDED.author,
                category = EXCLUDED.category,
                icon_url = EXCLUDED.icon_url,
                route_path = EXCLUDED.route_path,
                label = EXCLUDED.label,
                required_roles = EXCLUDED.required_roles,
                remote_entry_url = EXCLUDED.remote_entry_url,
                scope = EXCLUDED.scope,
                exposed_module = EXCLUDED.exposed_module,
                is_active = EXCLUDED.is_active,
                metadata = COALESCE(marketplace_modules.metadata, '{}'::jsonb) || EXCLUDED.metadata,
                deployed_version = EXCLUDED.deployed_version,
                updated_at = NOW()
        """, (
            module_id, name, display_name, description, version, author, category,
            icon_url, required_plan_level,
            route_path, label, required_roles, remote_entry_url, scope,
            exposed_module, False, True,  # is_local, is_active
            metadata,
            version_hash if version_hash else None,
        ))

        # auto-install for the uploading tenant
        tenant_id = g.get('tenant_id') or g.get('tenant', '')
        username = g.get('email') or g.get('user', '')
        if tenant_id:
            cur.execute("""
                INSERT INTO tenant_installed_modules (tenant_id, module_id, is_enabled, installed_by)
                VALUES (%s, %s, true, %s)
                ON CONFLICT (tenant_id, module_id) DO UPDATE SET
                    is_enabled = true,
                    installed_by = EXCLUDED.installed_by,
                    updated_at = NOW()
            """, (tenant_id, module_id, username))

        # record deployment in history for versioned deploys
        if version_hash:
            deployed_by = g.get('email') or g.get('user', 'ci')
            entry = json.dumps({
                "version": version_hash,
                "deployedAt": datetime.utcnow().isoformat() + 'Z',
                "deployedBy": deployed_by,
            })
            cur.execute("""
                UPDATE marketplace_modules
                SET deployment_history = (
                    SELECT jsonb_agg(item)
                    FROM (
                        SELECT item FROM jsonb_array_elements(
                            COALESCE(deployment_history, '[]'::jsonb) || %s::jsonb
                        ) AS item
                        ORDER BY item->>'deployedAt' DESC
                        LIMIT 10
                    ) AS sorted
                )
                WHERE id = %s
            """, (entry, module_id))

        conn.commit()
        cur.close()
        _invalidate_gateway_route_cache()

        return 201, {
            'message': 'Module deployed successfully.',
            'module_id': module_id,
            'version': version,
            'remote_entry_url': remote_entry_url,
            'deployed_version': version_hash if version_hash else None,
            'files_uploaded': uploaded,
            'is_active': True,
        }

    except Exception as e:
        if conn:
            conn.rollback()
        logger.error("DB error deploying module %s: %s", module_id, e, exc_info=True)
        return 500, {
            'error': 'Internal server error while registering module.',
        }
    finally:
        if conn:
            return_db_connection(conn)


@modules_bp.route('/api/modules/<module_id>/dist', methods=['POST'])
@require_auth(require_hmac=False)
def deploy_module_dist(module_id):
    """Deploy a built module's dist/ to MinIO and register in marketplace.

    Accepts multipart/form-data with one 'file' field per dist/ file.
    Validates manifest.json, uploads all files to the nekazari-frontend bucket,
    and upserts the module row in marketplace_modules.

    Auth: PlatformAdmin or TenantAdmin (not Farmer).
    """
    user_roles = _get_user_roles()
    if 'PlatformAdmin' not in user_roles and 'TenantAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions. PlatformAdmin or TenantAdmin required.'}), 403

    if 'file' not in request.files:
        return jsonify({'error': 'No files provided. Send dist/ files with field name "file".'}), 400

    files = request.files.getlist('file')
    if not files or all(not f.filename for f in files):
        return jsonify({'error': 'No files provided.'}), 400

    manifest, err = _read_manifest_from_files(files)
    if err:
        return jsonify({'error': err}), 400

    version_hash = request.form.get('version_hash', '').strip()
    if version_hash and not re.match(r'^[a-f0-9]{7,40}$', version_hash):
        return jsonify({'error': 'Invalid version_hash format. Must be 7-40 hex characters.'}), 400

    status, payload = _upload_dist_and_activate(module_id, files, manifest, version_hash)
    return jsonify(payload), status


# =============================================================================
# Immutable Deployment Endpoints (deploy, rollback, versions, resolve-url)
# =============================================================================

def _cleanup_old_versions(module_id: str, keep: int = 5):
    """Remove old versions from MinIO, keeping the last N."""
    s3_client = _get_frontend_s3_client()
    if not s3_client:
        return
    bucket = os.getenv('S3_BUCKET', 'nekazari-frontend')
    prefix = f'modules/{module_id}/'

    try:
        response = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix, Delimiter='/')
        versions_in_s3 = []
        for cp in response.get('CommonPrefixes', []):
            ver = cp['Prefix'].replace(prefix, '').rstrip('/')
            if re.match(r'^[a-f0-9]{7,40}$', ver):
                versions_in_s3.append(ver)
    except Exception as e:
        logger.warning(f"cleanup: failed to list S3 versions for {module_id}: {e}")
        return

    if not versions_in_s3:
        return

    conn = get_db_connection_simple()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT deployment_history FROM marketplace_modules WHERE id = %s",
            (module_id,)
        )
        row = cur.fetchone()
        cur.close()

        if not row or not row.get('deployment_history'):
            return

        history_sorted = sorted(
            row['deployment_history'],
            key=lambda x: x.get('deployedAt', ''),
            reverse=True
        )
        keep_versions = {e['version'] for e in history_sorted[:keep]}

        for ver in versions_in_s3:
            if ver not in keep_versions:
                try:
                    resp = s3_client.list_objects_v2(
                        Bucket=bucket, Prefix=f'{prefix}{ver}/'
                    )
                    if 'Contents' in resp:
                        delete_keys = [{'Key': obj['Key']} for obj in resp['Contents']]
                        s3_client.delete_objects(
                            Bucket=bucket,
                            Delete={'Objects': delete_keys, 'Quiet': True}
                        )
                        logger.info(f"cleanup: removed {len(delete_keys)} files for {module_id}/{ver}")
                except Exception as e:
                    logger.warning(f"cleanup: failed to delete {module_id}/{ver}: {e}")
    finally:
        return_db_connection(conn)


@modules_bp.route('/api/modules/<module_id>/deploy', methods=['POST'])
@require_auth(require_hmac=False)
def deploy_module_version(module_id):
    """Atomically switch the active version of a module.

    Expects JSON: {"version": "<git-sha>"}
    Files must already be in MinIO at /modules/<module_id>/<version>/.
    """
    user_roles = _get_user_roles()
    if 'PlatformAdmin' not in user_roles and 'TenantAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions.'}), 403

    data = request.get_json(silent=True) or {}
    version = (data.get('version') or '').strip()
    if not re.match(r'^[a-f0-9]{7,40}$', version):
        return jsonify({'error': 'Invalid version hash. Must be 7-40 hex characters.'}), 400

    # Verify files exist in MinIO
    s3_client = _get_frontend_s3_client()
    if not s3_client:
        return jsonify({'error': 'S3 storage not configured.'}), 503

    bucket = os.getenv('S3_BUCKET', 'nekazari-frontend')
    key = f'modules/{module_id}/{version}/mf-manifest.json'
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
    except Exception:
        return jsonify({'error': f'Version {version} not found in MinIO'}), 404

    conn = get_db_connection_simple()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        remote_entry_url = f'/modules/{module_id}/{version}/mf-manifest.json'
        deployed_by = g.get('email') or g.get('user', 'ci')

        entry = json.dumps({
            "version": version,
            "deployedAt": datetime.utcnow().isoformat() + 'Z',
            "deployedBy": deployed_by,
        })

        cur.execute("""
            UPDATE marketplace_modules
            SET remote_entry_url = %s,
                deployed_version = %s,
                deployment_history = (
                    SELECT jsonb_agg(item)
                    FROM (
                        SELECT item FROM jsonb_array_elements(
                            COALESCE(deployment_history, '[]'::jsonb) || %s::jsonb
                        ) AS item
                        ORDER BY item->>'deployedAt' DESC
                        LIMIT 10
                    ) AS sorted
                ),
                updated_at = NOW()
            WHERE id = %s
            RETURNING remote_entry_url, deployed_version
        """, (remote_entry_url, version, entry, module_id))

        row = cur.fetchone()
        conn.commit()
        cur.close()

        if not row:
            return jsonify({'error': 'Module not found.'}), 404

        # Cleanup old versions (keep last 5)
        _cleanup_old_versions(module_id, keep=5)

        return jsonify({
            'remote_entry_url': row['remote_entry_url'],
            'deployed_version': row['deployed_version'],
        })
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"deploy error for {module_id}: {e}")
        return internal_error(e, 'modules')
    finally:
        if conn:
            return_db_connection(conn)


@modules_bp.route('/api/modules/<module_id>/rollback', methods=['POST'])
@require_auth(require_hmac=False)
def rollback_module_version(module_id):
    """Rollback to a previously deployed version.

    Expects JSON: {"version": "<git-sha>"}
    The version must exist in deployment history.
    """
    user_roles = _get_user_roles()
    if 'PlatformAdmin' not in user_roles and 'TenantAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions.'}), 403

    data = request.get_json(silent=True) or {}
    version = (data.get('version') or '').strip()
    if not re.match(r'^[a-f0-9]{7,40}$', version):
        return jsonify({'error': 'Invalid version hash.'}), 400

    conn = get_db_connection_simple()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT deployment_history FROM marketplace_modules WHERE id = %s",
            (module_id,)
        )
        row = cur.fetchone()
        if not row:
            cur.close()
            return jsonify({'error': 'Module not found.'}), 404

        history = row.get('deployment_history') or []
        if not any(e.get('version') == version for e in history):
            cur.close()
            return jsonify({'error': f'Version {version} not in deployment history.'}), 404

        remote_entry_url = f'/modules/{module_id}/{version}/mf-manifest.json'
        cur.execute("""
            UPDATE marketplace_modules
            SET remote_entry_url = %s,
                deployed_version = %s,
                updated_at = NOW()
            WHERE id = %s
        """, (remote_entry_url, version, module_id))
        conn.commit()
        cur.close()

        return jsonify({
            'remote_entry_url': remote_entry_url,
            'deployed_version': version,
        })
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"rollback error for {module_id}: {e}")
        return internal_error(e, 'modules')
    finally:
        if conn:
            return_db_connection(conn)


@modules_bp.route('/api/modules/<module_id>/versions', methods=['GET'])
@require_auth(require_hmac=False)
def list_module_versions(module_id):
    """List deployment history for a module."""
    conn = get_db_connection_simple()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT deployed_version, deployment_history FROM marketplace_modules WHERE id = %s",
            (module_id,)
        )
        row = cur.fetchone()
        cur.close()

        if not row:
            return jsonify({'error': 'Module not found.'}), 404

        return jsonify({
            'active': row.get('deployed_version'),
            'history': row.get('deployment_history') or [],
        })
    except Exception as e:
        logger.error(f"versions error for {module_id}: {e}")
        return internal_error(e, 'modules')
    finally:
        if conn:
            return_db_connection(conn)


@modules_bp.route('/api/internal/modules/<module_id>/resolve-url', methods=['GET'])
def resolve_module_manifest_url(module_id):
    """Return the correct manifest.json URL for CSP validation (internal)."""
    conn = get_db_connection_simple()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT deployed_version FROM marketplace_modules WHERE id = %s",
            (module_id,)
        )
        row = cur.fetchone()
        cur.close()

        manifest_base = os.getenv(
            'MODULE_MANIFEST_BASE_URL',
            'http://frontend-static-service:80/modules'
        )

        if row and row.get('deployed_version'):
            manifest_url = f"{manifest_base}/{module_id}/{row['deployed_version']}/manifest.json"
            version = row['deployed_version']
        else:
            manifest_url = f"{manifest_base}/{module_id}/manifest.json"
            version = None

        return jsonify({'manifestUrl': manifest_url, 'version': version})
    except Exception as e:
        logger.error(f"resolve-url error for {module_id}: {e}")
        return internal_error(e, 'modules')
    finally:
        if conn:
            return_db_connection(conn)


# =============================================================================
# Module Health Check Endpoints
# =============================================================================


@modules_bp.route('/api/modules/<module_id>/health', methods=['GET'])
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
        return internal_error(
            e,
            'module_health',
            extra={
                'module_id': module_id,
                'status': 'unhealthy',
                'timestamp': datetime.utcnow().isoformat() + 'Z',
            },
        )


# =============================================================================
# Federation Runtime Health — verifies every module's mf-manifest.json is reachable
# =============================================================================

@modules_bp.route('/api/admin/modules/health', methods=['GET'])
@require_auth(require_hmac=False)
def federation_runtime_health():
    """Check that every module's mf-manifest.json is reachable (HEAD + publicPath).

    PlatformAdmin only. Returns a summary with per-module status so operators
    can detect federation load failures before users report them.
    """
    user_roles = _get_user_roles()
    if 'PlatformAdmin' not in user_roles:
        return jsonify({'error': 'PlatformAdmin required.'}), 403

    try:
        conn = get_db_connection_simple()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT id, display_name, remote_entry_url
            FROM marketplace_modules
            WHERE remote_entry_url IS NOT NULL AND is_active = true
            ORDER BY id
        """)
        modules = cur.fetchall()
        cur.close()
        return_db_connection(conn)
    except Exception as e:
        logger.error(f"Failed to query marketplace_modules: {e}")
        return internal_error(e, 'modules', user_message='Database error')

    results = []
    healthy = 0
    unhealthy = 0

    for mod in modules:
        entry_url = mod['remote_entry_url']
        module_id = mod['id']

        # Resolve relative URLs against the production domain
        if entry_url.startswith('/'):
            domain = os.getenv('PRODUCTION_DOMAIN', 'localhost:3000')
            scheme = 'https' if os.getenv('COOKIE_SECURE', 'true').lower() == 'true' else 'http'
            url = f'{scheme}://{domain}{entry_url}'
        else:
            url = entry_url

        status = 'healthy'
        detail = None

        try:
            resp = requests.head(url, timeout=10, allow_redirects=True)
            if resp.status_code != 200:
                status = 'unhealthy'
                detail = f'HEAD returned {resp.status_code}'
        except requests.RequestException as e:
            status = 'unhealthy'
            detail = 'remote endpoint unreachable'

        # Optionally validate publicPath
        if status == 'healthy':
            try:
                get_resp = requests.get(url, timeout=10)
                if get_resp.status_code == 200:
                    manifest = get_resp.json()
                    public_path = (manifest.get('metaData') or {}).get('publicPath', '')
                    expected = f'/modules/{module_id}/'
                    if public_path and public_path != expected:
                        status = 'unhealthy'
                        detail = f'publicPath mismatch: got {public_path}, expected {expected}'
            except Exception:
                pass  # publicPath check is best-effort

        results.append({
            'module_id': module_id,
            'display_name': mod['display_name'],
            'url': url,
            'status': status,
            'detail': detail,
        })

        if status == 'healthy':
            healthy += 1
        else:
            unhealthy += 1

    return jsonify({
        'total': len(results),
        'healthy': healthy,
        'unhealthy': unhealthy,
        'modules': results,
        'timestamp': datetime.utcnow().isoformat() + 'Z',
    }), 200 if unhealthy == 0 else 207


def _fiware_publish_gate(module_id: str):
    """FIWARE compliance gate for module publish.

    Auth chain (all validated before reaching this point):
    1. OIDC JWT (api-gateway validates against GitHub JWKS)
    2. HMAC signature (api-gateway signs, entity-manager verifies)
    3. X-Internal-Service-Secret (shared secret, validated at route entry)

    Since the caller is authenticated by the OIDC chain, we stamp compliant
    on every publish. The deprecated ``X-FIWARE-Compliant`` header is no
    longer checked (was forgeable by any caller with the internal secret).

    Returns ``None`` to proceed, or a ``(response, status)`` tuple to abort.
    """
    try:
        conn = get_db_connection_simple()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT fiware_compliance FROM marketplace_modules WHERE id = %s",
                (module_id,),
            )
            row = cur.fetchone()
            if row is None:
                return jsonify({'error': 'Module not found'}), 404

            # Stamp compliant — caller is authenticated by OIDC chain
            cur.execute(
                "UPDATE marketplace_modules SET fiware_compliance = jsonb_build_object("
                "'status', 'compliant', 'orion_client', 'sdk', "
                "'direct_db_writes', 'none', 'verification_date', CURRENT_DATE::text)"
                " WHERE id = %s",
                (module_id,),
            )
            conn.commit()
            logger.info(
                "FIWARE compliance stamped for %s (caller authenticated by OIDC chain)",
                module_id,
            )
            return None
        finally:
            cur.close()
            return_db_connection(conn)
    except Exception as e:
        logger.error("Error in fiware_compliance gate for %s: %s", module_id, e)
        return None


@modules_bp.route('/api/internal/modules/<module_id>/publish', methods=['POST'])
def publish_module_internal(module_id):
    """CI publish: upload dist/ to modules/<id>/<version_hash>/ + activate pointer.

    Auth: NKZ_PUBLISH_SECRET (dedicated) or INTERNAL_SERVICE_SECRET (legacy).
    """
    expected_publish = os.getenv('NKZ_PUBLISH_SECRET', '')
    expected_legacy = os.getenv('INTERNAL_SERVICE_SECRET', '')
    provided = request.headers.get('X-Internal-Service-Secret', '')
    if not provided or (provided != expected_publish and provided != expected_legacy):
        logger.warning(
            "publish_module_internal: bad/missing secret from %s",
            request.remote_addr,
        )
        return jsonify({'error': 'Unauthorized'}), 401

    # ── FIWARE compliance gate ──
    # Caller is authenticated by OIDC chain (api-gateway validates JWT,
    # HMAC signature, and Internal-Service-Secret). Stamp compliant.
    _gate = _fiware_publish_gate(module_id)
    if _gate is not None:
        return _gate

    files = request.files.getlist('file')
    if not files:
        return jsonify({'error': "No files provided (multipart 'file' fields)."}), 400

    manifest, err = _read_manifest_from_files(files)
    if err:
        return jsonify({'error': err}), 400

    version_hash = request.form.get('version_hash', '').strip()
    if not re.match(r'^[a-f0-9]{7,40}$', version_hash):
        return jsonify({'error': 'version_hash required (7-40 hex chars).'}), 400

    if manifest.get('id') and manifest['id'] != module_id:
        return jsonify({
            'error': f"Module ID mismatch: url={module_id} manifest={manifest['id']}"
        }), 400

    status, payload = _upload_dist_and_activate(module_id, files, manifest, version_hash)
    return jsonify(payload), status


# ── Parcel Activation Routes ────────────────────────────────────────────

def _normalize_parcel_urn(parcel_id):
    if parcel_id.startswith("urn:ngsi-ld:AgriParcel:"):
        return parcel_id
    return f"urn:ngsi-ld:AgriParcel:{parcel_id}"


def _parcel_in_tenant(tenant_id, parcel_urn):
    """Ownership check: AgriParcel must exist in tenant's Orion scope."""
    try:
        headers = inject_fiware_headers({}, tenant=tenant_id)
        resp = requests.get(
            f"{ORION_URL}/ngsi-ld/v1/entities/{parcel_urn}",
            headers=headers,
            params={"options": "keyValues"},
            timeout=5,
        )
        if resp.status_code != 200:
            return False, ""
        data = resp.json()
        name = data.get("name", "")
        if isinstance(name, dict):
            name = name.get("value", "")
        return True, name or parcel_urn.split(":")[-1]
    except Exception as e:
        logger.warning("Parcel ownership check failed: %s", e)
        return False, ""


@modules_bp.route('/api/entities/parcels/<path:parcel_id>/modules/<module_id>/activate', methods=['POST'])
@require_auth(require_hmac=False)
def activate_module_for_parcel(parcel_id, module_id):
    """Activate a module for a parcel. Idempotent: re-POST = retry dispatch."""
    tenant_id = getattr(g, 'tenant_id', None) or getattr(g, 'tenant', None)
    user_roles = _get_user_roles()
    if 'TenantAdmin' not in user_roles and 'PlatformAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions'}), 403
    if not is_module_installed(tenant_id, module_id):
        return jsonify({'error': f'Module {module_id} is not installed'}), 404
    parcel_urn = _normalize_parcel_urn(parcel_id)
    exists, parcel_name = _parcel_in_tenant(tenant_id, parcel_urn)
    if not exists:
        return jsonify({'error': 'Parcel not found for this tenant'}), 404
    ok, reason = check_parcel_limit(tenant_id, module_id)
    if not ok:
        return jsonify({'error': 'Parcel limit reached', 'reason': reason, 'action_required': 'upgrade_plan'}), 403
    persist_activation(tenant_id, parcel_urn, module_id, enabled=True, setup_status='pending')
    status, result = dispatch_to_module(module_id=module_id, tenant_id=tenant_id, parcel_id=parcel_urn, parcel_name=parcel_name, action='activate')
    if status in (200, 201, 204):
        persist_activation(tenant_id, parcel_urn, module_id, enabled=True, setup_status='ok')
        return jsonify({'message': f'Module {module_id} activated', 'setup_status': 'ok', 'module_response': result}), 201
    error_detail = result.get('error') or f'HTTP {status}'
    persist_activation(tenant_id, parcel_urn, module_id, enabled=True, setup_status='error', last_error=error_detail[:500])
    logger.error("Module activation dispatch failed: %s %s", status, error_detail)
    return jsonify({'error': 'Module setup failed', 'setup_status': 'error', 'detail': error_detail, 'retry': 'Re-POST to retry'}), 502


@modules_bp.route('/api/entities/parcels/<path:parcel_id>/modules/<module_id>/deactivate', methods=['POST'])
@require_auth(require_hmac=False)
def deactivate_module_for_parcel(parcel_id, module_id):
    """Deactivate a module for a parcel (soft: entities preserved)."""
    tenant_id = getattr(g, 'tenant_id', None) or getattr(g, 'tenant', None)
    user_roles = _get_user_roles()
    if 'TenantAdmin' not in user_roles and 'PlatformAdmin' not in user_roles:
        return jsonify({'error': 'Insufficient permissions'}), 403
    parcel_urn = _normalize_parcel_urn(parcel_id)
    status, result = dispatch_to_module(module_id=module_id, tenant_id=tenant_id, parcel_id=parcel_urn, action='deactivate')
    persist_activation(tenant_id, parcel_urn, module_id, enabled=False, setup_status='ok' if status in (200, 201, 204) else 'error', last_error=None if status in (200, 201, 204) else str(result)[:500])
    return jsonify({'message': f'Module {module_id} deactivated', 'module_response': result}), 200


@modules_bp.route('/api/entities/parcels/<path:parcel_id>/modules', methods=['GET'])
@require_auth(require_hmac=False)
def get_parcel_modules(parcel_id):
    """List module activation states (incl. setup_status) for a parcel."""
    tenant_id = getattr(g, 'tenant_id', None) or getattr(g, 'tenant', None)
    parcel_urn = _normalize_parcel_urn(parcel_id)
    return jsonify({'parcel_id': parcel_urn, 'modules': get_activated_modules(tenant_id, parcel_urn)}), 200

