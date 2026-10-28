"""Tests for the trilingual support welcome email."""

import importlib
import os
import sys
from unittest.mock import patch

import pytest

# ── Path setup (mirrors test_email_service_smoke.py) ────────────────────────
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")

for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

email_service_module = importlib.import_module("email_service")

SUPPORT = "support@example.org"


@pytest.fixture
def client():
    email_service_module.app.config["TESTING"] = True
    with email_service_module.app.test_client() as c:
        yield c


@pytest.fixture
def svc():
    s = email_service_module.email_service
    with patch.object(s.config, "support_email", SUPPORT), \
         patch.object(s.config, "platform_env_notice", ""):
        yield s


class TestSendSupportWelcomeEmail:

    def test_three_languages_in_order(self, svc):
        with patch.object(svc, "send_email", return_value=True) as mock_send:
            assert svc.send_support_welcome_email("user@example.com") is True

        html_content = mock_send.call_args[0][2]
        es = html_content.index("Gracias por confiar en NKZ")
        eu = html_content.index("Eskerrik asko NKZn")
        en = html_content.index("Thank you for choosing NKZ")
        assert es < eu < en

    def test_support_address_in_body_and_reply_to(self, svc):
        with patch.object(svc, "send_email", return_value=True) as mock_send:
            svc.send_support_welcome_email("user@example.com")

        args, kwargs = mock_send.call_args
        assert f"mailto:{SUPPORT}" in args[2]
        assert SUPPORT in args[3]
        assert kwargs["reply_to"] == SUPPORT

    def test_subject_is_trilingual(self, svc):
        with patch.object(svc, "send_email", return_value=True) as mock_send:
            svc.send_support_welcome_email("user@example.com")

        subject = mock_send.call_args[0][1]
        assert "Bienvenida" in subject and "Ongi etorri" in subject and "Welcome" in subject

    def test_no_dev_notice_by_default(self, svc):
        with patch.object(svc, "send_email", return_value=True) as mock_send:
            svc.send_support_welcome_email("user@example.com")

        args = mock_send.call_args[0]
        assert "plataforma de desarrollo" not in args[2]
        assert "development platform" not in args[3]

    def test_dev_notice_in_three_languages_when_enabled(self, svc):
        with patch.object(svc.config, "platform_env_notice", "development"), \
             patch.object(svc, "send_email", return_value=True) as mock_send:
            svc.send_support_welcome_email("user@example.com")

        html_content, text_content = mock_send.call_args[0][2:4]
        for body in (html_content, text_content):
            assert "plataforma de desarrollo" in body
            assert "garapen-plataforma" in body
            assert "development platform" in body

    def test_placeholders_are_substituted(self, svc):
        with patch.object(svc, "send_email", return_value=True) as mock_send:
            svc.send_support_welcome_email("user@example.com")

        html_content = mock_send.call_args[0][2]
        for placeholder in ("{SUPPORT_EMAIL}", "{YEAR}", "{NKZ_URL}", "{DEV_NOTICE_ES}"):
            assert placeholder not in html_content

    def test_missing_support_address_sends_nothing(self, svc):
        with patch.object(svc.config, "support_email", ""), \
             patch.object(svc, "send_email", return_value=True) as mock_send:
            assert svc.send_support_welcome_email("user@example.com") is False
        mock_send.assert_not_called()


class TestSupportWelcomeEndpoint:

    def test_missing_email_returns_400(self, client, svc):
        assert client.post("/email/support-welcome", json={}).status_code == 400

    def test_success_returns_200(self, client, svc):
        with patch.object(svc, "send_email", return_value=True):
            resp = client.post("/email/support-welcome", json={"email": "user@example.com"})
        assert resp.status_code == 200

    def test_send_failure_returns_500(self, client, svc):
        with patch.object(svc, "send_email", return_value=False):
            resp = client.post("/email/support-welcome", json={"email": "user@example.com"})
        assert resp.status_code == 500

    def test_unconfigured_support_address_returns_503(self, client, svc):
        with patch.object(svc.config, "support_email", ""), \
             patch.object(svc, "send_email", return_value=True) as mock_send:
            resp = client.post("/email/support-welcome", json={"email": "user@example.com"})
        assert resp.status_code == 503
        mock_send.assert_not_called()


class TestReplyToHeader:

    def test_reply_to_header_set_when_given(self):
        s = email_service_module.email_service
        sent = {}

        class FakeSMTP:
            def __init__(self, *a, **kw):
                pass

            def starttls(self):
                pass

            def login(self, *a):
                pass

            def sendmail(self, _from, _to, msg):
                sent["msg"] = msg

            def quit(self):
                pass

        with patch.object(s.config, "enabled", True), \
             patch.object(s.config, "smtp_port", 587), \
             patch.object(email_service_module.smtplib, "SMTP", FakeSMTP):
            assert s.send_email("user@example.com", "s", "<p>x</p>", reply_to=SUPPORT) is True

        assert f"Reply-To: {SUPPORT}" in sent["msg"]
