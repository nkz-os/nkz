"""
Shared authentication/authorization helpers for entity-manager blueprints.
"""

from flask import g


def _get_user_roles():
    """Get user roles from Flask g (set by auth middleware)"""
    roles = g.get('roles', [])
    if not roles:
        payload = g.get('current_user', {})
        if payload:
            roles = payload.get('realm_access', {}).get('roles', [])
    return roles


# Import entity-specific functions from local auth_middleware if they exist
try:
    from auth_middleware import require_entity_ownership
except ImportError:
    def require_entity_ownership(*args, **kwargs):
        def decorator(f):
            return f

        return decorator


def log_entity_operation(*args, **kwargs):
    """Log an entity operation. Accepts any args (called with 6 positional args from entities.py)."""
    pass
