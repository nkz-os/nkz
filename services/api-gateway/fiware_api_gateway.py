#!/usr/bin/env python3
# =============================================================================
# FIWARE API Gateway - NGSI-LD Production Service
# =============================================================================

import os
import json
import logging
import sys
from flask import Flask, g, request, jsonify, make_response
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_cors import cross_origin
import jwt
import requests

# Module routes blueprint
from module_routes import module_bp
from storage_routes import storage_bp
from module_csp import is_entity_type_allowed, is_timeseries_allowed
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import time
import uuid
import boto3
import re
import ipaddress
from collections import defaultdict, deque

# Configure logging FIRST
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PatSanitizingFilter(logging.Filter):
    """Redact nkz_pat_ tokens from log records."""

    _pat_re = re.compile(r"nkz_pat_[A-Za-z0-9_-]{32,}")

    def filter(self, record):
        if isinstance(record.msg, str):
            record.msg = self._pat_re.sub("nkz_pat_[REDACTED]", record.msg)
        if record.args:
            record.args = tuple(
                self._pat_re.sub("nkz_pat_[REDACTED]", str(a))
                if isinstance(a, str)
                else a
                for a in record.args
            )
        return True


# Attach to root logger
logging.getLogger().addFilter(PatSanitizingFilter())


# CORS configuration
_cors_env = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173")
ALLOWED_ORIGINS = {o.strip() for o in _cors_env.split(",") if o.strip()}
_cors_origins = list(ALLOWED_ORIGINS)

# Add common directory to path for keycloak_auth and tenant_utils
# Try both relative path (for local dev) and absolute path (for Docker)
common_paths = [os.path.join(os.path.dirname(__file__), "..", "common"), "/common"]
for path in common_paths:
    if os.path.exists(path) and path not in sys.path:
        sys.path.insert(0, path)

# Import Keycloak authentication
try:
    from keycloak_auth import (
        validate_keycloak_token,
        TokenValidationError,
        extract_tenant_id,
        generate_hmac_signature,
        get_request_token,
    )

    KEYCLOAK_AUTH_AVAILABLE = True
except ImportError as e:
    logger.error(f"Failed to import keycloak_auth: {e}")
    logger.warning("Falling back to old JWT_SECRET validation")
    KEYCLOAK_AUTH_AVAILABLE = False

    def get_request_token():
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            return auth_header.split(" ")[1]
        return request.cookies.get("nkz_token")


try:
    from gateway_pat import (
        is_pat_token,
        obtain_gateway_service_jwt,
        resolve_pat_info,
        resolve_pat_tenant_id,
    )
except ImportError:
    is_pat_token = lambda t: False  # noqa: E731

    def obtain_gateway_service_jwt():
        return None

    def resolve_pat_info(raw, base):
        return None

    def resolve_pat_tenant_id(raw, base):
        return None


app = Flask(__name__)
# Trust X-Forwarded-For from Traefik (needed for GitHub Actions IP allowlist)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
# CORS: Handled by Traefik Middleware at infrastructure level

# Configuration - All environment variables are REQUIRED for security
POSTGRES_URL = os.getenv("POSTGRES_URL")
JWT_SECRET = os.getenv("JWT_SECRET")  # Deprecated, kept for fallback
ORION_URL = os.getenv("ORION_URL")
if not ORION_URL:
    raise ValueError("ORION_URL environment variable is required")

KEYCLOAK_URL = os.getenv("KEYCLOAK_URL")
if not KEYCLOAK_URL:
    raise ValueError("KEYCLOAK_URL environment variable is required")

CONTEXT_URL = os.getenv("CONTEXT_URL")
if not CONTEXT_URL:
    raise ValueError("CONTEXT_URL environment variable is required")

GEOSERVER_URL = os.getenv("GEOSERVER_URL", "http://geoserver-service:8080")
TENANT_WEBHOOK_URL = os.getenv("TENANT_WEBHOOK_URL", "http://tenant-webhook:8080")
ENTITY_MANAGER_URL = os.getenv("ENTITY_MANAGER_URL", "http://entity-manager:5000")
DATAHUB_BFF_URL = os.getenv("DATAHUB_BFF_URL", "http://datahub-bff-service:8000")
NDVI_SERVICE_URL = os.getenv("NDVI_SERVICE_URL", "http://entity-manager:5000")
TENANT_USER_API_URL = os.getenv("TENANT_USER_API_URL", "http://tenant-user-api:5000")
CADASTRAL_API_URL = os.getenv("CADASTRAL_API_URL", "http://cadastral-api-service:5000")
SDM_INTEGRATION_URL = os.getenv(
    "SDM_INTEGRATION_URL", "http://sdm-integration-service:5000"
)
VEGETATION_API_URL = os.getenv(
    "VEGETATION_API_URL", "http://vegetation-prime-api-service:8000"
)
WEATHER_API_URL = os.getenv("WEATHER_API_URL", "http://weather-api-service:8000")
GEOCODE_URL = os.environ.get("GEOCODE_URL", "http://photon-service:2322")
INTELLIGENCE_API_URL = os.getenv(
    "INTELLIGENCE_API_URL", "http://intelligence-api-service:8000"
)
AGRIENERGY_API_URL = os.getenv(
    "AGRIENERGY_API_URL", "http://agrienergy-api-service:8000"
)
RISK_API_URL = os.getenv("RISK_API_URL", "http://risk-api-service:5000")
N8N_NKZ_API_URL = os.getenv("N8N_NKZ_API_URL", "http://n8n-nkz-api-service:8000")
N8N_PUBLIC_HOST = os.getenv("N8N_PUBLIC_HOST", "nekazari.robotika.cloud")
LIDAR_API_URL = os.getenv("LIDAR_API_URL", "http://lidar-api-service:80")
BIOORCHESTRATOR_API_URL = os.getenv(
    "BIOORCHESTRATOR_API_URL", "http://bioorchestrator-api-service:8420"
)
CROP_HEALTH_API_URL = os.getenv(
    "CROP_HEALTH_API_URL", "http://crop-health-api-service:8000"
)
GREENHOUSE_DT_URL = os.getenv(
    "GREENHOUSE_DT_URL", "http://greenhouse-dt-backend:8420"
)
FIELD_OPERATIONS_API_URL = os.getenv(
    "FIELD_OPERATIONS_API_URL", "http://field-operations-api-service:8420"
)
CARBON_API_URL = os.getenv(
    "CARBON_API_URL", "http://carbon-api-service:8000"
)
ROBOTICS_API_URL = os.getenv("ROBOTICS_API_URL", "http://robotics-api-service:80")
RISK_API_URL = os.getenv("RISK_API_URL", "http://risk-api-service:5000")
ROUTING_API_URL = os.getenv(
    "ROUTING_API_URL", "http://nkz-module-gis-routing-service:8000"
)
SOIL_API_URL = os.getenv("SOIL_API_URL", "http://soil-module-service:8000")
ZULIP_SERVICE_URL = os.getenv("ZULIP_SERVICE_URL", "http://zulip-service:80")
ZULIP_BOT_EMAIL = os.getenv("ZULIP_BOT_EMAIL", "")
ZULIP_BOT_API_KEY = os.getenv("ZULIP_BOT_API_KEY", "")
ZULIP_HOST = os.getenv("ZULIP_HOST", "messaging.robotika.cloud")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
REQUESTS_PER_MINUTE = int(
    os.getenv("REQUESTS_PER_MINUTE", "120")
)  # Default: 60 req/min per tenant
ALLOW_JWT_FALLBACK = os.getenv("ALLOW_JWT_FALLBACK", "false").lower() == "true"
COOKIE_DOMAIN = os.getenv("COOKIE_DOMAIN", ".robotika.cloud")

# CORS whitelist — configured via CORS_ORIGINS env var (comma-separated)
_cors_env = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173")
ALLOWED_ORIGINS = {o.strip() for o in _cors_env.split(",") if o.strip()}

# Set logging level
logging.getLogger().setLevel(getattr(logging, LOG_LEVEL))

# Rate limiting simple por tenant (ventana deslizante en memoria)
tenant_requests = defaultdict(deque)

# PAT scope -> allowed (method, path_prefix) tuples
PAT_SCOPE_ROUTES = {
    "timeseries": [
        ("GET", "/api/timeseries/"),
        ("POST", "/api/timeseries/"),
    ],
    "entities": [
        ("GET", "/ngsi-ld/v1/entities"),
        ("POST", "/ngsi-ld/v1/entityOperations/query"),
    ],
    "export": [
        ("POST", "/api/datahub/export"),
        ("POST", "/api/datahub/timeseries/align"),
    ],
    "telemetry": [
        ("GET", "/api/devices/"),
        ("GET", "/api/sensors"),
    ],
}


def _scope_hint_for_path(path: str) -> str:
    """Map path to scope name for 403 hints (no info leak)."""
    if path.startswith("/api/timeseries"):
        return "timeseries"
    if path.startswith("/ngsi-ld/v1/entities") or path.startswith(
        "/ngsi-ld/v1/entityOperations"
    ):
        return "entities"
    if path.startswith("/api/datahub/export") or path.startswith(
        "/api/datahub/timeseries/align"
    ):
        return "export"
    if path.startswith("/api/devices/") or path.startswith("/api/sensors"):
        return "telemetry"
    return "unknown"


def _tenant_zulip_stream_prefix(tenant: str) -> str:
    """Canonical Zulip stream prefix for a tenant.

    Centralized so callers cannot drift. Matches the on-disk DB tenant_id
    exactly — tenant_id values are already canonical (see
    services/common/tenant_utils.py), so no extra normalization is done here.
    """
    return f"tenant-{tenant}-"


_suspended_tenant_cache: dict[str, tuple[bool, float]] = {}
_SUSPENDED_CACHE_TTL = 300  # 5 minutes


def _is_tenant_suspended(tenant_id: str) -> bool:
    """Check if a tenant is suspended, with a 5-minute cache. Fail-open on DB errors."""
    now = time.time()
    cached = _suspended_tenant_cache.get(tenant_id)
    if cached and (now - cached[1]) < _SUSPENDED_CACHE_TTL:
        return cached[0]

    try:
        postgres_url = os.getenv("POSTGRES_URL")
        if not postgres_url:
            logger.critical("POSTGRES_URL is required but not set. Refusing to start.")
            raise RuntimeError("POSTGRES_URL environment variable is required")
        conn = psycopg2.connect(postgres_url, connect_timeout=3)
        cur = conn.cursor()
        cur.execute("SELECT deleted_at FROM tenants WHERE tenant_id = %s", (tenant_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        is_suspended = row is not None and row[0] is not None
    except Exception as e:
        logger.warning(f"Failed to check tenant suspension for {tenant_id}: {e}")
        is_suspended = False  # Fail-open

    _suspended_tenant_cache[tenant_id] = (is_suspended, now)
    return is_suspended


# ---------------------------------------------------------------------------
# GitHub Actions IP allowlist for internal CI-only endpoints
# ---------------------------------------------------------------------------

_github_actions_cache = {"ips": None, "fetched_at": 0.0}
_GH_IPS_TTL = 86400  # refresh every 24h


def _github_actions_ip_ranges():
    """Fetch GitHub Actions IP ranges from meta API, cached 24h."""
    now = time.time()
    cached = _github_actions_cache
    if cached["ips"] is not None and (now - cached["fetched_at"]) < _GH_IPS_TTL:
        return cached["ips"]
    try:
        resp = requests.get("https://api.github.com/meta", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            actions_ips = data.get("actions", [])
            logger.info(f"Loaded {len(actions_ips)} GitHub Actions IP ranges")
            _github_actions_cache["ips"] = frozenset(actions_ips)
            _github_actions_cache["fetched_at"] = now
            return _github_actions_cache["ips"]
    except Exception as e:
        logger.error(f"Failed to fetch GitHub Actions IP ranges: {e}")
    # Fail-closed: if we can't fetch, deny all
    return cached["ips"] or frozenset()


def _is_github_actions_ip(client_ip: str) -> bool:
    """Check if the given IP is within any GitHub Actions range."""
    if not client_ip:
        return False
    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for cidr in _github_actions_ip_ranges():
        try:
            if ip in ipaddress.ip_network(cidr):
                return True
        except ValueError:
            pass
    return False


# ---------------------------------------------------------------------------
# GitHub Actions OIDC JWT validation for internal CI endpoints
# Replaces IP-based allowlisting. The runner requests an OIDC token from
# token.actions.githubusercontent.com; the gateway validates the JWT
# against GitHub's JWKS and checks claims (iss, aud, repo, ref).
# ---------------------------------------------------------------------------

_GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
_GITHUB_OIDC_JWKS_URL = f"{_GITHUB_OIDC_ISSUER}/.well-known/jwks"
_GITHUB_OIDC_AUDIENCE = "nkz-publish"
_OIDC_JWKS_CLIENT = None
_OIDC_JWKS_LAST_FETCH = 0.0
_OIDC_JWKS_TTL = 3600.0


def _get_oidc_signing_key(token_headers):
    """Fetch the signing key from GitHub JWKS, cached 1h."""
    global _OIDC_JWKS_CLIENT, _OIDC_JWKS_LAST_FETCH
    now = time.time()
    if _OIDC_JWKS_CLIENT is None or (now - _OIDC_JWKS_LAST_FETCH) > _OIDC_JWKS_TTL:
        _OIDC_JWKS_CLIENT = jwt.PyJWKClient(_GITHUB_OIDC_JWKS_URL, cache_keys=False)
        _OIDC_JWKS_LAST_FETCH = now
        logger.info("OIDC: refreshed JWKS from GitHub")
    try:
        kid = token_headers.get("kid")
        return _OIDC_JWKS_CLIENT.get_signing_key(kid).key
    except Exception as e:
        logger.error(f"OIDC: cannot fetch signing key for kid={kid}: {e}")
        return None


def _validate_oidc_token(token, module_id=""):
    """
    Validate a GitHub Actions OIDC JWT.
    Returns True if the token is valid and the workflow is authorized.
    """
    if not token:
        return False
    try:
        # Log token length for debugging
        logger.info(
            f"OIDC: validating token ({len(token)} chars) for module={module_id}"
        )
        headers = jwt.get_unverified_header(token)
        signing_key = _get_oidc_signing_key(headers)
        if signing_key is None:
            return False

        payload = jwt.decode(
            token,
            key=signing_key,
            algorithms=["RS256"],
            issuer=_GITHUB_OIDC_ISSUER,
            audience=_GITHUB_OIDC_AUDIENCE,
            options={"require": ["exp", "iss", "aud"]},
        )

        repo = payload.get("repository", "")
        repo_owner = payload.get("repository_owner", "")
        ref = payload.get("ref", "")

        if repo_owner != "nkz-os":
            logger.warning(f"OIDC: rejected repo_owner={repo_owner}")
            return False

        # Accept default branch pushes (main/master) and tags
        if ref not in ("refs/heads/main", "refs/heads/master") and not ref.startswith("refs/tags/"):
            logger.warning(f"OIDC: rejected ref={ref}")
            return False

        logger.info(f"OIDC: validated repo={repo} ref={ref} module={module_id}")
        return True

    except jwt.ExpiredSignatureError:
        logger.warning("OIDC: token expired")
        return False
    except jwt.InvalidTokenError as e:
        logger.warning(f"OIDC: invalid token: {e}")
        return False
    except Exception as e:
        logger.error(f"OIDC: validation error: {e}")
        return False


@app.before_request
def enforce_pat_scopes():
    """Validate PAT tokens: check scope covers (method, path)."""
    if request.method == "OPTIONS":
        return None
    path = request.path or ""
    if path == "/health" or path.startswith("/health"):
        return None

    auth = request.headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return None

    tok = auth[7:].strip()
    if not tok.startswith("nkz_pat_"):
        return None

    # Resolve PAT metadata
    info = resolve_pat_info(tok, TENANT_WEBHOOK_URL)
    if not info:
        return jsonify({"error": "Invalid or expired PAT"}), 401

    scopes = info.get("scopes") or []
    if not scopes:
        return jsonify(
            {
                "error": "PAT has no scopes assigned",
                "required_scope_hint": _scope_hint_for_path(path),
            }
        ), 403

    # Check if any scope allows this (method, path)
    allowed = False
    for scope in scopes:
        for method, prefix in PAT_SCOPE_ROUTES.get(scope, []):
            if request.method == method and path.startswith(prefix):
                allowed = True
                break
        if allowed:
            break

    if not allowed:
        return jsonify(
            {
                "error": "PAT does not have required scope for this route",
                "required_scope_hint": _scope_hint_for_path(path),
            }
        ), 403

    # Store for downstream routes
    g.pat_info = info
    g.pat_tenant_id = info["tenant_id"]

    # Sanitize Authorization header for logging
    g.pat_auth_truncated = f"nkz_pat_...{tok[-8:]}"

    return None


PAT_ENTITIES_MAX_LIMIT = 500
PAT_ENTITIES_DEFAULT_LIMIT = 100
PAT_EXPORT_MAX_ROWS = 10000


@app.before_request
def enforce_pat_pagination():
    """Cap pagination and max_rows for PAT requests to entities/export endpoints."""
    if not hasattr(g, "pat_info"):
        return None

    path = request.path or ""
    scopes = g.pat_info.get("scopes") or []

    # Entities scope: cap pagination
    if "entities" in scopes:
        # GET /ngsi-ld/v1/entities — cap query param 'limit'
        if request.method == "GET" and path.startswith("/ngsi-ld/v1/entities"):
            limit_raw = request.args.get("limit") if request.args else None
            if limit_raw:
                try:
                    limit = int(limit_raw)
                except (ValueError, TypeError):
                    limit = PAT_ENTITIES_DEFAULT_LIMIT
                if limit > PAT_ENTITIES_MAX_LIMIT:
                    limit = PAT_ENTITIES_MAX_LIMIT
                elif limit < 1:
                    limit = PAT_ENTITIES_DEFAULT_LIMIT
            else:
                limit = PAT_ENTITIES_DEFAULT_LIMIT

            # Store capped limit for route handlers (request.args is immutable)
            g.pat_entity_limit = limit
            return None

        # POST /ngsi-ld/v1/entityOperations/query — cap JSON body 'limit'
        if request.method == "POST" and path == "/ngsi-ld/v1/entityOperations/query":
            if request.is_json:
                body = request.get_json(silent=True) or {}
                limit = body.get("limit")
                if limit is not None:
                    try:
                        limit = int(limit)
                    except (ValueError, TypeError):
                        limit = PAT_ENTITIES_DEFAULT_LIMIT
                    if limit > PAT_ENTITIES_MAX_LIMIT:
                        limit = PAT_ENTITIES_MAX_LIMIT
                    elif limit < 1:
                        limit = PAT_ENTITIES_DEFAULT_LIMIT
                else:
                    limit = PAT_ENTITIES_DEFAULT_LIMIT
                body["limit"] = limit
                g.pat_modified_body = body

            return None

    # Export scope: cap max_rows
    if (
        request.method == "POST"
        and path.startswith("/api/datahub/export")
        and "export" in scopes
    ):
        if request.is_json:
            body = request.get_json(silent=True) or {}
            max_rows = body.get("max_rows")
            if max_rows is not None:
                try:
                    max_rows = int(max_rows)
                except (ValueError, TypeError):
                    max_rows = PAT_EXPORT_MAX_ROWS
                if max_rows > PAT_EXPORT_MAX_ROWS:
                    max_rows = PAT_EXPORT_MAX_ROWS
                elif max_rows < 1:
                    max_rows = PAT_EXPORT_MAX_ROWS
            else:
                max_rows = PAT_EXPORT_MAX_ROWS
            body["max_rows"] = max_rows
            g.pat_modified_body = body

        return None


@app.before_request
def enforce_module_csp_of_data():
    """Fase A.2.3c — when a module-originated request (X-Module-Id) hits
    NGSI-LD or Timescale routes, validate the requested entity type or
    hypertable against the module's declared `data.entities` /
    `data.timeseries` in its published manifest. Fail-open when the
    manifest is missing or hasn't declared a list — no breaking change
    for modules that pre-date the manifest publish flow."""
    if request.method == "OPTIONS":
        return None
    module_id = request.headers.get("X-Module-Id")
    if not module_id:
        return None
    p = request.path or ""

    # NGSI-LD entity routes — the type lives in the query string for list/GET,
    # in the JSON body for POST/PATCH.
    if p.startswith("/api/ngsi-ld/v1/entities"):
        entity_type = request.args.get("type")
        if entity_type is None and request.method in ("POST", "PATCH"):
            body = request.get_json(silent=True) or {}
            entity_type = body.get("type") if isinstance(body, dict) else None
        if entity_type:
            allowed, reason = is_entity_type_allowed(module_id, entity_type)
            if not allowed:
                logger.warning("CSP block: %s", reason)
                return jsonify(
                    {
                        "error": "module-csp",
                        "detail": reason,
                    }
                ), 403
        return None

    # Timeseries routes — hypertable name is the segment immediately
    # following `/api/timeseries/`.
    if p.startswith("/api/timeseries/"):
        rest = p[len("/api/timeseries/") :].split("/", 1)[0]
        if rest:
            allowed, reason = is_timeseries_allowed(module_id, rest)
            if not allowed:
                logger.warning("CSP block: %s", reason)
                return jsonify(
                    {
                        "error": "module-csp",
                        "detail": reason,
                    }
                ), 403
        return None

    return None


def rate_limit(tenant: str) -> bool:
    """Devuelve True si permitido, False si excede el límite."""
    if REQUESTS_PER_MINUTE <= 0:
        return True
    now = time.time()
    window_start = now - 60
    q = tenant_requests[tenant]
    # limpiar ventana
    while q and q[0] < window_start:
        q.popleft()
    if len(q) >= REQUESTS_PER_MINUTE:
        return False
    q.append(now)
    return True


def get_cors_origin():
    """Return the validated CORS origin or None if not allowed"""
    origin = request.headers.get("Origin")
    if origin and origin in ALLOWED_ORIGINS:
        return origin
    return None


def set_cors_headers(response, origin=None):
    """Set CORS headers on a response if origin is allowed"""
    cors_origin = origin or get_cors_origin()
    if cors_origin:
        response.headers["Access-Control-Allow-Origin"] = cors_origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type, X-Tenant-ID, X-Module-Id, Cookie"
        )
        response.headers["Vary"] = "Origin"
    return response


@app.after_request
def add_security_headers(response):
    """Add security + CORS headers to all responses"""
    set_cors_headers(response)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000; includeSubDomains"
    )
    # Skip CSP for n8n tenant proxy — n8n sets its own CSP
    if not getattr(g, "skip_csp", False):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'none'"
        )
    response.headers.pop("Server", None)
    return response


def validate_jwt_token(token):
    """Validate JWT token - uses Keycloak if available, falls back to JWT_SECRET"""
    if KEYCLOAK_AUTH_AVAILABLE:
        try:
            payload = validate_keycloak_token(token)
            return payload
        except TokenValidationError as e:
            logger.warning(f"Keycloak validation failed: {e}")
            if not ALLOW_JWT_FALLBACK:
                logger.error(
                    f"Keycloak validation failed and JWT fallback disabled: {e}"
                )
                return None
        except Exception as e:
            logger.error(f"Unexpected error in keycloak validation: {e}")

    # Fallback to old JWT_SECRET validation
    if not JWT_SECRET:
        logger.error("No JWT_SECRET available for fallback")
        return None

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        logger.warning("Using deprecated JWT_SECRET validation (should use Keycloak)")
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("JWT token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid JWT token (fallback): {e}")
        return None


def inject_fiware_headers(headers, tenant=None):
    """Inject FIWARE service headers for NGSI-LD.

    Delegates to canonical ngsi_headers implementation.
    Auto-detects @context in request body for POST/PUT/PATCH requests.
    """
    from ngsi_headers import inject_fiware_headers as _canonical

    # Auto-detect @context in body (only for mutation requests)
    has_context_in_body = False
    if request.method in ("POST", "PUT", "PATCH") and request.is_json:
        json_body = request.get_json(silent=True)
        if isinstance(json_body, dict) and "@context" in json_body:
            has_context_in_body = True

    return _canonical(headers, tenant=tenant, has_context_in_body=has_context_in_body)


@app.route("/api/auth/session", methods=["POST", "OPTIONS"])
def create_session():
    """Set httpOnly cookie with JWT token (BFF session endpoint)"""
    if request.method == "OPTIONS":
        resp = make_response("", 204)
        return set_cors_headers(resp)

    data = request.get_json(silent=True)
    if not data or not data.get("token"):
        return jsonify({"error": "Missing token in request body"}), 400

    token = data["token"]

    # STRICT VALIDATION: Restore JWKS signature and issuer check
    payload = validate_jwt_token(token)
    if not payload:
        logger.warning("Session creation failed: Invalid or expired token")
        return jsonify({"error": "Invalid or expired token"}), 401

    # Block login for suspended tenants
    tenant = extract_tenant_id(payload)
    if tenant and tenant != "platform" and _is_tenant_suspended(tenant):
        logger.warning(f"Blocked login for suspended tenant: {tenant}")
        return jsonify(
            {
                "error": "TENANT_SUSPENDED",
                "message": "Tu cuenta ha sido suspendida. Contacta con el administrador de la plataforma.",
            }
        ), 403

    # Extract expiration for cookie max_age
    exp = payload.get("exp")
    max_age = max(int(exp - time.time()), 0) if exp else 3600

    resp = make_response(jsonify({"ok": True}))
    resp.set_cookie(
        "nkz_token",
        token,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "true").lower() == "true",
        samesite="Strict",  # Standard SOTA for BFF session cookies
        domain=COOKIE_DOMAIN or None,
        path="/",
        max_age=max_age,
    )
    return set_cors_headers(resp)


@app.route("/api/auth/session", methods=["DELETE"])
def delete_session():
    """Clear httpOnly session cookie"""
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie("nkz_token", domain=COOKIE_DOMAIN or None, path="/")
    return set_cors_headers(resp)


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify(
        {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "service": "fiware-api-gateway",
        }
    )


@app.route("/internal/cache/invalidate", methods=["POST"])
def internal_cache_invalidate():
    """Invalidate cache entries. Called internally by backend services after state changes."""
    data = request.get_json(silent=True) or {}
    key = data.get("key", "")
    if key.startswith("suspended:"):
        tenant_id = key.split(":", 1)[1]
        _suspended_tenant_cache.pop(tenant_id, None)
    return jsonify({"ok": True}), 200


@app.route(
    "/ngsi-ld/v1/entities/<path:entity_id>", methods=["GET", "PUT", "PATCH", "DELETE"]
)
def entity_by_id(entity_id):
    """Proxy to Orion-LD Context Broker for individual entity operations"""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401

    if is_pat_token(token):
        tenant = getattr(g, "pat_tenant_id", None)
        if not tenant:
            return jsonify({"error": "PAT tenant not resolved"}), 401
        gw_jwt = obtain_gateway_service_jwt()
        if not gw_jwt:
            return jsonify({"error": "Service authentication not configured"}), 503
        headers = {
            "Authorization": f"Bearer {gw_jwt}",
            "X-Delegated-Tenant-ID": tenant,
            "X-Tenant-ID": tenant,
        }
        headers = inject_fiware_headers(headers, tenant)
    else:
        payload = validate_jwt_token(token)
        if not payload:
            return jsonify({"error": "Invalid or expired token"}), 401

        tenant = extract_tenant_id(payload)
        if not tenant:
            return jsonify({"error": "Tenant not present in token"}), 401

        if not rate_limit(tenant):
            return jsonify({"error": "Rate limit exceeded"}), 429

        has_pro_expired = has_role("role_pro_expired", payload)
        if has_pro_expired and request.method in ["POST", "PUT", "PATCH", "DELETE"]:
            logger.warning(
                f"Blocked mutation request to {request.path} for user with role_pro_expired"
            )
            return jsonify(
                {"error": "Subscription expired. Read-only mode active."}
            ), 403

        headers = {}
        headers = inject_fiware_headers(headers, tenant)
        headers["X-Tenant-ID"] = tenant
        signature = generate_hmac_signature(token, tenant)
        if signature:
            headers["X-Auth-Signature"] = signature

    # Forward request to Orion-LD
    try:
        orion_url = f"{ORION_URL}/ngsi-ld/v1/entities/{entity_id}"
        if request.method == "GET":
            response = requests.get(orion_url, headers=headers, params=request.args)
        elif request.method == "PUT":
            response = requests.put(orion_url, headers=headers, json=request.json)
        elif request.method == "PATCH":
            response = requests.patch(orion_url, headers=headers, json=request.json)
        elif request.method == "DELETE":
            response = requests.delete(orion_url, headers=headers)

        if response.status_code >= 400:
            logger.error(
                f"Orion-LD error {response.status_code} for entity {entity_id}: {response.text}"
            )

        return make_response(
            response.content, response.status_code, dict(response.headers)
        )

    except requests.exceptions.RequestException as e:
        logger.error(
            f"Error forwarding request to Orion-LD for entity {entity_id}: {e}"
        )
        return jsonify({"error": "Internal server error"}), 500


@app.route("/ngsi-ld/v1/entities", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def entities():
    """Proxy to Orion-LD Context Broker entities endpoint"""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401

    if is_pat_token(token):
        tenant = getattr(g, "pat_tenant_id", None)
        if not tenant:
            return jsonify({"error": "PAT tenant not resolved"}), 401
        gw_jwt = obtain_gateway_service_jwt()
        if not gw_jwt:
            return jsonify({"error": "Service authentication not configured"}), 503
        headers = {
            "Authorization": f"Bearer {gw_jwt}",
            "X-Delegated-Tenant-ID": tenant,
            "X-Tenant-ID": tenant,
        }
        headers = inject_fiware_headers(headers, tenant)
    else:
        payload = validate_jwt_token(token)
        if not payload:
            return jsonify({"error": "Invalid or expired token"}), 401

        tenant = extract_tenant_id(payload)
        if not tenant:
            return jsonify({"error": "Tenant not present in token"}), 401

        if not rate_limit(tenant):
            return jsonify({"error": "Rate limit exceeded"}), 429

        has_pro_expired = has_role("role_pro_expired", payload)
        if has_pro_expired and request.method in ["POST", "PUT", "PATCH", "DELETE"]:
            logger.warning(
                f"Blocked mutation request to {request.path} for user with role_pro_expired"
            )
            return jsonify(
                {"error": "Subscription expired. Read-only mode active."}
            ), 403

        headers = {}
        headers = inject_fiware_headers(headers, tenant)
        headers["X-Tenant-ID"] = tenant
        signature = generate_hmac_signature(token, tenant)
        if signature:
            headers["X-Auth-Signature"] = signature

    # Inject @context into body for mutation requests so Orion-LD preserves
    # short entity types (e.g. "AgriParcel") instead of expanding to full
    # JSON-LD URIs (e.g. "https://saref.etsi.org/saref4agri/AgriParcel").
    # Without this, entities created via api-gateway are invisible to
    # queries by short type name.
    json_body = (
        request.get_json(silent=True)
        if request.method in ("POST", "PUT", "PATCH")
        else None
    )
    if isinstance(json_body, dict) and "@context" not in json_body:
        json_body["@context"] = [
            "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld",
            CONTEXT_URL,
        ]
        headers["Content-Type"] = "application/ld+json"
        headers.pop("Link", None)  # Remove Link header; @context is now in body
    # Forward request to Orion-LD
    try:
        orion_url = f"{ORION_URL}/ngsi-ld/v1/entities"
        if request.method == "GET":
            params = dict(request.args)
            if hasattr(g, "pat_entity_limit"):
                params["limit"] = str(g.pat_entity_limit)
            response = requests.get(orion_url, headers=headers, params=params)
        elif request.method == "POST":
            response = requests.post(orion_url, headers=headers, json=json_body)
        elif request.method == "PUT":
            response = requests.put(orion_url, headers=headers, json=json_body)
        elif request.method == "PATCH":
            response = requests.patch(orion_url, headers=headers, json=json_body)
        elif request.method == "DELETE":
            response = requests.delete(orion_url, headers=headers)

        if response.status_code >= 400:
            logger.error(f"Orion-LD error {response.status_code}: {response.text}")

        return make_response(
            response.content, response.status_code, dict(response.headers)
        )

    except requests.exceptions.RequestException as e:
        logger.error(f"Error forwarding request to Orion-LD: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/ngsi-ld/v1/entityOperations/query", methods=["POST", "GET"])
def entity_operations_query():
    """Proxy NGSI-LD entityOperations/query to Orion-LD (complex queries with filters in body)."""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401

    # Use PAT-modified body if present (from pagination interceptor)
    json_body = getattr(g, "pat_modified_body", None)

    if is_pat_token(token):
        tenant = getattr(g, "pat_tenant_id", None)
        if not tenant:
            return jsonify({"error": "PAT tenant not resolved"}), 401
        gw_jwt = obtain_gateway_service_jwt()
        if not gw_jwt:
            return jsonify({"error": "Service authentication not configured"}), 503
        headers = {
            "Authorization": f"Bearer {gw_jwt}",
            "X-Delegated-Tenant-ID": tenant,
            "X-Tenant-ID": tenant,
        }
        headers = inject_fiware_headers(headers, tenant)
        if json_body is None:
            json_body = request.get_json(silent=True) or {}
    else:
        payload = validate_jwt_token(token)
        if not payload:
            return jsonify({"error": "Invalid or expired token"}), 401

        tenant = extract_tenant_id(payload)
        if not tenant:
            return jsonify({"error": "Tenant not present in token"}), 401

        if not rate_limit(tenant):
            return jsonify({"error": "Rate limit exceeded"}), 429

        headers = {}
        headers = inject_fiware_headers(headers, tenant)
        headers["X-Tenant-ID"] = tenant
        signature = generate_hmac_signature(token, tenant)
        if signature:
            headers["X-Auth-Signature"] = signature
        if json_body is None:
            json_body = request.get_json(silent=True) or {}

    try:
        orion_url = f"{ORION_URL}/ngsi-ld/v1/entityOperations/query"

        if request.method == "GET":
            response = requests.get(
                orion_url, headers=headers, params=request.args, timeout=60
            )
        else:
            headers["Content-Type"] = "application/json"
            response = requests.post(
                orion_url, headers=headers, json=json_body, timeout=60
            )

        if response.status_code >= 400:
            logger.error(
                f"Orion-LD entityOperations/query error {response.status_code}: {response.text[:300]}"
            )

        return make_response(
            response.content, response.status_code, dict(response.headers)
        )

    except requests.exceptions.RequestException as e:
        logger.error(f"Error forwarding to Orion-LD entityOperations/query: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route(
    "/ngsi-ld/v1/subscriptions", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]
)
def subscriptions():
    """Proxy to Orion-LD Context Broker subscriptions endpoint"""
    # Validate JWT token
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    # Extract tenant from JWT payload - support multiple claim names
    tenant = extract_tenant_id(payload)
    if not tenant:
        return jsonify({"error": "Tenant not present in token"}), 401
    # Rate limit por tenant
    if not rate_limit(tenant):
        return jsonify({"error": "Rate limit exceeded"}), 429

    # Role based access control (Read-Only fallback)
    has_pro_expired = has_role("role_pro_expired", payload)
    if has_pro_expired and request.method in ["POST", "PUT", "PATCH", "DELETE"]:
        logger.warning(
            f"Blocked mutation request to {request.path} for user with role_pro_expired"
        )
        return jsonify({"error": "Subscription expired. Read-only mode active."}), 403

    # Prepare headers for Orion-LD
    headers = {}
    headers = inject_fiware_headers(headers, tenant)
    headers["X-Tenant-ID"] = tenant
    signature = generate_hmac_signature(token, tenant)
    if signature:
        headers["X-Auth-Signature"] = signature

    # Forward request to Orion-LD
    try:
        orion_url = f"{ORION_URL}/ngsi-ld/v1/subscriptions"
        if request.method == "GET":
            response = requests.get(orion_url, headers=headers, params=request.args)
        elif request.method == "POST":
            response = requests.post(orion_url, headers=headers, json=request.json)
        elif request.method == "PUT":
            response = requests.put(orion_url, headers=headers, json=request.json)
        elif request.method == "PATCH":
            response = requests.patch(orion_url, headers=headers, json=request.json)
        elif request.method == "DELETE":
            response = requests.delete(orion_url, headers=headers)

        return make_response(
            response.content, response.status_code, dict(response.headers)
        )

    except requests.exceptions.RequestException as e:
        logger.error(f"Error forwarding request to Orion-LD: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/devices/stats", methods=["GET"])
def get_device_stats():
    """Get device statistics (AutonomousMobileRobot count)"""
    # Validate JWT token
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    # Extract tenant
    tenant = extract_tenant_id(payload)
    if not tenant:
        return jsonify({"error": "Tenant not present in token"}), 401

    # Prepare headers for Orion-LD
    headers = {}
    headers = inject_fiware_headers(headers, tenant)

    # Query Orion for AutonomousMobileRobot count
    try:
        orion_url = f"{ORION_URL}/ngsi-ld/v1/entities"
        params = {"type": "AutonomousMobileRobot", "limit": 1, "count": "true"}

        response = requests.get(orion_url, headers=headers, params=params, timeout=10)

        count = 0
        if response.status_code == 200:
            # Check Ngsild-Results-Count header
            count_header = response.headers.get(
                "Ngsild-Results-Count"
            ) or response.headers.get("Content-Range")
            if count_header:
                if "/" in count_header:
                    count = int(count_header.split("/")[-1])
                else:
                    count = int(count_header)

        # AdminPanel expects 'active' for devices
        return jsonify({"active": count}), 200

    except Exception as e:
        logger.error(f"Error getting device stats: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/sensors/stats", methods=["GET"])
def get_sensor_stats():
    """Get sensor statistics (AgriSensor count)"""
    # Validate JWT token
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    # Extract tenant
    tenant = extract_tenant_id(payload)
    if not tenant:
        return jsonify({"error": "Tenant not present in token"}), 401

    # Prepare headers for Orion-LD
    headers = {}
    headers = inject_fiware_headers(headers, tenant)

    # Query Orion for AgriSensor count
    try:
        orion_url = f"{ORION_URL}/ngsi-ld/v1/entities"
        params = {"type": "AgriSensor", "limit": 1, "count": "true"}

        response = requests.get(orion_url, headers=headers, params=params, timeout=10)

        count = 0
        if response.status_code == 200:
            count_header = response.headers.get(
                "Ngsild-Results-Count"
            ) or response.headers.get("Content-Range")
            if count_header:
                if "/" in count_header:
                    count = int(count_header.split("/")[-1])
                else:
                    count = int(count_header)

        return jsonify({"total": count}), 200

    except Exception as e:
        logger.error(f"Error getting sensor stats: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/sensors", methods=["GET"])
def get_sensors():
    """Proxy to Orion-LD for AgriSensor entities (Legacy API support)"""
    # Validate JWT token
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    # Extract tenant from JWT payload
    tenant = extract_tenant_id(payload)
    if not tenant:
        return jsonify({"error": "Tenant not present in token"}), 401

    # Prepare headers for Orion-LD
    headers = {}
    headers = inject_fiware_headers(headers, tenant)
    headers["X-Tenant-ID"] = tenant

    # Forward request to Orion-LD
    try:
        orion_url = f"{ORION_URL}/ngsi-ld/v1/entities"
        params = {"type": "AgriSensor"}
        # Merge with existing query params
        params.update(request.args)

        response = requests.get(orion_url, headers=headers, params=params)

        if response.status_code >= 400:
            logger.error(f"Orion-LD error {response.status_code}: {response.text}")

        return make_response(
            response.content, response.status_code, dict(response.headers)
        )

    except requests.exceptions.RequestException as e:
        logger.error(f"Error forwarding request to Orion-LD: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/devices/<path:device_id>/telemetry/latest", methods=["GET"])
def get_device_latest_telemetry(device_id):
    """Get latest telemetry for a device (Proxy to Orion-LD)"""
    # Validate JWT token
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    # Extract tenant
    tenant = extract_tenant_id(payload)
    if not tenant:
        return jsonify({"error": "Tenant not present in token"}), 401

    # Rate limit
    if not rate_limit(tenant):
        return jsonify({"error": "Rate limit exceeded"}), 429

    # Prepare headers for Orion-LD
    headers = {}
    headers = inject_fiware_headers(headers, tenant)
    headers["X-Tenant-ID"] = tenant

    # Forward request to Orion-LD (keyValues mode for simple JSON)
    try:
        # Handle URNs properly (pass through as is)
        orion_url = f"{ORION_URL}/ngsi-ld/v1/entities/{device_id}"
        params = {"options": "keyValues"}

        response = requests.get(orion_url, headers=headers, params=params, timeout=10)

        if response.status_code >= 400:
            logger.error(f"Orion-LD error {response.status_code}: {response.text}")

        return make_response(
            response.content, response.status_code, dict(response.headers)
        )

    except requests.exceptions.RequestException as e:
        logger.error(f"Error forwarding request to Orion-LD: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/timeseries/<path:path>", methods=["GET", "POST"])
def timeseries_proxy(path):
    """Proxy to Timeseries Reader Service (GET for data/align, POST for export)."""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401

    # ADR 003: PAT only on this route; strip any client-supplied delegation header (do not forward).
    if is_pat_token(token):
        tenant = getattr(g, "pat_tenant_id", None)
        if not tenant:
            return jsonify({"error": "Invalid or expired PAT"}), 401
        if not rate_limit(tenant):
            return jsonify({"error": "Rate limit exceeded"}), 429
        gw_jwt = obtain_gateway_service_jwt()
        if not gw_jwt:
            return jsonify({"error": "Service authentication not configured"}), 503
        headers = {
            "Authorization": f"Bearer {gw_jwt}",
            "X-Delegated-Tenant-ID": tenant,
            "NGSILD-Tenant": tenant,
            "Fiware-Service": tenant,
            "X-Tenant-ID": tenant,
        }
        if request.method == "POST" and request.is_json:
            headers["Content-Type"] = "application/json"
    else:
        payload = validate_jwt_token(token)
        if not payload:
            return jsonify({"error": "Invalid or expired token"}), 401

        tenant = extract_tenant_id(payload)
        if not tenant:
            return jsonify({"error": "Tenant not present in token"}), 401

        headers = {
            "Authorization": f"Bearer {token}",
            "NGSILD-Tenant": tenant,
            "Fiware-Service": tenant,
            "X-Tenant-ID": tenant,
        }
        if request.method == "POST" and request.is_json:
            headers["Content-Type"] = "application/json"

        has_pro_expired = has_role("role_pro_expired", payload)
        if has_pro_expired and request.method in ["POST", "PUT", "PATCH", "DELETE"]:
            logger.warning(
                f"Blocked mutation request to {path} for user with role_pro_expired"
            )
            return jsonify(
                {"error": "Subscription expired. Read-only mode active."}
            ), 403

    # Forward request
    try:
        service_url = f"http://timeseries-reader-service:5000/api/timeseries/{path}"
        if request.method == "GET":
            response = requests.get(
                service_url, headers=headers, params=request.args, timeout=60
            )
        else:
            response = requests.post(
                service_url,
                headers=headers,
                params=request.args,
                json=request.get_json(silent=True) or {},
                timeout=120,
            )
        return make_response(
            response.content, response.status_code, dict(response.headers)
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Error forwarding request to timeseries-reader: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/gis/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def geoserver_proxy(path):
    """Proxy to GeoServer with JWT validation and tenant isolation"""
    # Validate JWT token
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    # Extract tenant from JWT payload
    tenant = extract_tenant_id(payload)
    if not tenant:
        return jsonify({"error": "Tenant not present in token"}), 401

    # Rate limit por tenant
    if not rate_limit(tenant):
        return jsonify({"error": "Rate limit exceeded"}), 429

    # Prepare GeoServer URL
    # Remove /api/gis prefix from path and forward to GeoServer
    geoserver_path = (
        path if not path.startswith("api/gis/") else path.replace("api/gis/", "", 1)
    )

    # Build full GeoServer URL
    geoserver_url = f"{GEOSERVER_URL}/{geoserver_path}"

    # Add tenant_id as parameter for GeoServer filtering
    # GeoServer can use this to filter data by tenant
    params = dict(request.args)
    params["viewparams"] = f"tid:{tenant}"  # Add tenant_id as view parameter

    # Prepare headers for GeoServer
    headers = {
        "X-Tenant-ID": tenant,
        "Content-Type": request.headers.get("Content-Type", "application/json"),
    }

    # Forward request to GeoServer
    try:
        if request.method == "GET":
            response = requests.get(
                geoserver_url, headers=headers, params=params, timeout=30
            )
        elif request.method == "POST":
            response = requests.post(
                geoserver_url,
                headers=headers,
                params=params,
                json=request.json if request.is_json else None,
                data=request.data if not request.is_json else None,
                timeout=30,
            )
        elif request.method == "PUT":
            response = requests.put(
                geoserver_url,
                headers=headers,
                params=params,
                json=request.json if request.is_json else None,
                data=request.data if not request.is_json else None,
                timeout=30,
            )
        elif request.method == "PATCH":
            response = requests.patch(
                geoserver_url,
                headers=headers,
                params=params,
                json=request.json if request.is_json else None,
                data=request.data if not request.is_json else None,
                timeout=30,
            )
        elif request.method == "DELETE":
            response = requests.delete(
                geoserver_url, headers=headers, params=params, timeout=30
            )

        # Forward response from GeoServer
        return make_response(
            response.content, response.status_code, dict(response.headers)
        )

    except requests.exceptions.Timeout:
        logger.error(f"Timeout forwarding request to GeoServer: {geoserver_url}")
        return jsonify({"error": "GeoServer request timeout"}), 504
    except requests.exceptions.RequestException as e:
        logger.error(f"Error forwarding request to GeoServer: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route(
    "/api/greenhouse",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    strict_slashes=False,
)
@app.route(
    "/api/greenhouse/<path:path>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
def greenhouse_proxy(path=""):
    """Proxy to Greenhouse DT backend with JWT auth and tenant injection.

    Skips JWT validation for paths under /api/greenhouse/internal/
    (module activation flow uses X-Internal-Service-Secret).
    """
    # Internal paths: skip JWT, require X-Internal-Service-Secret
    if path.startswith("internal/"):
        internal_secret = request.headers.get("X-Internal-Service-Secret", "")
        if not internal_secret:
            return jsonify({"error": "Missing X-Internal-Service-Secret"}), 401
        tenant = request.headers.get("X-Tenant-ID", "")
        user_id = request.headers.get("X-User-ID", "internal")
        user_roles = request.headers.get("X-User-Roles", "internal")
    else:
        # Standard auth: JWT validation
        token = get_request_token()
        if not token:
            return jsonify({"error": "Missing or invalid authorization"}), 401
        payload = validate_jwt_token(token)
        if not payload:
            return jsonify({"error": "Invalid or expired token"}), 401

        tenant = extract_tenant_id(payload)
        if not tenant:
            return jsonify({"error": "Tenant not present in token"}), 401

        if not rate_limit(tenant):
            return jsonify({"error": "Rate limit exceeded"}), 429

        user_id = payload.get("sub", "")
        user_roles = ",".join(payload.get("realm_access", {}).get("roles", []))

    # Build target URL
    target = f"{GREENHOUSE_DT_URL}/api/greenhouse"
    if path:
        target += f"/{path}"

    # Forward headers
    headers = {
        "X-Tenant-ID": tenant,
        "X-User-ID": user_id,
        "X-User-Roles": user_roles,
        "Content-Type": request.headers.get("Content-Type", "application/json"),
    }
    if path.startswith("internal/"):
        headers["X-Internal-Service-Secret"] = request.headers.get(
            "X-Internal-Service-Secret", ""
        )

    try:
        response = requests.request(
            method=request.method,
            url=target,
            headers=headers,
            params=request.args,
            json=request.get_json(silent=True) if request.is_json else None,
            data=request.get_data() if not request.is_json else None,
            timeout=30.0,
        )
        return make_response(
            response.content, response.status_code, dict(response.headers)
        )
    except requests.exceptions.Timeout:
        logger.error(f"Timeout forwarding to greenhouse-dt: {target}")
        return jsonify({"error": "Greenhouse backend timeout"}), 504
    except requests.exceptions.RequestException as e:
        logger.error(f"Error forwarding to greenhouse-dt: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/ngsi-ld/notify", methods=["POST"])
def ngsi_ld_notify_proxy():
    """Proxy NGSI-LD subscription notifications to greenhouse-dt.

    Called by Orion-LD when watched attributes change on AgriSensor entities.
    No JWT required — validated by greenhouse backend via subscription payload.
    """
    target = f"{GREENHOUSE_DT_URL}/api/ngsi-ld/notify"
    headers = {
        "Content-Type": request.headers.get("Content-Type", "application/json"),
        "NGSILD-Tenant": request.headers.get("NGSILD-Tenant", ""),
        "Fiware-Service": request.headers.get("Fiware-Service", ""),
    }
    try:
        response = requests.post(
            url=target,
            headers=headers,
            json=request.get_json(silent=True) or {},
            timeout=10.0,
        )
        return make_response(
            response.content, response.status_code, dict(response.headers)
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Error forwarding NGSI-LD notify to greenhouse-dt: {e}")
        return jsonify({"error": "Greenhouse backend unreachable"}), 502


@app.route("/ngsi-ld-context.json", methods=["GET"])
def get_context():
    """Serve NGSI-LD context file"""
    try:
        # Context file is copied to /config/ngsi-ld-context.json in Dockerfile
        context_file = "/config/ngsi-ld-context.json"
        with open(context_file, "r") as f:
            context = json.load(f)
        return jsonify(context)
    except Exception as e:
        logger.error(f"Error loading context file: {e}")
        return jsonify({"error": "Context file not found"}), 404


@app.route("/api/core/basemap/package", methods=["POST"])
def request_offline_basemap():
    """Enqueue a job to generate a PMTiles offline map package"""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    tenant = extract_tenant_id(payload)
    if not tenant:
        return jsonify({"error": "Tenant not present in token"}), 401

    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400

    data = request.json
    parcel_id = data.get("parcel_id")
    bbox = data.get("bbox")
    max_zoom = data.get("max_zoom", 18)

    if not parcel_id or not bbox or len(bbox) != 4:
        return jsonify({"error": "Missing or invalid parcel_id or bbox"}), 400

    try:
        # Import task queue dynamically to avoid coupling problems
        import importlib.util

        task_queue_file = "/app/task-queue/task_queue.py"
        if os.path.exists(task_queue_file):
            spec = importlib.util.spec_from_file_location("task_queue", task_queue_file)
            task_queue_module = importlib.util.module_from_spec(spec)
            sys.modules["task_queue"] = task_queue_module
            spec.loader.exec_module(task_queue_module)
            TaskQueue = task_queue_module.TaskQueue
            pmtiles_queue = TaskQueue(stream_name="pmtiles:requests")

            task_id = pmtiles_queue.enqueue_task(
                tenant_id=tenant,
                task_type="pmtiles_generation",
                payload={
                    "tenant_id": tenant,
                    "parcel_id": parcel_id,
                    "bbox": bbox,
                    "max_zoom": max_zoom,
                },
                max_retries=1,
            )
            return jsonify(
                {"message": "Packaging task enqueued", "task_id": task_id}
            ), 202
        else:
            logger.error("Task Queue module not found")
            return jsonify({"error": "Task Queue module not found in API Gateway"}), 500

    except Exception as e:
        logger.error(f"Failed to enqueue PMTiles task: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/version", methods=["GET"])
def version():
    """Get service version"""
    return jsonify(
        {
            "service": "fiware-api-gateway",
            "version": "1.0.0",
            "timestamp": datetime.utcnow().isoformat(),
        }
    )


# =============================================================================
# External API Credentials Management Endpoints (PlatformAdmin only)
# =============================================================================


def has_role(role: str, payload: dict = None) -> bool:
    """Check if user has a specific role - checks multiple locations"""
    if not payload:
        return False
    # Check realm_access first
    roles = payload.get("realm_access", {}).get("roles", []) or []
    # Also check resource_access
    resource_roles = []
    for resource in payload.get("resource_access", {}).values():
        if isinstance(resource, dict) and "roles" in resource:
            resource_roles.extend(resource["roles"])
    # Also check root level
    all_roles = list(set(roles + resource_roles + payload.get("roles", [])))
    return role in all_roles


@app.route("/admin/external-api-credentials", methods=["GET"])
def list_external_api_credentials():
    """List all external API credentials (PlatformAdmin only)"""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    if not has_role("PlatformAdmin", payload):
        return jsonify({"error": "Only PlatformAdmin can access this endpoint"}), 403

    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        # Use global POSTGRES_URL
        if not POSTGRES_URL:
            return jsonify({"error": "POSTGRES_URL not configured"}), 500

        conn = psycopg2.connect(POSTGRES_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT 
                id,
                service_name,
                service_url,
                auth_type,
                username,
                description,
                is_active,
                created_at,
                updated_at,
                last_used_at,
                last_used_by
            FROM external_api_credentials
            ORDER BY service_name
        """)

        credentials = cur.fetchall()
        cur.close()
        conn.close()

        return jsonify({"credentials": [dict(c) for c in credentials]}), 200

    except Exception as e:
        logger.error(f"Error listing credentials: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/admin/external-api-credentials", methods=["POST"])
def create_external_api_credential():
    """Create new external API credential (PlatformAdmin only)"""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    if not has_role("PlatformAdmin", payload):
        return jsonify({"error": "Only PlatformAdmin can access this endpoint"}), 403

    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        import hashlib

        data = request.json
        # Use global POSTGRES_URL
        if not POSTGRES_URL:
            return jsonify({"error": "POSTGRES_URL not configured"}), 500

        # Validate required fields
        required = ["service_name", "service_url", "auth_type"]
        for field in required:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400

        # Validate auth_type
        if data["auth_type"] not in ["api_key", "bearer", "basic_auth", "none"]:
            return jsonify({"error": "Invalid auth_type"}), 400

        # Encrypt credentials
        def encrypt_credential(plain_text: str) -> str:
            salt = os.getenv(
                "CREDENTIAL_ENCRYPTION_SALT", "default-salt-change-in-production"
            )
            return hashlib.sha256((plain_text + salt).encode()).hexdigest()

        password_encrypted = None
        api_key_encrypted = None

        if data["auth_type"] == "basic_auth":
            if "username" not in data or not data["username"]:
                return jsonify({"error": "Username required for basic_auth"}), 400
            if "password" not in data or not data["password"]:
                return jsonify({"error": "Password required for basic_auth"}), 400
            password_encrypted = encrypt_credential(data["password"])
        elif data["auth_type"] in ["api_key", "bearer"]:
            if "api_key" not in data or not data["api_key"]:
                return jsonify({"error": "API key required"}), 400
            api_key_encrypted = encrypt_credential(data["api_key"])

        conn = psycopg2.connect(POSTGRES_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        user_email = payload.get("email") or payload.get(
            "preferred_username", "unknown"
        )

        cur.execute(
            """
            INSERT INTO external_api_credentials (
                service_name,
                service_url,
                auth_type,
                username,
                password_encrypted,
                api_key_encrypted,
                additional_params,
                description,
                is_active,
                created_by
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            RETURNING id
        """,
            (
                data["service_name"],
                data["service_url"],
                data["auth_type"],
                data.get("username"),
                password_encrypted,
                api_key_encrypted,
                json.dumps(data.get("additional_params", {})),
                data.get("description"),
                data.get("is_active", True),
                user_email,
            ),
        )

        credential_id = cur.fetchone()["id"]
        conn.commit()
        cur.close()
        conn.close()

        logger.info(f"Created external API credential: {data['service_name']}")
        return jsonify(
            {"id": credential_id, "message": "Credential created successfully"}
        ), 201

    except psycopg2.IntegrityError:
        return jsonify({"error": "Service name already exists"}), 409
    except Exception as e:
        logger.error(f"Error creating credential: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/admin/external-api-credentials/<credential_id>", methods=["PUT"])
def update_external_api_credential(credential_id):
    """Update external API credential (PlatformAdmin only)"""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    if not has_role("PlatformAdmin", payload):
        return jsonify({"error": "Only PlatformAdmin can access this endpoint"}), 403

    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        import hashlib

        data = request.json
        # Use global POSTGRES_URL
        if not POSTGRES_URL:
            return jsonify({"error": "POSTGRES_URL not configured"}), 500

        def encrypt_credential(plain_text: str) -> str:
            salt = os.getenv(
                "CREDENTIAL_ENCRYPTION_SALT", "default-salt-change-in-production"
            )
            return hashlib.sha256((plain_text + salt).encode()).hexdigest()

        conn = psycopg2.connect(POSTGRES_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Build update query
        updates = []
        values = []

        if "service_url" in data:
            updates.append("service_url = %s")
            values.append(data["service_url"])

        if "auth_type" in data:
            if data["auth_type"] not in ["api_key", "bearer", "basic_auth", "none"]:
                return jsonify({"error": "Invalid auth_type"}), 400
            updates.append("auth_type = %s")
            values.append(data["auth_type"])

        if "username" in data:
            updates.append("username = %s")
            values.append(data["username"])

        if "password" in data and data["password"]:
            updates.append("password_encrypted = %s")
            values.append(encrypt_credential(data["password"]))

        if "api_key" in data and data["api_key"]:
            updates.append("api_key_encrypted = %s")
            values.append(encrypt_credential(data["api_key"]))

        if "additional_params" in data:
            updates.append("additional_params = %s")
            values.append(json.dumps(data["additional_params"]))

        if "description" in data:
            updates.append("description = %s")
            values.append(data["description"])

        if "is_active" in data:
            updates.append("is_active = %s")
            values.append(data["is_active"])

        if not updates:
            return jsonify({"error": "No fields to update"}), 400

        updates.append("updated_at = NOW()")
        values.append(credential_id)

        query = f"""
            UPDATE external_api_credentials
            SET {", ".join(updates)}
            WHERE id = %s
            RETURNING id
        """

        cur.execute(query, values)
        updated = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        if not updated:
            return jsonify({"error": "Credential not found"}), 404

        return jsonify({"message": "Credential updated successfully"}), 200

    except Exception as e:
        logger.error(f"Error updating credential: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/admin/external-api-credentials/<credential_id>", methods=["DELETE"])
def delete_external_api_credential(credential_id):
    """Delete external API credential (PlatformAdmin only)"""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    if not has_role("PlatformAdmin", payload):
        return jsonify({"error": "Only PlatformAdmin can access this endpoint"}), 403

    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        # Use global POSTGRES_URL
        if not POSTGRES_URL:
            return jsonify({"error": "POSTGRES_URL not configured"}), 500

        conn = psycopg2.connect(POSTGRES_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute(
            "DELETE FROM external_api_credentials WHERE id = %s RETURNING id",
            (credential_id,),
        )
        deleted = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        if not deleted:
            return jsonify({"error": "Credential not found"}), 404

        logger.info(f"Deleted external API credential: {credential_id}")
        return jsonify({"message": "Credential deleted successfully"}), 200

    except Exception as e:
        logger.error(f"Error deleting credential: {e}")
        return jsonify({"error": "Internal server error"}), 500


# =============================================================================
# Platform API Credentials Management Endpoints (PlatformAdmin only)
# =============================================================================
# Manages platform-wide credentials (Copernicus CDSE, AEMET) stored in Kubernetes secrets


def create_or_update_k8s_secret(secret_name: str, namespace: str, data: dict) -> bool:
    """Create or update Kubernetes secret"""
    try:
        from kubernetes import client as k8s_client, config as k8s_config
        from kubernetes.client import ApiException

        # Load in-cluster config (runs inside Kubernetes)
        try:
            k8s_config.load_incluster_config()
        except Exception:
            # Fallback to kubeconfig (for local development)
            k8s_config.load_kube_config()

        v1 = k8s_client.CoreV1Api()

        # Prepare secret data (base64 encoded)
        import base64

        secret_data = {}
        for key, value in data.items():
            if value:
                secret_data[key] = base64.b64encode(value.encode("utf-8")).decode(
                    "utf-8"
                )

        # Check if secret exists
        try:
            existing = v1.read_namespaced_secret(secret_name, namespace)
            # Update existing secret
            existing.data = secret_data
            v1.replace_namespaced_secret(secret_name, namespace, existing)
            logger.info(f"Updated Kubernetes secret: {secret_name}")
            return True
        except ApiException as e:
            if e.status == 404:
                # Create new secret
                secret = k8s_client.V1Secret(
                    metadata=k8s_client.V1ObjectMeta(name=secret_name),
                    data=secret_data,
                    type="Opaque",
                )
                v1.create_namespaced_secret(namespace, secret)
                logger.info(f"Created Kubernetes secret: {secret_name}")
                return True
            else:
                logger.error(f"Error managing Kubernetes secret: {e}")
                return False
    except ImportError:
        logger.warning("kubernetes library not available, cannot manage secrets")
        return False
    except Exception as e:
        logger.error(f"Error managing Kubernetes secret: {e}")
        return False


@app.route("/api/admin/platform-credentials/copernicus-cdse", methods=["GET"])
def get_copernicus_credentials():
    """Get Copernicus CDSE credentials status (PlatformAdmin only)"""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    if not has_role("PlatformAdmin", payload):
        return jsonify({"error": "Only PlatformAdmin can access this endpoint"}), 403

    try:
        from kubernetes import client as k8s_client, config as k8s_config
        import base64

        try:
            k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()

        v1 = k8s_client.CoreV1Api()

        try:
            secret = v1.read_namespaced_secret("copernicus-cdse-secret", "nekazari")
            username = (
                base64.b64decode(secret.data.get("username", "")).decode("utf-8")
                if secret.data.get("username")
                else ""
            )

            return jsonify(
                {
                    "configured": True,
                    "username": username,
                    "url": "https://dataspace.copernicus.eu",
                }
            ), 200
        except Exception as e:
            if "404" in str(e) or "Not Found" in str(e):
                return jsonify(
                    {
                        "configured": False,
                        "username": "",
                        "url": "https://dataspace.copernicus.eu",
                    }
                ), 200
            raise
    except ImportError:
        return jsonify(
            {
                "configured": False,
                "username": "",
                "url": "https://dataspace.copernicus.eu",
            }
        ), 200
    except Exception as e:
        logger.error(f"Error getting Copernicus credentials: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/admin/platform-credentials/copernicus-cdse", methods=["POST"])
def save_copernicus_credentials():
    """Save Copernicus CDSE credentials to Kubernetes secret (PlatformAdmin only)"""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    if not has_role("PlatformAdmin", payload):
        return jsonify({"error": "Only PlatformAdmin can access this endpoint"}), 403

    try:
        data = request.json
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()
        url = data.get("url", "https://dataspace.copernicus.eu").strip()

        if not username:
            return jsonify({"error": "Username (Client ID) is required"}), 400
        if not password:
            return jsonify({"error": "Password (Client Secret) is required"}), 400

        # Save to Kubernetes secret
        secret_data = {"username": username, "password": password}

        if create_or_update_k8s_secret(
            "copernicus-cdse-secret", "nekazari", secret_data
        ):
            # Also save to database for reference
            try:
                import psycopg2
                from psycopg2.extras import RealDictCursor
                import hashlib

                # Use global POSTGRES_URL
                if POSTGRES_URL:
                    conn = psycopg2.connect(POSTGRES_URL)
                    cur = conn.cursor(cursor_factory=RealDictCursor)

                    # Check if exists
                    cur.execute("""
                        SELECT id FROM external_api_credentials
                        WHERE service_name = 'copernicus-cdse'
                    """)
                    existing = cur.fetchone()

                    password_hash = hashlib.sha256(password.encode()).hexdigest()

                    if existing:
                        cur.execute(
                            """
                            UPDATE external_api_credentials
                            SET username = %s, password_encrypted = %s, service_url = %s,
                                updated_at = NOW()
                            WHERE service_name = 'copernicus-cdse'
                        """,
                            (username, password_hash, url),
                        )
                    else:
                        cur.execute(
                            """
                            INSERT INTO external_api_credentials
                            (service_name, service_url, auth_type, username, password_encrypted, is_active)
                            VALUES ('copernicus-cdse', %s, 'basic_auth', %s, %s, true)
                        """,
                            (url, username, password_hash),
                        )

                    conn.commit()
                    cur.close()
                    conn.close()
            except Exception as db_err:
                logger.warning(f"Could not save to database: {db_err}")

            return jsonify(
                {
                    "message": "Copernicus CDSE credentials saved successfully",
                    "configured": True,
                }
            ), 200
        else:
            return jsonify({"error": "Failed to save credentials to Kubernetes"}), 500

    except Exception as e:
        logger.error(f"Error saving Copernicus credentials: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/admin/platform-credentials/aemet", methods=["GET"])
def get_aemet_credentials():
    """Get AEMET credentials status (PlatformAdmin only)"""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    if not has_role("PlatformAdmin", payload):
        return jsonify({"error": "Only PlatformAdmin can access this endpoint"}), 403

    try:
        from kubernetes import client as k8s_client, config as k8s_config
        import base64

        try:
            k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()

        v1 = k8s_client.CoreV1Api()

        try:
            secret = v1.read_namespaced_secret("aemet-secret", "nekazari")
            # Check both possible key names
            api_key = ""
            if secret.data.get("api_key"):
                api_key = base64.b64decode(secret.data["api_key"]).decode("utf-8")
            elif secret.data.get("api-key"):
                api_key = base64.b64decode(secret.data["api-key"]).decode("utf-8")

            return jsonify(
                {
                    "configured": bool(api_key),
                    "url": "https://opendata.aemet.es/opendata/api",
                }
            ), 200
        except Exception as e:
            if "404" in str(e) or "Not Found" in str(e):
                return jsonify(
                    {
                        "configured": False,
                        "url": "https://opendata.aemet.es/opendata/api",
                    }
                ), 200
            raise
    except ImportError:
        return jsonify(
            {"configured": False, "url": "https://opendata.aemet.es/opendata/api"}
        ), 200
    except Exception as e:
        logger.error(f"Error getting AEMET credentials: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/admin/platform-credentials/aemet", methods=["POST"])
def save_aemet_credentials():
    """Save AEMET credentials to Kubernetes secret (PlatformAdmin only)"""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    if not has_role("PlatformAdmin", payload):
        return jsonify({"error": "Only PlatformAdmin can access this endpoint"}), 403

    try:
        data = request.json
        api_key = data.get("api_key", "").strip()
        url = data.get("url", "https://opendata.aemet.es/opendata/api").strip()

        if not api_key:
            return jsonify({"error": "API Key is required"}), 400

        # Save to Kubernetes secret (use 'api_key' as key name for consistency)
        secret_data = {
            "api_key": api_key,
            "api-key": api_key,  # Also add legacy key name for backward compatibility
        }

        if create_or_update_k8s_secret("aemet-secret", "nekazari", secret_data):
            # Also save to database for reference
            try:
                import psycopg2
                from psycopg2.extras import RealDictCursor
                import hashlib

                # Use global POSTGRES_URL
                if POSTGRES_URL:
                    conn = psycopg2.connect(POSTGRES_URL)
                    cur = conn.cursor(cursor_factory=RealDictCursor)

                    # Check if exists
                    cur.execute("""
                        SELECT id FROM external_api_credentials
                        WHERE service_name = 'aemet'
                    """)
                    existing = cur.fetchone()

                    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()

                    if existing:
                        cur.execute(
                            """
                            UPDATE external_api_credentials
                            SET api_key_encrypted = %s, service_url = %s,
                                updated_at = NOW()
                            WHERE service_name = 'aemet'
                        """,
                            (api_key_hash, url),
                        )
                    else:
                        cur.execute(
                            """
                            INSERT INTO external_api_credentials
                            (service_name, service_url, auth_type, api_key_encrypted, is_active)
                            VALUES ('aemet', %s, 'api_key', %s, true)
                        """,
                            (url, api_key_hash),
                        )

                    conn.commit()
                    cur.close()
                    conn.close()
            except Exception as db_err:
                logger.warning(f"Could not save to database: {db_err}")

            return jsonify(
                {"message": "AEMET credentials saved successfully", "configured": True}
            ), 200
        else:
            return jsonify({"error": "Failed to save credentials to Kubernetes"}), 500

    except Exception as e:
        logger.error(f"Error saving AEMET credentials: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/assets/<path:subpath>", methods=["GET", "POST", "DELETE", "OPTIONS"])
@cross_origin(origins=_cors_origins, supports_credentials=True)
def proxy_assets_requests(subpath):
    """Proxy asset management requests to entity-manager"""
    if request.method == "OPTIONS":
        return "", 204

    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing authorization"}), 401

    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid token"}), 401

    target_url = f"{ENTITY_MANAGER_URL}/api/assets/{subpath}"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        if request.method == "GET":
            response = requests.get(
                target_url, headers=headers, params=request.args, timeout=10
            )
        elif request.method == "POST":
            # Handle multipart upload if present
            if "multipart/form-data" in request.content_type:
                response = requests.post(
                    target_url,
                    headers=headers,
                    data=request.data,
                    files=request.files,
                    timeout=30,
                )
            else:
                response = requests.post(
                    target_url,
                    headers=headers,
                    json=request.get_json(silent=True),
                    timeout=30,
                )
        elif request.method == "DELETE":
            response = requests.delete(target_url, headers=headers, timeout=10)
        else:
            return jsonify({"error": "Method not supported"}), 405

        return (response.content, response.status_code, response.headers.items())
    except Exception as e:
        logger.error(f"Error proxying asset request: {e}")
        return jsonify({"error": "Internal service connection error"}), 502


_OSM_TYPE_MAP = {
    "country": "country", "state": "region", "region": "region", "county": "county",
    "city": "city", "town": "town", "village": "village", "hamlet": "village",
    "street": "street", "residential": "street", "house": "house", "house_number": "house",
}


def _normalise_photon(payload):
    """Normalise Photon JSON to the frozen frontend contract."""
    out = []
    for f in (payload or {}).get("features", []):
        p = f.get("properties", {}) or {}
        coords = (f.get("geometry", {}) or {}).get("coordinates", [None, None])
        lon, lat = coords[0], coords[1]
        if lon is None or lat is None:
            continue
        label = ", ".join([x for x in [p.get("name"), p.get("state"), p.get("country")] if x])
        ext = p.get("extent")  # photon: [minLon, maxLat, maxLon, minLat]
        bbox = [ext[0], ext[3], ext[2], ext[1]] if ext and len(ext) == 4 else None
        out.append({
            "label": label or p.get("name", ""),
            "lat": lat, "lon": lon,
            "bbox": bbox,
            "type": _OSM_TYPE_MAP.get(p.get("osm_value") or p.get("type"), "other"),
            "countryCode": (p.get("countrycode") or "").upper(),
        })
    return out


@app.route("/api/geocode", methods=["GET"])
@cross_origin(origins=_cors_origins, supports_credentials=True)
def proxy_geocode():
    token = get_request_token()
    if not token:
        return jsonify({"error": "unauthorized"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid token"}), 401
    tenant = extract_tenant_id(payload) or request.headers.get("X-Tenant-ID", "platform")
    if not rate_limit(tenant):
        return jsonify({"error": "Rate limit exceeded"}), 429
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"results": []}), 200
    try:
        limit = min(int(request.args.get("limit", 5)), 10)
    except ValueError:
        limit = 5
    params = {"q": q, "limit": limit, "lang": request.args.get("lang", "es")}
    for k in ("lat", "lon"):
        if request.args.get(k):
            params[k] = request.args.get(k)
    try:
        r = requests.get(f"{GEOCODE_URL}/api", params=params, timeout=8)
        r.raise_for_status()
        return jsonify({"results": _normalise_photon(r.json())[:limit]}), 200
    except Exception as e:
        app.logger.warning("geocode upstream error: %s", e)
        return jsonify({"error": "geocode_unavailable"}), 502


@app.route("/api/entities/parcels/<path:subpath>", methods=["GET", "POST", "OPTIONS"])
@cross_origin(origins=_cors_origins, supports_credentials=True)
def proxy_parcel_modules(subpath):
    """Proxy the parcel-module CONTROL plane to entity-manager (activation/list).

    Entity CRUD does NOT go here — AgriParcel is written via /ngsi-ld like every
    entity. Only `/modules...` subpaths are valid here; anything else is rejected so
    this route never shadows entity writes.
    """
    if request.method == "OPTIONS":
        return "", 204
    if "/modules" not in f"/{subpath}":
        return jsonify({"error": "not_found"}), 404

    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401
    tenant = extract_tenant_id(payload)
    if not tenant:
        return jsonify({"error": "Tenant not present in token"}), 401
    if not rate_limit(tenant):
        return jsonify({"error": "Rate limit exceeded"}), 429

    target_url = f"{ENTITY_MANAGER_URL}/api/entities/parcels/{subpath}"
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-ID": tenant}
    content_type = request.headers.get("Content-Type")
    if content_type:
        headers["Content-Type"] = content_type
    if KEYCLOAK_AUTH_AVAILABLE:
        try:
            signature = generate_hmac_signature(token, tenant)
            if signature:
                headers["X-Auth-Signature"] = signature
        except Exception as e:
            logger.warning(f"Failed to generate HMAC signature for parcel-modules: {e}")
    try:
        response = requests.request(
            request.method, target_url, headers=headers,
            params=request.args, data=request.get_data(), timeout=30,
        )
        return (response.content, response.status_code, response.headers.items())
    except Exception as e:
        logger.error(f"Error proxying parcel-module request: {e}")
        return jsonify({"error": "Internal service connection error"}), 502


@app.route("/api/core/sync/vectorial", methods=["GET", "POST", "OPTIONS"])
@cross_origin(origins=_cors_origins, supports_credentials=True)
def proxy_vector_sync_requests():
    """Proxy WatermelonDB vector sync (GET pull, POST push) to entity-manager."""
    if request.method == "OPTIONS":
        return "", 204

    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing authorization"}), 401

    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid token"}), 401

    tenant = extract_tenant_id(payload)
    if not tenant:
        return jsonify({"error": "Tenant not present in token"}), 401

    target_url = f"{ENTITY_MANAGER_URL}/api/core/sync/vectorial"
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-ID": tenant}

    try:
        if request.method == "GET":
            response = requests.get(
                target_url, headers=headers, params=request.args, timeout=30
            )
        elif request.method == "POST":
            response = requests.post(
                target_url,
                headers=headers,
                params=request.args,
                json=request.get_json(silent=True),
                timeout=60,
            )
        else:
            return jsonify({"error": "Method not supported"}), 405
        return (response.content, response.status_code, response.headers.items())
    except Exception as e:
        logger.error(f"Error proxying vector sync request: {e}")
        return jsonify({"error": "Internal service connection error"}), 502


@app.route("/api/routing/tiles/<path:subpath>", methods=["GET", "OPTIONS"])
@cross_origin(origins=_cors_origins, supports_credentials=True)
def proxy_routing_tiles(subpath):
    """Proxy PMTiles download requests to GIS routing module."""
    if request.method == "OPTIONS":
        return "", 204
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid token"}), 401
    tenant = extract_tenant_id(payload)
    if not tenant:
        return jsonify({"error": "Tenant not present in token"}), 401
    target_url = (
        "http://nkz-module-gis-routing-service:8000/api/nkz-module-gis-routing/tiles"
    )
    req_params = dict(request.args)
    req_params["parcel_id"] = subpath
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-ID": tenant}
    try:
        response = requests.get(
            target_url, headers=headers, params=req_params, timeout=120
        )
        return (response.content, response.status_code, response.headers.items())
    except Exception as e:
        logger.error(f"Error proxying PMTiles request: {e}")
        return jsonify({"error": "Internal service connection error"}), 502


# Field-image parcel resolution constants (generous buffer by design — see spec).
PARCEL_BUFFER_K = 3.0
PARCEL_BUFFER_FLOOR_M = 50


def resolve_parcel_for_point(tenant, lng, lat, accuracy):
    """Best-effort resolution of the AgriParcel a field photo belongs to.

    Two-step NGSI-LD geo-query against Orion-LD, tenant-scoped:
      1. georel=intersects  -> parcel strictly containing the point.
      2. georel=near;maxDistance==<margin> -> nearest parcel within buffer.
    Margin = max(accuracy * K, FLOOR_M). Never raises; returns parcel id or None.
    """
    try:
        from ngsi_headers import inject_fiware_headers as _inject_headers

        orion = os.getenv("ORION_URL", "http://orion-service:1026")
        headers = _inject_headers({"Accept": "application/json"}, tenant)
        coords = f"[{lng},{lat}]"
        base = {
            "type": "AgriParcel",
            "geometry": "Point",
            "coordinates": coords,
            "limit": "1",
        }
        intersects = dict(base, georel="intersects")
        r = requests.get(
            f"{orion}/ngsi-ld/v1/entities",
            params=intersects,
            headers=headers,
            timeout=8,
        )
        if r.status_code == 200 and r.json():
            return r.json()[0].get("id")
        margin = int(max((accuracy or 0) * PARCEL_BUFFER_K, PARCEL_BUFFER_FLOOR_M))
        near = dict(base, georel=f"near;maxDistance=={margin}")
        r = requests.get(
            f"{orion}/ngsi-ld/v1/entities", params=near, headers=headers, timeout=8
        )
        if r.status_code == 200 and r.json():
            return r.json()[0].get("id")
    except Exception as e:
        logger.warning("Parcel resolution failed (non-fatal): %s", e)
    return None


@app.route("/api/field-images/upload", methods=["POST", "OPTIONS"])
@cross_origin(origins=_cors_origins, supports_credentials=True)
def field_image_upload():
    """Upload field-captured image to MinIO and optionally create NGSI-LD observation."""
    if request.method == "OPTIONS":
        return "", 204
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid token"}), 401
    tenant = extract_tenant_id(payload)
    if not tenant:
        return jsonify({"error": "Tenant not present in token"}), 401
    image_file = request.files.get("image")
    if not image_file:
        return jsonify({"error": "image file is required"}), 400
    filename = image_file.filename or "capture.jpg"
    if not filename.lower().endswith((".jpg", ".jpeg", ".png")):
        return jsonify({"error": "Only JPEG and PNG images are accepted"}), 400
    image_file.seek(0, 2)
    size = image_file.tell()
    image_file.seek(0)
    if size > 20 * 1024 * 1024:
        return jsonify({"error": "Image must be under 20MB"}), 400
    lat = request.form.get("lat", type=float)
    lng = request.form.get("lng", type=float)
    accuracy = request.form.get("accuracy", type=float)
    note = (request.form.get("note") or "")[:200]
    captured_at = request.form.get("captured_at") or datetime.utcnow().isoformat()
    if lat is None or lng is None:
        return jsonify({"error": "lat and lng are required"}), 400
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return jsonify({"error": "lat must be in [-90, 90], lng in [-180, 180]"}), 400
    MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio-service:9000")
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    uuid_str = str(uuid.uuid4())[:8]
    ext = "jpg" if filename.lower().endswith((".jpg", ".jpeg")) else "png"
    minio_key = f"field-images/{tenant}/{ts}_{uuid_str}.{ext}"
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=f"http://{MINIO_ENDPOINT}",
            aws_access_key_id=os.getenv("MINIO_ACCESS_KEY", ""),
            aws_secret_access_key=os.getenv("MINIO_SECRET_KEY", ""),
            config=boto3.session.Config(signature_version="s3v4"),
            region_name="us-east-1",
        )
        s3.put_object(
            Bucket="nekazari-frontend",
            Key=minio_key,
            Body=image_file.read(),
            ContentType="image/jpeg" if ext == "jpg" else "image/png",
        )
    except Exception as e:
        logger.error(f"MinIO upload failed: {e}")
        return jsonify({"error": "Image storage unavailable"}), 502
    # Relative URL: consumers (web viewer, mobile) prepend their own API base
    # (VITE_API_URL / EXPO_PUBLIC_API_URL), per platform convention. Avoids
    # hardcoding the production domain in source.
    image_url = f"/api/field-images/{minio_key}"
    entity_id = None
    parcel_id = resolve_parcel_for_point(tenant, lng, lat, accuracy)
    try:
        ngsi_entity = {
            "id": f"urn:ngsi-ld:AgriParcelRecord:photo-{ts}-{uuid_str}",
            "type": "AgriParcelRecord",
            "imageUrl": {"type": "Property", "value": image_url},
            "location": {
                "type": "GeoProperty",
                "value": {"type": "Point", "coordinates": [lng, lat]},
            },
            "dateObserved": {"type": "Property", "value": captured_at},
            "belongsTo": {
                "type": "Relationship",
                "object": f"urn:ngsi-ld:Tenant:{tenant}",
            },
        }
        if parcel_id:
            ngsi_entity["hasAgriParcel"] = {"type": "Relationship", "object": parcel_id}
        if note:
            ngsi_entity["note"] = {"type": "Property", "value": note}
        if accuracy is not None:
            ngsi_entity["accuracy"] = {"type": "Property", "value": accuracy}
        ctx_url = os.getenv(
            "CONTEXT_URL", "http://api-gateway-service:5000/ngsi-ld-context.json"
        )
        ORION_LD_URL = os.getenv("ORION_URL", "http://orion-service:1026")
        ngsi_entity["@context"] = [ctx_url]
        # Use the canonical header builder with has_context_in_body=True. The
        # request-aware gateway wrapper would mis-detect this multipart upload
        # request as non-JSON and force Content-Type application/json + Link,
        # which combined with the body @context makes Orion-LD reject the
        # entity (400) — silently dropping the observation.
        from ngsi_headers import inject_fiware_headers as _canonical_headers

        ngsi_headers = _canonical_headers({}, tenant=tenant, has_context_in_body=True)
        ngsi_resp = requests.post(
            f"{ORION_LD_URL}/ngsi-ld/v1/entities",
            json=ngsi_entity,
            headers=ngsi_headers,
            timeout=10,
        )
        if ngsi_resp.status_code in (200, 201):
            entity_id = ngsi_entity["id"]
    except Exception as e:
        logger.warning("NGSI-LD observation creation failed (non-fatal): %s", e)
    return jsonify(
        {"success": True, "image_url": image_url, "entity_id": entity_id}
    ), 200


@app.route("/api/field-images/<path:key>", methods=["GET", "OPTIONS"])
@cross_origin(origins=_cors_origins, supports_credentials=True)
def field_image_read(key):
    """Authenticated, tenant-scoped read proxy for field images in MinIO."""
    if request.method == "OPTIONS":
        return "", 204
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid token"}), 401
    tenant = extract_tenant_id(payload)
    if not tenant:
        return jsonify({"error": "Tenant not present in token"}), 401
    # Keys are field-images/<tenant>/<file>; enforce ownership.
    parts = key.split("/")
    if len(parts) < 3 or parts[0] != "field-images" or parts[1] != tenant:
        return jsonify({"error": "Forbidden"}), 403
    MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio-service:9000")
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=f"http://{MINIO_ENDPOINT}",
            aws_access_key_id=os.getenv("MINIO_ACCESS_KEY", ""),
            aws_secret_access_key=os.getenv("MINIO_SECRET_KEY", ""),
            config=boto3.session.Config(signature_version="s3v4"),
            region_name="us-east-1",
        )
        obj = s3.get_object(Bucket="nekazari-frontend", Key=key)
    except Exception as e:
        logger.warning("Field image read failed: %s", e)
        return jsonify({"error": "Image not found"}), 404
    content_type = obj.get("ContentType", "image/jpeg")
    data = obj["Body"].read()
    resp = make_response(data)
    resp.headers["Content-Type"] = content_type
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@app.route("/api/push/register", methods=["POST", "OPTIONS"])
@cross_origin(origins=_cors_origins, supports_credentials=True)
def proxy_push_register():
    """Proxy device token registration to push-notification-service."""
    if request.method == "OPTIONS":
        return "", 204
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid token"}), 401
    target_url = "http://push-notification-service:5000/register"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        response = requests.post(
            target_url, headers=headers, json=request.get_json(silent=True), timeout=10
        )
        return (response.content, response.status_code, response.headers.items())
    except Exception as e:
        logger.error(f"Error proxying push register: {e}")
        return jsonify({"error": "Internal service connection error"}), 502


@app.route(
    "/api/tenant/<path:subpath>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
@cross_origin(origins=_cors_origins, supports_credentials=True)
def proxy_tenant_requests(subpath):
    """Proxy tenant requests to tenant-user-api or tenant-webhook"""
    if request.method == "OPTIONS":
        return "", 204

    # Validate JWT token from header or cookie
    token = get_request_token()
    if not token:
        logger.warning(f"Missing or invalid authorization for /api/tenant/{subpath}")
        return jsonify({"error": "Missing or invalid authorization header"}), 401

    payload = validate_jwt_token(token)
    if not payload:
        logger.warning(f"Token validation failed for /api/tenant/{subpath}")
        return jsonify({"error": "Invalid or expired token"}), 401

    # Suspension check: block requests from suspended tenants
    tenant = extract_tenant_id(payload)
    if tenant and tenant != "platform" and _is_tenant_suspended(tenant):
        return jsonify(
            {
                "error": "TENANT_SUSPENDED",
                "message": "Tu cuenta ha sido suspendida. Contacta con el administrador de la plataforma.",
            }
        ), 403

    # Route logic:
    # 1. tenant/users -> tenant-user-api-service
    # 2. Everything else -> tenant-webhook-service
    if subpath.startswith("users") or subpath.startswith("profile"):
        target_url = f"{TENANT_USER_API_URL}/api/tenant/{subpath}"
    else:
        target_url = f"{TENANT_WEBHOOK_URL}/api/tenant/{subpath}"

    # Forward request
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": request.content_type or "application/json",
    }

    # Forward query parameters
    params = dict(request.args)

    try:
        if request.method == "GET":
            response = requests.get(
                target_url, headers=headers, params=params, timeout=10
            )
        elif request.method == "POST":
            response = requests.post(
                target_url,
                headers=headers,
                params=params,
                json=request.get_json(silent=True),
                timeout=10,
            )
        elif request.method == "PUT":
            response = requests.put(
                target_url,
                headers=headers,
                params=params,
                json=request.get_json(silent=True),
                timeout=10,
            )
        elif request.method == "PATCH":
            response = requests.patch(
                target_url,
                headers=headers,
                params=params,
                json=request.get_json(silent=True),
                timeout=10,
            )
        elif request.method == "DELETE":
            response = requests.delete(
                target_url, headers=headers, params=params, timeout=10
            )
        else:
            return jsonify({"error": f"Method {request.method} not supported"}), 405

        return (response.content, response.status_code, response.headers.items())

    except Exception as e:
        logger.error(f"Error proxying tenant request to {target_url}: {e}")
        return jsonify({"error": "Failed to connect to internal service"}), 502


@app.route("/api/terms/<language>", methods=["GET", "OPTIONS"])
def public_terms_proxy(language):
    """Public endpoint for terms & conditions (used during registration)."""
    if request.method == "OPTIONS":
        return "", 204
    target_url = f"{ENTITY_MANAGER_URL}/api/admin/terms/{language}"
    try:
        resp = requests.get(target_url, timeout=10)
        return (resp.content, resp.status_code, dict(resp.headers))
    except Exception as e:
        logger.error(f"Error proxying public terms: {e}")
        return jsonify({"content": "", "last_updated": None, "language": language}), 200


@app.route("/api/public/platform-settings", methods=["GET", "OPTIONS"])
def public_platform_settings_proxy():
    """Public endpoint for non-sensitive platform settings used before login."""
    if request.method == "OPTIONS":
        return "", 204
    target_url = f"{ENTITY_MANAGER_URL}/api/public/platform-settings"
    try:
        resp = requests.get(target_url, timeout=10)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        logger.error(f"Error proxying public platform settings: {e}")
        return jsonify({"landing_mode": "standard"}), 200


@app.route(
    "/api/admin/<path:subpath>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
def proxy_admin_requests(subpath):
    """Proxy administrative requests using a deterministic routing map."""
    if request.method == "OPTIONS":
        return "", 204

    # 1. Enforcement: Only admins can access these routes
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    if not has_role("PlatformAdmin", payload):
        logger.warning(
            f"Unauthorized admin access attempt by {payload.get('preferred_username')} on /api/admin/{subpath}"
        )
        return jsonify({"error": "PlatformAdmin access required"}), 403

    # 2. Deterministic Routing Map
    # Hostnames match internal Kubernetes service names
    ADMIN_ROUTE_MAP = {
        # ENTITY-MANAGER: Core entity metadata, logs, and assets
        "audit-logs": ENTITY_MANAGER_URL,
        "terms": ENTITY_MANAGER_URL,
        "platform-settings": ENTITY_MANAGER_URL,
        "tenant-usage": ENTITY_MANAGER_URL,
        "assets": ENTITY_MANAGER_URL,
        "parcels": ENTITY_MANAGER_URL,
        # TENANT-WEBHOOK: Marketplace, Tenants, Activations, Limits, and Codes
        "tenants": TENANT_WEBHOOK_URL,
        "activations": TENANT_WEBHOOK_URL,
        "tenant-limits": TENANT_WEBHOOK_URL,
        "api-keys": TENANT_WEBHOOK_URL,
        "users": TENANT_WEBHOOK_URL,
        "platform-credentials": TENANT_WEBHOOK_URL,
    }

    path_parts = subpath.split("/")
    route_key = path_parts[0]

    # Special cases: sub-resource routing within tenants/*
    if route_key == "tenants" and len(path_parts) > 2:
        sub_resource = path_parts[2]
        if sub_resource == "purge":
            target_base_url = (
                TENANT_WEBHOOK_URL  # Moved from entity-manager (was broken)
            )
        elif sub_resource == "inventory":
            target_base_url = ENTITY_MANAGER_URL  # Read-only aggregation
        elif sub_resource == "suspend":
            target_base_url = TENANT_WEBHOOK_URL
        elif sub_resource == "restore":
            target_base_url = TENANT_WEBHOOK_URL
        else:
            target_base_url = TENANT_WEBHOOK_URL  # Default for tenants/* sub-routes
    else:
        target_base_url = ADMIN_ROUTE_MAP.get(route_key)

    # Special case: user tenant reassignment
    if route_key == "users" and len(path_parts) > 2 and path_parts[2] == "tenant":
        target_base_url = TENANT_WEBHOOK_URL

    if not target_base_url:
        logger.error(f"Unmapped admin route: /api/admin/{subpath}")
        return jsonify({"error": f"Admin route /{route_key} is not configured"}), 404

    target_url = f"{target_base_url}/api/admin/{subpath}"

    tenant = extract_tenant_id(payload)

    try:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": request.content_type or "application/json",
            "X-Tenant-ID": tenant,
        }

        signature = generate_hmac_signature(token, tenant)
        if signature:
            headers["X-Auth-Signature"] = signature

        method = request.method
        params = dict(request.args)
        json_data = (
            request.get_json(silent=True)
            if method in ["POST", "PUT", "PATCH"]
            else None
        )

        # Long-running admin operations need a generous timeout because they
        # orchestrate Kubernetes script execution (create-tenant.sh: kubectl
        # apply + namespace + RBAC + ResourceQuota + NetworkPolicy + waits)
        # which routinely takes 60-180 s. Default stays 30 s for fast ops
        # like listing/updating.
        SLOW_ADMIN_OPS = {
            ("tenants", "POST"),  # create_tenant_directly
            ("tenants", "DELETE"),  # legacy delete (now goes through suspend+purge)
        }
        if (route_key, method) in SLOW_ADMIN_OPS or (
            route_key == "tenants"
            and len(path_parts) > 2
            and path_parts[2] in {"purge", "suspend", "restore"}
        ):
            proxy_timeout = 300
        else:
            proxy_timeout = 30

        response = requests.request(
            method=method,
            url=target_url,
            headers=headers,
            params=params,
            json=json_data,
            timeout=proxy_timeout,
        )

        return (response.content, response.status_code, response.headers.items())

    except Exception as e:
        logger.error(f"Error proxying admin request to {target_url}: {e}")
        return jsonify({"error": "Internal service connection error"}), 502


@app.route(
    "/api/ndvi/<path:subpath>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
def proxy_ndvi_requests(subpath):
    """Proxy NDVI service requests"""
    # Handle CORS preflight
    if request.method == "OPTIONS":
        response = make_response()
        cors_origin = get_cors_origin()
        if cors_origin:
            response.headers["Access-Control-Allow-Origin"] = cors_origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        )
        response.headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type, X-Tenant-ID, X-Module-Id, Cookie"
        )
        response.headers["Access-Control-Max-Age"] = "3600"
        response.headers["Vary"] = "Origin"
        return response, 200

    # Validate JWT token
    token = get_request_token()
    if not token:
        logger.warning(f"Missing or invalid authorization for /api/ndvi/{subpath}")
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        logger.warning(f"Token validation failed for /api/ndvi/{subpath}")
        return jsonify({"error": "Invalid or expired token"}), 401

    # Extract tenant
    tenant = extract_tenant_id(payload)

    # Check if user is PlatformAdmin (can work without tenant or with specified tenant)
    user_roles = payload.get("realm_access", {}).get("roles", []) or []
    resource_roles = []
    for resource in payload.get("resource_access", {}).values():
        if isinstance(resource, dict) and "roles" in resource:
            resource_roles.extend(resource["roles"])
    all_roles = list(set(user_roles + resource_roles + payload.get("roles", [])))
    is_platform_admin = "PlatformAdmin" in all_roles

    # If no tenant in token, check if PlatformAdmin can use default or request tenant
    if not tenant:
        if is_platform_admin:
            # PlatformAdmin can specify tenant in request header or use default
            tenant = request.headers.get("X-Tenant-ID") or request.args.get("tenant_id")
            if not tenant:
                # For PlatformAdmin, try to get tenant from request body (for POST requests)
                if request.is_json and request.json:
                    tenant = request.json.get("tenant_id") or request.json.get("tenant")

                if not tenant:
                    # Use default platform admin tenant for cross-tenant operations
                    tenant = os.getenv("PLATFORM_ADMIN_TENANT", "platform_admin")
                    logger.info(
                        f"PlatformAdmin user {payload.get('preferred_username')} ({payload.get('email')}) using default tenant: {tenant}"
                    )
                else:
                    logger.info(
                        f"PlatformAdmin user {payload.get('preferred_username')} ({payload.get('email')}) using tenant from request: {tenant}"
                    )
            else:
                logger.info(
                    f"PlatformAdmin user {payload.get('preferred_username')} ({payload.get('email')}) using specified tenant: {tenant}"
                )
        else:
            logger.warning(
                f"No tenant found in token for /api/ndvi/{subpath}. User: {payload.get('preferred_username')}, Payload keys: {list(payload.keys())}, roles: {all_roles}"
            )
            return jsonify(
                {
                    "error": "Tenant not present in token",
                    "suggestion": "Your user account may not have a tenant assigned. Please contact an administrator.",
                    "user": payload.get("preferred_username"),
                    "roles": all_roles,
                }
            ), 401

    # Rate limit
    if not rate_limit(tenant):
        logger.warning(
            f"Rate limit exceeded for tenant {tenant} on /api/ndvi/{subpath}"
        )
        return jsonify({"error": "Rate limit exceeded"}), 429

    logger.info(
        f"NDVI request to /api/ndvi/{subpath} for tenant {tenant} - forwarding to service"
    )

    try:
        # Entity-manager has endpoints at /ndvi/ not /api/ndvi/
        # All subpaths (including download/) are forwarded to entity-manager
        target_url = f"{NDVI_SERVICE_URL}/ndvi/{subpath}"

        # Prepare headers
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": request.content_type or "application/json",
            "X-Tenant-ID": tenant,  # Pass tenant to trusted internal service
        }
        # Generate and add HMAC signature for internal service authentication
        signature = generate_hmac_signature(token, tenant)
        if signature:
            headers["X-Auth-Signature"] = signature

        # Forward query params
        params = dict(request.args)

        # Forward request body for POST/PUT/PATCH
        data = None
        json_data = None
        if request.method in ["POST", "PUT", "PATCH"] and request.is_json:
            json_data = request.get_json(silent=True)
        elif request.data:
            data = request.data

        logger.info(
            f"Forwarding {request.method} request to {target_url} with json_data={json_data is not None}, data={data is not None}, headers={list(headers.keys())}"
        )

        # Forward request to NDVI service
        if request.method == "GET":
            response = requests.get(
                target_url, headers=headers, params=params, timeout=30
            )
        elif request.method == "POST":
            response = requests.post(
                target_url,
                headers=headers,
                params=params,
                json=json_data,
                data=data,
                timeout=30,
            )
            logger.info(
                f"NDVI service response: {response.status_code} - {response.text[:200]}"
            )
            if response.status_code == 404:
                logger.error(
                    f"NDVI endpoint not found. Target URL: {target_url}, Service URL: {NDVI_SERVICE_URL}, Subpath: {subpath}"
                )
        elif request.method == "PUT":
            response = requests.put(
                target_url,
                headers=headers,
                params=params,
                json=json_data,
                data=data,
                timeout=30,
            )
        elif request.method == "PATCH":
            response = requests.patch(
                target_url,
                headers=headers,
                params=params,
                json=json_data,
                data=data,
                timeout=30,
            )
        elif request.method == "DELETE":
            response = requests.delete(
                target_url, headers=headers, params=params, timeout=30
            )
            if response.status_code >= 400:
                logger.error(
                    f"NDVI DELETE request failed: {response.status_code} - {response.text[:500]}"
                )
                logger.error(
                    f"Target URL: {target_url}, Subpath: {subpath}, Params: {params}"
                )
        else:
            return jsonify({"error": "Method not allowed"}), 405

        # Return response from NDVI service
        response_headers = dict(response.headers)
        # Remove content-encoding if present to avoid double encoding
        response_headers.pop("Content-Encoding", None)
        response_headers.pop("Transfer-Encoding", None)

        return make_response((response.text, response.status_code, response_headers))

    except requests.exceptions.Timeout:
        logger.error(f"Timeout connecting to NDVI service for /api/ndvi/{subpath}")
        return jsonify({"error": "NDVI service timeout"}), 504
    except requests.exceptions.RequestException as e:
        logger.error(f"Error proxying request to NDVI service: {e}")
        return jsonify({"error": f"Failed to connect to NDVI service: {str(e)}"}), 502
    except Exception as e:
        logger.error(f"Error in proxy_ndvi_requests: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route(
    "/api/weather/<path:subpath>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
def proxy_weather_requests(subpath):
    """Proxy weather service requests to entity-manager"""
    logger.info(f"Weather request received: {request.method} /api/weather/{subpath}")
    # Handle CORS preflight
    if request.method == "OPTIONS":
        response = make_response()
        cors_origin = get_cors_origin()
        if cors_origin:
            response.headers["Access-Control-Allow-Origin"] = cors_origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        )
        response.headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type, X-Tenant-ID, X-Module-Id, Cookie"
        )
        response.headers["Access-Control-Max-Age"] = "3600"
        response.headers["Vary"] = "Origin"
        return response, 200

    # Validate JWT token (optional for some endpoints like municipalities/search)
    token = get_request_token()
    payload = None
    tenant = None

    if token:
        payload = validate_jwt_token(token)
        if payload:
            tenant = extract_tenant_id(payload)
            logger.info(f"Token validated for /api/weather/{subpath}, tenant: {tenant}")
        else:
            logger.warning(
                f"Token validation failed for /api/weather/{subpath}, but continuing (endpoint may allow unauthenticated)"
            )
    else:
        logger.info(
            f"No authorization header for /api/weather/{subpath}, continuing (endpoint may allow unauthenticated)"
        )

    # For municipalities/search, allow unauthenticated requests (entity-manager will handle auth)
    # For other endpoints, require authentication
    if subpath != "municipalities/search" and not payload:
        logger.warning(
            f"Missing or invalid authorization header for /api/weather/{subpath}"
        )
        return jsonify({"error": "Missing or invalid authorization header"}), 401

    # Use tenant from token if available, otherwise use X-Tenant-ID header or default
    if not tenant:
        tenant = request.headers.get("X-Tenant-ID", "platform")
        logger.info(f"Using tenant from X-Tenant-ID header or default: {tenant}")

    # Rate limit
    if not rate_limit(tenant):
        logger.warning(
            f"Rate limit exceeded for tenant {tenant} on /api/weather/{subpath}"
        )
        return jsonify({"error": "Rate limit exceeded"}), 429

    logger.info(
        f"Weather request to /api/weather/{subpath} for tenant {tenant} - forwarding to entity-manager"
    )

    try:
        # Build target URL
        target_url = f"{WEATHER_API_URL}/api/weather/{subpath}"

        # Prepare headers for entity-manager
        headers = {
            "Content-Type": request.content_type or "application/json",
            "X-Tenant-ID": tenant,
        }
        # Only add Authorization header if we have a valid token
        if token:
            headers["Authorization"] = f"Bearer {token}"

        # Add HMAC signature if available (entity-manager may require it for some endpoints)
        if KEYCLOAK_AUTH_AVAILABLE:
            try:
                signature = generate_hmac_signature(token, tenant)
                if signature:
                    headers["X-Auth-Signature"] = signature
            except Exception as e:
                logger.warning(f"Failed to generate HMAC signature: {e}")

        # Forward query params
        params = dict(request.args)

        # Forward request body for POST/PUT/PATCH
        json_data = None
        if request.method in ["POST", "PUT", "PATCH"] and request.is_json:
            json_data = request.get_json(silent=True)
        elif request.data:
            json_data = request.get_json(silent=True)

        # Forward request to entity-manager
        if request.method == "GET":
            response = requests.get(
                target_url, headers=headers, params=params, timeout=30
            )
        elif request.method == "POST":
            response = requests.post(
                target_url, headers=headers, json=json_data, params=params, timeout=30
            )
        elif request.method == "PUT":
            response = requests.put(
                target_url, headers=headers, json=json_data, params=params, timeout=30
            )
        elif request.method == "PATCH":
            response = requests.patch(
                target_url, headers=headers, json=json_data, params=params, timeout=30
            )
        elif request.method == "DELETE":
            response = requests.delete(
                target_url, headers=headers, params=params, timeout=30
            )
        else:
            return jsonify({"error": "Method not allowed"}), 405

        # Return response from entity-manager
        response_headers = dict(response.headers)
        # Remove content-encoding if present to avoid double encoding
        response_headers.pop("Content-Encoding", None)
        response_headers.pop("Transfer-Encoding", None)

        # Ensure CORS headers are present in the response
        cors_origin = get_cors_origin()
        if cors_origin:
            response_headers["Access-Control-Allow-Origin"] = cors_origin
            response_headers["Access-Control-Allow-Credentials"] = "true"
            response_headers["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type, X-Tenant-ID"
            )
            response_headers["Vary"] = "Origin"

        return make_response((response.text, response.status_code, response_headers))

    except requests.exceptions.Timeout:
        logger.error(f"Timeout connecting to entity-manager for /api/weather/{subpath}")
        return jsonify({"error": "Entity manager service timeout"}), 504
    except requests.exceptions.RequestException as e:
        logger.error(f"Error proxying request to entity-manager: {e}")
        return jsonify(
            {"error": f"Failed to connect to entity-manager service: {str(e)}"}
        ), 502
    except Exception as e:
        logger.error(f"Error in proxy_weather_requests: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route(
    "/api/modules/<path:subpath>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
def proxy_modules_requests(subpath):
    """Proxy modules requests to entity-manager"""
    logger.info(f"Modules request received: {request.method} /api/modules/{subpath}")
    # Handle CORS preflight
    if request.method == "OPTIONS":
        response = make_response()
        cors_origin = get_cors_origin()
        if cors_origin:
            response.headers["Access-Control-Allow-Origin"] = cors_origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        )
        response.headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type, X-Tenant-ID, X-Module-Id, Cookie, X-Gestor-Target-Tenant"
        )
        response.headers["Access-Control-Max-Age"] = "3600"
        response.headers["Vary"] = "Origin"
        return response, 200

    # Validate JWT token
    token = get_request_token()
    if not token:
        logger.warning(f"Missing or invalid authorization for /api/modules/{subpath}")
        return jsonify({"error": "Missing or invalid authorization"}), 401

    payload = validate_jwt_token(token)
    if not payload:
        logger.warning(f"Token validation failed for /api/modules/{subpath}")
        return jsonify({"error": "Invalid or expired token"}), 401

    # Extract tenant
    tenant = extract_tenant_id(payload)
    if not tenant:
        logger.warning(f"No tenant found in token for /api/modules/{subpath}")
        return jsonify({"error": "Tenant not present in token"}), 401

    # Rate limit
    if not rate_limit(tenant):
        logger.warning(
            f"Rate limit exceeded for tenant {tenant} on /api/modules/{subpath}"
        )
        return jsonify({"error": "Rate limit exceeded"}), 429

    logger.info(
        f"Modules request to /api/modules/{subpath} for tenant {tenant} - forwarding to entity-manager"
    )

    try:
        # Build target URL
        target_url = f"{ENTITY_MANAGER_URL}/api/modules/{subpath}"

        # Prepare headers
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": request.content_type or "application/json",
            "X-Tenant-ID": tenant,
        }

        # Forward gestor target tenant header for cross-tenant CUE access
        gestor_target = request.headers.get("X-Gestor-Target-Tenant")
        if gestor_target:
            headers["X-Gestor-Target-Tenant"] = gestor_target

        # Add HMAC signature if available
        if KEYCLOAK_AUTH_AVAILABLE:
            try:
                signature = generate_hmac_signature(token, tenant)
                if signature:
                    headers["X-Auth-Signature"] = signature
            except Exception as e:
                logger.warning(f"Failed to generate HMAC signature: {e}")

        # Forward query params
        params = dict(request.args)

        # Forward request body
        json_data = None
        if request.method in ["POST", "PUT", "PATCH"] and request.is_json:
            json_data = request.get_json(silent=True)
        elif request.data:
            json_data = request.get_json(silent=True)

        # Forward request to entity-manager
        if request.method == "GET":
            response = requests.get(
                target_url, headers=headers, params=params, timeout=30
            )
        elif request.method == "POST":
            response = requests.post(
                target_url, headers=headers, json=json_data, params=params, timeout=30
            )
        elif request.method == "PUT":
            response = requests.put(
                target_url, headers=headers, json=json_data, params=params, timeout=30
            )
        elif request.method == "PATCH":
            response = requests.patch(
                target_url, headers=headers, json=json_data, params=params, timeout=30
            )
        elif request.method == "DELETE":
            response = requests.delete(
                target_url, headers=headers, params=params, timeout=30
            )
        else:
            return jsonify({"error": "Method not allowed"}), 405

        # Return response
        response_headers = dict(response.headers)
        response_headers.pop("Content-Encoding", None)
        response_headers.pop("Transfer-Encoding", None)

        # Ensure CORS headers
        cors_origin = get_cors_origin()
        if cors_origin:
            response_headers["Access-Control-Allow-Origin"] = cors_origin
            response_headers["Access-Control-Allow-Credentials"] = "true"
            response_headers["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type, X-Tenant-ID"
            )
            response_headers["Vary"] = "Origin"

        return make_response((response.text, response.status_code, response_headers))

    except requests.exceptions.Timeout:
        logger.error(f"Timeout connecting to entity-manager for /api/modules/{subpath}")
        return jsonify({"error": "Entity manager service timeout"}), 504
    except requests.exceptions.RequestException as e:
        logger.error(f"Error proxying request to entity-manager: {e}")
        return jsonify(
            {"error": f"Failed to connect to entity-manager service: {str(e)}"}
        ), 502
    except Exception as e:
        logger.error(f"Error in proxy_modules_requests: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route(
    "/api/cadastral-api/<path:subpath>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
def proxy_cadastral_api_requests(subpath):
    """Proxy cadastral-api service requests"""
    logger.info(
        f"Cadastral API request received: {request.method} /api/cadastral-api/{subpath}"
    )
    # Handle CORS preflight
    if request.method == "OPTIONS":
        response = make_response()
        cors_origin = get_cors_origin()
        if cors_origin:
            response.headers["Access-Control-Allow-Origin"] = cors_origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        )
        response.headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type, X-Tenant-ID, X-Module-Id, Cookie"
        )
        response.headers["Access-Control-Max-Age"] = "3600"
        response.headers["Vary"] = "Origin"
        return response, 200

    # Validate JWT token
    token = get_request_token()
    if not token:
        logger.warning(
            f"Missing or invalid authorization for /api/cadastral-api/{subpath}"
        )
        return jsonify({"error": "Missing or invalid authorization"}), 401

    payload = validate_jwt_token(token)
    if not payload:
        logger.warning(f"Token validation failed for /api/cadastral-api/{subpath}")
        return jsonify({"error": "Invalid or expired token"}), 401

    # Extract tenant
    tenant = extract_tenant_id(payload)
    if not tenant:
        logger.warning(f"No tenant found in token for /api/cadastral-api/{subpath}")
        return jsonify({"error": "Tenant not present in token"}), 401

    # Rate limit
    if not rate_limit(tenant):
        logger.warning(
            f"Rate limit exceeded for tenant {tenant} on /api/cadastral-api/{subpath}"
        )
        return jsonify({"error": "Rate limit exceeded"}), 429

    logger.info(
        f"Cadastral API request to /api/cadastral-api/{subpath} for tenant {tenant} - forwarding to cadastral-api service"
    )

    try:
        # Build target URL - cadastral-api service expects paths like /parcels/query-by-coordinates
        target_url = f"{CADASTRAL_API_URL}/{subpath}"

        # Prepare headers
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": request.content_type or "application/json",
            "X-Tenant-ID": tenant,  # Pass tenant to trusted internal service
        }

        # Add HMAC signature if available
        if KEYCLOAK_AUTH_AVAILABLE:
            try:
                signature = generate_hmac_signature(token, tenant)
                if signature:
                    headers["X-Auth-Signature"] = signature
            except Exception as e:
                logger.warning(f"Failed to generate HMAC signature: {e}")

        # Forward query params
        params = dict(request.args)

        # Forward request body for POST/PUT/PATCH
        json_data = None
        if request.method in ["POST", "PUT", "PATCH"] and request.is_json:
            json_data = request.get_json(silent=True)
        elif request.data:
            json_data = request.get_json(silent=True)
        data = request.data if not request.is_json else None

        # Forward request to cadastral-api service
        if request.method == "GET":
            response = requests.get(
                target_url, headers=headers, params=params, timeout=30
            )
        elif request.method == "POST":
            response = requests.post(
                target_url,
                headers=headers,
                params=params,
                json=json_data,
                data=data,
                timeout=30,
            )
        elif request.method == "PUT":
            response = requests.put(
                target_url,
                headers=headers,
                params=params,
                json=json_data,
                data=data,
                timeout=30,
            )
        elif request.method == "PATCH":
            response = requests.patch(
                target_url,
                headers=headers,
                params=params,
                json=json_data,
                data=data,
                timeout=30,
            )
        elif request.method == "DELETE":
            response = requests.delete(
                target_url, headers=headers, params=params, timeout=30
            )
        else:
            return jsonify({"error": "Method not allowed"}), 405

        # Log errors
        if response.status_code >= 400:
            logger.warning(
                f"Cadastral API service returned {response.status_code} for /api/cadastral-api/{subpath}: {response.text}"
            )

        # Forward response
        return make_response(
            response.content, response.status_code, dict(response.headers)
        )

    except requests.exceptions.Timeout:
        logger.error(
            f"Timeout forwarding request to cadastral-api service: {target_url}"
        )
        return jsonify({"error": "Cadastral API service request timeout"}), 504
    except requests.exceptions.RequestException as e:
        logger.error(f"Error forwarding cadastral-api request: {e}")
        return jsonify({"error": "Internal server error"}), 500
    except Exception as e:
        logger.error(f"Unexpected error in cadastral-api proxy: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


# =============================================================================
# Processing Profiles CRUD Endpoints
# =============================================================================


@app.route("/api/v1/profiles", methods=["GET"])
def list_profiles():
    """List all processing profiles."""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    # Only admins can access profiles
    if not has_role("PlatformAdmin", payload) and not has_role("TenantAdmin", payload):
        return jsonify({"error": "Admin access required"}), 403

    try:
        # POSTGRES_URL inherited from global
        if not POSTGRES_URL:
            logger.error("POSTGRES_URL not configured")
            return jsonify({"error": "Database not configured"}), 500

        try:
            conn = psycopg2.connect(POSTGRES_URL)
        except Exception as conn_err:
            logger.error(f"Failed to connect to database: {conn_err}")
            return jsonify({"error": "Database connection failed"}), 500

        cur = conn.cursor(cursor_factory=RealDictCursor)

        device_type = request.args.get("device_type")
        tenant_id = request.args.get("tenant_id")

        query = """
            SELECT id::text, device_type, device_id, tenant_id::text,
                   name, description, config, priority, is_active,
                   created_at, updated_at
            FROM processing_profiles
            WHERE 1=1
        """
        params = []

        if device_type:
            query += " AND device_type = %s"
            params.append(device_type)

        if tenant_id:
            query += " AND (tenant_id = %s::uuid OR tenant_id IS NULL)"
            params.append(tenant_id)

        # If no tenant_id provided and user is not PlatformAdmin, filter by their tenant
        # Note: tenant_id in processing_profiles is UUID, but extract_tenant_id may return a string
        # For non-PlatformAdmin users, we only show profiles with their tenant_id or NULL (global profiles)
        if not tenant_id and not has_role("PlatformAdmin", payload):
            user_tenant_id = extract_tenant_id(payload)
            if user_tenant_id:
                # Try to convert to UUID if it's a valid UUID string, otherwise skip tenant filtering
                try:
                    import uuid

                    # Validate if it's a UUID format
                    uuid.UUID(user_tenant_id)
                    query += " AND (tenant_id = %s::uuid OR tenant_id IS NULL)"
                    params.append(user_tenant_id)
                except (ValueError, AttributeError):
                    # If tenant_id is not a valid UUID (e.g., "platform"), only show global profiles
                    query += " AND tenant_id IS NULL"

        query += " ORDER BY device_type, priority DESC"

        cur.execute(query, params)
        profiles = [dict(row) for row in cur.fetchall()]
        cur.close()
        conn.close()

        # Convert datetime to string for JSON
        for p in profiles:
            if p.get("created_at"):
                p["created_at"] = p["created_at"].isoformat()
            if p.get("updated_at"):
                p["updated_at"] = p["updated_at"].isoformat()

        return jsonify({"profiles": profiles}), 200

    except Exception as e:
        logger.error(f"Error listing profiles: {e}")
        import traceback

        logger.error(traceback.format_exc())
        return jsonify({"error": "Internal server error", "details": str(e)}), 500


@app.route("/api/v1/profiles", methods=["POST"])
def create_profile():
    """Create a new processing profile."""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    if not has_role("PlatformAdmin", payload) and not has_role("TenantAdmin", payload):
        return jsonify({"error": "Admin access required"}), 403

    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        data = request.json
        required = ["device_type", "name", "config"]
        for field in required:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400

        # Use global POSTGRES_URL
        conn = psycopg2.connect(POSTGRES_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute(
            """
            INSERT INTO processing_profiles (
                device_type, device_id, tenant_id, name, description,
                config, priority, is_active
            )
            VALUES (%s, %s, %s::uuid, %s, %s, %s::jsonb, %s, %s)
            RETURNING id::text
        """,
            (
                data["device_type"],
                data.get("device_id"),
                data.get("tenant_id"),
                data["name"],
                data.get("description"),
                json.dumps(data["config"]),
                data.get("priority", 0),
                data.get("is_active", True),
            ),
        )

        profile_id = cur.fetchone()["id"]
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"id": profile_id, "message": "Profile created"}), 201

    except Exception as e:
        logger.error(f"Error creating profile: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/v1/profiles/<profile_id>", methods=["PUT"])
def update_profile(profile_id):
    """Update a processing profile."""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    if not has_role("PlatformAdmin", payload) and not has_role("TenantAdmin", payload):
        return jsonify({"error": "Admin access required"}), 403

    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        data = request.json
        # Use global POSTGRES_URL
        conn = psycopg2.connect(POSTGRES_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        updates = []
        values = []

        if "name" in data:
            updates.append("name = %s")
            values.append(data["name"])
        if "description" in data:
            updates.append("description = %s")
            values.append(data["description"])
        if "config" in data:
            updates.append("config = %s::jsonb")
            values.append(json.dumps(data["config"]))
        if "priority" in data:
            updates.append("priority = %s")
            values.append(data["priority"])
        if "is_active" in data:
            updates.append("is_active = %s")
            values.append(data["is_active"])

        if not updates:
            return jsonify({"error": "No fields to update"}), 400

        updates.append("updated_at = NOW()")
        values.append(profile_id)

        query = f"""
            UPDATE processing_profiles
            SET {", ".join(updates)}
            WHERE id = %s::uuid
            RETURNING id::text
        """

        cur.execute(query, values)
        if not cur.fetchone():
            return jsonify({"error": "Profile not found"}), 404

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"message": "Profile updated"}), 200

    except Exception as e:
        logger.error(f"Error updating profile: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/v1/profiles/<profile_id>", methods=["DELETE"])
def delete_profile(profile_id):
    """Delete a processing profile."""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    if not has_role("PlatformAdmin", payload):
        return jsonify({"error": "PlatformAdmin access required"}), 403

    try:
        import psycopg2

        # Use global POSTGRES_URL
        conn = psycopg2.connect(POSTGRES_URL)
        cur = conn.cursor()

        cur.execute(
            """
            DELETE FROM processing_profiles
            WHERE id = %s::uuid
            RETURNING id
        """,
            (profile_id,),
        )

        if not cur.fetchone():
            return jsonify({"error": "Profile not found"}), 404

        conn.commit()
        cur.close()
        conn.close()

        return "", 204

    except Exception as e:
        logger.error(f"Error deleting profile: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/v1/profiles/stats", methods=["GET"])
def get_telemetry_stats():
    """Get telemetry statistics including storage savings."""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    try:
        # POSTGRES_URL inherited from global
        if not POSTGRES_URL:
            logger.error("POSTGRES_URL not configured")
            return jsonify({"error": "Database not configured"}), 500

        try:
            conn = psycopg2.connect(POSTGRES_URL)
        except Exception as conn_err:
            logger.error(f"Failed to connect to database: {conn_err}")
            return jsonify({"error": "Database connection failed"}), 500

        cur = conn.cursor(cursor_factory=RealDictCursor)

        hours = int(request.args.get("hours", 24))
        tenant_id = extract_tenant_id(payload)

        query = """
            SELECT 
                COUNT(*) as persisted,
                entity_type as device_type
            FROM telemetry_events
            WHERE observed_at > NOW() - INTERVAL '%s hours'
        """
        params = [hours]

        if tenant_id and not has_role("PlatformAdmin", payload):
            query += " AND tenant_id = %s"
            params.append(tenant_id)

        query += " GROUP BY entity_type"

        cur.execute(query, params)
        rows = cur.fetchall()

        total_persisted = sum(row["persisted"] for row in rows)
        by_type = {
            row["device_type"] or "unknown": {"persisted": row["persisted"]}
            for row in rows
        }

        # Estimate received (from profiles throttle settings)
        # Rough estimate: 2.5x multiplier for throttled data
        estimated_received = int(total_persisted * 2.5)
        savings = (
            (estimated_received - total_persisted) / max(estimated_received, 1)
        ) * 100

        cur.close()
        conn.close()

        return jsonify(
            {
                "total_received": estimated_received,
                "total_persisted": total_persisted,
                "storage_savings_percent": round(savings, 1),
                "by_device_type": by_type,
                "period_hours": hours,
            }
        ), 200

    except Exception as e:
        logger.error(f"Error getting telemetry stats: {e}")
        import traceback

        logger.error(traceback.format_exc())
        return jsonify({"error": "Internal server error", "details": str(e)}), 500


@app.route("/api/v1/profiles/device-types", methods=["GET"])
def list_device_types():
    """List unique device types that have profiles."""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    try:
        # POSTGRES_URL inherited from global
        if not POSTGRES_URL:
            logger.error("POSTGRES_URL not configured")
            return jsonify({"error": "Database not configured"}), 500

        try:
            conn = psycopg2.connect(POSTGRES_URL)
        except Exception as conn_err:
            logger.error(f"Failed to connect to database: {conn_err}")
            return jsonify({"error": "Database connection failed"}), 500

        cur = conn.cursor()

        cur.execute("""
            SELECT DISTINCT device_type 
            FROM processing_profiles 
            ORDER BY device_type
        """)

        types = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()

        return jsonify({"device_types": types}), 200

    except Exception as e:
        logger.error(f"Error listing device types: {e}")
        import traceback

        logger.error(traceback.format_exc())
        return jsonify({"error": "Internal server error", "details": str(e)}), 500


def generic_proxy(target_url, path):
    """Generic proxy handler with auth and tenant isolation"""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401
    tenant = extract_tenant_id(payload)
    if not tenant:
        return jsonify({"error": "Tenant not present in token"}), 401
    if not rate_limit(tenant):
        return jsonify({"error": "Rate limit exceeded"}), 429

    # Role based access control (Read-Only fallback)
    has_pro_expired = has_role("role_pro_expired", payload)
    if has_pro_expired and request.method in ["POST", "PUT", "PATCH", "DELETE"]:
        logger.warning(
            f"Blocked mutation request to {target_url}/{path} for user with role_pro_expired"
        )
        return jsonify({"error": "Subscription expired. Read-only mode active."}), 403

    url = f"{target_url}/{path}"

    # Extract user_id from JWT sub claim
    user_id = payload.get("sub", "")
    # Collect all roles from realm_access + resource_access
    realm_roles = payload.get("realm_access", {}).get("roles", []) or []
    resource_roles = []
    for resource in payload.get("resource_access", {}).values():
        if isinstance(resource, dict) and "roles" in resource:
            resource_roles.extend(resource["roles"])
    all_roles = list(set(realm_roles + resource_roles + payload.get("roles", [])))

    headers = {
        "X-Tenant-ID": tenant,
        "X-User-ID": user_id,
        "X-User-Roles": ",".join(all_roles),
        "Authorization": f"Bearer {token}",
    }
    if request.headers.get("Content-Type"):
        headers["Content-Type"] = request.headers.get("Content-Type")
    # Forward per-user DAD-IS credentials (FAO prohibits commercial use —
    # each user brings their own API key stored client-side in localStorage)
    for hdr in ("X-Dadis-Api-Url", "X-Dadis-Api-Token"):
        if request.headers.get(hdr):
            headers[hdr] = request.headers[hdr]

    try:
        resp = requests.request(
            method=request.method,
            url=url,
            headers=headers,
            params=request.args,
            data=request.get_data(),
            cookies=request.cookies,
            allow_redirects=False,
            timeout=30,
        )
        return make_response(resp.content, resp.status_code, dict(resp.headers))
    except Exception as e:
        logger.error(f"Proxy error to {url}: {e}")
        return jsonify({"error": "Gateway proxy error", "details": str(e)}), 502


@app.route("/api/vegetation/tiles/<path:path>", methods=["GET"])
def vegetation_tiles_proxy(path):
    """Public proxy for vegetation raster tiles.

    Tile URLs contain a job UUID which acts as an unguessable access token.
    Cesium's UrlTemplateImageryProvider does not send httpOnly cookies,
    so these requests must bypass JWT auth.
    """
    url = f"{VEGETATION_API_URL}/api/vegetation/tiles/{path}"
    try:
        resp = requests.request(
            method="GET",
            url=url,
            params=request.args,
            allow_redirects=False,
            timeout=30,
        )
        response_headers = dict(resp.headers)
        response_headers["Cache-Control"] = "public, max-age=3600"
        return make_response(resp.content, resp.status_code, response_headers)
    except Exception as e:
        logger.error(f"Tile proxy error to {url}: {e}")
        return jsonify({"error": "Gateway proxy error"}), 502


@app.route(
    "/api/vegetation/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]
)
def vegetation_proxy(path):
    return generic_proxy(VEGETATION_API_URL, f"api/vegetation/{path}")


@app.route(
    "/api/intelligence/<path:path>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
def intelligence_proxy(path):
    return generic_proxy(INTELLIGENCE_API_URL, f"api/intelligence/{path}")


@app.route(
    "/api/agrienergy/<path:path>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
def agrienergy_proxy(path):
    return generic_proxy(AGRIENERGY_API_URL, f"api/agrienergy/{path}")


@app.route(
    "/api/lidar/<path:path>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
def lidar_proxy(path):
    return generic_proxy(LIDAR_API_URL, f"api/lidar/{path}")


@app.route(
    "/api/bioorchestrator/<path:path>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
def bioorchestrator_proxy(path):
    return generic_proxy(BIOORCHESTRATOR_API_URL, path)


@app.route(
    "/api/crop-health/<path:path>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
def crop_health_proxy(path):
    return generic_proxy(CROP_HEALTH_API_URL, f"api/crop-health/{path}")


@app.route(
    "/api/field-operations/<path:path>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
def field_operations_proxy(path):
    return generic_proxy(FIELD_OPERATIONS_API_URL, f"api/field-operations/{path}")


@app.route(
    "/api/carbon/<path:path>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
def carbon_proxy(path):
    return generic_proxy(CARBON_API_URL, f"api/carbon/{path}")


@app.route(
    "/api/robotics/<path:path>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
def robotics_proxy(path):
    return generic_proxy(ROBOTICS_API_URL, f"api/robotics/{path}")


@app.route(
    "/api/risks/<path:path>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
def risk_proxy(path):
    return generic_proxy(RISK_API_URL, f"api/risks/{path}")


@app.route(
    "/api/n8n-nkz/<path:path>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
def n8n_nkz_proxy(path):
    return generic_proxy(N8N_NKZ_API_URL, f"api/n8n-nkz/{path}")


@app.route(
    "/n8n/<tenant_id>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    strict_slashes=False,
)
@app.route(
    "/n8n/<tenant_id>/<path:subpath>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
@app.route(
    "/<tenant_id>/<path:subpath>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
@app.route(
    "/<tenant_id>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    strict_slashes=False,
)
@app.route(
    "/assets/<path:subpath>",
    methods=["GET", "HEAD", "OPTIONS"],
)
@app.route(
    "/static/<path:subpath>",
    methods=["GET", "HEAD", "OPTIONS"],
)
def n8n_tenant_proxy(tenant_id=None, subpath=""):
    """Proxy per-tenant n8n instances via path-based routing.

    n8n.robotika.cloud/<tenant_id>/  ->  http://n8n-<tenant_id>-service:5678/

    n8n uses its own basic auth, so this route is PUBLIC (no JWT required).
    Rewrites /static/base-path.js to inject correct BASE_PATH for the tenant.
    """
    import re

    # Only serve this for n8n.robotika.cloud host
    host = request.headers.get("Host", "").split(":")[0]
    if host != "n8n.robotika.cloud" and not host.endswith(".n8n.robotika.cloud"):
        return jsonify({"error": "Not Found"}), 404

    g.skip_csp = True  # n8n sets its own CSP

    # If this is a root-level /assets/ or /static/ request (from hardcoded JS imports),
    # extract the tenant from the Referer header
    if not tenant_id or tenant_id in ("assets", "static"):
        referer = request.headers.get("Referer", "")
        m = re.search(r"/n8n\.robotika\.cloud/([a-z0-9-]+)/", referer)
        if not m:
            # Fallback: use the only tenant currently provisioned
            tenant_id = "montiko"
        else:
            tenant_id = m.group(1)

    safe_tenant = re.sub(r"[^a-z0-9-]", "-", tenant_id.lower()).strip("-")[:63]
    service = f"n8n-{safe_tenant}-service"
    # Reconstruct the full path that n8n expects
    if request.path.startswith("/assets/"):
        target = f"http://{service}:5678/assets/{subpath}"
    elif request.path.startswith("/static/"):
        target = f"http://{service}:5678/static/{subpath}"
    else:
        target = f"http://{service}:5678/{subpath}"

    headers = {}
    for hdr in ("Content-Type", "Accept", "X-N8N-API-KEY"):
        if request.headers.get(hdr):
            headers[hdr] = request.headers[hdr]

    try:
        resp = requests.request(
            method=request.method,
            url=target,
            headers=headers,
            params=request.args,
            data=request.get_data(),
            cookies=request.cookies,
            allow_redirects=True,
            timeout=60,
        )
        body = resp.content
        resp_headers = dict(resp.headers)
        resp_headers.pop("Content-Encoding", None)
        resp_headers.pop("Transfer-Encoding", None)
        resp_headers.pop("Content-Security-Policy", None)

        # Rewrite base-path.js to inject correct tenant prefix
        if "/static/base-path.js" in target and resp.status_code == 200:
            body = f"window.BASE_PATH = '/{tenant_id}/';\n".encode()
            resp_headers["Content-Type"] = "application/javascript"
            resp_headers["Content-Length"] = str(len(body))
        elif "text/html" in resp_headers.get("Content-Type", ""):
            # Rewrite absolute asset URLs in HTML to include tenant prefix
            html = body.decode("utf-8", errors="replace")
            prefix = f"/{tenant_id}"
            html = html.replace('src="/static/', f'src="{prefix}/static/')
            html = html.replace('src="/assets/', f'src="{prefix}/assets/')
            html = html.replace('href="/static/', f'href="{prefix}/static/')
            html = html.replace('href="/assets/', f'href="{prefix}/assets/')
            html = html.replace('href="/favicon.ico"', f'href="{prefix}/favicon.ico"')
            body = html.encode("utf-8")
            resp_headers["Content-Length"] = str(len(body))
        else:
            resp_headers["Content-Length"] = str(len(body))

        return make_response(body, resp.status_code, resp_headers)
    except Exception as e:
        logger.error(f"n8n tenant proxy error to {target}: {e}")
        return jsonify({"error": "n8n instance unavailable", "details": str(e)}), 502


@app.route("/", methods=["GET"])
def n8n_landing():
    """Simple landing page for n8n.robotika.cloud root."""
    return jsonify(
        {
            "service": "n8n tenant proxy",
            "usage": "Access your n8n instance at /<tenant-id>/",
            "example": "/montiko/",
        }
    )


@app.route(
    "/api/routing/<path:path>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
def routing_proxy(path):
    return generic_proxy(ROUTING_API_URL, f"api/routing/{path}")


@app.route(
    "/api/soil/<path:path>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
def soil_proxy(path):
    """Proxy soil module requests — /api/soil/* → /v1/soil/*"""
    return generic_proxy(SOIL_API_URL, f"v1/soil/{path}")


# =============================================================================
# IoT MQTT Provisioning
# =============================================================================
MQTT_CREDENTIALS_URL = os.getenv(
    "MQTT_CREDENTIALS_URL", "http://mqtt-credentials-manager-service:5000"
)


@app.route("/api/iot/provision-mqtt", methods=["POST"])
def provision_mqtt_credentials():
    """Provision MQTT credentials for a newly created IoT device."""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401
    tenant = extract_tenant_id(payload)
    if not tenant:
        return jsonify({"error": "Tenant not present in token"}), 401

    data = request.get_json(silent=True) or {}
    device_id = data.get("device_id")
    if not device_id:
        return jsonify({"error": "device_id is required"}), 400

    try:
        resp = requests.post(
            f"{MQTT_CREDENTIALS_URL}/api/mqtt/credentials/create",
            json={"tenant_id": tenant, "device_id": device_id},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        return make_response(resp.content, resp.status_code, dict(resp.headers))
    except Exception as e:
        logger.error(f"MQTT provisioning error: {e}")
        return jsonify({"error": "MQTT provisioning failed"}), 502


# =============================================================================
# SDM Integration Proxy (/api/sdm/*)
# =============================================================================


@app.route(
    "/api/sdm/<path:subpath>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
@cross_origin(origins=_cors_origins, supports_credentials=True)
def proxy_sdm_integration(subpath):
    """Proxy SDM integration requests (device profiles, schemas)."""
    if request.method == "OPTIONS":
        return "", 204

    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401

    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    tenant = extract_tenant_id(payload)
    if not tenant:
        return jsonify({"error": "Tenant not present in token"}), 401

    if not rate_limit(tenant):
        return jsonify({"error": "Rate limit exceeded"}), 429

    target_url = f"{SDM_INTEGRATION_URL}/sdm/{subpath}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": request.content_type or "application/json",
        "X-Tenant-ID": tenant,
    }

    try:
        params = dict(request.args)
        json_data = None
        if request.method in ("POST", "PUT", "PATCH") and request.is_json:
            json_data = request.get_json(silent=True)

        resp = requests.request(
            method=request.method,
            url=target_url,
            headers=headers,
            params=params,
            json=json_data,
            timeout=30,
        )

        if resp.status_code >= 400:
            logger.warning(
                f"SDM integration returned {resp.status_code} for /api/sdm/{subpath}: {resp.text[:200]}"
            )

        return make_response(resp.content, resp.status_code, dict(resp.headers))

    except requests.exceptions.RequestException as e:
        logger.error(f"Error forwarding SDM integration request: {e}")
        return jsonify({"error": "Internal server error"}), 500


# =============================================================================
# Zulip Communications Proxy
# =============================================================================

# --- Zulip API key cache (Redis) ---
_redis_url_for_zulip = os.getenv("REDIS_URL", "redis://redis-service:6379/4")


def _ensure_zulip_user(user_email: str, full_name: str = ""):
    """Ensure user exists in Zulip. Auto-creates if missing.

    Returns True if user exists (or was created), False on failure.
    """
    if not ZULIP_BOT_EMAIL or not ZULIP_BOT_API_KEY:
        logger.error("ZULIP_BOT_EMAIL/ZULIP_BOT_API_KEY not configured")
        return False

    try:
        zulip_headers = {"Host": ZULIP_HOST}
        # Use /api/v1/users (list all) instead of /api/v1/users/{email}
        # because Zulip's URL routing returns 400 for emails with dots in domain
        resp = requests.get(
            f"{ZULIP_SERVICE_URL}/api/v1/users",
            auth=(ZULIP_BOT_EMAIL, ZULIP_BOT_API_KEY),
            headers=zulip_headers,
            timeout=10,
        )
        resp.raise_for_status()
        members = resp.json().get("members", [])
        existing_user = next((m for m in members if m["email"] == user_email), None)

        if existing_user is None:
            logger.info("Auto-creating Zulip user: %s", user_email)
            import secrets

            create_resp = requests.post(
                f"{ZULIP_SERVICE_URL}/api/v1/users",
                auth=(ZULIP_BOT_EMAIL, ZULIP_BOT_API_KEY),
                headers=zulip_headers,
                data={
                    "email": user_email,
                    "password": secrets.token_urlsafe(32),
                    "full_name": full_name or user_email.split("@")[0],
                },
                timeout=15,
            )
            if create_resp.status_code != 200:
                logger.error(
                    "Failed to auto-create Zulip user %s: %s",
                    user_email,
                    create_resp.text,
                )
                return False
        return True
    except Exception as e:
        logger.error("Zulip user check/create failed for %s: %s", user_email, e)
        return False


def _get_zulip_api_key(user_email: str, full_name: str = ""):
    """Ensure user exists and return bot credentials for proxying.

    In Zulip 9.x, per-user API keys cannot be fetched via admin API.
    We use the bot credentials for all proxy operations instead.
    """
    import redis as redis_lib

    # Check Redis cache to avoid repeated user-list calls
    cache_key = f"zulip:user_exists:{user_email}"
    r = None
    try:
        r = redis_lib.from_url(_redis_url_for_zulip, decode_responses=True)
        cached = r.get(cache_key)
        if cached:
            return ZULIP_BOT_API_KEY
    except Exception:
        logger.warning("Redis unavailable for Zulip cache")
        r = None

    if not _ensure_zulip_user(user_email, full_name):
        return None

    if r:
        try:
            r.setex(cache_key, 86400, "1")
        except Exception:
            pass

    return ZULIP_BOT_API_KEY


def _zulip_proxy_request(user_email, api_key, zulip_path, tenant_id):
    """Proxy a request to Zulip API using bot credentials on behalf of user."""
    url = f"{ZULIP_SERVICE_URL}/api/v1/{zulip_path}"
    try:
        resp = requests.request(
            method=request.method,
            url=url,
            auth=(ZULIP_BOT_EMAIL, ZULIP_BOT_API_KEY),
            params=request.args,
            data=request.get_data(),
            headers={
                "Content-Type": request.headers.get("Content-Type", "application/json"),
                "Host": ZULIP_HOST,
            },
            allow_redirects=False,
            timeout=120,
        )
        return make_response(
            resp.content,
            resp.status_code,
            {
                "Content-Type": resp.headers.get("Content-Type", "application/json"),
            },
        )
    except Exception as e:
        logger.error("Zulip proxy error to %s: %s", url, e)
        return jsonify({"error": "Zulip proxy error"}), 502


def _zulip_auth_and_tenant():
    """Authenticate user and extract tenant for Zulip routes."""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401
    tenant = extract_tenant_id(payload)
    if not tenant:
        return jsonify({"error": "Tenant not present in token"}), 401
    if not rate_limit(tenant):
        return jsonify({"error": "Rate limit exceeded"}), 429
    email = payload.get("email")
    if not email:
        return jsonify({"error": "Email not present in token"}), 401
    full_name = payload.get("name", payload.get("preferred_username", ""))
    api_key = _get_zulip_api_key(email, full_name)
    if not api_key:
        return jsonify({"error": "Failed to provision Zulip account"}), 502
    return email, api_key, tenant, payload


@app.route("/api/zulip/streams", methods=["GET"])
def zulip_streams():
    result = _zulip_auth_and_tenant()
    if isinstance(result, tuple) and len(result) == 2:
        return result
    email, api_key, tenant, payload = result
    try:
        resp = requests.get(
            f"{ZULIP_SERVICE_URL}/api/v1/streams",
            auth=(ZULIP_BOT_EMAIL, ZULIP_BOT_API_KEY),
            headers={"Host": ZULIP_HOST},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        tenant_prefix = _tenant_zulip_stream_prefix(tenant)
        filtered = [
            s
            for s in data.get("streams", [])
            if s["name"].startswith(tenant_prefix)
            or s["name"] == "platform-announcements"
        ]
        data["streams"] = filtered
        return jsonify(data), 200
    except Exception as e:
        logger.error("Zulip streams error: %s", e)
        return jsonify({"error": "Failed to fetch streams"}), 502


@app.route("/api/zulip/streams/<int:stream_id>/topics", methods=["GET"])
def zulip_stream_topics(stream_id):
    result = _zulip_auth_and_tenant()
    if isinstance(result, tuple) and len(result) == 2:
        return result
    email, api_key, tenant, payload = result
    return _zulip_proxy_request(email, api_key, f"users/me/{stream_id}/topics", tenant)


@app.route("/api/zulip/messages", methods=["GET"])
def zulip_get_messages():
    result = _zulip_auth_and_tenant()
    if isinstance(result, tuple) and len(result) == 2:
        return result
    email, api_key, tenant, payload = result
    import json as json_mod

    narrow = request.args.get("narrow")
    if narrow:
        try:
            narrow_list = json_mod.loads(narrow)
            for clause in narrow_list:
                if clause.get("operator") == "stream":
                    stream_name = clause.get("operand", "")
                    tenant_prefix = _tenant_zulip_stream_prefix(tenant)
                    if (
                        not stream_name.startswith(tenant_prefix)
                        and stream_name != "platform-announcements"
                    ):
                        return jsonify({"error": "Access denied to stream"}), 403
        except (json_mod.JSONDecodeError, TypeError):
            pass
    return _zulip_proxy_request(email, api_key, "messages", tenant)


@app.route("/api/zulip/messages", methods=["POST"])
def zulip_send_message():
    result = _zulip_auth_and_tenant()
    if isinstance(result, tuple) and len(result) == 2:
        return result
    email, api_key, tenant, payload = result
    data = request.get_json(silent=True) or {}
    msg_type = data.get("type", "")
    if msg_type == "stream":
        stream_name = data.get("to", "")
        tenant_prefix = _tenant_zulip_stream_prefix(tenant)
        if not stream_name.startswith(tenant_prefix):
            return jsonify({"error": "Cannot send to streams outside your tenant"}), 403
    return _zulip_proxy_request(email, api_key, "messages", tenant)


@app.route("/api/zulip/messages/<int:message_id>/reactions", methods=["POST", "DELETE"])
def zulip_reactions(message_id):
    result = _zulip_auth_and_tenant()
    if isinstance(result, tuple) and len(result) == 2:
        return result
    email, api_key, tenant, payload = result
    return _zulip_proxy_request(
        email, api_key, f"messages/{message_id}/reactions", tenant
    )


@app.route("/api/zulip/users/me", methods=["GET"])
def zulip_user_me():
    result = _zulip_auth_and_tenant()
    if isinstance(result, tuple) and len(result) == 2:
        return result
    email, api_key, tenant, payload = result
    return _zulip_proxy_request(email, api_key, "users/me", tenant)


@app.route("/api/zulip/events/register", methods=["POST"])
def zulip_register_events():
    result = _zulip_auth_and_tenant()
    if isinstance(result, tuple) and len(result) == 2:
        return result
    email, api_key, tenant, payload = result
    return _zulip_proxy_request(email, api_key, "register", tenant)


@app.route("/api/zulip/events", methods=["GET", "DELETE"])
def zulip_events():
    result = _zulip_auth_and_tenant()
    if isinstance(result, tuple) and len(result) == 2:
        return result
    email, api_key, tenant, payload = result
    return _zulip_proxy_request(email, api_key, "events", tenant)


@app.route("/api/zulip/provisioning/<path:subpath>", methods=["POST", "DELETE"])
def zulip_provisioning(subpath):
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid token"}), 401
    if not has_role("platform_admin", payload):
        return jsonify({"error": "Platform admin role required"}), 403
    provisioner_url = os.getenv(
        "ZULIP_PROVISIONER_URL", "http://zulip-provisioner-service:5000"
    )
    url = f"{provisioner_url}/api/provisioning/{subpath}"
    headers = {
        "Content-Type": request.headers.get("Content-Type", "application/json"),
        "X-Tenant-ID": extract_tenant_id(payload) or "",
    }
    try:
        resp = requests.request(
            method=request.method,
            url=url,
            headers=headers,
            data=request.get_data(),
            timeout=30,
        )
        return make_response(
            resp.content,
            resp.status_code,
            {"Content-Type": resp.headers.get("Content-Type", "application/json")},
        )
    except Exception as e:
        logger.error("Zulip provisioner proxy error: %s", e)
        return jsonify({"error": "Provisioner unavailable"}), 502


@app.route("/api/datahub/export", methods=["POST"])
def proxy_datahub_export():
    """Proxy export requests to DataHub BFF, enforcing PAT scopes upstream."""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401

    if is_pat_token(token):
        tenant = getattr(g, "pat_tenant_id", None)
        if not tenant:
            return jsonify({"error": "PAT tenant not resolved"}), 401
        gw_jwt = obtain_gateway_service_jwt()
        if not gw_jwt:
            return jsonify({"error": "Service authentication not configured"}), 503
        headers = {
            "Authorization": f"Bearer {gw_jwt}",
            "X-Delegated-Tenant-ID": tenant,
            "X-Tenant-ID": tenant,
            "Content-Type": "application/json",
        }
    else:
        payload = validate_jwt_token(token)
        if not payload:
            return jsonify({"error": "Invalid or expired token"}), 401
        tenant = extract_tenant_id(payload)
        if not tenant:
            return jsonify({"error": "Tenant not present in token"}), 401
        if not rate_limit(tenant):
            return jsonify({"error": "Rate limit exceeded"}), 429
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": tenant,
            "Content-Type": "application/json",
        }

    try:
        url = f"{DATAHUB_BFF_URL}/api/datahub/export"
        body = (
            getattr(g, "pat_modified_body", None) or request.get_json(silent=True) or {}
        )
        resp = requests.post(url, headers=headers, json=body, timeout=120)
        return make_response(resp.content, resp.status_code, dict(resp.headers))
    except requests.exceptions.RequestException as e:
        logger.error(f"DataHub export proxy error: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/datahub/timeseries/align", methods=["POST"])
def proxy_datahub_align():
    """Proxy timeseries align requests to DataHub BFF."""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401

    if is_pat_token(token):
        tenant = getattr(g, "pat_tenant_id", None)
        if not tenant:
            return jsonify({"error": "PAT tenant not resolved"}), 401
        gw_jwt = obtain_gateway_service_jwt()
        if not gw_jwt:
            return jsonify({"error": "Service authentication not configured"}), 503
        headers = {
            "Authorization": f"Bearer {gw_jwt}",
            "X-Delegated-Tenant-ID": tenant,
            "X-Tenant-ID": tenant,
            "Content-Type": "application/json",
        }
    else:
        payload = validate_jwt_token(token)
        if not payload:
            return jsonify({"error": "Invalid or expired token"}), 401
        tenant = extract_tenant_id(payload)
        if not tenant:
            return jsonify({"error": "Tenant not present in token"}), 401
        if not rate_limit(tenant):
            return jsonify({"error": "Rate limit exceeded"}), 429
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": tenant,
            "Content-Type": "application/json",
        }

    try:
        url = f"{DATAHUB_BFF_URL}/api/datahub/timeseries/align"
        resp = requests.post(
            url, headers=headers, json=request.get_json(silent=True) or {}, timeout=60
        )
        return make_response(resp.content, resp.status_code, dict(resp.headers))
    except requests.exceptions.RequestException as e:
        logger.error(f"DataHub align proxy error: {e}")
        return jsonify({"error": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# Internal CI-only endpoints — OIDC-gated, rate-limited
# ---------------------------------------------------------------------------

_INTERNAL_CI_ACTIONS = {"publish", "resolve-url"}
_PUBLISH_RATE_LIMIT = 10  # max publish requests per minute (global, not per-IP)
_publish_requests = deque()


def _check_publish_rate_limit():
    """Simple sliding-window rate limiter for the publish endpoint."""
    now = time.time()
    window = now - 60
    while _publish_requests and _publish_requests[0] < window:
        _publish_requests.popleft()
    if len(_publish_requests) >= _PUBLISH_RATE_LIMIT:
        return False
    _publish_requests.append(now)
    return True


@app.route("/api/internal/modules/<module_id>/<action>", methods=["GET", "POST"])
def internal_module_ci(module_id, action):
    """
    Proxy internal module CI endpoints (publish, resolve-url) to entity-manager.
    POST (publish) requires a valid GitHub Actions OIDC JWT.
    GET (resolve-url) is public (returns non-sensitive manifest URL).
    entity-manager validates X-Internal-Service-Secret as defense-in-depth.
    """
    if action not in _INTERNAL_CI_ACTIONS:
        return jsonify({"error": "Not found"}), 404

    # POST publish requires GitHub OIDC JWT + rate limiting
    if request.method == "POST" and action == "publish":
        oidc_token = request.headers.get("X-OIDC-Token", "")
        if not _validate_oidc_token(oidc_token, module_id):
            return jsonify({"error": "Forbidden"}), 403
        if not _check_publish_rate_limit():
            logger.warning(f"Rate limit exceeded for publish/{module_id}")
            return jsonify({"error": "Too many requests"}), 429

    # Forward to entity-manager — pass through the internal secret header
    target = f"{ENTITY_MANAGER_URL}/api/internal/modules/{module_id}/{action}"
    fwd_headers = {
        "Content-Type": request.headers.get("Content-Type", ""),
    }
    secret = request.headers.get("X-Internal-Service-Secret", "")
    if secret:
        fwd_headers["X-Internal-Service-Secret"] = secret
    # Strip empty values
    fwd_headers = {k: v for k, v in fwd_headers.items() if v}

    try:
        if request.method == "GET":
            resp = requests.get(
                target, headers=fwd_headers, params=request.args, timeout=30
            )
        else:
            resp = requests.post(
                target,
                headers=fwd_headers,
                data=request.get_data(),
                timeout=60,
            )
        return make_response(resp.content, resp.status_code, dict(resp.headers))
    except requests.exceptions.RequestException as e:
        logger.error(f"internal CI proxy error for {module_id}/{action}: {e}")
        return jsonify({"error": "Internal server error"}), 500


# Register dynamic module routing blueprint
app.register_blueprint(module_bp)
app.register_blueprint(storage_bp)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    debug = LOG_LEVEL == "DEBUG"

    logger.info(f"Starting FIWARE API Gateway on port {port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
