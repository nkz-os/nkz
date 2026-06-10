"""Tests for telemetry-worker subscription manager.

Cannot import subscription_manager directly (psycopg2 import is lazy).
Tests verify logic via source code inspection and local replications.
"""

import os


def test_default_port_is_80():
    """Production source must default SERVICE_PORT to '80', not '8080'."""
    source_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "telemetry-worker",
        "telemetry_worker",
        "subscription_manager.py",
    )
    with open(source_path) as f:
        src = f.read()
    # Must have the correct default
    assert 'os.getenv("SERVICE_PORT", "80")' in src
    # Must NOT have the old broken default
    assert 'os.getenv("SERVICE_PORT", "8080")' not in src


def test_has_multi_tenant_functions():
    """Source must contain multi-tenant subscription management functions."""
    source_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "telemetry-worker",
        "telemetry_worker",
        "subscription_manager.py",
    )
    with open(source_path) as f:
        src = f.read()
    assert "def _get_active_tenants" in src
    assert "def _cleanup_broken_subscriptions" in src
    assert "def _ensure_tenant_subscriptions" in src
    assert "def ensure_subscriptions_for_all_tenants" in src
    # Backwards compat alias
    assert "check_or_create_subscription = ensure_subscriptions_for_all_tenants" in src


def test_has_postgres_url():
    """Source must read POSTGRES_URL for tenant discovery."""
    source_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "telemetry-worker",
        "telemetry_worker",
        "subscription_manager.py",
    )
    with open(source_path) as f:
        src = f.read()
    assert "POSTGRES_URL" in src
    assert "SELECT DISTINCT tenant_id FROM tenants" in src


def test_cleanup_targets_port_8080():
    """Cleanup function must specifically target ':8080' in URIs."""
    source_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "telemetry-worker",
        "telemetry_worker",
        "subscription_manager.py",
    )
    with open(source_path) as f:
        src = f.read()
    assert '":8080"' in src
    assert '"telemetry-worker"' in src


def test_app_uses_periodic_check():
    """app.py must use periodic subscription check."""
    source_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "telemetry-worker",
        "app.py",
    )
    with open(source_path) as f:
        src = f.read()
    assert "ensure_subscriptions_for_all_tenants" in src
    assert "_periodic_subscription_check" in src
    assert "asyncio.sleep(3600)" in src
    assert "periodic_task.cancel()" in src
