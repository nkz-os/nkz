"""A caller must not register a device under another user or tenant."""
from unittest.mock import MagicMock, patch

import fiware_api_gateway as gw


def _ok():
    r = MagicMock()
    r.status_code = 200
    r.content = b'{"ok": true}'
    r.headers = {"Content-Type": "application/json"}
    return r


def test_register_overrides_identity_from_token(client, monkeypatch):
    monkeypatch.setattr(
        gw, "validate_jwt_token", lambda token, **kw: {"sub": "user-1", "tenant_id": "acme"}
    )
    with patch.object(gw.requests, "post", return_value=_ok()) as m:
        resp = client.post(
            "/api/push/register",
            json={
                "token": "ExponentPushToken[x]",
                "platform": "ios",
                "user_id": "victim",
                "tenant_id": "other-tenant",
            },
            headers={"Authorization": "Bearer t"},
        )
    assert resp.status_code == 200
    sent = m.call_args.kwargs["json"]
    assert sent["user_id"] == "user-1"
    assert sent["tenant_id"] == "acme"
    assert sent["token"] == "ExponentPushToken[x]"


def test_register_rejects_non_object_body(client, monkeypatch):
    monkeypatch.setattr(
        gw, "validate_jwt_token", lambda token, **kw: {"sub": "user-1", "tenant_id": "acme"}
    )
    resp = client.post(
        "/api/push/register",
        json=["not", "an", "object"],
        headers={"Authorization": "Bearer t"},
    )
    assert resp.status_code == 400
