"""Per-tenant n8n runs user-authored workflows; a Webhook node can read the
headers of any request that reaches it. The gateway forwards only n8n's own
cookies (never the platform session cookie), and scopes n8n's Set-Cookie
answers to the tenant path prefix so one tenant's n8n session is not sent to
another tenant's URLs on the shared host."""
from unittest.mock import MagicMock, patch

import pytest

import fiware_api_gateway as gw


def _upstream(set_cookies=()):
    r = MagicMock()
    r.status_code = 200
    r.content = b"ok"
    r.headers = {"Content-Type": "text/plain"}
    r.raw.headers.getlist.return_value = list(set_cookies)
    return r


def test_n8n_proxy_does_not_forward_platform_cookie(client, monkeypatch):
    monkeypatch.setattr(gw, "N8N_PROXY_HOST", "n8n.test")
    client.set_cookie("nkz_token", "session-secret", domain="n8n.test")
    with patch.object(gw.requests, "request", return_value=_upstream()) as m:
        resp = client.get("/acme/", base_url="http://n8n.test")
    assert resp.status_code == 200
    kwargs = m.call_args.kwargs
    assert "nkz_token" not in (kwargs.get("cookies") or {})
    assert "Cookie" not in kwargs.get("headers", {})


def test_n8n_proxy_forwards_only_n8n_cookies(client, monkeypatch):
    monkeypatch.setattr(gw, "N8N_PROXY_HOST", "n8n.test")
    client.set_cookie("nkz_token", "secret", domain="n8n.test")
    client.set_cookie("n8n-auth", "sess", domain="n8n.test")
    with patch.object(gw.requests, "request", return_value=_upstream()) as m:
        resp = client.get("/acme/", base_url="http://n8n.test")
    assert resp.status_code == 200
    assert m.call_args.kwargs.get("cookies") == {"n8n-auth": "sess"}


def test_n8n_proxy_scopes_set_cookie_to_tenant_path(client, monkeypatch):
    monkeypatch.setattr(gw, "N8N_PROXY_HOST", "n8n.test")
    upstream = _upstream(
        ["n8n-auth=abc; Path=/; HttpOnly", "n8n-x=1; path=/; SameSite=Lax"]
    )
    with patch.object(gw.requests, "request", return_value=upstream):
        resp = client.get("/acme/", base_url="http://n8n.test")
    assert resp.status_code == 200
    assert resp.headers.getlist("Set-Cookie") == [
        "n8n-auth=abc; Path=/acme/; HttpOnly",
        "n8n-x=1; path=/acme/; SameSite=Lax",
    ]


def test_n8n_proxy_keeps_narrower_cookie_path_unchanged(client, monkeypatch):
    monkeypatch.setattr(gw, "N8N_PROXY_HOST", "n8n.test")
    upstream = _upstream(["n8n-y=1; Path=/rest; HttpOnly"])
    with patch.object(gw.requests, "request", return_value=upstream):
        resp = client.get("/acme/", base_url="http://n8n.test")
    assert resp.headers.getlist("Set-Cookie") == ["n8n-y=1; Path=/rest; HttpOnly"]


@pytest.mark.parametrize(
    "path",
    [
        "/;acme/rest/login",
        "/acme'+(!![]+[])+'/static/base-path.js",
        "/ACME/",
    ],
)
def test_n8n_proxy_rejects_unsafe_tenant_segment(client, monkeypatch, path):
    monkeypatch.setattr(gw, "N8N_PROXY_HOST", "n8n.test")
    upstream = _upstream(["n8n-auth=abc; Path=/; HttpOnly"])
    with patch.object(gw.requests, "request", return_value=upstream) as m:
        resp = client.get(path, base_url="http://n8n.test")
    assert resp.status_code == 404
    assert not m.called
    assert not resp.headers.getlist("Set-Cookie")


def test_n8n_proxy_still_serves_valid_tenant_segment(client, monkeypatch):
    monkeypatch.setattr(gw, "N8N_PROXY_HOST", "n8n.test")
    with patch.object(gw.requests, "request", return_value=_upstream()) as m:
        resp = client.get("/acme/", base_url="http://n8n.test")
    assert resp.status_code == 200
    assert m.call_args.kwargs["url"] == "http://n8n-acme-service:5678/"
