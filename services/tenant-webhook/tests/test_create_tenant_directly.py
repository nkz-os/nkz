"""Integration tests for the rewritten admin tenant-creation endpoint
`POST /api/admin/tenants` (Task 3.1).

The new handler is atomic with structured `error_code` payloads and
rolls back partial state on failure. These tests drive the handler
through Flask's test client with the heavy collaborators
(`provision_tenant_core`, `_drop_orion_tenant_db`, `get_db_connection`,
`create_keycloak_user`, `audit_log`) monkeypatched
on the SUT module / service singleton.

`require_platform_admin` is bypassed by patching
`validate_keycloak_token` to return a payload with the PlatformAdmin
role, mirroring the pattern other Flask-route tests in this directory
already use for KEYCLOAK_AUTH_AVAILABLE=True.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

_TP = None  # bound to webhook_module.TenantProvisioning inside tests


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def helpers(webhook_module):
    return webhook_module


@pytest.fixture
def client(webhook_module):
    return webhook_module.app.test_client()


@pytest.fixture
def admin_headers():
    """Authorization header that will be accepted once
    `validate_keycloak_token` is patched to return a PlatformAdmin
    payload."""
    return {"Authorization": "Bearer test-admin-token",
            "Content-Type": "application/json"}


@pytest.fixture
def platform_admin_payload():
    return {
        "preferred_username": "admin@test",
        "email": "admin@test",
        "realm_access": {"roles": ["PlatformAdmin"]},
        "resource_access": {},
    }


@pytest.fixture
def patched_admin(webhook_module, platform_admin_payload):
    """Bypass `@require_platform_admin` by stubbing token validation."""
    with patch.object(
        webhook_module, "KEYCLOAK_AUTH_AVAILABLE", True
    ), patch.object(
        webhook_module, "validate_keycloak_token",
        return_value=platform_admin_payload,
    ), patch.object(
        webhook_module, "extract_tenant_id", return_value=None,
    ):
        yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestInputValidation:
    """Bad input must be rejected with 400 + a specific error_code,
    BEFORE any side effect (K8s, Mongo, DB, Keycloak)."""

    def test_invalid_tenant_name_returns_400_with_specific_error(
        self, client, admin_headers, patched_admin, webhook_module
    ):
        # "ab" is too short for the canonical normalize_tenant_id (>=3 chars).
        # Patch the collaborators so a regression that *does* reach them is
        # observable as side-effects on the mocks.
        svc = webhook_module.webhook_service
        with patch.object(svc, "provision_tenant_core") as provision, \
             patch.object(svc, "get_db_connection", return_value=None), \
             patch.object(svc, "create_keycloak_user") as kc:
            resp = client.post(
                "/api/admin/tenants",
                data=json.dumps({"tenant_name": "ab",
                                 "email": "owner@example.test",
                                 "plan": "basic"}),
                headers=admin_headers,
            )

        assert resp.status_code == 400, resp.data
        body = resp.get_json()
        assert body["error_code"] == "INVALID_TENANT_NAME"
        # And no side effects fired.
        provision.assert_not_called()
        kc.assert_not_called()

    def test_missing_tenant_name_returns_400(
        self, client, admin_headers, patched_admin
    ):
        resp = client.post(
            "/api/admin/tenants",
            data=json.dumps({"email": "owner@example.test"}),
            headers=admin_headers,
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["error_code"] == "MISSING_TENANT_NAME"

    def test_missing_email_returns_400(
        self, client, admin_headers, patched_admin
    ):
        resp = client.post(
            "/api/admin/tenants",
            data=json.dumps({"tenant_name": "Test Allotarra"}),
            headers=admin_headers,
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["error_code"] == "MISSING_EMAIL"

    def test_invalid_plan_returns_400(
        self, client, admin_headers, patched_admin
    ):
        resp = client.post(
            "/api/admin/tenants",
            data=json.dumps({"tenant_name": "Test Allotarra",
                             "email": "owner@example.test",
                             "plan": "super-deluxe"}),
            headers=admin_headers,
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["error_code"] == "INVALID_PLAN"


class TestNormalizationContract:
    """The new handler must use the canonical normalize_tenant_id and
    must NOT prepend a second 'tenant-' prefix. This is the regression
    we are paying off in Phase 3."""

    def test_tenant_id_has_no_double_prefix(
        self, client, admin_headers, patched_admin, webhook_module
    ):
        svc = webhook_module.webhook_service
        captured = {}

        def capture_resources(tenant_id, plan_info):
            # provision_tenant_core is the single boundary to tenant storage
            # (Orion-LD database + API key). Capturing here pins the
            # canonical id at that integration point.
            captured["resources_tenant_id"] = tenant_id
            return webhook_module.TenantProvisioning(
                tenant_id=tenant_id, orion_db=True, api_key="api-key-xyz"
            )

        kc_result = {"success": True, "user_id": "u-1"}

        with patch.object(svc, "provision_tenant_core",
                          side_effect=capture_resources) as resources, \
             patch.object(svc, "get_db_connection", return_value=None), \
             patch.object(svc, "create_keycloak_user",
                          return_value=kc_result), \
             patch.object(webhook_module, "audit_log"):
            resp = client.post(
                "/api/admin/tenants",
                data=json.dumps({"tenant_name": "Test Allotarra",
                                 "email": "owner@example.test",
                                 "plan": "basic"}),
                headers=admin_headers,
            )

        assert resp.status_code == 201, resp.data
        body = resp.get_json()

        # Boundary: the canonical id was passed to provision_tenant_core.
        assert captured["resources_tenant_id"] == "test-allotarra"

        # Response: tenant_id is the canonical id, no 'tenant-' prefix at all.
        # The strict equality above already pins the form; the prefix check is
        # belt-and-braces against a future change that loosens that assertion
        # (e.g. switching to a parametrized test) while reintroducing the
        # legacy f"tenant-{...}" bug.
        assert body["tenant_id"] == "test-allotarra"
        assert not body["tenant_id"].startswith("tenant-")
        # tenant_name in the response mirrors the canonical id under
        # the new contract — humanization is a read-time concern.
        assert body["tenant_name"] == "test-allotarra"

        # Sanity: resources provisioning was exercised exactly once.
        assert resources.call_count == 1


class TestRollback:
    """Atomicity contract: a Postgres failure after the tenant storage was
    provisioned drops the Orion-LD database; a provisioning failure never
    reaches Keycloak."""

    @staticmethod
    def _provisioned(tenant_id, plan_info):
        return _TP(tenant_id=tenant_id, orion_db=True, api_key="api-key-xyz")

    def test_db_failure_rolls_back_mongo(
        self, client, admin_headers, patched_admin, webhook_module
    ):
        global _TP
        _TP = webhook_module.TenantProvisioning
        svc = webhook_module.webhook_service
        fake_conn = MagicMock()
        fake_cursor = MagicMock()
        fake_cursor.fetchone.return_value = None  # no duplicate
        fake_conn.cursor.return_value = fake_cursor

        with patch.object(svc, "provision_tenant_core",
                          side_effect=self._provisioned), \
             patch.object(svc, "get_db_connection",
                          return_value=fake_conn), \
             patch.object(svc, "ensure_tenant_record",
                          side_effect=RuntimeError("simulated DB outage")), \
             patch.object(svc, "_drop_orion_tenant_db",
                          return_value=True) as drop_mongo, \
             patch.object(svc, "create_keycloak_user") as kc, \
             patch.object(webhook_module, "audit_log"):
            resp = client.post(
                "/api/admin/tenants",
                data=json.dumps({"tenant_name": "Rollback Tenant",
                                 "email": "owner@example.test",
                                 "plan": "basic"}),
                headers=admin_headers,
            )

        assert resp.status_code == 500, resp.data
        body = resp.get_json()
        assert body["error_code"] == "DB_PROVISION_FAILED"
        drop_mongo.assert_called_once_with("rollback-tenant")
        # Keycloak must NOT be touched once we have decided to fail.
        kc.assert_not_called()

    def test_storage_failure_rolls_back_mongo(
        self, client, admin_headers, patched_admin, webhook_module
    ):
        """If the Orion-LD database could not be ensured the handler drops
        whatever may exist and stops before the record and Keycloak."""
        svc = webhook_module.webhook_service
        failed = webhook_module.TenantProvisioning(
            tenant_id="k8s-fail", orion_db=False, api_key=None,
            errors=("orion-ld database could not be ensured",),
        )
        with patch.object(svc, "provision_tenant_core", return_value=failed), \
             patch.object(svc, "_drop_orion_tenant_db",
                          return_value=True) as drop_mongo, \
             patch.object(svc, "get_db_connection", return_value=None), \
             patch.object(svc, "ensure_tenant_record") as ensure, \
             patch.object(svc, "create_keycloak_user") as kc, \
             patch.object(webhook_module, "audit_log"):
            resp = client.post(
                "/api/admin/tenants",
                data=json.dumps({"tenant_name": "K8s Fail",
                                 "email": "owner@example.test",
                                 "plan": "basic"}),
                headers=admin_headers,
            )

        assert resp.status_code == 500, resp.data
        body = resp.get_json()
        assert body["error_code"] == "PROVISION_FAILED"
        drop_mongo.assert_called_once_with("k8s-fail")
        ensure.assert_not_called()
        kc.assert_not_called()

    def test_api_key_failure_rolls_back_mongo(
        self, client, admin_headers, patched_admin, webhook_module
    ):
        """A tenant without an API key is not usable: the database created
        for it must be dropped and the flow stopped."""
        svc = webhook_module.webhook_service
        no_key = webhook_module.TenantProvisioning(
            tenant_id="apikey-fail", orion_db=True, api_key=None,
            errors=("api key could not be stored",),
        )
        with patch.object(svc, "provision_tenant_core", return_value=no_key), \
             patch.object(svc, "_drop_orion_tenant_db",
                          return_value=True) as drop_mongo, \
             patch.object(svc, "get_db_connection", return_value=None), \
             patch.object(svc, "ensure_tenant_record") as ensure, \
             patch.object(svc, "create_keycloak_user") as kc, \
             patch.object(webhook_module, "audit_log"):
            resp = client.post(
                "/api/admin/tenants",
                data=json.dumps({"tenant_name": "ApiKey Fail",
                                 "email": "owner@example.test",
                                 "plan": "basic"}),
                headers=admin_headers,
            )

        assert resp.status_code == 500, resp.data
        body = resp.get_json()
        assert body["error_code"] == "API_KEY_FAILED"
        drop_mongo.assert_called_once_with("apikey-fail")
        ensure.assert_not_called()
        kc.assert_not_called()


class TestEndpointBinding:
    """Pin the @app.route binding so a future refactor can't silently
    move the URL onto a helper function."""

    def test_route_is_bound_to_public_function(self, webhook_module):
        endpoint_names = {
            rule.endpoint for rule in webhook_module.app.url_map.iter_rules()
            if rule.rule == "/api/admin/tenants" and "POST" in (rule.methods or set())
        }
        assert "create_tenant_directly" in endpoint_names, (
            f"Route POST /api/admin/tenants is not bound to "
            f"create_tenant_directly. Bound to: {endpoint_names}"
        )
