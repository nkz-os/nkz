"""Derived notification tokens — per-tenant, per-receiver credentials for Orion-LD deliveries.

Orion-LD stores ``notification.endpoint.receiverInfo`` verbatim, so whatever header a
subscription carries is readable by anyone who can read the subscription (a tenant
listing its own, a broker backup, a log line). Carrying the platform master secret there
turns every such read into a leak of the key that authenticates every ``/internal/*``
route. A derived token is worth exactly one receiver of one tenant::

    token = HMAC-SHA256(INTERNAL_SERVICE_SECRET,
                        "nkz-notify-v1|{tenant}|{receiver_host}").hexdigest()

The creator derives ``receiver_host`` from the notification URI it registers; the
receiver takes it from the ``Host`` header of the delivery. Neither side stores the
token: the receiver recomputes it from the master secret it already holds.

This is deliberately NOT the platform request signature (``{hexdigest}:{timestamp}``,
five-minute window): ``receiverInfo`` is static, so the token is a derived credential,
not a signed request. It is the documented exception to the single-format rule.

Rollout flags (read through the helpers below so every service spells them the same):

- ``NKZ_NOTIFY_DERIVED_TOKENS`` (creators, default off): emit the derived token instead
  of the master secret.
- ``NKZ_NOTIFY_ACCEPT_MASTER`` (receivers, default on): keep accepting the master secret
  while creators are still switching over.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Mapping
from urllib.parse import urlsplit

TOKEN_HEADER = "X-Notification-Token"
MASTER_HEADER = "X-Internal-Service-Secret"
TENANT_HEADERS = ("NGSILD-Tenant", "Fiware-Service")

DERIVED_TOKENS_ENV = "NKZ_NOTIFY_DERIVED_TOKENS"
ACCEPT_MASTER_ENV = "NKZ_NOTIFY_ACCEPT_MASTER"

_DOMAIN = "nkz-notify-v1"
_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


def normalize_tenant(tenant: str) -> str:
    """The tenant as bound into the token: stripped and lowercased.

    Orion-LD treats the tenant case-insensitively, so the value it echoes back in
    ``NGSILD-Tenant`` may not match the casing the creator used; normalising both
    sides keeps the token stable.
    """
    return (tenant or "").strip().lower()


def receiver_host_from_url(uri: str) -> str:
    """Hostname of a notification URI, lowercase, no port. Raises ``ValueError`` without one."""
    try:
        host = urlsplit(uri or "").hostname
    except ValueError as e:  # malformed IPv6 literal and friends
        raise ValueError(f"notification URI has no usable host: {uri!r}") from e
    if not host:
        raise ValueError(f"notification URI has no usable host: {uri!r}")
    return host.lower()


def receiver_host_from_headers(headers: Mapping[str, str]) -> str | None:
    """Hostname of the ``Host`` header of a delivery, lowercase, no port; None if absent."""
    raw = _header(headers, "Host")
    if not raw:
        return None
    try:
        host = urlsplit(f"//{raw.strip()}").hostname
    except ValueError:
        return None
    return host.lower() if host else None


def notification_token(secret: str, tenant: str, receiver_host: str) -> str:
    """The derived token for ``tenant`` delivering to ``receiver_host``. 64 hex chars."""
    tenant = normalize_tenant(tenant)
    receiver_host = (receiver_host or "").strip().lower()
    if not secret:
        raise ValueError("notification_token: secret is empty")
    if not tenant:
        raise ValueError("notification_token: tenant is empty")
    if not receiver_host:
        raise ValueError("notification_token: receiver_host is empty")
    msg = f"{_DOMAIN}|{tenant}|{receiver_host}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_notification(
    headers: Mapping[str, str], *, secret: str, accept_master: bool = True
) -> bool:
    """True when a delivery carries a valid derived token (or, while accepted, the master).

    ``headers`` is any mapping of the request headers (Flask's and Starlette's are
    fine, so is a plain dict); lookups are case-insensitive. The tenant comes from
    ``NGSILD-Tenant`` (NGSI-LD) or ``Fiware-Service`` (NGSIv2) and the receiver host
    from ``Host`` — a token that cannot be bound to both is refused. Fails closed when
    ``secret`` is empty. Never raises on odd header values.
    """
    if not secret:
        return False

    token = _header(headers, TOKEN_HEADER)
    if token:
        tenant = normalize_tenant(_tenant(headers))
        host = receiver_host_from_headers(headers)
        if tenant and host:
            expected = notification_token(secret, tenant, host)
            if _equal(token, expected):
                return True

    if accept_master:
        master = _header(headers, MASTER_HEADER)
        if master and _equal(master, secret):
            return True

    return False


def derived_tokens_from_env() -> bool:
    """Creators: emit derived tokens? Default False until the receivers are deployed."""
    return os.getenv(DERIVED_TOKENS_ENV, "").strip().lower() in _TRUE


def accept_master_from_env() -> bool:
    """Receivers: still accept the master secret? Default True until creators switch."""
    return os.getenv(ACCEPT_MASTER_ENV, "").strip().lower() not in _FALSE


def _tenant(headers: Mapping[str, str]) -> str:
    for name in TENANT_HEADERS:
        value = _header(headers, name)
        if value:
            return value
    return ""


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive lookup that also works on a plain dict."""
    value = headers.get(name)
    if value is not None:
        return value
    lname = name.lower()
    for key, val in headers.items():
        if key.lower() == lname:
            return val
    return None


def _equal(provided: str, expected: str) -> bool:
    """Constant-time comparison that refuses, rather than raises on, non-ASCII input."""
    try:
        return hmac.compare_digest(provided.encode("ascii"), expected.encode("ascii"))
    except (UnicodeEncodeError, AttributeError):
        return False
