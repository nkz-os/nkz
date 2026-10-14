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


def _notify(body):
    with patch.object(bp, '_ensure_worker'):  # drained explicitly below, no background thread
        return client.post('/api/internal/notify/crop-cycles', json=body, headers={'NGSILD-Tenant': 't'})


def test_reconcile_all_requires_secret():
    assert client.post('/api/internal/crop-cycles/reconcile-all').status_code == 401


def test_broker_rejection_is_502():
    with patch.object(svc, 'timeline', side_effect=svc.BrokerRejected('400 bad request')):
        assert client.get('/api/entities/parcels/p1/crop-cycles').status_code == 502


def test_route_without_at_lets_the_service_use_the_parcel_day():
    with patch.object(svc, 'timeline', return_value={'ok': 1}) as tl:
        assert client.get('/api/entities/parcels/p1/crop-cycles').status_code == 200
    assert tl.call_args.args[2] is None


# ── The writer moved to field-operations: entity-manager only retires its subscriptions ──

def test_notify_is_accepted_and_does_nothing():
    with patch.object(svc, 'timeline') as tl:
        r = client.post('/api/internal/notify/crop-cycles', headers={**H, 'NGSILD-Tenant': 't'},
                        json={'data': [{'type': 'AgriCrop', 'hasAgriParcel': 'urn:ngsi-ld:AgriParcel:p1'}]})
    assert r.status_code == 204 and r.data == b''
    tl.assert_not_called()
    assert not hasattr(bp, 'drain_pending') and not hasattr(bp, '_pending')  # no worker, no queue


def test_reconcile_all_retires_the_crop_cycle_subscriptions():
    with patch('blueprints.notifications._get_active_tenants', return_value=['a', 'b'], create=True), \
            patch.object(bp, 'retire_crop_cycle_subscriptions', return_value={'removed': 4, 'absent': 0, 'errors': []}) as retire:
        r = client.post('/api/internal/crop-cycles/reconcile-all', headers=H)
    assert r.status_code == 200 and r.json['removed'] == 4
    retire.assert_called_once_with(['a', 'b'])


def test_retire_removes_only_the_entity_manager_crop_cycle_subscriptions():
    class FakeRegistrar:
        def __init__(self, **kw):
            self.kw = kw

        async def remove_all(self, tenants):
            return {'removed': len(tenants), 'absent': 0, 'errors': [], 'tenants': tenants, 'registrar': self}

    with patch.object(bp, 'SubscriptionRegistrar', FakeRegistrar), \
            patch('blueprints.notifications.registrar_auth_kwargs', return_value={}, create=True):
        result = bp.retire_crop_cycle_subscriptions(['a', 'b'])
    assert result['tenants'] == ['a', 'b']
    # Only this service's own subscriptions: its module name and its two types.
    reg = result['registrar']
    assert reg.kw['module_name'] == 'entity-manager-crop-cycles'
    assert {s['type'] for s in reg.kw['subscriptions']} == {'AgriCrop', 'AgriParcelOperation'}


def test_no_writer_left_in_the_service():
    assert not hasattr(svc, 'reconcile_parcel') and not hasattr(svc, 'list_parcels')
