"""Tests for the tenant-scoped `GET /api/tenant/plan` endpoint and its
shared computation (`_compute_tenant_plan_window`).

The plan window (plan, plan_level, status, expires_at, days_remaining) is
served to regular tenant members for the dashboard's expiry banner —
`/api/admin/tenants` is PlatformAdmin-only. The tenant is resolved from
the JWT on the server (ADR 003 pattern, same as /api/tenant/api-keys), so
a member can only ever read their own tenant's window.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def helpers(webhook_module):
    return webhook_module


class TestComputeTenantPlanWindow:
    """`_compute_tenant_plan_window(tenant_id, row, activation)` is the
    single computation shared with the admin listing (list_tenants)."""

    def test_platform_tenant_returns_null_expiry(self, helpers):
        future = datetime.utcnow() + timedelta(days=30)
        out = helpers._compute_tenant_plan_window(
            "platform",
            {"plan_type": "basic", "status": "active", "expires_at": future},
            {"expires_at": future},
        )
        assert out["expires_at"] is None
        assert out["days_remaining"] is None
        assert out["plan"] == "basic"

    def test_tenants_expires_at_wins_over_activation(self, helpers):
        tenants_expires = datetime.utcnow() + timedelta(days=10)
        out = helpers._compute_tenant_plan_window(
            "t1",
            {"plan_type": "pro", "plan_level": 2, "status": "active",
             "expires_at": tenants_expires},
            {"expires_at": datetime.utcnow() + timedelta(days=99)},
        )
        assert out["expires_at"] == tenants_expires.isoformat()
        assert 9 <= out["days_remaining"] <= 10

    def test_null_tenants_expires_falls_back_to_activation(self, helpers):
        activation_expires = datetime.utcnow() + timedelta(days=5)
        out = helpers._compute_tenant_plan_window(
            "t1",
            {"plan_type": "pro", "expires_at": None},
            {"expires_at": activation_expires},
        )
        assert out["expires_at"] == activation_expires.isoformat()
        assert 4 <= out["days_remaining"] <= 5

    def test_defaults_for_missing_plan_and_status(self, helpers):
        out = helpers._compute_tenant_plan_window(
            "t1", {"plan_type": None, "status": None, "expires_at": None}, {}
        )
        assert out["plan"] == "basic"
        assert out["status"] == "active"
        assert out["plan_level"] is None
        assert out["expires_at"] is None
        assert out["days_remaining"] is None

    def test_negative_days_are_clamped_to_zero(self, helpers):
        expired = datetime.utcnow() - timedelta(days=3)
        out = helpers._compute_tenant_plan_window(
            "t1", {"plan_type": "pro", "expires_at": expired}, None
        )
        assert out["days_remaining"] == 0


@pytest.fixture
def client(webhook_module, monkeypatch):
    """Flask test client with JWT auth and DB seams patched."""
    mod = webhook_module
    monkeypatch.setattr(mod, "POSTGRES_URL", "postgresql://test")
    monkeypatch.setattr(
        mod,
        "validate_keycloak_token",
        lambda token: {"tenant_id": "acme", "preferred_username": "u"},
    )
    mod.app.config["TESTING"] = True
    return mod.app.test_client(), mod, monkeypatch


AUTH = {"Authorization": "Bearer test-token"}


class TestGetTenantPlanEndpoint:
    def test_requires_auth(self, client):
        tc, _, _ = client
        res = tc.get("/api/tenant/plan")
        assert res.status_code == 401

    def test_returns_own_tenant_window(self, client):
        tc, mod, monkeypatch = client
        tenants_expires = datetime.utcnow() + timedelta(days=12)
        row = {"tenant_name": "Acme", "plan_type": "pro", "plan_level": 2,
               "status": "active", "expires_at": tenants_expires}
        activation = {"email": "o@acme.io", "expires_at": None}
        seen = {}

        def fake_load(conn, tenant_id):
            seen["load_tenant"] = tenant_id
            return row

        monkeypatch.setattr(mod.webhook_service, "get_db_connection", lambda: MagicMock())
        monkeypatch.setattr(
            mod.webhook_service,
            "_apply_tenant_context",
            lambda conn, tenant_id: seen.setdefault("ctx", tenant_id),
        )
        monkeypatch.setattr(mod, "_load_tenant_plan_row", fake_load)
        monkeypatch.setattr(
            mod.webhook_service,
            "get_latest_activation_for_tenant",
            lambda conn, tenant_id: activation,
        )

        res = tc.get("/api/tenant/plan", headers=AUTH)
        assert res.status_code == 200
        body = res.get_json()
        assert body == {
            "tenant": "acme",
            "plan": "pro",
            "plan_level": 2,
            "status": "active",
            "expires_at": tenants_expires.isoformat(),
            "days_remaining": body["days_remaining"],  # 11 or 12
        }
        assert 11 <= body["days_remaining"] <= 12
        # Tenant scoping: everything resolved for the JWT tenant, never client input.
        assert seen["load_tenant"] == "acme"
        assert seen["ctx"] == "acme"

    def test_platform_tenant_gets_null_window(self, client):
        tc, mod, monkeypatch = client
        monkeypatch.setattr(
            mod,
            "validate_keycloak_token",
            lambda token: {"tenant_id": "platform"},
        )
        monkeypatch.setattr(mod.webhook_service, "get_db_connection", lambda: MagicMock())
        monkeypatch.setattr(
            mod.webhook_service, "_apply_tenant_context", lambda conn, tid: None
        )
        monkeypatch.setattr(
            mod,
            "_load_tenant_plan_row",
            lambda conn, tid: {"plan_type": "basic", "status": "active",
                               "plan_level": None, "expires_at": None},
        )
        monkeypatch.setattr(
            mod.webhook_service,
            "get_latest_activation_for_tenant",
            lambda conn, tid: {},
        )
        res = tc.get("/api/tenant/plan", headers=AUTH)
        assert res.status_code == 200
        body = res.get_json()
        assert body["tenant"] == "platform"
        assert body["expires_at"] is None
        assert body["days_remaining"] is None

    def test_404_when_tenant_row_missing(self, client):
        tc, mod, monkeypatch = client
        monkeypatch.setattr(mod.webhook_service, "get_db_connection", lambda: MagicMock())
        monkeypatch.setattr(
            mod.webhook_service, "_apply_tenant_context", lambda conn, tid: None
        )
        monkeypatch.setattr(mod, "_load_tenant_plan_row", lambda conn, tid: None)
        res = tc.get("/api/tenant/plan", headers=AUTH)
        assert res.status_code == 404

    def test_no_tenant_context_in_token_forbidden(self, client):
        tc, mod, monkeypatch = client
        monkeypatch.setattr(
            mod, "validate_keycloak_token", lambda token: {"preferred_username": "u"}
        )
        res = tc.get("/api/tenant/plan", headers=AUTH)
        assert res.status_code == 403
