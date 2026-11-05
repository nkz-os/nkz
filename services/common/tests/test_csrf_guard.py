"""Tests for common.csrf_guard."""

from __future__ import annotations

import importlib.util
import logging
import os

import pytest
from flask import Flask

_path = os.path.join(os.path.dirname(__file__), "..", "csrf_guard.py")
_spec = importlib.util.spec_from_file_location("csrf_guard_under_test", _path)
assert _spec is not None and _spec.loader is not None
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

ALLOWED = frozenset({"https://app.invalid"})
EVIL = "https://evil.invalid"
LOGGER = "csrf_guard_under_test"


@pytest.mark.parametrize(
    "method,authorization,cookie,origin,expected",
    [
        ("GET", None, True, EVIL, False),
        ("HEAD", None, True, EVIL, False),
        ("OPTIONS", None, True, EVIL, False),
        ("POST", "Bearer t", True, EVIL, False),
        # Token extraction falls back to the cookie unless the scheme is Bearer.
        ("POST", "Basic x", True, EVIL, True),
        ("POST", "", True, EVIL, True),
        ("POST", None, False, EVIL, False),
        ("POST", None, True, None, False),
        ("POST", None, True, "https://app.invalid", False),
        ("POST", None, True, "null", True),
        ("POST", None, True, EVIL, True),
        ("PUT", None, True, EVIL, True),
        ("PATCH", None, True, EVIL, True),
        ("DELETE", None, True, EVIL, True),
    ],
)
def test_decision(method, authorization, cookie, origin, expected):
    assert (
        guard.is_cross_site_cookie_mutation(method, authorization, cookie, origin, ALLOWED)
        is expected
    )


def test_parse_trims_and_drops_wildcard():
    raw = " https://a.invalid , *,, https://b.invalid"
    assert guard.parse_allowed_origins(raw) == {"https://a.invalid", "https://b.invalid"}


@pytest.mark.parametrize("raw", [None, "", "  ", "*"])
def test_parse_without_usable_origin_is_empty(raw):
    assert guard.parse_allowed_origins(raw) == frozenset()


@pytest.mark.parametrize(
    "raw,expected",
    [(None, True), ("", True), ("true", True), ("false", False), ("FALSE", False), (" false ", False)],
)
def test_enforce_enabled(raw, expected):
    assert guard.enforce_enabled(raw) is expected


def _client(monkeypatch, origins="https://app.invalid", enforce=None):
    if origins is None:
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("CORS_ORIGINS", origins)
    if enforce is None:
        monkeypatch.delenv("CSRF_GUARD_ENFORCE", raising=False)
    else:
        monkeypatch.setenv("CSRF_GUARD_ENFORCE", enforce)
    app = Flask(__name__)
    guard.install_csrf_guard(app, "svc-a")
    app.calls = []

    @app.before_request
    def _later_hook():
        app.calls.append("hook")

    @app.route("/things", methods=["GET", "POST"])
    def things():
        app.calls.append("handler")
        return "ok", 200

    return app, app.test_client()


def _post(client, origin=EVIL, cookie="t", **headers):
    if cookie is not None:
        client.set_cookie("nkz_token", cookie)
    if origin is not None:
        headers["Origin"] = origin
    return client.post("/things", headers=headers)


def test_enforce_rejects_with_exact_body(monkeypatch):
    _, client = _client(monkeypatch)
    resp = _post(client)
    assert resp.status_code == 403
    assert resp.get_json() == {"error": "Cross-site request rejected"}


def test_rejected_request_runs_no_later_hook(monkeypatch):
    app, client = _client(monkeypatch)
    _post(client)
    assert app.calls == []


def test_allowed_origin_reaches_handler(monkeypatch):
    app, client = _client(monkeypatch)
    assert _post(client, origin="https://app.invalid").status_code == 200
    assert app.calls == ["hook", "handler"]


def test_empty_cookie_counts_as_cookie_auth(monkeypatch):
    _, client = _client(monkeypatch)
    assert _post(client, cookie="").status_code == 403


def test_bearer_passes(monkeypatch):
    _, client = _client(monkeypatch)
    assert _post(client, Authorization="Bearer t").status_code == 200


def test_log_only_passes_and_logs(monkeypatch, caplog):
    app, client = _client(monkeypatch, enforce="false")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        resp = _post(client)
    assert resp.status_code == 200
    assert app.calls == ["hook", "handler"]
    line = next(r.getMessage() for r in caplog.records if "csrf_guard" in r.getMessage())
    assert '"enforced": false' in line
    assert '"service": "svc-a"' in line
    assert '"method": "POST"' in line
    assert '"path": "/things"' in line
    assert f'"origin": "{EVIL}"' in line


def test_enforce_logs_rejection(monkeypatch, caplog):
    _, client = _client(monkeypatch)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _post(client)
    assert any('"enforced": true' in r.getMessage() for r in caplog.records)


def test_allowed_request_logs_nothing(monkeypatch, caplog):
    _, client = _client(monkeypatch)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _post(client, origin="https://app.invalid")
    assert caplog.records == []


@pytest.mark.parametrize("origins", [None, "", "*"])
def test_empty_list_logs_critical_and_rejects(monkeypatch, caplog, origins):
    with caplog.at_level(logging.CRITICAL, logger=LOGGER):
        _, client = _client(monkeypatch, origins=origins)
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)
    assert _post(client).status_code == 403


def test_empty_list_still_passes_requests_without_origin(monkeypatch):
    _, client = _client(monkeypatch, origins=None)
    assert _post(client, origin=None).status_code == 200
