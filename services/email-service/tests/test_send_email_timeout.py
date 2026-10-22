"""Tests for SMTP connection timeout handling in EmailService.send_email().

Covers:
  - SMTP_SSL / SMTP constructors receive timeout=<SMTP_TIMEOUT_SECONDS> (default 15)
  - SMTP_TIMEOUT_SECONDS env var overrides the default
  - send_email returns False (never raises) when the connection times out
  - server.quit() is called on the happy path
"""

import importlib
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# ── Path setup (mirrors tests/test_email_service_smoke.py) ────────────────────
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")

for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import email_service  # noqa: E402


def _make_service(monkeypatch, **env):
    """Build a fresh EmailConfig/EmailService pair with SMTP creds enabled.

    EmailConfig reads env vars at __init__ time, so setting them via
    monkeypatch before instantiation is sufficient — no need to touch the
    module-level singleton created at import time.
    """
    monkeypatch.setenv('SMTP_USERNAME', 'user@example.com')
    monkeypatch.setenv('SMTP_PASSWORD', 'secret')
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.com')
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config = email_service.EmailConfig()
    assert config.enabled is True
    return email_service.EmailService(config)


class TestTimeoutConfiguration:
    """The SMTP constructors must receive an explicit timeout."""

    def test_default_timeout_is_15_seconds_ssl(self, monkeypatch):
        """Port 465 (SMTP_SSL) gets timeout=15 when SMTP_TIMEOUT_SECONDS unset."""
        monkeypatch.delenv('SMTP_TIMEOUT_SECONDS', raising=False)
        service = _make_service(monkeypatch, SMTP_PORT='465')

        mock_server = MagicMock()
        with patch.object(email_service.smtplib, 'SMTP_SSL', return_value=mock_server) as mock_ssl:
            result = service.send_email('to@example.com', 'Subject', '<p>hi</p>')

        assert result is True
        mock_ssl.assert_called_once_with('smtp.example.com', 465, timeout=15)

    def test_default_timeout_is_15_seconds_starttls(self, monkeypatch):
        """Port 587 (SMTP + starttls) gets timeout=15 when SMTP_TIMEOUT_SECONDS unset."""
        monkeypatch.delenv('SMTP_TIMEOUT_SECONDS', raising=False)
        service = _make_service(monkeypatch, SMTP_PORT='587')

        mock_server = MagicMock()
        with patch.object(email_service.smtplib, 'SMTP', return_value=mock_server) as mock_smtp:
            result = service.send_email('to@example.com', 'Subject', '<p>hi</p>')

        assert result is True
        mock_smtp.assert_called_once_with('smtp.example.com', 587, timeout=15)

    def test_env_override_is_respected(self, monkeypatch):
        """SMTP_TIMEOUT_SECONDS env var overrides the default of 15."""
        service = _make_service(monkeypatch, SMTP_PORT='465', SMTP_TIMEOUT_SECONDS='5')
        assert service.config.smtp_timeout == 5

        mock_server = MagicMock()
        with patch.object(email_service.smtplib, 'SMTP_SSL', return_value=mock_server) as mock_ssl:
            result = service.send_email('to@example.com', 'Subject', '<p>hi</p>')

        assert result is True
        mock_ssl.assert_called_once_with('smtp.example.com', 465, timeout=5)


class TestConnectionFailureHandling:
    """send_email must never raise, and must not leak connections."""

    def test_connect_timeout_returns_false_not_raises(self, monkeypatch):
        """Constructor raising TimeoutError -> send_email returns False."""
        service = _make_service(monkeypatch, SMTP_PORT='465')

        with patch.object(email_service.smtplib, 'SMTP_SSL', side_effect=TimeoutError('timed out')):
            result = service.send_email('to@example.com', 'Subject', '<p>hi</p>')

        assert result is False

    def test_quit_called_on_happy_path(self, monkeypatch):
        """server.quit() is invoked after a successful send."""
        service = _make_service(monkeypatch, SMTP_PORT='465')

        mock_server = MagicMock()
        with patch.object(email_service.smtplib, 'SMTP_SSL', return_value=mock_server):
            result = service.send_email('to@example.com', 'Subject', '<p>hi</p>')

        assert result is True
        mock_server.login.assert_called_once()
        mock_server.sendmail.assert_called_once()
        mock_server.quit.assert_called_once()

    def test_quit_called_and_swallowed_on_login_failure(self, monkeypatch):
        """login() raising -> quit() still attempted, send_email returns False."""
        service = _make_service(monkeypatch, SMTP_PORT='465')

        mock_server = MagicMock()
        mock_server.login.side_effect = Exception('auth failed')
        with patch.object(email_service.smtplib, 'SMTP_SSL', return_value=mock_server):
            result = service.send_email('to@example.com', 'Subject', '<p>hi</p>')

        assert result is False
        mock_server.quit.assert_called_once()

    def test_quit_exception_is_swallowed(self, monkeypatch):
        """quit() itself raising must not propagate — send_email still returns True."""
        service = _make_service(monkeypatch, SMTP_PORT='465')

        mock_server = MagicMock()
        mock_server.quit.side_effect = Exception('already closed')
        with patch.object(email_service.smtplib, 'SMTP_SSL', return_value=mock_server):
            result = service.send_email('to@example.com', 'Subject', '<p>hi</p>')

        assert result is True
        mock_server.quit.assert_called_once()
