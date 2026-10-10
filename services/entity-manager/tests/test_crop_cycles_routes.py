import os
import sys
import types
from unittest.mock import patch

os.environ.setdefault('INTERNAL_SERVICE_SECRET', 'test-secret')
_em = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
for _p in (_em, os.path.join(_em, '..')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from flask import Flask, g  # noqa: E402

_auth = types.ModuleType('common.auth_middleware')


def _require_auth(f):
    from functools import wraps

    @wraps(f)
    def w(*a, **k):
        g.tenant_id = 'tenant-a'
        return f(*a, **k)
    return w


_auth.require_auth = _require_auth

# Stub the auth decorator only while the blueprint binds it, so other test
# modules keep the real common.auth_middleware.
import crop_cycles_service as svc  # noqa: E402

sys.modules.pop('blueprints.crop_cycles', None)
with patch.dict(sys.modules, {'common.auth_middleware': _auth}):
    import blueprints.crop_cycles as bp  # noqa: E402

app = Flask(__name__)
app.register_blueprint(bp.crop_cycles_bp)
client = app.test_client()
H = {'X-Internal-Service-Secret': 'test-secret'}


def test_crop_cycles_route_200():
    with patch.object(svc, 'timeline', return_value={'current': None}) as t:
        r = client.get('/api/entities/parcels/p1/crop-cycles?at=2026-10-09')
    assert r.status_code == 200 and r.json == {'current': None}
    assert t.call_args.args[:2] == ('tenant-a', 'urn:ngsi-ld:AgriParcel:p1')


def test_crop_cycles_route_404_and_503():
    with patch.object(svc, 'timeline', side_effect=svc.ParcelNotFound('x')):
        assert client.get('/api/entities/parcels/p1/crop-cycles').status_code == 404
    with patch.object(svc, 'timeline', side_effect=svc.OrionUnavailable('x')):
        assert client.get('/api/entities/parcels/p1/crop-cycles').status_code == 503


def test_crop_cycles_route_503():
    with patch.object(svc, 'timeline', side_effect=svc.OrionUnavailable('down')):
        r = client.get('/api/entities/parcels/p1/crop-cycles')
    assert r.status_code == 503


def test_bad_at_is_400():
    assert client.get('/api/entities/parcels/p1/crop-cycles?at=09-10-2026').status_code == 400


def test_internal_route_requires_secret_and_tenant():
    assert client.get('/api/internal/parcels/p1/crop-cycles?tenant_id=t').status_code == 401
    assert client.get('/api/internal/parcels/p1/crop-cycles', headers=H).status_code == 422
    with patch.object(svc, 'timeline', return_value={'ok': 1}):
        r = client.get('/api/internal/parcels/p1/crop-cycles?tenant_id=t', headers=H)
    assert r.status_code == 200


def test_notify_returns_204_and_reconciles_each_parcel():
    body = {'data': [{'type': 'AgriCrop', 'hasAgriParcel': {'type': 'Relationship', 'object': 'urn:ngsi-ld:AgriParcel:p1'}}]}
    with patch.object(svc, 'reconcile_parcel', return_value=[]) as rec:
        r = client.post('/api/internal/notify/crop-cycles', json=body, headers={'NGSILD-Tenant': 't'})
    assert r.status_code == 204 and r.data == b''
    rec.assert_called_once_with('t', 'urn:ngsi-ld:AgriParcel:p1')


def test_notify_survives_reconcile_errors():
    body = {'data': [{'type': 'AgriCrop', 'hasAgriParcel': 'urn:ngsi-ld:AgriParcel:p1'}]}
    with patch.object(svc, 'reconcile_parcel', side_effect=svc.OrionUnavailable('x')):
        assert client.post('/api/internal/notify/crop-cycles', json=body, headers={'NGSILD-Tenant': 't'}).status_code == 204


def test_subscription_spec():
    types_ = {s['type'] for s in bp.CROP_CYCLE_SUBSCRIPTIONS}
    assert types_ == {'AgriCrop', 'AgriParcelOperation'}


def test_reconcile_all_walks_every_parcel_of_every_tenant():
    import blueprints.notifications as notif
    with patch.object(notif, '_get_active_tenants', return_value=['a', 'b']), \
         patch.object(svc, 'list_parcels', side_effect=lambda t: [f'urn:ngsi-ld:AgriParcel:{t}1', f'urn:ngsi-ld:AgriParcel:{t}2']), \
         patch.object(svc, 'reconcile_parcel', return_value=[('x', 'y')]) as rec, \
         patch.object(bp, 'ensure_crop_cycle_subscriptions'):
        r = client.post('/api/internal/crop-cycles/reconcile-all', headers=H)
    assert r.status_code == 200
    assert r.json == {'tenants': 2, 'parcels': 4, 'written': 4, 'errors': 0}
    assert rec.call_count == 4


def test_reconcile_all_requires_secret():
    assert client.post('/api/internal/crop-cycles/reconcile-all').status_code == 401


def test_notify_is_gated_when_the_flag_is_on(monkeypatch):
    monkeypatch.setenv('NOTIFY_REQUIRE_INTERNAL_SECRET', 'true')
    body = {'data': [{'type': 'AgriCrop', 'hasAgriParcel': 'urn:ngsi-ld:AgriParcel:p1'}]}
    with patch.object(svc, 'reconcile_parcel', return_value=[]) as rec:
        r = client.post('/api/internal/notify/crop-cycles', json=body, headers={'NGSILD-Tenant': 't'})
        assert r.status_code == 401 and rec.call_count == 0
        r = client.post('/api/internal/notify/crop-cycles', json=body, headers={'NGSILD-Tenant': 't', **H})
        assert r.status_code == 204 and rec.call_count == 1


def test_broker_rejection_is_502():
    with patch.object(svc, 'timeline', side_effect=svc.BrokerRejected('400 bad request')):
        assert client.get('/api/entities/parcels/p1/crop-cycles').status_code == 502


def test_subscriptions_are_not_throttled():
    # Orion drops notifications inside a throttling window: a burst of edits would only
    # reach the reconciler through the daily sweep.
    from nkz_platform_sdk.subscriptions import SubscriptionDef
    assert all(SubscriptionDef(**s).throttling == 0 for s in bp.CROP_CYCLE_SUBSCRIPTIONS)


def test_reconcile_all_ensures_every_tenant_subscriptions():
    import blueprints.notifications as notif
    with patch.object(notif, '_get_active_tenants', return_value=['a', 'b']), \
         patch.object(svc, 'list_parcels', return_value=[]), \
         patch.object(bp, 'ensure_crop_cycle_subscriptions') as ensure:
        r = client.post('/api/internal/crop-cycles/reconcile-all', headers=H)
    assert r.status_code == 200
    ensure.assert_called_once_with(['a', 'b'])
