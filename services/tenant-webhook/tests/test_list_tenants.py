"""Regression tests for `_resolve_tenant_expires` extracted from
`list_tenants` during the C901 refactor (PR3c-2).

The 4-state expiration logic (platform tenant / tenants.expires_at /
activation expiry / both null) used to be inlined in the route body.
This test pins down each branch so future maintenance cannot silently
flip a precedence rule.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest


@pytest.fixture
def helpers(webhook_module):
    return webhook_module


class TestResolveTenantExpires:
    """`_resolve_tenant_expires(tenant_id, row, activation_expires)`
    returns (expires_at_iso, days_remaining)."""

    def test_platform_tenant_always_returns_none_none(self, helpers):
        future = datetime.utcnow() + timedelta(days=30)
        iso, days = helpers._resolve_tenant_expires(
            "platform", {"expires_at": future}, future
        )
        assert iso is None
        assert days is None

    def test_platform_tenant_case_insensitive(self, helpers):
        future = datetime.utcnow() + timedelta(days=30)
        for name in ("Platform", "PLATFORM", "platform"):
            iso, days = helpers._resolve_tenant_expires(
                name, {"expires_at": future}, future
            )
            assert (iso, days) == (None, None), name

    def test_tenants_expires_at_takes_precedence_over_activation(self, helpers):
        """The hard rule from CLAUDE.md: when tenants.expires_at is set,
        it is the canonical license window — activation expiry is
        irrelevant."""
        tenants_expires = datetime.utcnow() + timedelta(days=10)
        activation_expires = datetime.utcnow() + timedelta(days=99)
        iso, days = helpers._resolve_tenant_expires(
            "t1", {"expires_at": tenants_expires}, activation_expires
        )
        assert iso == tenants_expires.isoformat()
        assert days >= 9 and days <= 10

    def test_tenants_expires_null_falls_back_to_activation(self, helpers):
        activation_expires = datetime.utcnow() + timedelta(days=5)
        iso, days = helpers._resolve_tenant_expires(
            "t1", {"expires_at": None}, activation_expires
        )
        assert iso == activation_expires.isoformat()
        assert days >= 4 and days <= 5

    def test_tenants_expires_missing_key_falls_back_to_activation(self, helpers):
        """Behavior must match `expires_at: None` — the original code
        used `if "expires_at" in row and row["expires_at"] is not None`."""
        activation_expires = datetime.utcnow() + timedelta(days=5)
        iso, days = helpers._resolve_tenant_expires(
            "t1", {}, activation_expires
        )
        assert iso == activation_expires.isoformat()

    def test_both_null_returns_none_none(self, helpers):
        iso, days = helpers._resolve_tenant_expires("t1", {"expires_at": None}, None)
        assert iso is None
        assert days is None

    def test_string_expires_at_returns_str_and_none_days(self, helpers):
        """psycopg2 normally returns datetime, but in case the row
        carries a serialized string (legacy data, JSON loads), the
        function falls through to str() + None days_remaining."""
        iso, days = helpers._resolve_tenant_expires(
            "t1", {"expires_at": "2030-01-01T00:00:00"}, None
        )
        assert iso == "2030-01-01T00:00:00"
        assert days is None

    def test_already_expired_clamps_days_remaining_to_zero(self, helpers):
        past = datetime.utcnow() - timedelta(days=10)
        iso, days = helpers._resolve_tenant_expires(
            "t1", {"expires_at": past}, None
        )
        assert iso == past.isoformat()
        assert days == 0


class TestEndpointDecoratorBinding:
    """The C901 refactor inserted `_resolve_tenant_expires` and
    `_serialize_tenant_row` immediately above `list_tenants`. Verify
    the @app.route / @require_platform_admin stack stays attached to
    the public function and not to a helper above it."""

    def test_route_is_bound_to_public_function(self, webhook_module):
        endpoint_names = {
            rule.endpoint for rule in webhook_module.app.url_map.iter_rules()
            if rule.rule == "/tenants"
        }
        assert "list_tenants" in endpoint_names, (
            f"Route /tenants is not bound to list_tenants. "
            f"Bound to: {endpoint_names}"
        )
