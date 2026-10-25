"""Tenant sensor API (register, dataloggers, devices, profiles) is proxied to entity-manager."""
import pytest

import fiware_api_gateway as gw


class _Resp:
    content = b'{"ok": true}'
    status_code = 201
    headers = {}


def _patch_auth(monkeypatch, tenant="acme"):
    monkeypatch.setattr(gw, "get_request_token", lambda: "tok")
    monkeypatch.setattr(gw, "validate_jwt_token", lambda t: {"tenant_id": tenant})
    monkeypatch.setattr(gw, "extract_tenant_id", lambda p: tenant)
    monkeypatch.setattr(gw, "rate_limit", lambda t: True)
    monkeypatch.setattr(gw, "KEYCLOAK_AUTH_AVAILABLE", True)
    monkeypatch.setattr(gw, "generate_hmac_signature", lambda t, ten: "sig123")


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
def test_forwards_method_path_body_and_signature(monkeypatch, method):
    captured = {}

    def fake_request(m, url, headers=None, params=None, data=None, timeout=None):
        captured.update(method=m, url=url, headers=headers, data=data, params=params)
        return _Resp()

    _patch_auth(monkeypatch)
    monkeypatch.setattr(gw.requests, "request", fake_request)
    sub = "devices/urn:ngsi-ld:Device:acme:S1/datalogger"
    with gw.app.test_request_context(
        f"/api/sensors/{sub}?x=1", method=method, data=b'{"a": 1}',
        content_type="application/json",
    ):
        resp = gw.proxy_sensor_management(sub)
    assert resp.status_code == 201
    assert captured["method"] == method
    assert captured["url"] == f"{gw.ENTITY_MANAGER_URL}/api/sensors/{sub}"
    assert captured["data"] == b'{"a": 1}'
    assert captured["params"]["x"] == "1"
    h = captured["headers"]
    assert h["Authorization"] == "Bearer tok"
    assert h["X-Tenant-ID"] == "acme"
    assert h["X-Auth-Signature"] == "sig123"
    assert h["Content-Type"] == "application/json"


def test_requires_auth(monkeypatch):
    monkeypatch.setattr(gw, "get_request_token", lambda: None)
    with gw.app.test_request_context("/api/sensors/register", method="POST"):
        resp = gw.proxy_sensor_management("register")
    assert resp.status_code == 401


def test_route_is_registered_and_existing_routes_win():
    rules = {r.rule: r.endpoint for r in gw.app.url_map.iter_rules()}
    assert rules["/api/sensors/<path:subpath>"] == "proxy_sensor_management"
    assert rules["/api/sensors"] == "get_sensors"
    assert rules["/api/sensors/stats"] == "get_sensor_stats"


@pytest.fixture
def client():
    gw.app.config["TESTING"] = True
    return gw.app.test_client()


def _pat(monkeypatch, scopes):
    monkeypatch.setattr(
        gw, "resolve_pat_info", lambda tok, base: {"tenant_id": "acme", "scopes": scopes}
    )
    monkeypatch.setattr(gw, "_is_tenant_suspended", lambda tenant: False)
    return {"Authorization": "Bearer nkz_pat_" + "a" * 40}


@pytest.mark.parametrize("path", ["/api/sensors/dataloggers",
                                  "/api/sensors/dataloggers/x/config",
                                  "/api/sensors/profiles"])
def test_telemetry_pat_is_denied_on_management_paths(client, monkeypatch, path):
    resp = client.get(path, headers=_pat(monkeypatch, ["telemetry"]))
    assert resp.status_code == 403
    assert "scope" in resp.get_json()["error"]


@pytest.mark.parametrize("path", ["/api/sensors", "/api/sensors/stats"])
def test_telemetry_pat_still_reads_sensors(client, monkeypatch, path):
    headers = _pat(monkeypatch, ["telemetry"])
    monkeypatch.setattr(gw, "get_request_token", lambda: None)
    resp = client.get(path, headers=headers)
    # The scope check passed (not the PAT 403); the route then needs a JWT.
    assert resp.status_code != 403
