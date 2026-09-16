"""Client-side auth for internal service-to-service calls.

The server side of this contract lives in each service's ``/internal/*``
guard: it compares the ``X-Internal-Service-Secret`` header against the
``INTERNAL_SERVICE_SECRET`` environment variable with
``hmac.compare_digest()`` and fails closed when the variable is unset.

A caller that sends no header therefore gets a guaranteed 401, and so does
every caller of a service whose deployment forgot to declare the variable.
Both halves are silent by default -- the callers only logged a warning -- so
this module exists to make the misconfiguration loud at the point of use.
"""

import logging
import os
from typing import Dict

logger = logging.getLogger(__name__)

HEADER_NAME = "X-Internal-Service-Secret"


def internal_service_headers(caller: str) -> Dict[str, str]:
    """Return the headers that authenticate this service to an ``/internal/*`` route.

    ``caller`` names the call site (e.g. ``"entity-manager route cache"``) and
    is only used in the error message.

    When ``INTERNAL_SERVICE_SECRET`` is not configured the headers are empty
    and an error is logged: the call is going to be rejected, and a silent
    401 is precisely the failure mode this helper is here to surface.
    """
    secret = os.getenv("INTERNAL_SERVICE_SECRET", "")
    if not secret:
        logger.error(
            "INTERNAL_SERVICE_SECRET is not set - internal call from %s will be "
            "rejected with 401. Declare it in this service's deployment.",
            caller,
        )
        return {}
    return {HEADER_NAME: secret}
