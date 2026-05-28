"""Integration tests for the rewritten admin tenant-creation endpoint
`POST /api/admin/tenants` (Task 3.1).

The new handler is atomic with structured `error_code` payloads and
rolls back partial state on failure. These tests drive the handler
through Flask's test client with the heavy collaborators
(`create_tenant_resources`, `_ensure_orion_tenant_db`,
`_drop_orion_tenant_db`, `_delete_tenant_namespace`, `generate_api_key`,
`get_db_connection`, `create_keycloak_user`, `audit_log`) monkeypatched
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
        with patch.object(svc, "create_tenant_resources") as k8s, \
             patch.object(svc, "_ensure_orion_tenant_db") as mongo, \
             patch.object(svc, "generate_api_key") as api_key, \
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
        k8s.assert_not_called()
        mongo.assert_not_called()
        api_key.assert_not_called()
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
            # create_tenant_resources is the boundary that wraps both the
            # K8s shell script and the internal _ensure_orion_tenant_db
            # call. Capturing here pins the canonical id at the single
            # external integration point.
            captured["resources_tenant_id"] = tenant_id
            return True

        kc_result = {"success": True, "user_id": "u-1"}

        with patch.object(svc, "create_tenant_resources",
                          side_effect=capture_resources) as resources, \
             patch.object(svc, "generate_api_key",
                          return_value="api-key-xyz"), \
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

        # Boundary: the canonical id was passed to create_tenant_resources.
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
    """Atomicity contract: if the Postgres write fails after K8s + Mongo
    succeeded, both must be rolled back via the new helpers."""

    def test_db_failure_rolls_back_mongo_and_k8s(
        self, client, admin_headers, patched_admin, webhook_module
    ):
        svc = webhook_module.webhook_service

        # K8s + Mongo succeed.
        # ensure_tenant_record raises -> handler must call
        # _drop_orion_tenant_db AND _delete_tenant_namespace.
        fake_conn = MagicMock()
        fake_cursor = MagicMock()
        fake_cursor.fetchone.return_value = None  # no duplicate
        fake_conn.cursor.return_value = fake_cursor

        rollback_calls = {"mongo": [], "k8s": []}

        def record_mongo_drop(tenant_id):
            rollback_calls["mongo"].append(tenant_id)
            return True

        def record_ns_delete(tenant_id):
            rollback_calls["k8s"].append(tenant_id)
            return True

        with patch.object(svc, "create_tenant_resources",
                          return_value=True), \
             patch.object(svc, "generate_api_key",
                          return_value="api-key-xyz"), \
             patch.object(svc, "get_db_connection",
                          return_value=fake_conn), \
             patch.object(svc, "ensure_tenant_record",
                          side_effect=RuntimeError("simulated DB outage")), \
             patch.object(svc, "_drop_orion_tenant_db",
                          side_effect=record_mongo_drop) as drop_mongo, \
             patch.object(svc, "_delete_tenant_namespace",
                          side_effect=record_ns_delete) as del_ns, \
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
        # Both rollback helpers ran with the canonical id.
        assert rollback_calls["mongo"] == ["rollback-tenant"]
        assert rollback_calls["k8s"] == ["rollback-tenant"]
        drop_mongo.assert_called_once_with("rollback-tenant")
        del_ns.assert_called_once_with("rollback-tenant")
        # Keycloak must NOT be touched once we have decided to fail.
        kc.assert_not_called()

    def test_k8s_failure_rolls_back_mongo(
        self, client, admin_headers, patched_admin, webhook_module
    ):
        """If create_tenant_resources raises, its internal Mongo creation
        may have already succeeded; the handler must drop Mongo to avoid
        an orphaned database."""
        svc = webhook_module.webhook_service

        with patch.object(svc, "create_tenant_resources",
                          side_effect=RuntimeError("kubectl exit 1")), \
             patch.object(svc, "_drop_orion_tenant_db",
                          return_value=True) as drop_mongo, \
             patch.object(svc, "_delete_tenant_namespace") as del_ns, \
             patch.object(svc, "generate_api_key") as gen_key, \
             patch.object(svc, "get_db_connection", return_value=None), \
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
        assert body["error_code"] == "K8S_PROVISION_FAILED"
        drop_mongo.assert_called_once_with("k8s-fail")
        # K8s itself was never created (the script failed) so namespace
        # delete must NOT be called.
        del_ns.assert_not_called()
        gen_key.assert_not_called()
        kc.assert_not_called()

    def test_api_key_failure_rolls_back_mongo_and_k8s(
        self, client, admin_headers, patched_admin, webhook_module
    ):
        """generate_api_key writes a K8s Secret + Postgres api_keys row;
        if it raises after step 4, both Mongo and K8s must be torn down."""
        svc = webhook_module.webhook_service

        with patch.object(svc, "create_tenant_resources",
                          return_value=True), \
             patch.object(svc, "generate_api_key",
                          side_effect=RuntimeError("k8s secret API down")), \
             patch.object(svc, "_drop_orion_tenant_db",
                          return_value=True) as drop_mongo, \
             patch.object(svc, "_delete_tenant_namespace",
                          return_value=True) as del_ns, \
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
        del_ns.assert_called_once_with("apikey-fail")
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
