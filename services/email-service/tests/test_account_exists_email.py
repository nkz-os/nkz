"""Tests for the account-exists notification email (anti-enumeration follow-up)."""

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


@pytest.fixture
def client():
    email_service_module.app.config["TESTING"] = True
    with email_service_module.app.test_client() as c:
        yield c


class TestSendAccountExistsEmail:
    """Unit tests for EmailService.send_account_exists_email"""

    def test_english_language_uses_english_template(self):
        svc = email_service_module.email_service
        with patch.object(svc, "send_email", return_value=True) as mock_send:
            result = svc.send_account_exists_email("user@example.com", language="en")

        assert result is True
        mock_send.assert_called_once()
        args, _kwargs = mock_send.call_args
        subject, html_content = args[1], args[2]
        assert "You already have an account" in html_content
        assert 'lang="en"' in html_content
        assert subject == "You already have an account - Nekazari"

    def test_unsupported_language_falls_back_to_spanish(self):
        """The method itself only special-cases 'en'; anything else renders
        the Spanish template (endpoint-level normalization narrows the input
        further, see TestAccountExistsEndpoint)."""
        svc = email_service_module.email_service
        with patch.object(svc, "send_email", return_value=True) as mock_send:
            svc.send_account_exists_email("user@example.com", language="fr")

        args, _kwargs = mock_send.call_args
        subject, html_content = args[1], args[2]
        assert "Ya tienes una cuenta" in html_content
        assert 'lang="es"' in html_content
        assert subject == "Ya tienes una cuenta - Nekazari"

    def test_missing_language_defaults_to_spanish(self):
        svc = email_service_module.email_service
        with patch.object(svc, "send_email", return_value=True) as mock_send:
            svc.send_account_exists_email("user@example.com")

        args, _kwargs = mock_send.call_args
        assert "Ya tienes una cuenta" in args[2]

    def test_placeholders_are_substituted(self):
        svc = email_service_module.email_service
        with patch.object(svc, "send_email", return_value=True) as mock_send:
            svc.send_account_exists_email("user@example.com", language="es")

        html_content = mock_send.call_args[0][2]
        assert "{NKZ_URL}" not in html_content
        assert "{YEAR}" not in html_content
        assert "https://nekazari.robotika.cloud/login" in html_content

    def test_no_otp_box_in_content(self):
        """This is a plain notification, not an OTP email — no code box."""
        svc = email_service_module.email_service
        with patch.object(svc, "send_email", return_value=True) as mock_send:
            svc.send_account_exists_email("user@example.com", language="es")

        html_content = mock_send.call_args[0][2]
        assert "otp-box" not in html_content
        assert "otp-code" not in html_content

    def test_send_email_failure_propagates_false(self):
        svc = email_service_module.email_service
        with patch.object(svc, "send_email", return_value=False):
            result = svc.send_account_exists_email("user@example.com", language="es")
        assert result is False

    def test_exception_is_caught_and_returns_false(self):
        svc = email_service_module.email_service
        with patch.object(svc, "send_email", side_effect=RuntimeError("boom")):
            result = svc.send_account_exists_email("user@example.com", language="es")
        assert result is False


class TestAccountExistsEndpoint:
    """Integration tests for POST /email/account-exists"""

    def test_missing_email_returns_400(self, client):
        resp = client.post("/email/account-exists", json={"language": "es"})
        assert resp.status_code == 400

    def test_success_returns_200(self, client):
        with patch.object(email_service_module.email_service, "send_email", return_value=True):
            resp = client.post(
                "/email/account-exists",
                json={"email": "user@example.com", "language": "en"},
            )
        assert resp.status_code == 200

    def test_send_failure_returns_500(self, client):
        with patch.object(email_service_module.email_service, "send_email", return_value=False):
            resp = client.post(
                "/email/account-exists",
                json={"email": "user@example.com", "language": "es"},
            )
        assert resp.status_code == 500

    def test_language_region_subtag_normalizes_to_en(self, client):
        """'en-US' should normalize to 'en' for template selection."""
        with patch.object(
            email_service_module.email_service, "send_email", return_value=True
        ) as mock_send:
            resp = client.post(
                "/email/account-exists",
                json={"email": "user@example.com", "language": "en-US"},
            )
        assert resp.status_code == 200
        html_content = mock_send.call_args[0][2]
        assert "You already have an account" in html_content

    def test_unsupported_language_falls_back_to_es(self, client):
        with patch.object(
            email_service_module.email_service, "send_email", return_value=True
        ) as mock_send:
            resp = client.post(
                "/email/account-exists",
                json={"email": "user@example.com", "language": "fr"},
            )
        assert resp.status_code == 200
        html_content = mock_send.call_args[0][2]
        assert "Ya tienes una cuenta" in html_content

    def test_missing_language_defaults_to_es(self, client):
        with patch.object(
            email_service_module.email_service, "send_email", return_value=True
        ) as mock_send:
            resp = client.post(
                "/email/account-exists",
                json={"email": "user@example.com"},
            )
        assert resp.status_code == 200
        html_content = mock_send.call_args[0][2]
        assert "Ya tienes una cuenta" in html_content
