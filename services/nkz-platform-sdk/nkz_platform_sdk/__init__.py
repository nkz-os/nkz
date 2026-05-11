"""
Nekazari Platform SDK — Backend module development kit.

Provides:
- require_auth: FastAPI dependency for authenticated routes
- OrionClient: Typed NGSI-LD client with automatic tenant header injection
- ModuleLifecycle: Base class for install/uninstall/enable/disable hooks
- ModuleConfig: Per-tenant encrypted configuration storage

License: Apache-2.0 — modules using this SDK may use any license.
"""

from nkz_platform_sdk.auth import require_auth, AuthContext
from nkz_platform_sdk.orion import OrionClient
from nkz_platform_sdk.lifecycle import ModuleLifecycle, LifecycleResult
from nkz_platform_sdk.config import ModuleConfig

__all__ = [
    "require_auth",
    "AuthContext",
    "OrionClient",
    "ModuleLifecycle",
    "LifecycleResult",
    "ModuleConfig",
]
