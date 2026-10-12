"""Registration OTP uses the authenticated Redis URL, and logs never carry it.

Redis requires a password. The OTP store must connect with the same REDIS_URL
the rate limiter uses; a URL built from the host alone fails with
"Authentication required" and breaks self-registration.
"""
import ast
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

_SRC = Path(__file__).resolve().parents[1] / "enhanced-tenant-webhook.py"
AUTH_URL = "redis://:s3cret@redis-test:6379/0"


def _fake_redis(monkeypatch, client):
    fake = types.ModuleType("redis")
    fake.Redis = MagicMock()
    fake.Redis.from_url = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "redis", fake)
    return fake.Redis.from_url


def test_request_otp_stores_code_with_authenticated_url(webhook_module, monkeypatch):
    monkeypatch.setattr(webhook_module, "REDIS_URL", AUTH_URL)
    store = MagicMock()
    from_url = _fake_redis(monkeypatch, store)
    no_users = MagicMock(status_code=200)
    no_users.json.return_value = []
    with patch.object(webhook_module.webhook_service, "get_keycloak_token", return_value="t"), \
         patch.object(webhook_module.webhook_service, "_get_keycloak_base_url", return_value="http://kc"), \
         patch.object(webhook_module.requests, "get", return_value=no_users), \
         patch.object(webhook_module, "_email_post", return_value=MagicMock(status_code=200)):
        resp = webhook_module.app.test_client().post(
            "/webhook/register/request-otp", json={"email": "a@example.org"}
        )
    assert resp.status_code == 200
    from_url.assert_called_once_with(AUTH_URL, decode_responses=True)
    key, ttl, _code = store.setex.call_args.args
    assert (key, ttl) == ("registro:otp:a@example.org", 900)


def test_register_verifies_code_with_authenticated_url(webhook_module, monkeypatch):
    monkeypatch.setattr(webhook_module, "REDIS_URL", AUTH_URL)
    store = MagicMock()
    store.get.return_value = None  # no code stored → rejected before any provisioning
    from_url = _fake_redis(monkeypatch, store)
    resp = webhook_module.app.test_client().post(
        "/webhook/register",
        json={"email": "a@example.org", "organization_name": "o", "password": "p", "otp": "123456"},
    )
    from_url.assert_called_once_with(AUTH_URL, decode_responses=True)
    store.get.assert_called_once_with("registro:otp:a@example.org")
    assert resp.status_code == 401


def _logged_names(call: ast.Call) -> set:
    return {n.id for arg in call.args for n in ast.walk(arg) if isinstance(n, ast.Name)}


def test_no_log_call_formats_redis_credentials():
    tree = ast.parse(_SRC.read_text())
    leaks = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logger"
        and _logged_names(node) & {"REDIS_URL", "redis_password", "encoded_pass"}
    ]
    assert leaks == [], f"logger call formats Redis credentials at lines {leaks}"
