"""
crypto.py — Canonical HMAC signature primitive for the Nekazari platform.

This is the single source of truth for the platform's HMAC signature format.
It was extracted from nkz_platform_sdk.auth (inline HMAC block) so every
service/module on the platform can share one implementation instead of
re-deriving the format ad hoc.

Signature format is FROZEN — do not change it:
    payload  = "{data}|{tenant_id}|{timestamp}"       (pipe-separated)
    output   = "{HMAC-SHA256 hexdigest}:{timestamp}"  (colon-separated,
               sig FIRST, full 64-char hex, NO truncation)
    verification: hmac.compare_digest (constant-time)
    default timestamp window: 300 seconds (5 minutes)

Reference: services/common/keycloak_auth.py:generate_hmac_signature
"""

import hashlib
import hmac
import time

DEFAULT_WINDOW_SECONDS = 300


def generate_hmac_signature(
    secret: str,
    data: str,
    tenant_id: str,
    timestamp: int | None = None,
) -> str:
    """Generate the canonical platform HMAC signature.

    Args:
        secret: shared HMAC secret.
        data: the payload's "data" segment (e.g. bearer token; '' for
            internal service-to-service calls with no user token).
        tenant_id: tenant_id the signature is bound to.
        timestamp: unix seconds to embed. Defaults to now.

    Returns:
        "{hexdigest}:{timestamp}" — sig first, full 64-char hex, no truncation.
    """
    ts = timestamp if timestamp is not None else int(time.time())
    payload = f"{data}|{tenant_id}|{ts}"
    sig = hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{sig}:{ts}"


def verify_hmac_signature(
    secret: str,
    signature_header: str,
    data: str,
    tenant_id: str,
    *,
    fail_open: bool = False,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
) -> bool:
    """Verify a canonical HMAC signature header.

    Returns False on: malformed header (not exactly one ":"-separated
    sig/timestamp pair, or a non-integer timestamp), an expired timestamp
    (outside `window_seconds`), or a signature mismatch.

    When `secret` is falsy OR `signature_header` is empty, returns
    `fail_open` verbatim — callers choose fail-open vs fail-closed
    explicitly rather than the library silently picking one.
    """
    if not secret or not signature_header:
        return fail_open

    parts = signature_header.split(":")
    if len(parts) != 2:
        return False

    provided_sig, timestamp_str = parts
    try:
        timestamp = int(timestamp_str)
    except ValueError:
        return False

    if abs(int(time.time()) - timestamp) > window_seconds:
        return False

    expected = generate_hmac_signature(secret, data, tenant_id, timestamp)
    expected_sig = expected.split(":", 1)[0]

    return hmac.compare_digest(provided_sig, expected_sig)
