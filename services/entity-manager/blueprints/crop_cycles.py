"""Crop cycle read endpoints, and retirement of the old writer's subscriptions.

Read: GET .../crop-cycles (user via gateway, modules via the internal route).
Write: moved to field-operations, which records sowings and harvests. This
module only removes the crop-cycle subscriptions it used to own, at start-up
and on every sweep call, so nothing keeps delivering here.
"""
import asyncio
import hmac
import logging
import os
from datetime import date

from flask import Blueprint, g, jsonify, request
from nkz_platform_sdk import SubscriptionRegistrar

import crop_cycles_service as svc
from common.auth_middleware import require_auth

logger = logging.getLogger(__name__)
crop_cycles_bp = Blueprint('crop_cycles', __name__)

INTERNAL_SERVICE_SECRET = os.getenv('INTERNAL_SERVICE_SECRET', '')
SERVICE_HOST = os.getenv('SERVICE_HOST', 'entity-manager-service')
SERVICE_PORT = os.getenv('SERVICE_PORT', '5000')
NOTIFICATION_URL = f'http://{SERVICE_HOST}:{SERVICE_PORT}/api/internal/notify/crop-cycles'
# The subscriptions this service used to own, declared only so remove_all finds
# them by their deterministic ids (module name + type).
CROP_CYCLE_SUBSCRIPTIONS = [
    {'type': 'AgriCrop', 'throttling': None},
    {'type': 'AgriParcelOperation', 'throttling': None},
]


def _secret_ok() -> bool:
    provided = request.headers.get('X-Internal-Service-Secret', '')
    return bool(INTERNAL_SERVICE_SECRET) and hmac.compare_digest(provided, INTERNAL_SERVICE_SECRET)


_BAD_AT = object()


def _at():
    """The `at` query date; None when absent (the service then uses today at the parcel)."""
    raw = request.args.get('at')
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return _BAD_AT


def _serve(tenant_id: str, parcel_id: str):
    at = _at()
    if at is _BAD_AT:
        return jsonify({'error': 'at must be YYYY-MM-DD'}), 400
    try:
        return jsonify(svc.timeline(tenant_id, svc.normalize_parcel_urn(parcel_id), at)), 200
    except svc.ParcelNotFound:
        return jsonify({'error': 'parcel not found'}), 404
    except svc.BrokerRejected as e:
        logger.error('crop-cycles: broker rejected the request tenant=%s parcel=%s: %s', tenant_id, parcel_id, e)
        return jsonify({'error': 'context broker rejected the request'}), 502
    except svc.OrionUnavailable as e:
        logger.warning('crop-cycles unavailable tenant=%s parcel=%s: %s', tenant_id, parcel_id, e)
        return jsonify({'error': 'context broker unavailable'}), 503


@crop_cycles_bp.route('/api/entities/parcels/<path:parcel_id>/crop-cycles', methods=['GET'])
@require_auth
def get_crop_cycles(parcel_id):
    tenant_id = getattr(g, 'tenant', None) or getattr(g, 'tenant_id', None)
    return _serve(tenant_id, parcel_id)


@crop_cycles_bp.route('/api/internal/parcels/<path:parcel_id>/crop-cycles', methods=['GET'])
def get_crop_cycles_internal(parcel_id):
    if not _secret_ok():
        return jsonify({'error': 'Unauthorized'}), 401
    tenant_id = request.args.get('tenant_id')
    if not tenant_id:
        return jsonify({'error': 'tenant_id is required'}), 422
    return _serve(tenant_id, parcel_id)


@crop_cycles_bp.route('/api/internal/notify/crop-cycles', methods=['POST'])
def notify_crop_cycles():
    # The writer moved to field-operations; deliveries that still arrive before
    # the subscriptions are retired are acknowledged and dropped.
    return '', 204


@crop_cycles_bp.route('/api/internal/crop-cycles/reconcile-all', methods=['POST'])
def reconcile_all():
    """Kept for the sweep CronJob until it is removed: retires the old subscriptions."""
    if not _secret_ok():
        return jsonify({'error': 'Unauthorized'}), 401
    from blueprints.notifications import _get_active_tenants
    result = retire_crop_cycle_subscriptions(_get_active_tenants())
    return jsonify({k: v for k, v in result.items() if k != 'errors'} | {'errors': len(result.get('errors') or [])}), 200


def retire_crop_cycle_subscriptions(tenants=None):
    """Remove the crop-cycle subscriptions this service owned (only those) in every tenant."""
    from blueprints.notifications import _get_active_tenants, registrar_auth_kwargs
    registrar = SubscriptionRegistrar(
        orion_url=svc.ORION_URL,
        notification_url=NOTIFICATION_URL,
        subscriptions=CROP_CYCLE_SUBSCRIPTIONS,
        module_name='entity-manager-crop-cycles',
        **registrar_auth_kwargs(),
    )
    result = asyncio.run(registrar.remove_all(tenants if tenants is not None else _get_active_tenants()))
    log = logger.warning if result.get('errors') else logger.info
    log('crop-cycle subscriptions retired: removed=%d absent=%d errors=%d',
        result.get('removed', 0), result.get('absent', 0), len(result.get('errors') or []))
    return result


def init_crop_cycles(app):
    app.register_blueprint(crop_cycles_bp)
    try:
        retire_crop_cycle_subscriptions()
    except Exception as e:  # noqa: BLE001 — never block startup on the broker
        logger.error('crop-cycle subscription retirement failed (non-fatal): %s', e, exc_info=True)
