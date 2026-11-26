"""A gateway (a Device whose deviceCategory includes "network") is not a sensor: it uses up
no slot of the sensor limit and is not in the usage figures. Other types are untouched."""
from unittest.mock import MagicMock, patch

from requests.structures import CaseInsensitiveDict

from tests.telemetry_fakes import TENANT  # noqa: F401  (puts the service dirs on sys.path)

from helpers import tenant_limits  # noqa: E402


GATEWAY_Q = '(deviceCategory!="network"|!deviceCategory)'


def _orion(total_with_gateways, gateways, seen):
    """Orion double: the unfiltered Device count includes the gateways, the filtered one not."""
    def get(url, params=None, headers=None, timeout=None):
        seen.append((params["type"], params.get("q")))
        if params["type"] != "Device":
            count = 0
        elif params.get("q") == GATEWAY_Q:
            count = total_with_gateways - gateways
        else:
            count = total_with_gateways
        return MagicMock(status_code=200, headers=CaseInsensitiveDict({"NGSILD-Results-Count": str(count)}))
    return get


def test_the_sensor_count_leaves_gateways_out_and_other_types_alone():
    seen = []
    with patch.object(tenant_limits.requests, "get", side_effect=_orion(5, 3, seen)):
        assert tenant_limits._count_sensors("t") == 2
    assert ("Device", GATEWAY_Q) in seen
    assert all(q is None for t, q in seen if t != "Device")


def test_the_usage_figures_do_not_count_gateways():
    seen = []
    with patch.object(tenant_limits.requests, "get", side_effect=_orion(5, 3, seen)), \
            patch.object(tenant_limits, "_count_robots", return_value=0), \
            patch.object(tenant_limits, "_count_parcels", return_value=0), \
            patch.object(tenant_limits, "_sum_parcel_area", return_value=0):
        assert tenant_limits._gather_usage_for_tenant("t")["sensors"] == 2


def _create_device(limit, total_with_gateways, gateways):
    from flask import Flask, g
    import blueprints.entities as entities

    app = Flask(__name__)

    @app.before_request
    def _identity():
        g.tenant = TENANT
        g.current_user = {"tenant_id": TENANT, "user_id": "u1"}
        g.roles = ["TenantAdmin"]

    app.register_blueprint(entities.entities_bp)
    seen = []
    post = MagicMock(status_code=201, text="")
    with patch.object(entities, "get_limits_for_tenant", return_value={"maxSensors": limit}), \
            patch.object(entities, "require_entity_ownership", create=True), \
            patch.object(tenant_limits.requests, "get", side_effect=_orion(total_with_gateways, gateways, seen)), \
            patch.object(entities.requests, "post", return_value=post):
        resp = app.test_client().post("/instances/Device",
                                      headers={"Authorization": "Bearer t"},
                                      json={"name": "x"})
    return resp, seen


def _is_sensor_limit(resp):
    return resp.status_code == 403 and resp.get_json().get("error") == "Sensor limit exceeded"


def test_creating_a_sensor_is_not_blocked_by_gateways():
    resp, seen = _create_device(limit=3, total_with_gateways=5, gateways=3)   # 2 sensors < 3
    assert not _is_sensor_limit(resp), resp.get_data()
    assert ("Device", GATEWAY_Q) in seen


def test_the_sensor_limit_still_applies_to_the_sensors():
    resp, _ = _create_device(limit=2, total_with_gateways=5, gateways=3)      # 2 sensors >= 2
    assert _is_sensor_limit(resp)
