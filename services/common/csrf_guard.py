"""Same-site CSRF guard for services exposed without the API gateway in front.

``SameSite=Strict`` keeps the session cookie off cross-site requests, but sibling
subdomains of the same registrable domain are same-site, and some of them run
tenant-controlled code. Browsers stamp every mutating request with its ``Origin``
and scripts cannot forge it, so a mutation that relies on the cookie must come
from an origin this installation serves its UI from.

The gateway has its own copy of this check. A service that reads the cookie and is
reachable through a direct ingress route must install this one, because those
requests never pass through the gateway.

Environment:
    CORS_ORIGINS: comma-separated allowlist. ``*`` never authorizes: unset, empty
        or wildcard-only means an empty list, so every cookie-only browser
        mutation is rejected.
    CSRF_GUARD_ENFORCE: ``false`` logs would-be rejections and lets them through,
        so a rollout can prove the allowlist before enforcing it. Default: enforce.
"""

from __future__ import annotations

import json
import logging
import os

from flask import Flask, jsonify, request

logger = logging.getLogger(__name__)

SESSION_COOKIE = "nkz_token"
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def parse_allowed_origins(raw: str | None) -> frozenset[str]:
    """Parse the CSV allowlist, dropping blanks and the ``*`` wildcard."""
    origins = (origin.strip() for origin in (raw or "").split(","))
    return frozenset(origin for origin in origins if origin and origin != "*")


def enforce_enabled(raw: str | None) -> bool:
    """Enforce unless explicitly set to ``false``."""
    return (raw or "").strip().lower() != "false"


def is_cross_site_cookie_mutation(
    method: str,
    authorization: str | None,
    has_session_cookie: bool,
    origin: str | None,
    allowed: frozenset[str],
) -> bool:
    """Decide whether a request is a mutation riding the cookie from a foreign origin.

    Only a Bearer token exempts a request: with any other scheme, token extraction
    falls back to the cookie. A request without ``Origin`` is not browser-initiated.
    """
    if method not in MUTATING_METHODS:
        return False
    if authorization and authorization.startswith("Bearer "):
        return False
    if not has_session_cookie or origin is None:
        return False
    return origin not in allowed


def install_csrf_guard(app: Flask, service: str) -> None:
    """Register the guard. Call right after creating the app, so that it runs first."""
    allowed = parse_allowed_origins(os.getenv("CORS_ORIGINS"))
    enforce = enforce_enabled(os.getenv("CSRF_GUARD_ENFORCE"))
    if not allowed:
        logger.critical(
            "CORS_ORIGINS has no usable origin: every cookie-authenticated browser "
            "mutation in %s is treated as cross-site",
            service,
        )

    @app.before_request
    def reject_cross_site_cookie_auth():
        origin = request.headers.get("Origin")
        if not is_cross_site_cookie_mutation(
            request.method,
            request.headers.get("Authorization"),
            SESSION_COOKIE in request.cookies,
            origin,
            allowed,
        ):
            return None
        logger.warning(
            "csrf_guard %s",
            json.dumps(
                {
                    "enforced": enforce,
                    "method": request.method,
                    "origin": origin,
                    "path": request.path,
                    "service": service,
                },
                sort_keys=True,
            ),
        )
        if not enforce:
            return None
        return jsonify({"error": "Cross-site request rejected"}), 403
