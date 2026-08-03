"""
Authentication dependency for module backends.

The api-gateway validates the JWT and injects headers:
  X-Tenant-ID, X-User-ID, X-User-Roles, X-Request-ID, X-Auth-Signature

This module provides require_auth() which reads those headers.
Module backends do NOT validate JWT signatures — the gateway did that.
Defense in depth: we validate header presence, format, and optionally
HMAC signature (X-Auth-Signature) to prevent tenant spoofing bypassing
the gateway.

Set REQUIRE_HMAC_SIGNATURE=true + HMAC_SECRET to enable HMAC verification.
Without it, any pod in the namespace can spoof X-Tenant-ID.
"""

import hashlib
import hmac
import os
import re
import time
from dataclasses import dataclass
from typing import Sequence
from fastapi import Request, HTTPException, Depends

# ---------------------------------------------------------------------------
# HMAC configuration (defense-in-depth against tenant spoofing)
# ---------------------------------------------------------------------------
HMAC_SECRET = os.getenv("HMAC_SECRET", "")
REQUIRE_HMAC = os.getenv("REQUIRE_HMAC_SIGNATURE", "").lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# Canonical tenant_id format (mirrors services/common/tenant_utils.py):
# hyphen-separated lowercase alphanumeric segments, no underscores, no
# leading/trailing/double hyphens, length 3-63.
# ---------------------------------------------------------------------------
_TENANT_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _validate_tenant_format(tenant_id: str) -> None:
    if not (3 <= len(tenant_id) <= 63) or not _TENANT_RE.match(tenant_id):
        raise HTTPException(
            status_code=401,
            detail=f"Invalid X-Tenant-ID format: {tenant_id}",
        )


@dataclass(frozen=True)
class AuthContext:
    """Authenticated request context injected by the gateway."""

    tenant_id: str
    user_id: str
    roles: tuple[str, ...]
    request_id: str | None = None

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_any_role(self, roles: Sequence[str]) -> bool:
        return any(r in self.roles for r in roles)


def require_auth(roles: Sequence[str] | None = None):
    """
    FastAPI dependency that validates gateway-injected auth headers.

    Args:
        roles: If provided, at least one role must match.

    Returns:
        FastAPI dependency yielding AuthContext.

    Raises:
        HTTPException 401 if headers are missing or invalid.
        HTTPException 403 if roles are provided and none match.
    """

    async def _require_auth(request: Request) -> AuthContext:
        tenant_id = request.headers.get("X-Tenant-ID", "").strip()
        user_id = request.headers.get("X-User-ID", "").strip()
        roles_header = request.headers.get("X-User-Roles", "").strip()
        request_id = request.headers.get("X-Request-ID", "").strip() or None

        # Validate presence
        if not tenant_id:
            raise HTTPException(
                status_code=401,
                detail="Missing X-Tenant-ID header — gateway misconfiguration?",
            )
        if not user_id:
            raise HTTPException(
                status_code=401,
                detail="Missing X-User-ID header — gateway misconfiguration?",
            )

        # Validate tenant_id format (canonical hyphen-separated pattern)
        _validate_tenant_format(tenant_id)

        # HMAC signature verification (defense-in-depth against tenant spoofing)
        # Enabled when REQUIRE_HMAC_SIGNATURE=true + HMAC_SECRET is configured.
        # Canonical format (aligned with services/common/keycloak_auth.py):
        #   payload  = {token}|{tenant_id}|{timestamp}
        #   output   = {HMAC-SHA256 hexdigest}:{timestamp}
        if REQUIRE_HMAC and HMAC_SECRET:
            hmac_header = request.headers.get("X-Auth-Signature", "")
            if not hmac_header:
                raise HTTPException(
                    status_code=401,
                    detail="Missing X-Auth-Signature header",
                )
            try:
                parts = hmac_header.split(":")
                if len(parts) != 2:
                    raise HTTPException(
                        status_code=401,
                        detail="Invalid HMAC signature format (expected sig:ts)",
                    )
                provided_sig, timestamp_str = parts
                timestamp = int(timestamp_str)

                # 5-minute window
                if abs(int(time.time()) - timestamp) > 300:
                    raise HTTPException(
                        status_code=401,
                        detail="HMAC signature timestamp outside 5-minute window",
                    )

                # Recompute expected signature
                # token is '' for internal service-to-service calls
                token = request.headers.get("Authorization", "").removeprefix("Bearer ")
                payload = f"{token}|{tenant_id}|{timestamp}"
                expected = hmac.new(
                    HMAC_SECRET.encode("utf-8"),
                    payload.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()

                if not hmac.compare_digest(provided_sig, expected):
                    raise HTTPException(
                        status_code=401,
                        detail="Invalid HMAC signature",
                    )
            except (ValueError, IndexError) as e:
                raise HTTPException(
                    status_code=401,
                    detail=f"HMAC validation error: {e}",
                )

        user_roles = tuple(r.strip() for r in roles_header.split(",") if r.strip())

        # Role check (defense in depth — gateway also checks)
        if roles is not None:
            allowed = set(roles)
            if not any(r in allowed for r in user_roles):
                raise HTTPException(
                    status_code=403,
                    detail=f"Access denied. Required one of: {roles}",
                )

        return AuthContext(
            tenant_id=tenant_id,
            user_id=user_id,
            roles=user_roles,
            request_id=request_id,
        )

    return Depends(_require_auth)
