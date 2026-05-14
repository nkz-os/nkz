"""
Nekazari Platform SDK — Backend module development kit.

Provides:
- ModuleApp: FastAPI subclass pre-wired with CORS, /health, JSON logs, auth helpers
- require_auth: FastAPI dependency for authenticated routes
- OrionClient: Typed NGSI-LD client with automatic tenant header injection
- TimescaleClient: Typed timeseries reader, tenant headers auto-injected
- ModuleLifecycle: Base class for install/uninstall/enable/disable hooks
- ModuleConfig: Per-tenant encrypted configuration storage

License: Apache-2.0 — modules using this SDK may use any license.
"""

from nkz_platform_sdk.auth import require_auth, AuthContext
from nkz_platform_sdk.module_app import ModuleApp
from nkz_platform_sdk.orion import OrionClient
from nkz_platform_sdk.timescale import TimescaleClient
from nkz_platform_sdk.lifecycle import ModuleLifecycle, LifecycleResult
from nkz_platform_sdk.config import ModuleConfig

__all__ = [
    "ModuleApp",
    "require_auth",
    "AuthContext",
    "OrionClient",
    "TimescaleClient",
    "ModuleLifecycle",
    "LifecycleResult",
    "ModuleConfig",
]
