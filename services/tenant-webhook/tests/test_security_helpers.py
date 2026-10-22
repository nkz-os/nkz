"""Regression tests for the security helpers added in PR1a/PR1b/PR2b.

These helpers underpin the hardened auth and error-masking surface of
enhanced-tenant-webhook.py. They are tiny, pure functions but every
endpoint depends on at least one of them, so any silent regression here
re-introduces:

- Path-injection via tenant_id in subprocess/filesystem calls (PR1a)
- Fail-open webhooks when shared secrets are unconfigured (PR1a)
- Information disclosure in 5xx responses that leak `str(exc)` (PR2b)

The tests below exercise the real production code paths. The conftest's
heavy-mock setup keeps Redis/Keycloak/MongoDB/Kubernetes out of scope.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


@pytest.fixture
def helpers(webhook_module):
    """Bundle the helpers under test for compact access in test bodies."""
    return webhook_module


@pytest.fixture
def app_ctx(webhook_module):
    """Push a Flask request/app context — `_verify_shared_secret` reads
    `request.headers` and `_internal_error` calls `jsonify`, both of
    which require an active context."""
    with webhook_module.app.test_request_context("/"):
        yield


class TestIsValidTenantId:
    """`_is_valid_tenant_id` is the single guard that prevents tenant_id
    values received from Keycloak webhooks (and other untrusted sources)
    from being used as path components or subprocess arguments. Any
    relaxation of the regex re-opens the path-injection surface that PR1a
    closed."""

    @pytest.mark.parametrize(
        "tenant_id",
        [
            "a",
            "tenant1",
            "tenant_one",
            "tenant-one",
            "Tenant_One-123",
            "0abc",
            "a" * 64,
        ],
    )
    def test_accepts_valid_ids(self, helpers, tenant_id):
        assert helpers._is_valid_tenant_id(tenant_id) is True

    @pytest.mark.parametrize(
        "tenant_id",
        [
            "",
            "_underscore_first",
            "-dash-first",
            "../escape",
            "path/inside",
            "path\\inside",
            "name with space",
            "name.with.dot",
            "name$with$dollar",
            "name;with;semicolon",
            "name`with`backtick",
            "a" * 65,
        ],
    )
    def test_rejects_unsafe_strings(self, helpers, tenant_id):
        assert helpers._is_valid_tenant_id(tenant_id) is False

    @pytest.mark.parametrize(
        "tenant_id",
        [None, 123, 1.5, [], {}, b"bytes-not-str"],
    )
    def test_rejects_non_string_types(self, helpers, tenant_id):
        assert helpers._is_valid_tenant_id(tenant_id) is False

    def test_regex_compiled_constant_matches_function(self, helpers):
        """The exported `TENANT_ID_RE` is the canonical pattern; calls
        elsewhere in the file (or in future audits) may import the regex
        directly. Make sure both views agree."""
        for value in ("good_one", "Bad..value"):
            ok_re = bool(helpers.TENANT_ID_RE.match(value))
            ok_fn = helpers._is_valid_tenant_id(value)
            assert ok_re == ok_fn, value


class TestVerifySharedSecret:
    """`_verify_shared_secret` is the entry-point for every webhook
    auth check. It MUST fail-closed (503) when the env var is missing,
    rather than the legacy fail-open behavior where a missing secret
    silently disabled the auth check."""

    SECRET_ENV = "WEBHOOK_SECRET"
    HEADER = "X-Test-Secret"

    def test_unset_env_var_fails_closed_503(self, helpers, app_ctx):
        with patch.dict("os.environ", {self.SECRET_ENV: ""}, clear=False):
            ok, status, _ = helpers._verify_shared_secret(self.SECRET_ENV, self.HEADER)
        assert ok is False
        assert status == 503

    def test_unset_env_var_returns_diagnostic_message(self, helpers, app_ctx):
        with patch.dict("os.environ", {self.SECRET_ENV: ""}, clear=False):
            _, _, msg = helpers._verify_shared_secret(self.SECRET_ENV, self.HEADER)
        assert msg is not None
        assert self.SECRET_ENV in msg

    def test_missing_header_returns_401(self, helpers, webhook_module):
        with patch.dict("os.environ", {self.SECRET_ENV: "supersecret"}, clear=False):
            with webhook_module.app.test_request_context("/"):
                ok, status, _ = helpers._verify_shared_secret(self.SECRET_ENV, self.HEADER)
        assert ok is False
        assert status == 401

    def test_wrong_header_returns_401(self, helpers, webhook_module):
        with patch.dict("os.environ", {self.SECRET_ENV: "supersecret"}, clear=False):
            with webhook_module.app.test_request_context("/", headers={self.HEADER: "wrong"}):
                ok, status, _ = helpers._verify_shared_secret(self.SECRET_ENV, self.HEADER)
        assert ok is False
        assert status == 401

    def test_matching_header_returns_200(self, helpers, webhook_module):
        with patch.dict("os.environ", {self.SECRET_ENV: "supersecret"}, clear=False):
            with webhook_module.app.test_request_context("/", headers={self.HEADER: "supersecret"}):
                ok, status, msg = helpers._verify_shared_secret(self.SECRET_ENV, self.HEADER)
        assert ok is True
        assert status == 200
        assert msg is None

    def test_whitespace_in_header_is_stripped(self, helpers, webhook_module):
        """The helper strips both env var and header to tolerate accidental
        trailing whitespace from operators editing K8s secrets / curl
        commands. This is a deliberate convenience, not a security hole."""
        with patch.dict("os.environ", {self.SECRET_ENV: "  supersecret  "}, clear=False):
            with webhook_module.app.test_request_context(
                "/", headers={self.HEADER: "  supersecret  "}
            ):
                ok, status, _ = helpers._verify_shared_secret(self.SECRET_ENV, self.HEADER)
        assert ok is True
        assert status == 200


class TestInternalError:
    """`_internal_error` replaces every 5xx site that previously did
    `jsonify({"error": str(e)}), 500`. The contract: response body never
    leaks the exception text, but a request_id correlates the client
    report with the full traceback in the server logs."""

    def test_returns_tuple_with_default_status_500(self, helpers, app_ctx):
        resp, status = helpers._internal_error(RuntimeError("boom"), "test_ctx")
        assert status == 500

    def test_response_body_does_not_leak_exception_text(self, helpers, app_ctx):
        secret_in_exc = "DB_PASSWORD=hunter2_in_traceback"
        resp, _ = helpers._internal_error(RuntimeError(secret_in_exc), "test_ctx")
        body = json.loads(resp.get_data(as_text=True))
        assert secret_in_exc not in json.dumps(body)
        assert "RuntimeError" not in json.dumps(body)

    def test_response_carries_request_id_and_user_message(self, helpers, app_ctx):
        resp, _ = helpers._internal_error(ValueError("x"), "test_ctx")
        body = json.loads(resp.get_data(as_text=True))
        assert body.get("error") == "Internal server error"
        assert isinstance(body.get("request_id"), str)
        assert len(body["request_id"]) >= 8

    def test_each_call_has_unique_request_id(self, helpers, app_ctx):
        seen = set()
        for _ in range(20):
            resp, _ = helpers._internal_error(ValueError("x"), "ctx")
            body = json.loads(resp.get_data(as_text=True))
            seen.add(body["request_id"])
        assert len(seen) == 20

    def test_custom_user_message_overrides_default(self, helpers, app_ctx):
        resp, _ = helpers._internal_error(
            ValueError("internal"), "ctx", user_message="Custom user-facing message"
        )
        body = json.loads(resp.get_data(as_text=True))
        assert body["error"] == "Custom user-facing message"

    def test_custom_status_overrides_default(self, helpers, app_ctx):
        _, status = helpers._internal_error(ValueError("x"), "ctx", status=503)
        assert status == 503

    def test_extra_dict_merges_into_body(self, helpers, app_ctx):
        resp, _ = helpers._internal_error(ValueError("x"), "ctx", extra={"hint": "see docs"})
        body = json.loads(resp.get_data(as_text=True))
        assert body.get("hint") == "see docs"
        assert body.get("error") == "Internal server error"

    def test_logs_full_exception_internally(self, helpers, app_ctx):
        """The exception details that DON'T appear in the response MUST
        appear in the server log so support can diagnose the issue."""
        with patch.object(helpers.logger, "error") as mock_error:
            helpers._internal_error(RuntimeError("internal-detail-xyz"), "diag_ctx")
        mock_error.assert_called_once()
        call_args, call_kwargs = mock_error.call_args
        log_line = call_args[0]
        assert "diag_ctx" in log_line
        assert "internal-detail-xyz" in log_line
        assert call_kwargs.get("exc_info") is True


class TestDeleteKeycloakUserBestEffort:
    """`_delete_keycloak_user_best_effort` is the compensation for the
    Keycloak-first ordering in `accept_invitation` (PR1b). It must NEVER
    raise, otherwise a partial-success path becomes a 500 to the client
    and the orphan is invisible."""

    def test_no_token_does_not_raise(self, helpers, app_ctx):
        """When `get_keycloak_token()` returns None the helper logs and
        returns; no exception bubbles up."""
        with patch.object(helpers.webhook_service, "get_keycloak_token", return_value=None):
            helpers._delete_keycloak_user_best_effort("user-uuid", "test-ctx")

    def test_request_failure_does_not_raise(self, helpers, app_ctx):
        """A 5xx from Keycloak or a connection failure must be swallowed."""
        with (
            patch.object(helpers.webhook_service, "get_keycloak_token", return_value="t"),
            patch.object(
                helpers.webhook_service,
                "_get_keycloak_base_url",
                return_value="http://test",
            ),
            patch("requests.delete", side_effect=ConnectionError("boom")),
        ):
            helpers._delete_keycloak_user_best_effort("user-uuid", "test-ctx")

    def test_404_treated_as_success(self, helpers, app_ctx):
        """A 404 (user already gone) is logged as success — operator does
        not need to chase it."""

        class _Resp:
            status_code = 404
            text = ""

        with (
            patch.object(helpers.webhook_service, "get_keycloak_token", return_value="t"),
            patch.object(
                helpers.webhook_service,
                "_get_keycloak_base_url",
                return_value="http://test",
            ),
            patch("requests.delete", return_value=_Resp()),
            patch.object(helpers.logger, "info") as mock_info,
        ):
            helpers._delete_keycloak_user_best_effort("user-uuid", "test-ctx")
        mock_info.assert_called()
