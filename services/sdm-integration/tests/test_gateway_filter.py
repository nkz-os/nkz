"""Gateways (Device with deviceCategory network) stay out of Device lists and the sensor limit."""

import functools
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")
for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

ORION = "http://orion.invalid:1026"
CONTEXT = "http://context.invalid/ngsi-ld-context.json"
GATEWAY_Q = '(deviceCategory!="network"|!deviceCategory)'


@pytest.fixture(scope="module")
def sdm():
    from flask import g

    mp = pytest.MonkeyPatch()
    mp.setenv("ORION_URL", ORION)
    mp.setenv("MONGODB_URL", "mongodb://localhost:1/x")
    mp.setenv("CONTEXT_URL", CONTEXT)
    saved = sys.modules.pop("sdm_api", None)

    import auth_middleware

    def fake_auth(f):
        @functools.wraps(f)
        def wrapper(*a, **k):
            g.tenant = "test-tenant"
            g.farmer_id = "user-1"
            return f(*a, **k)

        return wrapper

    mp.setattr(auth_middleware, "require_auth", fake_auth)
    mp.setattr(auth_middleware, "require_entity_ownership", lambda f: f)
    import sdm_api

    yield sdm_api

    sys.modules.pop("sdm_api", None)
    if saved is not None:
        sys.modules["sdm_api"] = saved
    mp.undo()


def _resp(entities=(), count=0):
    r = MagicMock(status_code=200, text="")
    r.headers = {"NGSILD-Results-Count": str(count)}
    r.json.return_value = list(entities)
    return r


@pytest.mark.parametrize("entity_type", ["Device", "AgriSensor", "WeatherStation"])
def test_limit_count_excludes_gateways_for_device_only(sdm, entity_type):
    with patch.object(sdm.requests, "get", return_value=_resp(count=0)) as get:
        assert sdm._check_tenant_limits("test-tenant", entity_type) is True
    kwargs = get.call_args.kwargs
    if entity_type == "Device":
        assert kwargs["params"]["q"] == GATEWAY_Q
        assert kwargs["headers"]["Link"].startswith(f"<{CONTEXT}>")
    else:
        assert "q" not in kwargs["params"]


def test_gateways_do_not_use_up_the_sensor_limit(sdm):
    """Orion filters them out server side: the count it returns is what is compared."""
    with patch.object(sdm, "MAX_SENSORS_PER_TENANT", 3), \
         patch.object(sdm.requests, "get", return_value=_resp(count=2)):
        assert sdm._check_tenant_limits("test-tenant", "Device") is True
    with patch.object(sdm, "MAX_SENSORS_PER_TENANT", 3), \
         patch.object(sdm.requests, "get", return_value=_resp(count=3)):
        assert sdm._check_tenant_limits("test-tenant", "Device") is False


@pytest.mark.parametrize("entity_type,filtered", [
    ("Device", True), ("AgriSensor", False), ("AgriParcel", False)])
def test_list_instances_filters_only_devices(sdm, entity_type, filtered):
    sensor = {"id": "urn:ngsi-ld:Device:test-tenant:s1", "type": entity_type}
    with patch.object(sdm.requests, "get", return_value=_resp([sensor], 1)) as get, \
         patch.object(sdm, "log_entity_operation"):
        r = sdm.app.test_client().get(f"/sdm/entities/{entity_type}/instances?limit=5&offset=2")
    assert r.status_code == 200
    assert r.get_json()["instances"] == [sensor]
    assert get.call_count == 2  # count query and listing: both filtered
    for call in get.call_args_list:
        params = call.kwargs["params"]
        assert params["type"] == entity_type
        if filtered:
            assert params["q"] == GATEWAY_Q
            assert call.kwargs["headers"]["Link"].startswith(f"<{CONTEXT}>")
        else:
            assert "q" not in params
    assert get.call_args_list[1].kwargs["params"]["limit"] == 5
    assert get.call_args_list[1].kwargs["params"]["offset"] == 2
