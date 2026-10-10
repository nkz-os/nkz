"""Crop cycle endpoints and the reconciler wiring.

Read: GET .../crop-cycles (user via gateway, modules via the internal route).
Write: AgriCrop / AgriParcelOperation notifications -> reconcile the parcel.
"""
import asyncio
import hmac
import logging
import os
from datetime import date, datetime, timezone

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
# Throttling 0, explicit (the SDK default is 30 s): Orion drops the notifications that
# fall inside the window, so a burst of edits would only be seen by the daily sweep.
# The reconciler writes only on change, so its own writes do not loop.
CROP_CYCLE_SUBSCRIPTIONS = [
    {'type': 'AgriCrop', 'throttling': 0},
    {'type': 'AgriParcelOperation', 'throttling': 0},
]


def _secret_ok() -> bool:
    provided = request.headers.get('X-Internal-Service-Secret', '')
    return bool(INTERNAL_SERVICE_SECRET) and hmac.compare_digest(provided, INTERNAL_SERVICE_SECRET)


def _at():
    raw = request.args.get('at')
    if not raw:
        return datetime.now(timezone.utc).date()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _serve(tenant_id: str, parcel_id: str):
    at = _at()
    if at is None:
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
    # Same flag-gated receiver auth as the other notification endpoints.
    from blueprints.notifications import _notify_unauthorized
    if _notify_unauthorized():
        logger.warning('Invalid X-Internal-Service-Secret on /notify/crop-cycles')
        return jsonify({'error': 'Unauthorized'}), 401
    tenant_id = request.headers.get('NGSILD-Tenant') or request.headers.get('Fiware-Service')
    body = request.get_json(force=True, silent=True) or {}
    if tenant_id:
        for parcel in svc.parcels_in_notification(body.get('data', [])):
            try:
                svc.reconcile_parcel(tenant_id, parcel)
            except Exception as e:  # noqa: BLE001 — one parcel must not drop the batch
                logger.warning('crop-cycle reconcile failed tenant=%s parcel=%s: %s', tenant_id, parcel, e)
    return '', 204


@crop_cycles_bp.route('/api/internal/crop-cycles/reconcile-all', methods=['POST'])
def reconcile_all():
    if not _secret_ok():
        return jsonify({'error': 'Unauthorized'}), 401
    from blueprints.notifications import _get_active_tenants
    stats = {'tenants': 0, 'parcels': 0, 'written': 0, 'errors': 0}
    tenants = _get_active_tenants()
    # A tenant created after start-up gets its subscriptions here (idempotent).
    try:
        ensure_crop_cycle_subscriptions(tenants)
    except Exception as e:  # noqa: BLE001 — the sweep itself still runs
        logger.warning('reconcile-all: crop-cycle subscriptions not ensured: %s', e)
        stats['errors'] += 1
    for tenant_id in tenants:
        stats['tenants'] += 1
        try:
            parcels = svc.list_parcels(tenant_id)
        except Exception as e:  # noqa: BLE001
            logger.warning('reconcile-all: parcels unreadable tenant=%s: %s', tenant_id, e)
            stats['errors'] += 1
            continue
        for parcel in parcels:
            stats['parcels'] += 1
            try:
                stats['written'] += len(svc.reconcile_parcel(tenant_id, parcel))
            except Exception as e:  # noqa: BLE001
                logger.warning('reconcile-all failed tenant=%s parcel=%s: %s', tenant_id, parcel, e)
                stats['errors'] += 1
    return jsonify(stats), 200


def ensure_crop_cycle_subscriptions(tenants=None):
    from blueprints.notifications import _get_active_tenants
    registrar = SubscriptionRegistrar(
        orion_url=svc.ORION_URL,
        notification_url=NOTIFICATION_URL,
        subscriptions=CROP_CYCLE_SUBSCRIPTIONS,
        module_name='entity-manager-crop-cycles',
        notification_headers=({'X-Internal-Service-Secret': INTERNAL_SERVICE_SECRET}
                              if INTERNAL_SERVICE_SECRET else None),
    )
    result = asyncio.run(registrar.ensure_all(tenants if tenants is not None else _get_active_tenants()))
    logger.info('crop-cycle subscriptions: created=%d skipped=%d errors=%d',
                result['created'], result['skipped'], len(result['errors']))


def init_crop_cycles(app):
    app.register_blueprint(crop_cycles_bp)
    try:
        ensure_crop_cycle_subscriptions()
    except Exception as e:  # noqa: BLE001 — never block startup on the broker
        logger.error('crop-cycle subscription bootstrap failed (non-fatal): %s', e, exc_info=True)
