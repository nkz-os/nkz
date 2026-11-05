"""POST /send/report: own subject/HTML/text, one-click unsubscribe headers."""
import email
import importlib
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")
for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

es = importlib.import_module("email_service")

SECRET = "s3cret"
UNSUB = "https://example.org/unsubscribe/abc"
PAYLOAD = {
    "email": "user@example.org",
    "subject": "Weekly report",
    "content_html": "<p>Hello</p>",
    "text": "Hello",
    "title": "Report",
    "list_unsubscribe_url": UNSUB,
}
HDR = {"X-Internal-Service-Secret": SECRET}


@pytest.fixture
def client():
    es.app.config["TESTING"] = True
    with es.app.test_client() as c:
        yield c


def _gate(on):
    return patch.multiple(
        es, EMAIL_REQUIRE_INTERNAL_SECRET=on, INTERNAL_SERVICE_SECRET=SECRET
    )


def _sent_message(server):
    raw = server.sendmail.call_args[0][2]
    return email.message_from_string(raw)


def test_requires_secret_even_if_flag_off(client):
    with _gate(False):
        assert client.post("/send/report", json=PAYLOAD).status_code == 401
        assert (
            client.post(
                "/send/report", json=PAYLOAD, headers={"X-Internal-Service-Secret": "x"}
            ).status_code
            == 401
        )


def test_requires_secret_when_none_configured(client):
    with patch.multiple(es, EMAIL_REQUIRE_INTERNAL_SECRET=False, INTERNAL_SERVICE_SECRET=""):
        assert client.post("/send/report", json=PAYLOAD, headers=HDR).status_code == 401


@pytest.mark.parametrize("field", list(PAYLOAD))
def test_missing_field_400(client, field):
    body = {k: v for k, v in PAYLOAD.items() if k != field}
    with _gate(False):
        assert client.post("/send/report", json=body, headers=HDR).status_code == 400


def test_subject_crlf_rejected(client):
    body = dict(PAYLOAD, subject="a\r\nBcc: x@example.org")
    with _gate(False), patch.object(es.email_service, "send_report_email") as m:
        resp = client.post("/send/report", json=body, headers=HDR)
    assert resp.status_code == 400
    m.assert_not_called()


def test_unsubscribe_url_crlf_rejected(client):
    body = dict(PAYLOAD, list_unsubscribe_url="https://example.org/u\r\nBcc: x@example.org")
    with _gate(False), patch.object(es.email_service, "send_report_email") as m:
        resp = client.post("/send/report", json=body, headers=HDR)
    assert resp.status_code == 400
    m.assert_not_called()


def test_failure_returns_500(client):
    with _gate(False), patch.object(es.email_service, "send_report_email", return_value=False):
        assert client.post("/send/report", json=PAYLOAD, headers=HDR).status_code == 500


def test_mime_order_and_headers(client):
    server = MagicMock()
    with _gate(False), patch.object(es.email_service.config, "enabled", True), patch.object(
        es.smtplib, "SMTP", return_value=server
    ), patch.object(es.smtplib, "SMTP_SSL", return_value=server):
        resp = client.post("/send/report", json=PAYLOAD, headers=HDR)
    assert resp.status_code == 200
    msg = _sent_message(server)
    assert [p.get_content_type() for p in msg.get_payload()] == ["text/plain", "text/html"]
    assert msg["List-Unsubscribe"] == f"<{UNSUB}>"
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert msg["Subject"] == "Weekly report"
    assert "Notificación Nekazari" not in msg["Subject"]
    html_part = msg.get_payload()[1].get_payload(decode=True).decode()
    assert "<p>Hello</p>" in html_part and "{{ content }}" not in html_part


def test_other_routes_keep_mime_order():
    server = MagicMock()
    with patch.object(es.email_service.config, "enabled", True), patch.object(
        es.smtplib, "SMTP", return_value=server
    ), patch.object(es.smtplib, "SMTP_SSL", return_value=server):
        assert es.email_service.send_email("u@example.org", "S", "<p>h</p>", "t")
    msg = _sent_message(server)
    assert [p.get_content_type() for p in msg.get_payload()] == ["text/html", "text/plain"]
    assert msg["List-Unsubscribe"] is None
