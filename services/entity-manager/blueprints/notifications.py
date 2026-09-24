"""
Notifications Blueprint — Device/ManufacturingMachine → DeviceMeasurement pipeline.
"""

import asyncio
import hmac
import json
import logging
import os
from typing import Any, Dict, List, Optional

import psycopg2
import requests
from flask import Blueprint, jsonify, request
from psycopg2.extras import RealDictCursor

from nkz_platform_sdk import SubscriptionRegistrar, SyncOrionClient

logger = logging.getLogger(__name__)

notifications_bp = Blueprint('notifications', __name__)

# ── Configuration ──────────────────────────────────────────────────────
ORION_URL = os.getenv('ORION_URL', 'http://orion-ld-service:1026')
SERVICE_HOST = os.getenv('SERVICE_HOST', 'entity-manager-service')
SERVICE_PORT = os.getenv('SERVICE_PORT', '5000')
# Device/ManufacturingMachine -> DeviceMeasurement pipeline (see
# ensure_device_measurement_subscriptions below). The SDK's SubscriptionRegistrar
# supports notification_headers (receiverInfo), so the subscription targeting this
# route carries X-Internal-Service-Secret and the route is flag-gated like the
# other receivers (NOTIFY_REQUIRE_INTERNAL_SECRET).
DEVICE_MEASUREMENT_NOTIFICATION_URL = (
    f'http://{SERVICE_HOST}:{SERVICE_PORT}/api/internal/notify/measurements'
)
POSTGRES_URL = os.getenv('POSTGRES_URL', '')
INTERNAL_SERVICE_SECRET = os.getenv('INTERNAL_SERVICE_SECRET', '')

DEFAULT_TENANT = 'platform'


# ── Subscription management ──────────────────────────────────────────


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


# ── Device/ManufacturingMachine -> DeviceMeasurement subscriptions ────
#
# entity-manager reads Device and ManufacturingMachine attribute updates and turns them
# into DeviceMeasurement entities (see _handle_device_measurement_notification below and
# blueprints/measurements.py:build_measurements). Deliberately NOT subscribed here:
# DeviceMeasurement itself — that is what this pipeline WRITES, so subscribing to it would
# feed entity-manager its own output.
#
# Uses the SDK's SubscriptionRegistrar (deterministic subscription id + Orion's own 409 as
# the concurrency arbiter) rather than the hand-rolled list-then-create above: this service
# runs under gunicorn with several worker processes, and a plain listing read is not safe
# against several of them calling ensure at once.
DEVICE_MEASUREMENT_SUBSCRIPTIONS = [
    {'type': 'Device', 'throttling': 30},
    {'type': 'ManufacturingMachine', 'throttling': 30},
]


def ensure_device_measurement_subscriptions():
    """Ensure the Device/ManufacturingMachine subscriptions exist for every active tenant."""
    tenants = _get_active_tenants()
    registrar = SubscriptionRegistrar(
        orion_url=ORION_URL,
        notification_url=DEVICE_MEASUREMENT_NOTIFICATION_URL,
        subscriptions=DEVICE_MEASUREMENT_SUBSCRIPTIONS,
        module_name='entity-manager-measurements',
        notification_headers=(
            {'X-Internal-Service-Secret': INTERNAL_SERVICE_SECRET}
            if INTERNAL_SERVICE_SECRET else None
        ),
    )
    result = asyncio.run(registrar.ensure_all(tenants))
    logger.info(
        'Device measurement subscription heal: created=%d skipped=%d errors=%d',
        result['created'], result['skipped'], len(result['errors']),
    )


def _secret_matches(provided):
    """Constant-time check of the internal service secret."""
    return bool(INTERNAL_SERVICE_SECRET) and hmac.compare_digest(
        provided or '', INTERNAL_SERVICE_SECRET
    )


def _notify_unauthorized() -> bool:
    """Flag-gated auth for notification receivers (two-phase rollout).

    NOTIFY_REQUIRE_INTERNAL_SECRET stays off until subscription creators have
    converged to carry receiverInfo; then it flips on with no code deploy.
    """
    require = os.getenv('NOTIFY_REQUIRE_INTERNAL_SECRET', '').lower() in (
        '1', 'true', 'yes', 'on'
    )
    if not require:
        return False
    return not _secret_matches(request.headers.get('X-Internal-Service-Secret', ''))


def _get_sensor_profile_by_code(tenant_id: str, profile_code: str) -> Optional[dict]:
    """Look up a sensor_profiles row by code (tenant override wins over the global row).

    Same query shape as blueprints/sensors.py's profile lookup — that is the established
    read path for this table in this service.
    """
    if not POSTGRES_URL or not profile_code:
        return None
    try:
        conn = psycopg2.connect(POSTGRES_URL)
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """
                SELECT id, sdm_entity_type, mapping
                FROM sensor_profiles
                WHERE code = %s AND (tenant_id IS NULL OR tenant_id = %s)
                ORDER BY tenant_id NULLS LAST
                LIMIT 1
                """,
                (profile_code, tenant_id),
            )
            row = cur.fetchone()
            cur.close()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception as e:
        logger.error(
            'Error reading sensor_profiles for code=%s (tenant=%s): %s',
            profile_code, tenant_id, e,
        )
        return None


def _handle_device_measurement_notification(tenant_id: str, entity: dict) -> bool:
    """Turn one Device/ManufacturingMachine notification into DeviceMeasurement writes.

    Returns True once the entity was handled (including the legitimate "nothing to write"
    case — no profileCode, no matching profile, or a profile with no measurement
    attributes present on this entity), False only for an unexpected failure building or
    writing the batch, so callers can distinguish "processed" from "errored".

    Imports build_measurements lazily, only once there is an actual profile to build
    against (not at module level, and not at the top of this function): blueprints/
    measurements.py pulls in common.unit_codes at import time, and entity-manager's test
    suite stubs `common` as a bare MagicMock for files that never touch it — importing
    eagerly would break every one of those unrelated test files at collection time, and
    importing unconditionally at the top of this function would do the same to any test
    that exercises the no-profileCode/no-matching-profile early-outs below without also
    setting up common.unit_codes. See tests/test_measurement_transformer.py's docstring
    for the same constraint from the other side.
    """
    entity_id = entity.get('id', '')
    profile_code_attr = entity.get('profileCode')
    profile_code = (
        profile_code_attr.get('value') if isinstance(profile_code_attr, dict) else None
    )
    if not profile_code:
        logger.debug('No profileCode on %s, skipping measurement build', entity_id)
        return True

    profile = _get_sensor_profile_by_code(tenant_id, profile_code)
    if not profile:
        logger.warning(
            'No sensor_profiles row for code=%s (entity=%s, tenant=%s)',
            profile_code, entity_id, tenant_id,
        )
        return True

    from blueprints.measurements import build_measurements

    try:
        measurements = build_measurements(entity, profile)
    except Exception as e:
        # Task 1 made an unknown unit raise rather than guess a code. Operationally that
        # means: log it loudly and drop this one notification, not crash the request or
        # write a partial/garbage batch.
        logger.error(
            'build_measurements failed for %s (profile=%s, tenant=%s): %s',
            entity_id, profile_code, tenant_id, e,
        )
        return False

    if not measurements:
        return True

    try:
        client = SyncOrionClient(tenant_id, base_url=ORION_URL)
        result = client.upsert_entities_batch(measurements)
        logger.info(
            'Wrote %d DeviceMeasurement entities for %s (tenant=%s)',
            result.get('upserted', 0), entity_id, tenant_id,
        )
        if result.get('errors'):
            logger.warning(
                'Partial DeviceMeasurement upsert errors for %s: %s',
                entity_id, result['errors'],
            )
        return True
    except Exception as e:
        logger.error(
            'Failed to write DeviceMeasurement batch for %s (tenant=%s): %s',
            entity_id, tenant_id, e, exc_info=True,
        )
        return False


@notifications_bp.route('/api/internal/notify/measurements', methods=['POST'])
def handle_device_measurement_notification():
    """Receive Device/ManufacturingMachine notifications, write DeviceMeasurement entities.

    Flag-gated on X-Internal-Service-Secret (NOTIFY_REQUIRE_INTERNAL_SECRET): the
    SDK subscription targeting this route now carries receiverInfo, so the gate can
    close once the two-phase rollout flips it on.
    """
    if _notify_unauthorized():
        logger.warning('Invalid X-Internal-Service-Secret on /notify/measurements')
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
        if entity.get('type') in ('Device', 'ManufacturingMachine'):
            if _handle_device_measurement_notification(tenant_id, entity):
                handled += 1

    logger.info(
        'Processed %d device/machine notifications for tenant=%s', handled, tenant_id,
    )
    return jsonify({'status': 'ok', 'handled': handled})

# ── Init ─────────────────────────────────────────────────────────────


def init_notifications(app):
    """Register blueprint and bootstrap device measurement subscriptions."""
    app.register_blueprint(notifications_bp)
    logger.info('Notifications blueprint registered')
    try:
        ensure_device_measurement_subscriptions()
        logger.info('Device measurement subscription bootstrap complete')
    except Exception as e:
        logger.error(
            'Device measurement subscription bootstrap failed (non-fatal): %s', e, exc_info=True,
        )
