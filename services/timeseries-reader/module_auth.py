"""Accept module-to-service calls signed without a user JWT.

Module backends call the reader with no user token: they send X-Tenant-ID and
X-Auth-Signature = generate_hmac_signature("", tenant), signed with the shared
HMAC_SECRET (the same pattern weather-api accepts). common.auth_middleware's
require_auth demands a Bearer JWT first, so those calls got 401.

A request carrying a token (header or cookie) keeps the user path unchanged.
A request with no token but with X-Auth-Signature takes the module path: the
signature over the empty token and X-Tenant-ID is the only credential, so it
is always verified, whatever REQUIRE_HMAC_SIGNATURE says. Without HMAC_SECRET
the module path answers 503 (fail-closed).

This lives in the reader, not in common.auth_middleware: the common decorator
also guards entity-manager and sdm-integration write routes, which must keep
requiring a user.
"""

import logging
from functools import wraps

from flask import g, jsonify, request

logger = logging.getLogger(__name__)


def accept_module_signature(user_auth, auth_module):
    """Wrap ``user_auth`` (common require_auth) with the module-signature path.

    ``auth_module`` is common.auth_middleware: its HMAC_SECRET, token lookup and
    verify_hmac_signature are read at call time, so the canonical verifier is
    the one used on both paths.
    """

    def decorator(f):
        user_wrapped = user_auth(f)

        @wraps(f)
        def wrapped(*args, **kwargs):
            signature = (request.headers.get("X-Auth-Signature") or "").strip()
            if (
                request.method == "OPTIONS"
                or auth_module.get_request_token()
                or not signature
            ):
                return user_wrapped(*args, **kwargs)

            caller = request.headers.get("X-User-ID", "")
            if not auth_module.HMAC_SECRET:
                logger.critical(
                    "Module-signed request but HMAC_SECRET unset: path=%s caller=%s",
                    request.path,
                    caller,
                )
                return (
                    jsonify({"error": "Service misconfigured: signature secret missing"}),
                    503,
                )
            tenant = (request.headers.get("X-Tenant-ID") or "").strip()
            if not tenant:
                logger.warning(
                    "Module-signed request without X-Tenant-ID: path=%s caller=%s",
                    request.path,
                    caller,
                )
                return jsonify({"error": "Signed request without X-Tenant-ID"}), 401
            if not auth_module.verify_hmac_signature(signature, "", tenant):
                logger.warning(
                    "Invalid module signature: path=%s caller=%s tenant=%s",
                    request.path,
                    caller,
                    tenant,
                )
                return jsonify({"error": "Invalid or missing signature"}), 401

            g.module_caller = True
            g.current_user = None
            g.tenant = tenant
            g.user = caller or "module"
            g.user_id = None
            g.roles = []
            logger.debug(
                "Module-signed request: path=%s caller=%s tenant=%s",
                request.path,
                caller,
                tenant,
            )
            return f(*args, **kwargs)

        return wrapped

    return decorator
