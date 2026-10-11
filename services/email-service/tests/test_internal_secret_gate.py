"""Every send endpoint must require the internal service secret once the
flag is on; /health stays open for probes."""
import importlib
import os
import sys
from unittest.mock import patch

import pytest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")
for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

email_service_module = importlib.import_module("email_service")


@pytest.fixture
def client():
    email_service_module.app.config["TESTING"] = True
    with email_service_module.app.test_client() as c:
        yield c


def _gate(on: bool, secret: str):
    return patch.multiple(
        email_service_module,
        EMAIL_REQUIRE_INTERNAL_SECRET=on,
        INTERNAL_SERVICE_SECRET=secret,
    )


def test_health_stays_open(client):
    # /health answers 503 when SMTP credentials are absent (test env); force
    # "enabled" so a 200 proves the gate did not intercept it.
    with _gate(True, "s"), patch.object(email_service_module.config, "enabled", True):
        assert client.get("/health").status_code == 200


def test_send_rejected_without_secret(client):
    with _gate(True, "s"):
        resp = client.post("/send/notification", json={"email": "a@b.c"})
    assert resp.status_code == 401


def test_send_rejected_with_wrong_secret(client):
    with _gate(True, "s"):
        resp = client.post(
            "/send/notification", json={}, headers={"X-Internal-Service-Secret": "nope"}
        )
    assert resp.status_code == 401


def test_send_reaches_handler_with_secret(client):
    with _gate(True, "s"):
        resp = client.post(
            "/send/notification", json={}, headers={"X-Internal-Service-Secret": "s"}
        )
    assert resp.status_code != 401


def test_gate_off_keeps_old_behaviour(client):
    with _gate(False, ""):
        resp = client.post("/send/notification", json={})
    assert resp.status_code != 401


def test_gate_on_without_secret_configured_refuses(client):
    with _gate(True, ""):
        resp = client.post(
            "/send/notification", json={}, headers={"X-Internal-Service-Secret": ""}
        )
    assert resp.status_code == 401


def test_non_ascii_secret_header_is_401_not_500(client):
    # Werkzeug decodes header values as latin-1; comparing str with non-ASCII
    # characters would raise TypeError and surface as a 500.
    with _gate(True, "s"):
        resp = client.post(
            "/send/notification",
            json={},
            headers={"X-Internal-Service-Secret": "é"},
        )
    assert resp.status_code == 401
