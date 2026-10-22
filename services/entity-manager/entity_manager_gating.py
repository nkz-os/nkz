"""Module install gating helpers. Imported by entity_management_api.py."""
from typing import Optional


def can_tenant_install_module(tenant_level: Optional[int], required_level: Optional[int]) -> bool:
    """Return True if a tenant at `tenant_level` can install a module that requires `required_level`.

    NULL/None values are treated as 0 (basic) for both sides.
    """
    t = tenant_level if tenant_level is not None else 0
    r = required_level if required_level is not None else 0
    return t >= r
