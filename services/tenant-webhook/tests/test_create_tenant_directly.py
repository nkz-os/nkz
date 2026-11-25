"""Integration tests for the rewritten admin tenant-creation endpoint
`POST /api/admin/tenants` (Task 3.1).

The handler answers with structured `error_code` payloads and destroys
nothing on failure. These tests drive the handler
through Flask's test client with the heavy collaborators
(`provision_tenant_core`, `get_db_connection`,
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


def _no_duplicate_conn():
    """A reachable database with no tenant of that id."""
    conn = MagicMock()
    conn.cursor.return_value.fetchone.return_value = None
    return conn


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
            data=json.dumps({"tenant_name": "Test Coop"}),
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
            data=json.dumps({"tenant_name": "Test Coop",
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
             patch.object(svc, "ensure_tenant_record", return_value=1), \
             patch.object(svc, "get_db_connection", return_value=_no_duplicate_conn()), \
             patch.object(svc, "create_keycloak_user",
                          return_value=kc_result), \
             patch.object(webhook_module, "audit_log"):
            resp = client.post(
                "/api/admin/tenants",
                data=json.dumps({"tenant_name": "Test Coop",
                                 "email": "owner@example.test",
                                 "plan": "basic"}),
                headers=admin_headers,
            )

        assert resp.status_code == 201, resp.data
        body = resp.get_json()

        # Boundary: the canonical id was passed to provision_tenant_core.
        assert captured["resources_tenant_id"] == "test-coop"

        # Response: tenant_id is the canonical id, no 'tenant-' prefix at all.
        # The strict equality above already pins the form; the prefix check is
        # belt-and-braces against a future change that loosens that assertion
        # (e.g. switching to a parametrized test) while reintroducing the
        # legacy f"tenant-{...}" bug.
        assert body["tenant_id"] == "test-coop"
        assert not body["tenant_id"].startswith("tenant-")
        # tenant_name in the response mirrors the canonical id under
        # the new contract — humanization is a read-time concern.
        assert body["tenant_name"] == "test-coop"

        # Sanity: resources provisioning was exercised exactly once.
        assert resources.call_count == 1


class TestFailedCreation:
    """A failed creation stops before Keycloak and destroys nothing."""

    @staticmethod
    def _provisioned(tenant_id, plan_info):
        return _TP(tenant_id=tenant_id, orion_db=True, api_key="api-key-xyz")

    def test_db_failure_stops_before_keycloak(
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
        # Keycloak must NOT be touched once we have decided to fail.
        kc.assert_not_called()

    def test_storage_failure_stops(
        self, client, admin_headers, patched_admin, webhook_module
    ):
        """If the Orion-LD database could not be ensured the handler stops
        before the record and Keycloak."""
        svc = webhook_module.webhook_service
        failed = webhook_module.TenantProvisioning(
            tenant_id="k8s-fail", orion_db=False, api_key=None,
            errors=("orion-ld database could not be ensured",),
        )
        with patch.object(svc, "provision_tenant_core", return_value=failed), \
             patch.object(svc, "get_db_connection", return_value=_no_duplicate_conn()), \
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
        ensure.assert_not_called()
        kc.assert_not_called()

    def test_api_key_failure_stops(
        self, client, admin_headers, patched_admin, webhook_module
    ):
        """A tenant without an API key is not usable: the flow stops."""
        svc = webhook_module.webhook_service
        no_key = webhook_module.TenantProvisioning(
            tenant_id="apikey-fail", orion_db=True, api_key=None,
            errors=("api key could not be stored",),
        )
        with patch.object(svc, "provision_tenant_core", return_value=no_key), \
             patch.object(svc, "get_db_connection", return_value=_no_duplicate_conn()), \
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
        ensure.assert_not_called()
        kc.assert_not_called()


    def test_creation_never_drops_a_database(self, webhook_module):
        """A concurrent request for the same name may own it: what a failed
        creation leaves behind is an empty database, reused by the next one."""
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(webhook_module))
        calls = {
            n.func.attr
            for n in ast.walk(
                next(f for f in ast.walk(tree)
                     if isinstance(f, ast.FunctionDef)
                     and f.name == "create_tenant_directly")
            )
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert calls & {"drop_database", "_drop_orion_tenant_db"} == set()
        assert not hasattr(webhook_module.webhook_service, "_drop_orion_tenant_db")


class TestInUse:
    """A reserved id, or one whose broker data is still there, is refused
    before anything is provisioned."""

    @pytest.mark.parametrize("name, data, status, code", [
        ("platform", False, 409, "TENANT_EXISTS"),
        ("acme", True, 409, "TENANT_EXISTS"),
        ("acme", RuntimeError("down"), 503, "STORAGE_UNAVAILABLE")])
    def test_refused(self, client, admin_headers, patched_admin, webhook_module,
                     name, data, status, code):
        svc = webhook_module.webhook_service
        with patch.object(svc, "get_db_connection", return_value=_no_duplicate_conn()), \
             patch.object(svc, "_broker_has_data", side_effect=[data]), \
             patch.object(svc, "provision_tenant_core") as provision:
            resp = client.post(
                "/api/admin/tenants",
                data=json.dumps({"tenant_name": name, "email": "owner@example.test",
                                 "plan": "basic"}),
                headers=admin_headers,
            )
        assert resp.status_code == status and resp.get_json()["error_code"] == code
        provision.assert_not_called()


class TestRecord:
    """The admin endpoint creates exactly the id it was asked for."""

    def _post(self, client, admin_headers, webhook_module, conns, ensure):
        svc = webhook_module.webhook_service
        with patch.object(svc, "provision_tenant_core",
                          return_value=webhook_module.TenantProvisioning("acme", True, "k")), \
             patch.object(svc, "get_db_connection", side_effect=conns), \
             patch.object(svc, "ensure_tenant_record", ensure), \
             patch.object(svc, "create_keycloak_user",
                          return_value={"success": True, "user_id": "u"}) as kc, \
             patch.object(webhook_module, "_send_email_async"), \
             patch.object(webhook_module, "audit_log"):
            resp = client.post(
                "/api/admin/tenants",
                data=json.dumps({"tenant_name": "acme", "email": "owner@example.test",
                                 "plan": "basic"}),
                headers=admin_headers,
            )
        return resp, kc

    def test_matches_nothing(self, client, admin_headers, patched_admin, webhook_module):
        ensure = MagicMock(return_value="acme")
        resp, _ = self._post(client, admin_headers, webhook_module,
                             [_no_duplicate_conn(), _no_duplicate_conn()], ensure)
        assert resp.status_code == 201, resp.data
        assert ensure.call_args.kwargs["match"] == "none"

    def test_lost_race(self, client, admin_headers, patched_admin, webhook_module):
        ensure = MagicMock(side_effect=webhook_module.TenantExistsError("acme"))
        resp, kc = self._post(client, admin_headers, webhook_module,
                              [_no_duplicate_conn(), _no_duplicate_conn()], ensure)
        assert resp.status_code == 409 and resp.get_json()["error_code"] == "TENANT_EXISTS"
        kc.assert_not_called()

    def test_no_record_no_account(self, client, admin_headers, patched_admin, webhook_module):
        ensure = MagicMock()
        resp, kc = self._post(client, admin_headers, webhook_module,
                              [_no_duplicate_conn(), None], ensure)
        assert resp.status_code == 503
        ensure.assert_not_called()
        kc.assert_not_called()


class TestDuplicateCheck:
    """Nothing is provisioned for a taken name, nor without the check."""

    def test_existing_tenant_is_refused(
        self, client, admin_headers, patched_admin, webhook_module
    ):
        svc = webhook_module.webhook_service
        conn = MagicMock()
        conn.cursor.return_value.fetchone.return_value = {"?column?": 1}
        with patch.object(svc, "get_db_connection", return_value=conn), \
             patch.object(svc, "provision_tenant_core",
                          return_value=webhook_module.TenantProvisioning(
                              tenant_id="acme", orion_db=False, api_key=None)) as provision, \
             patch.object(svc, "create_keycloak_user") as kc:
            resp = client.post(
                "/api/admin/tenants",
                data=json.dumps({"tenant_name": "acme",
                                 "email": "owner@example.test",
                                 "plan": "basic"}),
                headers=admin_headers,
            )
        assert resp.status_code == 409, resp.data
        assert resp.get_json()["error_code"] == "TENANT_EXISTS"
        provision.assert_not_called()
        kc.assert_not_called()

    @pytest.mark.parametrize("mode", ["no_connection", "query_fails"])
    def test_fails_closed(
        self, client, admin_headers, patched_admin, webhook_module, mode
    ):
        svc = webhook_module.webhook_service
        conn = None
        if mode == "query_fails":
            conn = MagicMock()
            conn.cursor.return_value.execute.side_effect = RuntimeError("down")
        with patch.object(svc, "get_db_connection", return_value=conn), \
             patch.object(svc, "provision_tenant_core",
                          return_value=webhook_module.TenantProvisioning(
                              tenant_id="acme", orion_db=False, api_key=None)) as provision, \
             patch.object(svc, "create_keycloak_user") as kc:
            resp = client.post(
                "/api/admin/tenants",
                data=json.dumps({"tenant_name": "acme",
                                 "email": "owner@example.test",
                                 "plan": "basic"}),
                headers=admin_headers,
            )
        assert resp.status_code == 503, resp.data
        assert resp.get_json()["error_code"] == "DB_UNAVAILABLE"
        provision.assert_not_called()
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
