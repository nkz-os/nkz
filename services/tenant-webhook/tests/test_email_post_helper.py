"""All email-service calls carry the internal secret header.

Two layers: an AST guard over every module that talks to email-service
(no call site can reach it except through a path that sends the header), and
behaviour tests that the header is actually on the wire.
"""
import ast
import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SVC_DIR = Path(__file__).resolve().parents[1]
_WEBHOOK_SRC = _SVC_DIR / "enhanced-tenant-webhook.py"
_NOTIFIER_SRC = _SVC_DIR / "expiration_notifier.py"

_HTTP_METHODS = {"post", "request", "put", "patch"}
_EMAIL_TOKENS = ("EMAIL_SERVICE_URL", "email_service_url", "/send/", "/email/")
_CONTEXT_LINES = 5


class _RequestsCalls(ast.NodeVisitor):
    """Collect requests.<post|request|put|patch>(...) calls with their innermost function."""

    def __init__(self):
        self._stack = []
        self.calls = []

    def _enter(self, node):
        self._stack.append(node)
        self.generic_visit(node)
        self._stack.pop()

    visit_FunctionDef = _enter
    visit_AsyncFunctionDef = _enter

    def visit_Call(self, node):
        f = node.func
        if (
            isinstance(f, ast.Attribute)
            and f.attr in _HTTP_METHODS
            and isinstance(f.value, ast.Name)
            and f.value.id == "requests"
        ):
            self.calls.append((node, self._stack[-1] if self._stack else None))
        self.generic_visit(node)


def _sends_internal_headers(src: str, call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "headers":
            return "internal_service_headers" in (ast.get_source_segment(src, kw.value) or "")
    return False


def _email_call_scan(src: str, *, helper_name: str | None):
    """Return (violations, email_calls_seen).

    An HTTP call counts as an email-service call when its own source, or the
    5 lines before it inside the same function (a ``url = ...`` local), name
    the email-service URL or one of its routes. Such a call is allowed only
    inside ``helper_name``; and wherever it is allowed it must pass
    ``headers=internal_service_headers(...)``.
    """
    lines = src.splitlines()
    collector = _RequestsCalls()
    collector.visit(ast.parse(src))
    violations, seen = [], 0
    for call, fn in collector.calls:
        floor = fn.body[0].lineno if fn is not None else 1
        start = max(floor, call.lineno - _CONTEXT_LINES)
        text = (ast.get_source_segment(src, call) or "") + "\n" + "\n".join(lines[start - 1 : call.lineno - 1])
        if not any(token in text for token in _EMAIL_TOKENS):
            continue
        seen += 1
        where = f"line {call.lineno} in {fn.name if fn is not None else '<module>'}"
        if helper_name is not None and (fn is None or fn.name != helper_name):
            violations.append(f"{where}: email-service call outside {helper_name}")
        elif not _sends_internal_headers(src, call):
            violations.append(f"{where}: email-service call without internal_service_headers")
    return violations, seen


def test_webhook_email_calls_only_through_helper():
    src = _WEBHOOK_SRC.read_text()
    violations, seen = _email_call_scan(src, helper_name="_email_post")
    assert violations == []
    assert seen == 1  # the helper's own requests.post, nothing else


def test_notifier_email_call_sends_internal_headers():
    src = _NOTIFIER_SRC.read_text()
    violations, seen = _email_call_scan(src, helper_name=None)
    assert violations == []
    assert seen >= 1


_BYPASSES = {
    "fstring first arg": 'def f():\n    return requests.post(f"{EMAIL_SERVICE_URL}/send/x", json={})\n',
    "url local": 'def f():\n    url = f"{EMAIL_SERVICE_URL}/send/x"\n    return requests.post(url, json={})\n',
    "concatenation": "def f(path):\n    return requests.post(EMAIL_SERVICE_URL + path, json={})\n",
    "format": 'def f():\n    return requests.post("{}/x".format(email_service_url), json={})\n',
    "request()": 'def f():\n    return requests.request("POST", f"{EMAIL_SERVICE_URL}/x", json={})\n',
    "put": 'def f():\n    return requests.put(f"{EMAIL_SERVICE_URL}/x", json={})\n',
    "patch": 'def f():\n    return requests.patch(f"{EMAIL_SERVICE_URL}/x", json={})\n',
    "literal route": 'def f():\n    return requests.post("http://mail:5000/send/welcome", json={})\n',
}


@pytest.mark.parametrize("snippet", list(_BYPASSES.values()), ids=list(_BYPASSES))
def test_guard_catches_bypass(snippet):
    violations, seen = _email_call_scan(snippet, helper_name="_email_post")
    assert seen == 1
    assert len(violations) == 1


def test_guard_allows_helper_with_headers_and_unrelated_posts():
    ok = (
        "def _email_post(path, payload):\n"
        '    url = f"{EMAIL_SERVICE_URL}{path}"\n'
        '    return requests.post(url, json=payload, headers=internal_service_headers("x"))\n'
        "def keycloak(token_url):\n"
        "    return requests.post(token_url, data={})\n"
    )
    assert _email_call_scan(ok, helper_name="_email_post") == ([], 1)


def test_guard_rejects_helper_without_headers():
    bad = 'def _email_post(path, payload):\n    return requests.post(f"{EMAIL_SERVICE_URL}{path}", json=payload)\n'
    violations, seen = _email_call_scan(bad, helper_name="_email_post")
    assert seen == 1 and len(violations) == 1


def test_guard_notifier_mode_requires_headers():
    without = 'def send():\n    return requests.post(f"{EMAIL_SERVICE_URL}/send/expiration", json={})\n'
    with_headers = (
        "def send():\n"
        '    return requests.post(f"{EMAIL_SERVICE_URL}/send/expiration", json={}, '
        'headers=internal_service_headers("n"))\n'
    )
    assert len(_email_call_scan(without, helper_name=None)[0]) == 1
    assert _email_call_scan(with_headers, helper_name=None) == ([], 1)


def test_email_post_sends_internal_secret(webhook_module, monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "s")
    with patch.object(webhook_module.requests, "post", return_value=MagicMock(status_code=200)) as m:
        webhook_module._email_post("/send/welcome", {"email": "a@b.c"}, timeout=7)
    assert m.call_args.args[0].endswith("/send/welcome")
    assert m.call_args.kwargs["headers"]["X-Internal-Service-Secret"] == "s"
    assert m.call_args.kwargs["json"] == {"email": "a@b.c"}
    assert m.call_args.kwargs["timeout"] == 7


def test_expiration_notifier_sends_internal_secret(monkeypatch):
    # conftest already puts the service dir and services/ on sys.path; the
    # module reads POSTGRES_URL from the environment at import time.
    monkeypatch.setenv("POSTGRES_URL", "postgresql://unused")
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "s")
    notifier = importlib.import_module("expiration_notifier")
    activation = {
        "email": "a@b.c",
        "farmer_name": "n",
        "days_remaining": 7,
        "expires_at": "2030-01-01",
        "plan": "p",
        "tenant": "t",
    }
    with patch.object(notifier.requests, "post", return_value=MagicMock(status_code=200)) as m:
        assert notifier.send_expiration_notification(activation) is True
    assert m.call_args.args[0].endswith("/send/expiration")
    assert m.call_args.kwargs["headers"]["X-Internal-Service-Secret"] == "s"
