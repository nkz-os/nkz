"""Regression tests for the helpers extracted from
`WebhookService.create_keycloak_user` during the C901 refactor (PR3c-3).

`create_keycloak_user` is the single entry-point used by the
WooCommerce webhook, the admin tenant create flow, the public
self-service registration, and the activation-code redemption to
provision Keycloak users with their tenant group and role mapping.

The original implementation inlined the user_data dict three times
and the 409-recovery branching twice, which made it both C901
(complexity 16) AND a refactor hazard. After the split the
duplication is gone and each branch lives in a small testable
helper. These tests pin the behavior so the existing UI flow keeps
working byte-for-byte.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def service(webhook_module):
    """The module-level singleton; we call instance methods on it."""
    return webhook_module.webhook_service


@pytest.fixture
def plan_info():
    return {
        "plan": "pro",
        "max_users": 5,
        "max_robots": 1,
        "max_sensors": 10,
        "code": "NEK-AB12-CD34-EF56",
    }


class TestBuildKeycloakUserPayload:
    """Pure function — must produce a stable shape for Keycloak's
    /admin/realms/.../users POST + PUT bodies."""

    def test_owner_payload_marks_created_by_activation_code(
        self, service, plan_info
    ):
        payload = service._build_keycloak_user_payload(
            "owner@example.test",
            "tenant-1",
            plan_info,
            "Alice",
            "Smith",
            is_owner=True,
        )
        assert payload["attributes"]["created_by"] == ["activation_code"]
        assert payload["attributes"]["is_owner"] == ["true"]

    def test_non_owner_payload_marks_created_by_tenant_admin(
        self, service, plan_info
    ):
        payload = service._build_keycloak_user_payload(
            "farmer@example.test",
            "tenant-1",
            plan_info,
            "Bob",
            "Jones",
            is_owner=False,
        )
        assert payload["attributes"]["created_by"] == ["tenant_admin"]
        assert payload["attributes"]["is_owner"] == ["false"]

    def test_attributes_serialize_quotas_as_strings(self, service, plan_info):
        """Keycloak attributes are list-of-string by spec; ints from
        plan_info MUST be converted via str() not left as ints."""
        payload = service._build_keycloak_user_payload(
            "x@example.test", "t1", plan_info, "X", "Y", is_owner=False
        )
        for key in ("max_users", "max_robots", "max_sensors"):
            assert payload["attributes"][key] == [str(plan_info[key])]
            assert isinstance(payload["attributes"][key][0], str)

    def test_payload_includes_username_email_enabled_verified(
        self, service, plan_info
    ):
        payload = service._build_keycloak_user_payload(
            "user@example.test", "t1", plan_info, "U", "S", is_owner=False
        )
        assert payload["username"] == "user@example.test"
        assert payload["email"] == "user@example.test"
        assert payload["enabled"] is True
        assert payload["emailVerified"] is True
        assert payload["firstName"] == "U"
        assert payload["lastName"] == "S"

    def test_missing_code_in_plan_info_defaults_to_empty(
        self, service, plan_info
    ):
        without_code = {k: v for k, v in plan_info.items() if k != "code"}
        payload = service._build_keycloak_user_payload(
            "x@example.test", "t1", without_code, "X", "Y", is_owner=False
        )
        assert payload["attributes"]["activation_code"] == [""]


class TestRecoverUserAfter409:
    """The 409 recovery path runs in TWO outer branches of
    `_resolve_user_id`. Pinning it once here covers both call sites."""

    def _ok_search(self, users):
        resp = MagicMock(status_code=200)
        resp.json.return_value = users
        return resp

    def test_returns_user_id_when_search_finds_user(self, service):
        with patch("requests.get", return_value=self._ok_search([{"id": "u-123"}])):
            user_id = service._recover_user_after_409(
                "http://kc/users",
                {"email": "x@example.test", "exact": "true"},
                {"H": "v"},
                "ignored",
                "x@example.test",
            )
        assert user_id == "u-123"

    def test_raises_when_search_finds_no_user(self, service):
        with patch("requests.get", return_value=self._ok_search([])):
            with pytest.raises(Exception, match="user not found"):
                service._recover_user_after_409(
                    "http://kc/users",
                    {"email": "x@example.test", "exact": "true"},
                    {"H": "v"},
                    "POST body that triggered 409",
                    "x@example.test",
                )

    def test_raises_when_search_itself_fails(self, service):
        bad_resp = MagicMock(status_code=500)
        with patch("requests.get", return_value=bad_resp):
            with pytest.raises(Exception, match="search failed"):
                service._recover_user_after_409(
                    "http://kc/users",
                    {"email": "x@example.test", "exact": "true"},
                    {"H": "v"},
                    "POST 409 body",
                    "x@example.test",
                )


class TestCreateUserOrRecover:
    """POST /users with 409 -> recover semantics."""

    def test_201_returns_user_id_from_location_header(self, service, plan_info):
        post_resp = MagicMock(status_code=201)
        post_resp.headers = {"Location": "http://kc/admin/realms/r/users/u-new"}
        with patch("requests.post", return_value=post_resp):
            user_id = service._create_user_or_recover(
                "http://kc/users",
                "http://kc/users",
                {"email": "x", "exact": "true"},
                {"username": "x"},
                {"H": "v"},
                "x@example.test",
            )
        assert user_id == "u-new"

    def test_409_falls_back_to_recovery(self, service):
        post_resp = MagicMock(status_code=409, text="conflict")
        recover_search_resp = MagicMock(status_code=200)
        recover_search_resp.json.return_value = [{"id": "u-recovered"}]
        with patch("requests.post", return_value=post_resp), \
             patch("requests.get", return_value=recover_search_resp):
            user_id = service._create_user_or_recover(
                "http://kc/users",
                "http://kc/users",
                {"email": "x", "exact": "true"},
                {"username": "x"},
                {"H": "v"},
                "x@example.test",
            )
        assert user_id == "u-recovered"

    def test_5xx_raises_for_status(self, service):
        post_resp = MagicMock(status_code=500)
        post_resp.raise_for_status.side_effect = Exception("HTTP 500")
        with patch("requests.post", return_value=post_resp):
            with pytest.raises(Exception, match="HTTP 500"):
                service._create_user_or_recover(
                    "http://kc/users",
                    "http://kc/users",
                    {"email": "x", "exact": "true"},
                    {"username": "x"},
                    {"H": "v"},
                    "x@example.test",
                )


class TestUpdateExistingKeycloakUser:
    """Best-effort PUT — must NEVER raise (call site doesn't catch)."""

    def test_204_logs_info_no_exception(self, service):
        ok = MagicMock(status_code=204)
        with patch("requests.put", return_value=ok), \
             patch.object(service, "_get_keycloak_base_url", return_value="http://kc"):
            service._update_existing_keycloak_user(
                "http://kc", "u-1", {"email": "x"}, {"H": "v"}, "x@example.test"
            )

    def test_500_logs_warning_no_exception(self, service):
        bad = MagicMock(status_code=500, text="server error")
        with patch("requests.put", return_value=bad), \
             patch("logging.Logger.warning") as mock_warn:
            service._update_existing_keycloak_user(
                "http://kc", "u-1", {"email": "x"}, {"H": "v"}, "x@example.test"
            )
        mock_warn.assert_called_once()


class TestResolveUserId:
    """Three branches to cover, matching the original four-branch
    structure of `create_keycloak_user`:

      1. Search 200 + non-empty -> reuse + PUT update
      2. Search 200 + empty     -> POST new
      3. Search 5xx             -> POST anyway (with same 409 fallback)
    """

    def _search_resp(self, status, body=None):
        r = MagicMock(status_code=status)
        if body is not None:
            r.json.return_value = body
        return r

    def test_existing_user_reused_and_updated(self, service):
        search = self._search_resp(200, [{"id": "u-existing"}])
        put_ok = MagicMock(status_code=204)
        with patch("requests.get", return_value=search), \
             patch("requests.put", return_value=put_ok) as mock_put, \
             patch("requests.post") as mock_post:
            user_id = service._resolve_user_id(
                "http://kc",
                {"email": "x@example.test"},
                "x@example.test",
                {"H": "v"},
            )
        assert user_id == "u-existing"
        mock_put.assert_called_once()
        mock_post.assert_not_called()

    def test_no_existing_user_creates_via_post(self, service):
        search = self._search_resp(200, [])
        post_resp = MagicMock(status_code=201)
        post_resp.headers = {"Location": "http://kc/users/u-new"}
        with patch("requests.get", return_value=search), \
             patch("requests.post", return_value=post_resp):
            user_id = service._resolve_user_id(
                "http://kc",
                {"email": "x@example.test"},
                "x@example.test",
                {"H": "v"},
            )
        assert user_id == "u-new"

    def test_search_failure_still_attempts_post(self, service):
        search_fail = self._search_resp(503)
        post_resp = MagicMock(status_code=201)
        post_resp.headers = {"Location": "http://kc/users/u-new"}
        with patch("requests.get", return_value=search_fail), \
             patch("requests.post", return_value=post_resp):
            user_id = service._resolve_user_id(
                "http://kc",
                {"email": "x@example.test"},
                "x@example.test",
                {"H": "v"},
            )
        assert user_id == "u-new"
