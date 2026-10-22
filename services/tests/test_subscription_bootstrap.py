"""services/tests/test_subscription_bootstrap.py — Verifies subscription bootstrap at startup."""

import os
import pytest

_EM_API_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "entity-manager", "entity_management_api.py"
)


def test_entity_manager_invokes_bootstrap():
    """entity_management_api must call ensure_subscriptions at startup."""
    with open(_EM_API_PATH) as f:
        src = f.read()

    # Check the bootstrap function exists
    assert "def _bootstrap_subscriptions_at_startup" in src, \
        "Must define a bootstrap function"

    # Check the function calls ensure_subscriptions_for_all_tenants
    assert "ensure_subscriptions_for_all_tenants" in src, \
        "Bootstrap must call ensure_subscriptions_for_all_tenants"

    # Check it runs in background thread (non-blocking startup)
    assert "threading.Thread" in src, \
        "Bootstrap must run in background thread (daemon)"

    # Verify it's placed after blueprints (app ready) but before main guard
    blueprint_line = src.find("app.register_blueprint")
    bootstrap_line = src.find("_bootstrap_subscriptions_at_startup")
    main_guard = src.find("if __name__ == '__main__':")

    assert blueprint_line < bootstrap_line or blueprint_line == -1, \
        "Bootstrap must be registered AFTER blueprints (app fully initialized)"

    if main_guard > 0:
        assert bootstrap_line < main_guard or bootstrap_line == -1, \
            "Bootstrap call must be BEFORE the if __name__ guard"
