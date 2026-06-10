"""Regression tests for the /webhook/keycloak endpoint and the helpers
extracted from `keycloak_webhook` during the C901 refactor (PR3c-2).

Three layers:

1. `_authenticate_keycloak_webhook`: pure auth, fail-closed contract.
2. `_dispatch_keycloak_event`: dispatch table behavior.
3. `keycloak_webhook` end-to-end via Flask test client: verifies the
   `@app.route("/webhook/keycloak", methods=["POST"])` decorator stays
   bound to the public function and not to one of the new helpers — a
   real bug introduced (and caught) during the refactor itself.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


@pytest.fixture
def helpers(webhook_module):
    return webhook_module


@pytest.fixture
def client(webhook_module):
    return webhook_module.app.test_client()


class TestAuthenticateKeycloakWebhook:
    """Helper-level auth contract. The endpoint must NEVER process a
    request when the shared secret is unset, regardless of headers."""

    def test_unset_secret_returns_503(self, helpers, webhook_module):
        with patch.object(webhook_module, "WEBHOOK_SECRET", ""):
            with webhook_module.app.test_request_context(
                "/", headers={"Authorization": "Bearer anything"}
            ):
                resp = helpers._authenticate_keycloak_webhook()
        assert resp is not None
        body, status = resp
        assert status == 503

    def test_missing_bearer_prefix_returns_401(self, helpers, webhook_module):
        with patch.object(webhook_module, "WEBHOOK_SECRET", "secret"):
            with webhook_module.app.test_request_context(
                "/", headers={"Authorization": "secret"}
            ):
                resp = helpers._authenticate_keycloak_webhook()
        assert resp is not None
        _, status = resp
        assert status == 401

    def test_no_authorization_header_returns_401(self, helpers, webhook_module):
        with patch.object(webhook_module, "WEBHOOK_SECRET", "secret"):
            with webhook_module.app.test_request_context("/"):
                resp = helpers._authenticate_keycloak_webhook()
        assert resp is not None
        _, status = resp
        assert status == 401

    def test_wrong_token_returns_401(self, helpers, webhook_module):
        with patch.object(webhook_module, "WEBHOOK_SECRET", "secret"):
            with webhook_module.app.test_request_context(
                "/", headers={"Authorization": "Bearer wrong"}
            ):
                resp = helpers._authenticate_keycloak_webhook()
        assert resp is not None
        _, status = resp
        assert status == 401

    def test_correct_token_returns_none(self, helpers, webhook_module):
        with patch.object(webhook_module, "WEBHOOK_SECRET", "secret"):
            with webhook_module.app.test_request_context(
                "/", headers={"Authorization": "Bearer secret"}
            ):
                resp = helpers._authenticate_keycloak_webhook()
        assert resp is None


class TestDispatchKeycloakEvent:
    """Dispatch table maps event types to module-level handler
    functions. Patching the handler lets us assert delegation without
    pulling in DB/Keycloak machinery."""

    def test_unknown_event_returns_200_not_handled(self, helpers, webhook_module):
        with webhook_module.app.test_request_context("/"):
            resp = helpers._dispatch_keycloak_event(
                "SOME_RANDOM_TYPE", "tenant1", {}
            )
        body, status = resp
        assert status == 200
        assert b"not handled" in body.get_data()

    def test_tenant_created_routes_to_handler(self, helpers, webhook_module):
        sentinel = ({"ok": True}, 201)
        with patch.object(
            webhook_module, "handle_tenant_created", return_value=sentinel
        ) as mock:
            result = helpers._dispatch_keycloak_event(
                "TENANT_CREATED", "tenant1", {"foo": "bar"}
            )
        mock.assert_called_once_with("tenant1", {"foo": "bar"})
        assert result is sentinel

    def test_tenant_updated_routes_to_handler(self, helpers, webhook_module):
        sentinel = ({}, 202)
        with patch.object(
            webhook_module, "handle_tenant_updated", return_value=sentinel
        ) as mock:
            result = helpers._dispatch_keycloak_event(
                "TENANT_UPDATED", "tenant1", {}
            )
        mock.assert_called_once()
        assert result is sentinel

    def test_tenant_deleted_routes_to_handler(self, helpers, webhook_module):
        sentinel = ({}, 200)
        with patch.object(
            webhook_module, "handle_tenant_deleted", return_value=sentinel
        ) as mock:
            result = helpers._dispatch_keycloak_event(
                "TENANT_DELETED", "tenant1", {}
            )
        mock.assert_called_once()
        assert result is sentinel


class TestKeycloakWebhookEndpoint:
    """End-to-end via Flask test client. Verifies the route decorator
    stays bound to the public `keycloak_webhook` function (the C901
    refactor accidentally moved it to a helper at one point — these
    tests would have caught that)."""

    def _post(self, client, body, *, token="secret"):
        return client.post(
            "/webhook/keycloak",
            data=json.dumps(body),
            content_type="application/json",
            headers={"Authorization": f"Bearer {token}"},
        )

    def test_endpoint_is_registered_under_public_name(self, webhook_module):
        endpoint_names = {
            rule.endpoint for rule in webhook_module.app.url_map.iter_rules()
            if rule.rule == "/webhook/keycloak"
        }
        assert "keycloak_webhook" in endpoint_names, (
            f"Route /webhook/keycloak is not bound to keycloak_webhook. "
            f"Bound to: {endpoint_names}"
        )

    def test_unset_secret_returns_503(self, webhook_module, client):
        with patch.object(webhook_module, "WEBHOOK_SECRET", ""):
            resp = self._post(client, {"type": "TENANT_CREATED", "tenant_id": "t1"})
        assert resp.status_code == 503

    def test_missing_bearer_returns_401(self, webhook_module, client):
        with patch.object(webhook_module, "WEBHOOK_SECRET", "secret"):
            resp = client.post(
                "/webhook/keycloak",
                data=json.dumps({"type": "TENANT_CREATED", "tenant_id": "t1"}),
                content_type="application/json",
            )
        assert resp.status_code == 401

    def test_empty_payload_returns_400(self, webhook_module, client):
        with patch.object(webhook_module, "WEBHOOK_SECRET", "secret"):
            resp = client.post(
                "/webhook/keycloak",
                data="null",
                content_type="application/json",
                headers={"Authorization": "Bearer secret"},
            )
        assert resp.status_code == 400

    def test_missing_tenant_id_returns_400(self, webhook_module, client):
        with patch.object(webhook_module, "WEBHOOK_SECRET", "secret"):
            resp = self._post(client, {"type": "TENANT_CREATED"})
        assert resp.status_code == 400

    def test_invalid_tenant_id_returns_400(self, webhook_module, client):
        with patch.object(webhook_module, "WEBHOOK_SECRET", "secret"):
            resp = self._post(
                client, {"type": "TENANT_CREATED", "tenant_id": "../escape"}
            )
        assert resp.status_code == 400

    def test_unknown_event_type_returns_200(self, webhook_module, client):
        with patch.object(webhook_module, "WEBHOOK_SECRET", "secret"):
            resp = self._post(
                client, {"type": "ZZZ_UNKNOWN", "tenant_id": "tenant1"}
            )
        assert resp.status_code == 200
        assert b"not handled" in resp.data

    def test_valid_event_routes_to_handler(self, webhook_module, client):
        # `jsonify` requires an active app context, which only exists
        # while the request is being dispatched. Building the response
        # via `side_effect` defers construction to that moment.
        def _stub_handler(_tenant_id, _payload):
            return webhook_module.jsonify({"ok": True}), 201

        with patch.object(webhook_module, "WEBHOOK_SECRET", "secret"), \
             patch.object(
                 webhook_module,
                 "handle_tenant_created",
                 side_effect=_stub_handler,
             ) as mock:
            resp = self._post(
                client, {"type": "TENANT_CREATED", "tenant_id": "tenant1"}
            )
        assert resp.status_code == 201
        mock.assert_called_once()
        args, _ = mock.call_args
        assert args[0] == "tenant1"
