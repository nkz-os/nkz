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
os.environ['FRONTEND_URL'] = 'https://test.nkz.example.com'
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

# require_auth must be a proper pass-through decorator (preserves __name__)
def _require_auth_passthrough(f):
    f.__wrapped__ = f
    return f

_common_mock.require_auth = _require_auth_passthrough
sys.modules['common'] = _common_mock
sys.modules['common.auth_middleware'] = _common_mock
sys.modules['common.ngsi_headers'] = _common_mock

import importlib.util
_log_h_path = os.path.join(_services_dir, "common", "log_helpers.py")
_log_h_spec = importlib.util.spec_from_file_location("common.log_helpers", _log_h_path)
_log_h_mod = importlib.util.module_from_spec(_log_h_spec)
assert _log_h_spec.loader is not None
_log_h_spec.loader.exec_module(_log_h_mod)
sys.modules["common.log_helpers"] = _log_h_mod

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
        assert result['_link'] == f'{FRONTEND_URL}/alerts/urn:ngsi-ld:Alert:kv-1'

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
        assert result['_link'] == f'{FRONTEND_URL}/alerts/urn:ngsi-ld:Alert:norm-1'

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
        assert result['_link'] == f'{FRONTEND_URL}/alerts/urn:ngsi-ld:Alert:min-1'


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
        assert result['email_config'] == {'to': 'admin@test.com', 'enabled': True}
        assert result['zulip_config'] == {'stream': 'alerts', 'topic': 'critical'}
        assert result['webhook_config'] == {'url': 'https://hooks.test.com/alert'}

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
    """_handle_alert_notification - per-channel enabled flag behavior.

    Tests the channel selection logic synchronously (no pytest-asyncio needed).
    """

    @patch('blueprints.notifications._get_notification_config')
    @patch('blueprints.notifications._build_alert_payload')
    def test_enabled_true_all_channels(
        self, mock_build_payload, mock_get_config,
    ):
        """All channels enabled=True -> all fire."""
        config = {
            'enabled': True,
            'email_config': {'enabled': True, 'to': 'a@b.com'},
            'zulip_config': {'enabled': True, 'stream': 'alerts'},
            'webhook_config': {'enabled': True, 'url': 'https://hook.example.com'},
        }
        result = _select_channels(config)
        assert len(result) == 3

    @patch('blueprints.notifications._get_notification_config')
    @patch('blueprints.notifications._build_alert_payload')
    def test_enabled_false_skips_channel(
        self, mock_build_payload, mock_get_config,
    ):
        """enabled=False -> channel is skipped."""
        config = {
            'enabled': True,
            'email_config': {'enabled': False, 'to': 'a@b.com'},
            'zulip_config': {'enabled': True, 'stream': 'alerts'},
            'webhook_config': None,
        }
        result = _select_channels(config)
        # Only zulip should fire
        assert len(result) == 1
        assert result[0][0] == 'zulip'

    @patch('blueprints.notifications._get_notification_config')
    @patch('blueprints.notifications._build_alert_payload')
    def test_enabled_missing_defaults_true(
        self, mock_build_payload, mock_get_config,
    ):
        """enabled key missing -> channel fires (backward compatibility)."""
        config = {
            'enabled': True,
            'email_config': {'to': 'legacy@b.com'},
            'zulip_config': None,
            'webhook_config': None,
        }
        result = _select_channels(config)
        assert len(result) == 1
        assert result[0][0] == 'email'


# =============================================================================
# 5. Notification config endpoint tests
# =============================================================================

_AUTH = {'Authorization': 'Bearer test'}


class TestNotificationConfigEndpoints:
    """GET/PUT/POST /api/notifications/config — CRUD + test."""

    _AUTH = {'Authorization': 'Bearer test'}

    # ── GET ────────────────────────────────────────────────────────────────

    def test_get_config_no_auth_returns_400(self, client):
        """GET without X-Tenant-ID → 400."""
        resp = client.get('/api/notifications/config', headers=self._AUTH)
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'Missing X-Tenant-ID'

    @patch('blueprints.notifications.psycopg2.connect')
    def test_get_config_not_found_returns_defaults(self, mock_connect, client):
        """No config row → 200 with empty defaults."""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_connect.return_value = mock_conn

        resp = client.get(
            '/api/notifications/config',
            headers={**self._AUTH, 'X-Tenant-ID': 'test-tenant'},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['email_config'] == {}
        assert data['zulip_config'] == {}
        assert data['webhook_config'] == {}
        assert data['enabled'] is True

    @patch('blueprints.notifications.psycopg2.connect')
    def test_get_config_found(self, mock_connect, client):
        """Config exists → 200 with stored values."""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (
            json.dumps({'to': 'admin@test.com', 'enabled': True}),
            json.dumps({'stream': 'tenant-test-alerts'}),
            json.dumps({'url': 'https://hook.example.com'}),
            True,
        )
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_connect.return_value = mock_conn

        resp = client.get(
            '/api/notifications/config',
            headers={**self._AUTH, 'X-Tenant-ID': 'test-tenant'},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['enabled'] is True
        assert data['email_config']['to'] == 'admin@test.com'

    # ── PUT ────────────────────────────────────────────────────────────────

    def test_put_config_no_auth_returns_400(self, client):
        """PUT without X-Tenant-ID → 400."""
        resp = client.put(
            '/api/notifications/config',
            content_type='application/json',
            data=json.dumps({'email_config': {'to': 'a@b.com'}}),
            headers=self._AUTH,
        )
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'Missing X-Tenant-ID'

    def test_put_config_empty_body_returns_400(self, client):
        """PUT with empty body → 400."""
        resp = client.put(
            '/api/notifications/config',
            content_type='application/json',
            data='{}',
            headers={**self._AUTH, 'X-Tenant-ID': 'test-tenant'},
        )
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'Invalid or empty JSON body'

    @patch('blueprints.notifications.psycopg2.connect')
    def test_put_config_success(self, mock_connect, client):
        """PUT with valid body → 200 updated."""
        mock_cur = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_connect.return_value = mock_conn

        body = {
            'email_config': {'to': 'admin@finca.com', 'enabled': True},
            'zulip_config': {'stream': 'tenant-test-alerts'},
            'webhook_config': {},
            'enabled': True,
        }
        resp = client.put(
            '/api/notifications/config',
            content_type='application/json',
            data=json.dumps(body),
            headers={**self._AUTH, 'X-Tenant-ID': 'test-tenant'},
        )
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'updated'

        # Verify SQL was called with correct values
        mock_cur.execute.assert_called_once()
        sql, params = mock_cur.execute.call_args[0]
        assert 'INSERT INTO admin_platform.notification_config' in sql
        assert params[0] == 'test-tenant'  # tenant_id

    # ── POST /test ─────────────────────────────────────────────────────────

    def test_post_test_no_auth_returns_400(self, client):
        """POST /test without X-Tenant-ID → 400."""
        resp = client.post('/api/notifications/config/test', headers=self._AUTH)
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'Missing X-Tenant-ID'

    @patch('blueprints.notifications.psycopg2.connect')
    def test_post_test_no_config_returns_404(self, mock_connect, client):
        """POST /test without config → 404."""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_connect.return_value = mock_conn

        resp = client.post(
            '/api/notifications/config/test',
            headers={**self._AUTH, 'X-Tenant-ID': 'test-tenant'},
        )
        assert resp.status_code == 404
        assert 'No notification config found' in resp.get_json()['error']

    @patch('blueprints.notifications.psycopg2.connect')
    def test_post_test_disabled_returns_warning(self, mock_connect, client):
        """POST /test with enabled=false → 200 warning."""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (
            json.dumps({'to': 'admin@test.com'}),
            json.dumps({'stream': 'alerts'}),
            json.dumps({'url': 'https://hook.example.com'}),
            False,
        )
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_connect.return_value = mock_conn

        resp = client.post(
            '/api/notifications/config/test',
            headers={**self._AUTH, 'X-Tenant-ID': 'test-tenant'},
        )
        assert resp.status_code == 200
        assert 'Notifications are disabled' in resp.get_json()['warning']

    @patch('blueprints.notifications.psycopg2.connect')
    def test_post_test_all_skipped(self, mock_connect, client):
        """POST /test with no channels enabled → all skipped in results."""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (
            json.dumps({'to': 'admin@test.com', 'enabled': False}),
            json.dumps({'stream': 'alerts', 'enabled': False}),
            json.dumps({'url': 'https://hook.example.com', 'enabled': False}),
            True,
        )
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_connect.return_value = mock_conn

        resp = client.post(
            '/api/notifications/config/test',
            headers={**self._AUTH, 'X-Tenant-ID': 'test-tenant'},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'test_completed'
        assert data['results']['email'] == 'skipped (not enabled)'
        assert data['results']['zulip'] == 'skipped (not enabled)'
        assert data['results']['webhook'] == 'skipped (not enabled)'


def _select_channels(config: dict) -> list:
    """Mirrors the channel selection logic from _handle_alert_notification.

    Returns list of (channel_name, config) tuples that would fire.
    """
    channels = []
    ec = config.get('email_config', {})
    if isinstance(ec, dict) and ec.get('enabled', True):
        channels.append(('email', ec))
    zc = config.get('zulip_config', {})
    if isinstance(zc, dict) and zc.get('enabled', True):
        channels.append(('zulip', zc))
    wc = config.get('webhook_config', {})
    if isinstance(wc, dict) and wc.get('enabled', True):
        channels.append(('webhook', wc))
    return channels
