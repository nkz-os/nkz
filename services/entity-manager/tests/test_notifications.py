"""
Tests for the unified notification system blueprint.

Covers:
  - POST /api/internal/notify endpoint: auth gating, dispatching, entity filtering
  - _build_alert_payload: keyValues, normalized, minimal
  - _get_notification_config: DB-backed config reading
  - _handle_alert_notification channel enable/disable logic
"""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# ── Environment setup (must happen BEFORE any blueprint imports) ──────────────
os.environ.setdefault('INTERNAL_SERVICE_SECRET', 'test-secret')
os.environ.setdefault('POSTGRES_URL', 'postgresql://test:test@localhost:5432/test')
os.environ.setdefault('FRONTEND_URL', 'https://test.nkz.example.com')
os.environ.setdefault('ORION_URL', 'http://orion:1026')

_services_dir = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..')
)
if _services_dir not in sys.path:
    sys.path.insert(0, _services_dir)

# Also add entity-manager dir so 'blueprints' package is reachable
_em_dir = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..')
)
if _em_dir not in sys.path:
    sys.path.insert(0, _em_dir)

# Mock common modules used inside function bodies (not at import time)
_common_mock = MagicMock()
_common_mock.inject_fiware_headers = lambda h, t=None, **kw: h
sys.modules.setdefault('common', _common_mock)
sys.modules.setdefault('common.auth_middleware', _common_mock)
sys.modules.setdefault('common.ngsi_headers', _common_mock)

import pytest
from flask import Flask

from blueprints.notifications import (
    notifications_bp,
    _build_alert_payload,
    _get_notification_config,
    _handle_alert_notification,
    INTERNAL_SERVICE_SECRET,
    FRONTEND_URL,
)

# ── Minimal test app (notifications blueprint only) ─────────────────────────
_notify_app = Flask(__name__)
_notify_app.register_blueprint(notifications_bp)
_notify_app.testing = True


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def client():
    """Flask test client with the notifications blueprint registered."""
    with _notify_app.test_client() as c:
        yield c


# =============================================================================
# 1. Notification endpoint tests
# =============================================================================

class TestNotificationEndpoint:
    """POST /api/internal/notify — auth gating and dispatching."""

    def test_missing_secret_returns_401(self, client):
        """No X-Internal-Service-Secret header → 401."""
        resp = client.post(
            '/api/internal/notify',
            content_type='application/json',
            data=json.dumps({'data': []}),
        )
        assert resp.status_code == 401
        body = resp.get_json()
        assert body.get('error') == 'Unauthorized'

    def test_invalid_secret_returns_401(self, client):
        """Wrong X-Internal-Service-Secret header → 401."""
        resp = client.post(
            '/api/internal/notify',
            content_type='application/json',
            headers={'X-Internal-Service-Secret': 'wrong-secret'},
            data=json.dumps({'data': []}),
        )
        assert resp.status_code == 401
        body = resp.get_json()
        assert body.get('error') == 'Unauthorized'

    def test_empty_data_returns_200(self, client):
        """Empty data array → 200 with handled=0."""
        resp = client.post(
            '/api/internal/notify',
            content_type='application/json',
            headers={'X-Internal-Service-Secret': INTERNAL_SERVICE_SECRET},
            data=json.dumps({'data': []}),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['status'] == 'ok'
        assert body['handled'] == 0

    @patch('blueprints.notifications.threading.Thread')
    def test_valid_alert_dispatches(self, mock_thread_class, client):
        """Valid Alert entity → 200, handled=1, daemon Thread started."""
        resp = client.post(
            '/api/internal/notify',
            content_type='application/json',
            headers={
                'X-Internal-Service-Secret': INTERNAL_SERVICE_SECRET,
                'NGSILD-Tenant': 'test-tenant',
            },
            data=json.dumps({
                'data': [{
                    'id': 'urn:ngsi-ld:Alert:1',
                    'type': 'Alert',
                    'name': 'High Temperature',
                    'severity': 'high',
                }],
            }),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['status'] == 'ok'
        assert body['handled'] == 1

        # Verify a daemon background thread was created and started
        mock_thread_class.assert_called_once()
        assert mock_thread_class.call_args.kwargs.get('daemon') is True
        assert 'target' in mock_thread_class.call_args.kwargs
        mock_thread_class.return_value.start.assert_called_once()

    def test_non_alert_entity_skipped(self, client):
        """Entity type != Alert is ignored → handled=0, no dispatch."""
        resp = client.post(
            '/api/internal/notify',
            content_type='application/json',
            headers={
                'X-Internal-Service-Secret': INTERNAL_SERVICE_SECRET,
                'NGSILD-Tenant': 'test-tenant',
            },
            data=json.dumps({
                'data': [{
                    'id': 'urn:ngsi-ld:AgriParcel:456',
                    'type': 'AgriParcel',
                    'name': 'Test Parcel',
                }],
            }),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['handled'] == 0
        assert body['status'] == 'ok'


# =============================================================================
# 2. _build_alert_payload tests
# =============================================================================

class TestBuildAlertPayload:
    """_build_alert_payload — extraction logic for NGSI-LD Alert entities."""

    def test_build_payload_keyvalues(self):
        """Flat input (keyValues format) passes through directly."""
        entity = {
            'id': 'urn:ngsi-ld:Alert:kv-1',
            'type': 'Alert',
            'name': 'High Temperature',
            'severity': 'high',
            'description': 'Temperature exceeded 40°C in field-7',
            'category': 'environmental',
        }
        result = _build_alert_payload(entity)
        assert result['id'] == 'urn:ngsi-ld:Alert:kv-1'
        assert result['type'] == 'Alert'
        assert result['name'] == 'High Temperature'
        assert result['severity'] == 'high'
        assert result['description'] == 'Temperature exceeded 40°C in field-7'
        assert result['category'] == 'environmental'
        # _summary built from extracted payload
        assert result['_summary'] == '[HIGH] High Temperature: Temperature exceeded 40°C in field-7'
        assert result['_link'] == 'https://test.nkz.example.com/alerts/urn:ngsi-ld:Alert:kv-1'

    def test_build_payload_normalized(self):
        """Nested input (normalized NGSI-LD) extracts .value from Property objects."""
        entity = {
            'id': 'urn:ngsi-ld:Alert:norm-1',
            'type': 'Alert',
            'name': {'type': 'Property', 'value': 'Frost Risk'},
            'severity': {'type': 'Property', 'value': 'critical'},
            'description': {'type': 'Property', 'value': 'Frost expected tonight in valley areas'},
            'observedAt': {'type': 'Property', 'value': '2026-06-22T00:00:00Z'},
        }
        result = _build_alert_payload(entity)
        assert result['id'] == 'urn:ngsi-ld:Alert:norm-1'
        assert result['name'] == 'Frost Risk'
        assert result['severity'] == 'critical'
        assert result['description'] == 'Frost expected tonight in valley areas'
        assert result['observedAt'] == '2026-06-22T00:00:00Z'
        assert result['_summary'] == '[CRITICAL] Frost Risk: Frost expected tonight in valley areas'
        assert result['_link'] == 'https://test.nkz.example.com/alerts/urn:ngsi-ld:Alert:norm-1'

    def test_build_payload_minimal(self):
        """Minimal fields (id + type only) → defaults used for summary."""
        entity = {
            'id': 'urn:ngsi-ld:Alert:min-1',
            'type': 'Alert',
        }
        result = _build_alert_payload(entity)
        assert result['id'] == 'urn:ngsi-ld:Alert:min-1'
        assert result['type'] == 'Alert'
        # No extra keys beyond id, type, _summary, _link
        assert 'name' not in result
        assert 'severity' not in result
        # Default summary: [INFO] Alert:
        assert result['_summary'] == '[INFO] Alert: '
        assert result['_link'] == 'https://test.nkz.example.com/alerts/urn:ngsi-ld:Alert:min-1'


# =============================================================================
# 3. _get_notification_config tests
# =============================================================================

class TestGetNotificationConfig:
    """_get_notification_config — reads from admin_platform.notification_config."""

    @patch('blueprints.notifications.psycopg2.connect')
    def test_config_found(self, mock_connect):
        """Row found → full config dict with parsed values."""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (
            json.dumps({'to': 'admin@test.com', 'enabled': True}),
            json.dumps({'stream': 'alerts', 'topic': 'critical'}),
            json.dumps({'url': 'https://hooks.test.com/alert'}),
            True,
        )
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_connect.return_value = mock_conn

        result = _get_notification_config('test-tenant')

        assert result['enabled'] is True
        assert result['email_config'] == json.dumps({'to': 'admin@test.com', 'enabled': True})
        assert result['zulip_config'] == json.dumps({'stream': 'alerts', 'topic': 'critical'})
        assert result['webhook_config'] == json.dumps({'url': 'https://hooks.test.com/alert'})

        # Verify SQL was executed
        mock_cur.execute.assert_called_once()
        assert 'admin_platform.notification_config' in mock_cur.execute.call_args[0][0]
        assert mock_cur.execute.call_args[0][1] == ('test-tenant',)

    @patch('blueprints.notifications.psycopg2.connect')
    def test_config_not_found(self, mock_connect):
        """No row → returns disabled default dict."""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_connect.return_value = mock_conn

        result = _get_notification_config('test-tenant')

        assert result is None


# =============================================================================
# 4. Channel enabled logic tests
# =============================================================================

class TestChannelEnabledLogic:
    """_handle_alert_notification — per-channel enabled flag behavior."""

    @pytest.mark.asyncio
    @patch('blueprints.notifications._send_email_channel', new_callable=AsyncMock)
    @patch('blueprints.notifications._send_zulip_channel', new_callable=AsyncMock)
    @patch('blueprints.notifications._send_webhook_channel', new_callable=AsyncMock)
    @patch('blueprints.notifications._build_alert_payload')
    @patch('blueprints.notifications._get_notification_config')
    async def test_channel_enabled_true(
        self,
        mock_get_config,
        mock_build_payload,
        mock_webhook,
        mock_zulip,
        mock_email,
    ):
        """enabled=True → channel fires."""
        mock_get_config.return_value = {
            'enabled': True,
            'email_config': {'enabled': True, 'to': 'a@b.com'},
            'zulip_config': {'enabled': True, 'stream': 'alerts'},
            'webhook_config': {'enabled': True, 'url': 'https://hook.example.com'},
        }
        mock_build_payload.return_value = {
            'id': 'urn:ngsi-ld:Alert:1',
            'severity': 'high',
            '_summary': '[HIGH] Test',
        }

        await _handle_alert_notification('test-tenant', {'id': 'x', 'type': 'Alert'})

        mock_email.assert_called_once()
        mock_zulip.assert_called_once()
        mock_webhook.assert_called_once()

    @pytest.mark.asyncio
    @patch('blueprints.notifications._send_email_channel', new_callable=AsyncMock)
    @patch('blueprints.notifications._send_zulip_channel', new_callable=AsyncMock)
    @patch('blueprints.notifications._send_webhook_channel', new_callable=AsyncMock)
    @patch('blueprints.notifications._build_alert_payload')
    @patch('blueprints.notifications._get_notification_config')
    async def test_channel_enabled_false(
        self,
        mock_get_config,
        mock_build_payload,
        mock_webhook,
        mock_zulip,
        mock_email,
    ):
        """enabled=False → channel is skipped."""
        mock_get_config.return_value = {
            'enabled': True,
            'email_config': {'enabled': False, 'to': 'a@b.com'},
            'zulip_config': {'enabled': True, 'stream': 'alerts'},
            'webhook_config': None,  # not configured at all
        }
        mock_build_payload.return_value = {
            'id': 'urn:ngsi-ld:Alert:1',
            'severity': 'high',
            '_summary': '[HIGH] Test',
        }

        await _handle_alert_notification('test-tenant', {'id': 'x', 'type': 'Alert'})

        # Email channel skipped due to enabled=False
        mock_email.assert_not_called()
        # Zulip still fires because enabled=True
        mock_zulip.assert_called_once()
        # Webhook not configured → not called
        mock_webhook.assert_not_called()

    @pytest.mark.asyncio
    @patch('blueprints.notifications._send_email_channel', new_callable=AsyncMock)
    @patch('blueprints.notifications._send_zulip_channel', new_callable=AsyncMock)
    @patch('blueprints.notifications._send_webhook_channel', new_callable=AsyncMock)
    @patch('blueprints.notifications._build_alert_payload')
    @patch('blueprints.notifications._get_notification_config')
    async def test_channel_enabled_missing(
        self,
        mock_get_config,
        mock_build_payload,
        mock_webhook,
        mock_zulip,
        mock_email,
    ):
        """enabled key missing → channel fires (backward compatibility)."""
        mock_get_config.return_value = {
            'enabled': True,
            # email_config has NO 'enabled' key — should default to True
            'email_config': {'to': 'legacy@b.com'},
            'zulip_config': None,
            'webhook_config': None,
        }
        mock_build_payload.return_value = {
            'id': 'urn:ngsi-ld:Alert:1',
            'severity': 'warning',
            '_summary': '[WARNING] Legacy alert',
        }

        await _handle_alert_notification('test-tenant', {'id': 'x', 'type': 'Alert'})

        # Email channel fires because 'enabled' defaults to True when missing
        mock_email.assert_called_once()
        mock_zulip.assert_not_called()
        mock_webhook.assert_not_called()
