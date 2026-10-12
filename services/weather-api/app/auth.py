"""
FastAPI authentication dependencies.
Adapts the Flask @require_auth pattern to FastAPI's dependency injection.

The api-gateway validates the user's JWT and forwards it with X-Tenant-ID and
X-Auth-Signature, an HMAC over the token and the tenant. X-Tenant-ID is only
trustworthy with a valid signature: any pod in the namespace can set it.

REQUIRE_HMAC_SIGNATURE=true rejects unsigned requests. While in-cluster
callers are migrated to sign, it stays false: unsigned requests keep the old
behaviour and each caller is logged once, so the remaining ones can be found
before enforcing. A signature that is present but invalid is always rejected.
"""

import base64
import json
import logging
import os
from typing import Optional

from fastapi import Header, HTTPException, Request, status
from nkz_platform_sdk.crypto import verify_hmac_signature

logger = logging.getLogger(__name__)

JWT_SECRET = os.getenv("JWT_SECRET", "")

_UNSIGNED_SEEN: set = set()
_UNSIGNED_SEEN_MAX = 1000


def _require_signature() -> bool:
    return os.getenv("REQUIRE_HMAC_SIGNATURE", "false").strip().lower() == "true"


def _hmac_secret() -> str:
    return os.getenv("HMAC_SECRET", "")


def check_auth_config() -> None:
    """Log at startup when the configuration cannot verify signatures."""
    if _hmac_secret():
        return
    if _require_signature():
        logger.critical(
            "HMAC_SECRET is not set and REQUIRE_HMAC_SIGNATURE=true: every "
            "authenticated request will be rejected with 503."
        )
    else:
        logger.critical(
            "HMAC_SECRET is not set: X-Auth-Signature is not verified and "
            "X-Tenant-ID is trusted as sent."
        )


def _decode_jwt_tenant(token: str) -> Optional[str]:
    """Extract tenant_id from JWT payload without verifying signature."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1]
        missing = (-len(payload_b64)) % 4
        if missing:
            payload_b64 += "=" * missing
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("tenant_id") or payload.get("tenant") or None
    except Exception:
        return None


def get_request_token(request: Request) -> Optional[str]:
    """Extract Bearer token from Authorization header or nkz_token cookie."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.cookies.get("nkz_token")


def _log_unsigned(request: Request) -> None:
    """Log each unsigned caller once per route, to find who still has to sign."""
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)
    caller = request.headers.get("X-User-ID") or request.headers.get("User-Agent", "")
    client = request.client.host if request.client else ""
    key = (caller, client, path)
    if key in _UNSIGNED_SEEN or len(_UNSIGNED_SEEN) >= _UNSIGNED_SEEN_MAX:
        return
    _UNSIGNED_SEEN.add(key)
    logger.warning(
        "Unsigned request: path=%s caller=%s client=%s has_tenant=%s",
        path,
        caller,
        client,
        bool(request.headers.get("X-Tenant-ID")),
    )


def _signed_tenant(token: str, tenant: str, signature: str) -> str:
    """Return the tenant of a signed request, or raise when it does not verify."""
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Signed request without X-Tenant-ID",
        )
    if not verify_hmac_signature(_hmac_secret(), signature, token, tenant):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired signature",
        )
    return tenant


def _resolve_tenant(
    request: Request,
    authorization: Optional[str],
    x_tenant_id: Optional[str],
    x_auth_signature: Optional[str],
    *,
    required: bool,
) -> Optional[str]:
    tenant = (x_tenant_id or "").strip()
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    signature = (x_auth_signature or "").strip()
    enforce = _require_signature()

    if not _hmac_secret():
        if enforce and (tenant or token or signature):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Signature verification unavailable",
            )
        signature = ""  # nothing to verify against: legacy behaviour below

    if signature:
        return _signed_tenant(token, tenant, signature)

    if enforce:
        if required:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing X-Auth-Signature",
            )
        # Optional endpoints serve shared data to anonymous callers; an
        # unsigned tenant claim is ignored rather than trusted.
        return None

    if tenant or token:
        _log_unsigned(request)
    if tenant:
        return tenant
    if token:
        return _decode_jwt_tenant(token)
    return None


def require_auth(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_tenant_id: Optional[str] = Header(None),
    x_auth_signature: Optional[str] = Header(None),
):
    """
    FastAPI dependency: ensure request has valid tenant context.

    The tenant comes from X-Tenant-ID verified against X-Auth-Signature (see
    the module docstring for unsigned requests).
    """
    tenant = _resolve_tenant(
        request, authorization, x_tenant_id, x_auth_signature, required=True
    )
    if tenant:
        return tenant
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
    )


def require_auth_optional(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_tenant_id: Optional[str] = Header(None),
    x_auth_signature: Optional[str] = Header(None),
) -> Optional[str]:
    """Like require_auth but returns None instead of raising 401."""
    return _resolve_tenant(
        request, authorization, x_tenant_id, x_auth_signature, required=False
    )
