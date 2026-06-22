"""
Notifications Blueprint — unified NGSI-LD notification handler.

Subscription manager creates per-tenant subscriptions for Alert entities.
Endpoint /api/internal/notify receives notifications, routes to channels.
"""

import asyncio
import json
import logging
import os
import threading
from datetime import datetime
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

import psycopg2
import requests
import smtplib
from flask import Blueprint, jsonify, request
from tenacity import retry, stop_after_attempt, wait_fixed

from common.auth_middleware import require_auth
from common.ngsi_headers import inject_fiware_headers

logger = logging.getLogger(__name__)

notifications_bp = Blueprint('notifications', __name__)

# ── Configuration ──────────────────────────────────────────────────────
ORION_URL = os.getenv('ORION_URL', 'http://orion-ld-service:1026')
SERVICE_HOST = os.getenv('SERVICE_HOST', 'entity-manager-service')
SERVICE_PORT = os.getenv('SERVICE_PORT', '5000')
NOTIFICATION_URL = f'http://{SERVICE_HOST}:{SERVICE_PORT}/api/internal/notify'
POSTGRES_URL = os.getenv('POSTGRES_URL', '')
INTERNAL_SERVICE_SECRET = os.getenv('INTERNAL_SERVICE_SECRET', '')
def get_frontend_url() -> str:
    return os.getenv('FRONTEND_URL', 'https://nekazari.robotika.cloud')
FRONTEND_URL = os.getenv('FRONTEND_URL', 'https://nekazari.robotika.cloud')

CLUSTER_SMTP_HOST = os.getenv('CLUSTER_SMTP_HOST', '')
CLUSTER_SMTP_PORT = int(os.getenv('CLUSTER_SMTP_PORT', '587'))
CLUSTER_SMTP_USER = os.getenv('CLUSTER_SMTP_USER', '')
CLUSTER_SMTP_PASSWORD = os.getenv('CLUSTER_SMTP_PASSWORD', '')

ZULIP_URL = os.getenv('ZULIP_URL', '')
ZULIP_BOT_EMAIL = os.getenv('ZULIP_BOT_EMAIL', '')
ZULIP_BOT_API_KEY = os.getenv('ZULIP_BOT_API_KEY', '')

DEFAULT_TENANT = 'platform'

# ── Subscription definitions ──────────────────────────────────────────
SUBSCRIPTIONS = [
    {
        'description': 'Core Notifications - Alert entities',
        'type': 'Subscription',
        'entities': [{'type': 'Alert'}],
        'watchedAttributes': ['status'],
        'notification': {
            'endpoint': {
                'uri': NOTIFICATION_URL,
                'accept': 'application/json',
                'customHeaders': {
                    'X-Internal-Service-Secret': INTERNAL_SERVICE_SECRET,
                },
            },
            'format': 'keyValues',
            'attributes': [
                'id', 'category', 'alertType', 'description',
                'severity', 'refSourceSensor', 'affectedVariables',
                'status', 'observedAt',
            ],
        },
        'q': 'status=="active"',
        'throttling': 5,
        'isActive': True,
    },
]


# ── Subscription management ──────────────────────────────────────────


def _make_headers(tenant_id: str) -> dict:
    return inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False)


def _get_active_tenants() -> list:
    if not POSTGRES_URL:
        return [DEFAULT_TENANT]
    try:
        conn = psycopg2.connect(POSTGRES_URL)
        try:
            cur = conn.cursor()
            cur.execute(
                'SELECT DISTINCT tenant_id FROM tenants WHERE tenant_id IS NOT NULL'
            )
            rows = cur.fetchall()
            cur.close()
            return [r[0] for r in rows]
        finally:
            conn.close()
    except Exception as e:
        logger.error('Error querying active tenants: %s', e)
        return [DEFAULT_TENANT]


def _ensure_tenant_subscriptions(tenant_id: str):
    """Create missing NGSI-LD subscriptions for a single tenant."""
    headers = _make_headers(tenant_id)
    headers['Content-Type'] = 'application/json'
    try:
        response = requests.get(
            f'{ORION_URL}/ngsi-ld/v1/subscriptions',
            headers=headers,
            timeout=10,
        )
        if response.status_code != 200:
            logger.warning(
                'Failed to list subscriptions for %s: %s',
                tenant_id, response.status_code,
            )
            return
        existing = response.json() if isinstance(response.json(), list) else []

        for sub_def in SUBSCRIPTIONS:
            desc = sub_def['description']
            exists = any(s.get('description') == desc for s in existing)
            if exists:
                logger.debug(
                    "Subscription '%s' exists for tenant %s", desc, tenant_id
                )
                continue

            logger.info(
                "Creating subscription '%s' for tenant %s", desc, tenant_id
            )
            resp = requests.post(
                f'{ORION_URL}/ngsi-ld/v1/subscriptions',
                json=sub_def,
                headers=headers,
                timeout=10,
            )
            if resp.status_code not in (200, 201):
                logger.error(
                    "Failed to create subscription '%s' for %s: %s",
                    desc, tenant_id, resp.text,
                    "Failed to create subscription '%s' for %s: %.200s",
                    desc, tenant_id, resp.text or '',
                )
    except requests.RequestException as e:
        logger.error(
            'Error managing subscriptions for %s: %s', tenant_id, e,
        )


def ensure_subscriptions_for_all_tenants():
    """Create NGSI-LD subscriptions for all active tenants."""
    tenants = _get_active_tenants()
    logger.info('Ensuring notification subscriptions for %d tenants', len(tenants))
    for tenant_id in tenants:
        _ensure_tenant_subscriptions(tenant_id)


# ── Notification endpoint ────────────────────────────────────────────


@notifications_bp.route('/api/internal/notify', methods=['POST'])
def handle_notification():
    """Receive Orion-LD subscription notification.

    Validates X-Internal-Service-Secret, dispatches to handler by entity type.
    Returns 200 immediately — channel dispatch runs in background thread.
    """
    provided = request.headers.get('X-Internal-Service-Secret', '')
    if provided != INTERNAL_SERVICE_SECRET:
        logger.warning('Invalid X-Internal-Service-Secret on /notify')
        return jsonify({'error': 'Unauthorized'}), 401

    tenant_id = (
        request.headers.get('NGSILD-Tenant')
        or request.headers.get('Fiware-Service')
        or 'unknown'
    )

    body = request.get_json(force=True, silent=True)
    if not body:
        return jsonify({'status': 'ok', 'handled': 0})

    entities = body.get('data', [])
    if not entities:
        return jsonify({'status': 'ok', 'handled': 0})

    handled = 0
    for entity in entities:
        if entity.get('type') == 'Alert':
            threading.Thread(
                target=lambda e=entity: asyncio.run(
                    _handle_alert_notification(tenant_id, e)
                ),
                daemon=True,
            ).start()
            handled += 1

    logger.info('Dispatched %d Alert notifications for tenant=%s', handled, tenant_id)
    return jsonify({'status': 'ok', 'handled': handled})


# ── Alert handler ────────────────────────────────────────────────────


def _get_notification_config(tenant_id: str) -> Optional[dict]:
    """Read notification_config from PostgreSQL."""
    if not POSTGRES_URL:
        return None
    try:
        conn = psycopg2.connect(POSTGRES_URL)
        try:
            cur = conn.cursor()
            cur.execute(
                'SELECT email_config, zulip_config, webhook_config, enabled '
                'FROM admin_platform.notification_config WHERE tenant_id = %s',
                (tenant_id,),
            )
            row = cur.fetchone()
            cur.close()
            if row:
                return {
                    'email_config': row[0] or {},
                    'zulip_config': row[1] or {},
                    'webhook_config': row[2] or {},
                def _parse_jsonb(val):
                    if isinstance(val, str):
                        return json.loads(val)
                    return val or {}
                return {
                    'email_config': _parse_jsonb(row[0]),
                    'zulip_config': _parse_jsonb(row[1]),
                    'webhook_config': _parse_jsonb(row[2]),
                    'enabled': row[3] if row[3] is not None else True,
                }
            return None
        finally:
            conn.close()
    except Exception as e:
        logger.error('Error reading notification_config for %s: %s', tenant_id, e)
        return None


async def _handle_alert_notification(tenant_id: str, entity: dict) -> None:
    """Route an Alert notification to configured channels (background task)."""
    try:
        config = _get_notification_config(tenant_id)
        if not config or not config.get('enabled', True):
            logger.debug('Notifications disabled for tenant=%s', tenant_id)
            return

        payload = _build_alert_payload(entity)
        if not payload:
            logger.warning('Empty alert payload for tenant=%s', tenant_id)
            return

        channels = []

        ec = config.get('email_config', {})
        if isinstance(ec, dict) and ec.get('enabled', True):
            channels.append(_send_email_channel(ec, payload))

        zc = config.get('zulip_config', {})
        if isinstance(zc, dict) and zc.get('enabled', True):
            channels.append(_send_zulip_channel(zc, payload))

        wc = config.get('webhook_config', {})
        if isinstance(wc, dict) and wc.get('enabled', True):
            channels.append(_send_webhook_channel(wc, payload))

        if channels:
            results = await asyncio.gather(*channels, return_exceptions=True)
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error(
                        'Channel %d failed for tenant=%s: %s', i, tenant_id, r,
                    )
    except Exception as e:
        logger.error(
            'Error handling Alert notification for tenant=%s: %s',
            tenant_id, e, exc_info=True,
        )


def _build_alert_payload(entity: dict) -> dict:
    """Extract flat key-value pairs from an NGSI-LD Alert entity.

    Handles both:
    - keyValues format (values are plain types)
    - normalized format (values are {'type': 'Property', 'value': ...})
    """
    payload = {
        'id': entity.get('id', ''),
        'type': entity.get('type', 'Alert'),
    }
    for key, val in entity.items():
        if key in ('id', 'type', '@context'):
            continue
        if isinstance(val, dict) and 'value' in val:
            payload[key] = val['value']
        elif isinstance(val, dict) and val.get('type') == 'Property':
            payload[key] = val.get('value')
        elif not isinstance(val, dict):
            payload[key] = val

    alert_name = payload.get('name') or payload.get('alertName', 'Alert')
    severity = payload.get('severity', 'info')
    description = payload.get('description') or payload.get('alertMessage', '')
    payload['_summary'] = f'[{severity.upper()}] {alert_name}: {description}'
    payload['_link'] = f'{get_frontend_url()}/alerts/{payload.get("id", "")}'
    payload['_link'] = f'{FRONTEND_URL}/alerts/{payload.get("id", "")}'
    return payload


# ── Channel implementations ──────────────────────────────────────────


def _smtp_send_sync(to_addr: str, subject: str, body: str) -> None:
    """Synchronous SMTP send — runs in executor to avoid blocking event loop."""
    if not CLUSTER_SMTP_HOST:
        logger.warning('SMTP not configured, cannot send email')
        return
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = CLUSTER_SMTP_USER
    msg['To'] = to_addr
    try:
        with smtplib.SMTP(CLUSTER_SMTP_HOST, CLUSTER_SMTP_PORT) as server:
            if CLUSTER_SMTP_USER and CLUSTER_SMTP_PASSWORD:
                server.starttls()
                server.login(CLUSTER_SMTP_USER, CLUSTER_SMTP_PASSWORD)
            server.send_message(msg)
        logger.info('Email sent to %s: %s', to_addr, subject)
    except Exception as e:
        logger.error('SMTP send failed to %s: %s', to_addr, e)
        raise


async def _send_email_channel(email_config: dict, payload: dict):
    """Send alert via email using tenant-level config + cluster SMTP relay."""
    to_addr = email_config.get('to') or email_config.get('recipient', '')
    if not to_addr:
        logger.warning('No email recipient in config')
        return
    subject = f'[NKZ Alert] {payload.get("_summary", "Alert notification")}'
    email_body = (
        f'Alert: {payload.get("_summary", "N/A")}\n'
        f'Entity: {payload.get("id", "")}\n'
        f'Severity: {payload.get("severity", "info")}\n'
        f'Time: {payload.get("timestamp", payload.get("observedAt", "N/A"))}\n'
        f'Link: {payload.get("_link", "#")}\n'
        f'\n---\n'
        f'Full payload:\n{json.dumps(payload, indent=2, default=str)}'
    )
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, lambda: _smtp_send_sync(to_addr, subject, email_body)
    )


def _zulip_send_sync(stream: str, topic: str, content: str) -> None:
    """Synchronous Zulip API POST."""
    resp = requests.post(
        f'{ZULIP_URL.rstrip("/")}/api/v1/messages',
        data={
            'type': 'stream',
            'to': stream,
            'subject': topic,
            'content': content,
        },
        auth=(ZULIP_BOT_EMAIL, ZULIP_BOT_API_KEY),
        timeout=15,
    )
    if resp.status_code != 200:
        logger.error(
            'Zulip send failed: %s %s', resp.status_code, resp.text[:200],
            'Zulip send failed: %s %.200s', resp.status_code, resp.text or '',
        )
    else:
        logger.info('Zulip alert sent to stream=%s topic=%s', stream, topic)


async def _send_zulip_channel(zulip_config: dict, payload: dict):
    """Send alert to a Zulip stream/topic."""
    stream = zulip_config.get('stream', 'alerts')
    topic = zulip_config.get('topic', 'notifications')
    content = (
        f'**{payload.get("_summary", "Alert")}**\n\n'
        f'**Entity:** `{payload.get("id", "")}`\n'
        f'**Severity:** {payload.get("severity", "info")}\n'
        f'**Time:** {payload.get("timestamp", payload.get("observedAt", "N/A"))}\n'
        f'**Link:** {payload.get("_link", "#")}\n'
    )
    if not ZULIP_URL or not ZULIP_BOT_EMAIL or not ZULIP_BOT_API_KEY:
        logger.warning('Zulip not configured')
        return
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, lambda: _zulip_send_sync(stream, topic, content),
    )


def _webhook_send_sync(url: str, headers: dict, payload: dict) -> None:
    """Synchronous webhook POST."""
    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    if resp.status_code >= 400:
        logger.error(
            'Webhook %s failed: %s %s', url, resp.status_code, resp.text[:200],
            'Webhook %s failed: %s %.200s', url, resp.status_code, resp.text or '',
        )
    else:
        logger.info('Webhook sent to %s: %s', url, resp.status_code)


async def _send_webhook_channel(webhook_config: dict, payload: dict):
    """POST alert payload to a tenant-configured webhook URL."""
    url = webhook_config.get('url', '')
    if not url:
        logger.warning('No webhook URL in config')
        return
    headers = {'Content-Type': 'application/json'}
    custom_headers = webhook_config.get('headers', {})
    if isinstance(custom_headers, dict):
        headers.update(custom_headers)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, lambda: _webhook_send_sync(url, headers, payload),
    )


# ── Notification Config endpoints ────────────────────────────────────


@notifications_bp.route('/api/notifications/config', methods=['GET'])
@require_auth
def get_notification_config():
    """Get notification config for the current tenant."""
    tenant_id = request.headers.get('X-Tenant-ID', '')
    if not tenant_id:
        return jsonify({'error': 'Missing X-Tenant-ID'}), 400

    config = _get_notification_config(tenant_id)
    if config is None:
        return jsonify({
            'email_config': {},
            'zulip_config': {},
            'webhook_config': {},
            'enabled': True,
        })
    return jsonify(config)


@notifications_bp.route('/api/notifications/config', methods=['PUT'])
@require_auth
def update_notification_config():
    """Update notification config for the current tenant.

    Body: { 'email_config': {...}, 'zulip_config': {...},
            'webhook_config': {...}, 'enabled': bool }
    """
    tenant_id = request.headers.get('X-Tenant-ID', '')
    if not tenant_id:
        return jsonify({'error': 'Missing X-Tenant-ID'}), 400

    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({'error': 'Invalid or empty JSON body'}), 400

    if not POSTGRES_URL:
        return jsonify({'error': 'Database not configured'}), 500

    try:
        conn = psycopg2.connect(POSTGRES_URL)
        try:
            cur = conn.cursor()

            # Extract config fields with defaults
            email_config = json.dumps(data.get('email_config', {}))
            zulip_config = json.dumps(data.get('zulip_config', {}))
            webhook_config = json.dumps(data.get('webhook_config', {}))
            enabled = data.get('enabled', True)

            cur.execute(
                '''
                INSERT INTO admin_platform.notification_config
                    (tenant_id, email_config, zulip_config, webhook_config, enabled, updated_at)
                VALUES (%s, %s::jsonb, %s::jsonb, %s::jsonb, %s, NOW())
                ON CONFLICT (tenant_id)
                DO UPDATE SET
                    email_config = EXCLUDED.email_config,
                    zulip_config = EXCLUDED.zulip_config,
                    webhook_config = EXCLUDED.webhook_config,
                    enabled = EXCLUDED.enabled,
                    updated_at = NOW()
                ''',
                (tenant_id, email_config, zulip_config, webhook_config, enabled),
            )
            conn.commit()
            cur.close()

            logger.info('Notification config updated for tenant=%s', tenant_id)
            return jsonify({'status': 'updated'})
        finally:
            conn.close()
    except Exception as e:
        logger.error('Error updating notification config for %s: %s', tenant_id, e, exc_info=True)
        return jsonify({'error': 'Failed to update config'}), 500


@notifications_bp.route('/api/notifications/config/test', methods=['POST'])
@require_auth
def test_notification_config():
    """Send a test alert to all configured channels for the current tenant."""
    tenant_id = request.headers.get('X-Tenant-ID', '')
    if not tenant_id:
        return jsonify({'error': 'Missing X-Tenant-ID'}), 400

    config = _get_notification_config(tenant_id)
    if config is None:
        return jsonify({'error': 'No notification config found for tenant'}), 404
    if not config.get('enabled', True):
        return jsonify({'warning': 'Notifications are disabled for this tenant'})

    # Build a test alert payload
    test_payload = {
        'id': f'urn:ngsi-ld:Alert:{tenant_id}:test',
        'type': 'Alert',
        'alert_type': 'test',
        'severity': 'info',
        'description': 'This is a test alert from the notification config panel',
        'sensor_id': f'urn:ngsi-ld:AgriSensor:{tenant_id}:test-sensor',
        'sensor_name': 'test-sensor',
        'affected_variables': ['test'],
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'dashboard_url': f'{FRONTEND_URL}/entities?tenant={tenant_id}',
    }

    # Run channels synchronously and report results per channel
    async def _run_test_channels():
        results = {}
        channels = []

        ec = config.get('email_config', {})
        if isinstance(ec, dict) and ec.get('enabled', False):
            channels.append(('email', ec, _send_email_channel))
        else:
            results['email'] = 'skipped (not enabled)'

        zc = config.get('zulip_config', {})
        if isinstance(zc, dict) and zc.get('enabled', False):
            channels.append(('zulip', zc, _send_zulip_channel))
        else:
            results['zulip'] = 'skipped (not enabled)'

        wc = config.get('webhook_config', {})
        if isinstance(wc, dict) and wc.get('enabled', False):
            channels.append(('webhook', wc, _send_webhook_channel))
        else:
            results['webhook'] = 'skipped (not enabled)'

        if channels:
            outputs = await asyncio.gather(
                *(func(cfg, test_payload) for _, cfg, func in channels),
                return_exceptions=True,
            )
            for (name, _, _), out in zip(channels, outputs):
                results[name] = 'sent' if not isinstance(out, Exception) else f'error: {out}'
        return results

    results = asyncio.run(_run_test_channels())

    logger.info('Test notification sent for tenant=%s: %s', tenant_id, results)
    return jsonify({'status': 'test_completed', 'results': results})


# ── Init ─────────────────────────────────────────────────────────────


def init_notifications(app):
    """Register blueprint and bootstrap Alert subscriptions."""
    app.register_blueprint(notifications_bp)
    logger.info('Notifications blueprint registered')
    try:
        ensure_subscriptions_for_all_tenants()
        logger.info('Alert subscription bootstrap complete')
    except Exception as e:
        logger.error(
            'Alert subscription bootstrap failed (non-fatal): %s', e, exc_info=True,
        )
