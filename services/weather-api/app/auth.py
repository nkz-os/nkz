"""
FastAPI authentication dependencies.
Adapts the Flask @require_auth pattern to FastAPI's dependency injection.

The api-gateway already validates JWT/cookies. This service reads the injected
headers (Authorization, X-Tenant-ID) set by the gateway.
"""

import os
from typing import Optional

from fastapi import Header, HTTPException, Request, status

JWT_SECRET = os.getenv("JWT_SECRET", "")


def _decode_jwt_tenant(token: str) -> Optional[str]:
    """Extract tenant_id from JWT payload without verifying signature."""
    try:
        import base64
        import json

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


def require_auth(
    authorization: Optional[str] = Header(None),
    x_tenant_id: Optional[str] = Header(None),
):
    """
    FastAPI dependency: ensure request has valid tenant context.

    The api-gateway injects X-Tenant-ID. This dependency reads it and makes
    it available. Falls back to extracting tenant from JWT if X-Tenant-ID is
    absent.
    """
    if x_tenant_id and x_tenant_id.strip():
        return x_tenant_id.strip()

    # Fallback: extract tenant from JWT
    token = authorization.removeprefix("Bearer ") if authorization else None
    if token:
        tenant = _decode_jwt_tenant(token)
        if tenant:
            return tenant

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
    )


def require_auth_optional(
    authorization: Optional[str] = Header(None),
    x_tenant_id: Optional[str] = Header(None),
) -> Optional[str]:
    """Like require_auth but returns None instead of raising 401."""
    if x_tenant_id and x_tenant_id.strip():
        return x_tenant_id.strip()
    token = authorization.removeprefix("Bearer ") if authorization else None
    if token:
        return _decode_jwt_tenant(token)
    return None
