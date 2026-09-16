"""Tenant constants shared across core services.

``SHARED_TENANT`` is the scope for cross-tenant geographic/reference data
(weather alerts, municipal observations, ...). It is intentionally distinct
from any real tenant and from Orion-LD's implicit un-tenanted fallback
(formerly the literal ``"default"``).
"""

import os

SHARED_TENANT = os.getenv("SHARED_GEO_TENANT", "shared")
