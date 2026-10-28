"""Every email-service path tenant-webhook calls is a route email-service serves.

Calls to paths email-service does not have answer 404 and the email is lost
silently (callers treat email as best-effort and only log).
"""
import ast
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

_SERVICES = Path(__file__).resolve().parents[2]
_EMAIL_SRC = _SERVICES / "email-service" / "email_service.py"
_CALLERS = [
    _SERVICES / "tenant-webhook" / "enhanced-tenant-webhook.py",
    _SERVICES / "tenant-webhook" / "expiration_notifier.py",
]


def _email_service_routes() -> set:
    return set(re.findall(r"@app\.route\(\s*['\"]([^'\"]+)['\"]", _EMAIL_SRC.read_text()))


def _called_paths(src: str) -> set:
    paths = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in ("_email_post", "_send_email_async") and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                paths.add(first.value)
        for sub in ast.walk(node):
            if isinstance(sub, ast.JoinedStr):
                for part in sub.values:
                    if isinstance(part, ast.Constant) and re.match(r"^/(send|email)\b", str(part.value)):
                        paths.add(part.value)
    return paths


def test_called_paths_are_served():
    routes = _email_service_routes()
    called = set().union(*(_called_paths(p.read_text()) for p in _CALLERS))
    assert called, "no email-service calls found; the scan is broken"
    missing = sorted(called - routes)
    assert missing == [], f"email-service has no route for {missing}"


def test_purge_confirmation_uses_notification_route(webhook_module, monkeypatch):
    sent = MagicMock(return_value=MagicMock(status_code=200))
    monkeypatch.setattr(webhook_module, "_email_post", sent)
    monkeypatch.delenv("EMAIL_SERVICE_URL", raising=False)
    with webhook_module.app.test_request_context():
        webhook_module.g.current_user = {"email": "admin@example.org", "preferred_username": "admin"}
        with patch.object(webhook_module, "audit_log"):
            webhook_module._purge_phase_close("acme")
    path, payload = sent.call_args.args[:2]
    assert path == "/send/notification"
    assert payload["email"] == "admin@example.org"
    assert {"farmer_name", "notification_type", "message"} <= payload.keys()
    assert "acme" in payload["message"]


def test_tenant_creation_sends_support_welcome():
    """Both tenant-creation flows mail the creating admin the support welcome."""
    src = (_SERVICES / "tenant-webhook" / "enhanced-tenant-webhook.py").read_text()
    funcs = {n.name: n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)}
    for name in ("activate_tenant", "register_tenant"):
        paths = {
            c.args[0].value
            for c in ast.walk(funcs[name])
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
            and c.func.id == "_send_email_async" and c.args
            and isinstance(c.args[0], ast.Constant)
        }
        assert "/email/support-welcome" in paths, name
