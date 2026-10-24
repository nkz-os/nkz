import os
import sys
import logging
from flask import request, jsonify, g
from functools import wraps

logger = logging.getLogger(__name__)

# Add common directory to path for keycloak_auth
# In Docker container, common is at /app/common; in dev, at ../common
common_paths = [
    os.path.join(os.path.dirname(__file__), "common"),  # Docker: /app/common
    os.path.join(os.path.dirname(__file__), "..", "common"),  # Dev: services/common
]
for path in common_paths:
    if os.path.exists(path) and path not in sys.path:
        sys.path.insert(0, path)

from api_errors import internal_error  # noqa: E402

# Keycloak (RS256 via JWKS) is the only accepted token validation. If the
# module cannot be imported, every token is rejected (fail-closed).
try:
    from keycloak_auth import (
        validate_keycloak_token,
        TokenValidationError,
        extract_tenant_id,
    )

    KEYCLOAK_AUTH_AVAILABLE = True
except ImportError:
    KEYCLOAK_AUTH_AVAILABLE = False


def get_request_token():
    """Extract JWT token from Authorization header or httpOnly cookie (fallback)"""
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.split(" ")[1]
    return request.cookies.get("nkz_token")


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = get_request_token()

        if not token:
            return jsonify({"message": "Token is missing!"}), 401

        if not KEYCLOAK_AUTH_AVAILABLE:
            logger.error("keycloak_auth unavailable; rejecting request (fail-closed)")
            return jsonify({"message": "Token is invalid!"}), 401

        try:
            payload = validate_keycloak_token(token)
            g.user = payload.get("preferred_username", "unknown")
            g.tenant = extract_tenant_id(payload) or "master"
            g.user_id = payload.get("sub")
            g.farmer_id = payload.get("sub")  # For compatibility
        except TokenValidationError:
            return jsonify({"message": "Token is invalid!"}), 401
        except Exception as e:
            return internal_error(e, "sdm_auth", user_message="Authentication error")

        return f(*args, **kwargs)

    return decorated


def inject_fiware_headers(headers, tenant=None, body=None):
    """Add NGSI-LD + FIWARE tenant headers for Orion-LD multitenancy.

    Delegates to the canonical implementation in ngsi_headers.py. With ``body``
    the @context delivery mode is derived from the payload (ld+json and no Link
    when it carries @context); without it, behaviour is unchanged.
    """
    if tenant is None:
        tenant = getattr(g, "tenant", "master")

    from ngsi_headers import inject_fiware_headers as _canonical

    has_context = isinstance(body, dict) and "@context" in body
    return _canonical(headers, tenant=tenant, has_context_in_body=has_context)


def log_entity_operation(
    operation,
    entity_id=None,
    entity_type=None,
    tenant=None,
    user_id=None,
    metadata=None,
):
    """Log entity operations for audit purposes"""
    tenant = tenant or getattr(g, "tenant", "unknown")
    user = user_id or getattr(g, "user", "unknown")
    logger.info(
        f"[AUDIT] {operation} {entity_type or 'unknown'} {entity_id or 'N/A'} by {user} in tenant {tenant}"
    )


def require_entity_ownership(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Check if user owns the entity (simplified implementation)
        # In a real implementation, you would check against the database
        return f(*args, **kwargs)

    return decorated
