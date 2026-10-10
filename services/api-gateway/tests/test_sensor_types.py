"""Sensor stats and listing query the canonical Device type plus the legacy ones."""

import fiware_api_gateway as gw

EXPECTED = "Device,AgriSensor,AgriDevice"


def _patch_auth(monkeypatch, tenant="montiko"):
    monkeypatch.setattr(gw, "get_request_token", lambda: "tok")
    monkeypatch.setattr(gw, "validate_jwt_token", lambda t: {"tenant_id": tenant})
    monkeypatch.setattr(gw, "extract_tenant_id", lambda p: tenant)


class _Resp:
    content = b"[]"
    status_code = 200
    headers = {"NGSILD-Results-Count": "3"}


def _capture(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        seen["params"] = dict(params or {})
        return _Resp()

    monkeypatch.setattr(gw.requests, "get", fake_get)
    return seen


def test_sensor_stats_counts_canonical_and_legacy_types(monkeypatch):
    _patch_auth(monkeypatch)
    seen = _capture(monkeypatch)
    with gw.app.test_request_context("/api/sensors/stats"):
        gw.get_sensor_stats()
    assert seen["params"]["type"] == EXPECTED


def test_sensor_listing_queries_canonical_and_legacy_types(monkeypatch):
    _patch_auth(monkeypatch)
    seen = _capture(monkeypatch)
    with gw.app.test_request_context("/api/sensors"):
        gw.get_sensors()
    assert seen["params"]["type"] == EXPECTED
