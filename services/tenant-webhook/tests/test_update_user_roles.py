"""Regression tests for the helpers extracted from `update_user_roles`
during the C901 refactor (PR3c-2).

`_parse_roles_payload`, `_clear_user_realm_roles`, and
`_assign_realm_roles_to_user` carry the request/Keycloak boundary
behavior that previously lived inline. Pinning this lets future
maintenance (e.g. adding a new role validation rule) ship without
silently changing the public PUT contract.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def helpers(webhook_module):
    return webhook_module


@pytest.fixture
def app_ctx(webhook_module):
    """`_parse_roles_payload` builds Flask responses via jsonify, which
    needs an active app context."""
    with webhook_module.app.test_request_context("/"):
        yield


class TestParseRolesPayload:
    def test_none_payload_returns_400(self, helpers, app_ctx):
        roles, err = helpers._parse_roles_payload(None)
        assert roles is None
        assert err is not None
        _, status = err
        assert status == 400

    def test_missing_roles_key_returns_400(self, helpers, app_ctx):
        roles, err = helpers._parse_roles_payload({"other": "field"})
        assert roles is None
        _, status = err
        assert status == 400

    def test_non_list_roles_returns_400(self, helpers, app_ctx):
        roles, err = helpers._parse_roles_payload({"roles": "TenantAdmin"})
        assert roles is None
        _, status = err
        assert status == 400

    def test_valid_empty_list_passes(self, helpers, app_ctx):
        """Empty list is valid — admin may want to strip ALL roles
        from a user before re-assigning. The route's behavior here is
        idempotent role removal, not a 400."""
        roles, err = helpers._parse_roles_payload({"roles": []})
        assert roles == []
        assert err is None

    def test_valid_payload_returns_roles(self, helpers, app_ctx):
        roles, err = helpers._parse_roles_payload(
            {"roles": ["Farmer", "TenantAdmin"]}
        )
        assert roles == ["Farmer", "TenantAdmin"]
        assert err is None


class TestClearUserRealmRoles:
    """Best-effort delete of current role-mappings. Failures are
    silently swallowed because the caller is about to assign a fresh
    set anyway, but the helper MUST NOT raise."""

    def test_no_current_roles_skips_delete(self, helpers):
        get_resp = MagicMock(status_code=200)
        get_resp.json.return_value = []
        with patch("requests.get", return_value=get_resp) as mock_get, \
             patch("requests.delete") as mock_delete:
            helpers._clear_user_realm_roles("http://kc", "u1", {"H": "v"})
        mock_get.assert_called_once()
        mock_delete.assert_not_called()

    def test_with_current_roles_issues_delete(self, helpers):
        get_resp = MagicMock(status_code=200)
        get_resp.json.return_value = [{"name": "Farmer", "id": "r1"}]
        with patch("requests.get", return_value=get_resp), \
             patch("requests.delete") as mock_delete:
            helpers._clear_user_realm_roles("http://kc", "u1", {"H": "v"})
        mock_delete.assert_called_once()

    def test_get_failure_does_not_raise(self, helpers):
        get_resp = MagicMock(status_code=500)
        with patch("requests.get", return_value=get_resp), \
             patch("requests.delete") as mock_delete:
            helpers._clear_user_realm_roles("http://kc", "u1", {"H": "v"})
        mock_delete.assert_not_called()


class TestAssignRealmRolesToUser:
    """Iterates the requested roles, GETs each role definition, then
    POSTs the role-mapping. Returns only the names whose POST
    succeeded — caller surfaces this so clients detect partial
    application."""

    def test_returns_only_successfully_assigned_names(self, helpers):
        farmer_get = MagicMock(status_code=200)
        farmer_get.json.return_value = {"name": "Farmer", "id": "r1"}

        admin_get = MagicMock(status_code=200)
        admin_get.json.return_value = {"name": "TenantAdmin", "id": "r2"}

        post_responses = [
            MagicMock(status_code=204),
            MagicMock(status_code=500),
        ]
        with patch("requests.get", side_effect=[farmer_get, admin_get]), \
             patch("requests.post", side_effect=post_responses):
            assigned = helpers._assign_realm_roles_to_user(
                "http://kc", "u1", ["Farmer", "TenantAdmin"], {"H": "v"}
            )
        assert assigned == ["Farmer"]

    def test_skips_unknown_role_definitions(self, helpers):
        """When the GET on the role itself returns 404 (role doesn't
        exist), the helper must skip that role entirely, not POST a
        broken mapping."""
        unknown_get = MagicMock(status_code=404)
        with patch("requests.get", return_value=unknown_get), \
             patch("requests.post") as mock_post:
            assigned = helpers._assign_realm_roles_to_user(
                "http://kc", "u1", ["Bogus"], {"H": "v"}
            )
        assert assigned == []
        mock_post.assert_not_called()

    def test_empty_roles_list_returns_empty_list(self, helpers):
        with patch("requests.get") as mock_get, \
             patch("requests.post") as mock_post:
            assigned = helpers._assign_realm_roles_to_user(
                "http://kc", "u1", [], {"H": "v"}
            )
        assert assigned == []
        mock_get.assert_not_called()
        mock_post.assert_not_called()


class TestEndpointDecoratorBinding:
    """Smoke test the route registration. The C901 refactor extracted
    helpers immediately above `update_user_roles`, and the @app.route /
    @require_platform_admin decorators initially attached to the wrong
    function — this test catches that exact regression."""

    def test_route_is_bound_to_public_function(self, webhook_module):
        endpoint_names = {
            rule.endpoint for rule in webhook_module.app.url_map.iter_rules()
            if rule.rule == "/api/admin/users/<user_id>/roles"
        }
        assert "update_user_roles" in endpoint_names, (
            f"Route /api/admin/users/<user_id>/roles is not bound to "
            f"update_user_roles. Bound to: {endpoint_names}"
        )
