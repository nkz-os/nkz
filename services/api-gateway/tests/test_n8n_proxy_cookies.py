"""Per-tenant n8n runs user-authored workflows; a Webhook node can read the
headers of any request that reaches it. The gateway must therefore never
forward the browser's cookies (the platform session cookie among them)."""
from unittest.mock import MagicMock, patch

import fiware_api_gateway as gw


def _upstream():
    r = MagicMock()
    r.status_code = 200
    r.content = b"ok"
    r.headers = {"Content-Type": "text/plain"}
    return r


def test_n8n_proxy_does_not_forward_cookies(client, monkeypatch):
    monkeypatch.setattr(gw, "N8N_PROXY_HOST", "n8n.test")
    client.set_cookie("nkz_token", "session-secret", domain="n8n.test")
    with patch.object(gw.requests, "request", return_value=_upstream()) as m:
        resp = client.get("/acme/", base_url="http://n8n.test")
    assert resp.status_code == 200
    kwargs = m.call_args.kwargs
    assert not kwargs.get("cookies")
    assert "Cookie" not in kwargs.get("headers", {})
