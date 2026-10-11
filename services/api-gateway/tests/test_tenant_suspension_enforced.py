"""Suspension must bite on every request, not only at login."""
from unittest.mock import MagicMock, patch

import fiware_api_gateway as gw

PAYLOAD = {"sub": "u1", "tenant_id": "acme", "exp": 9999999999}


def _photon_ok():
    r = MagicMock()
    r.json.return_value = {"features": []}
    r.raise_for_status.return_value = None
    return r


def test_jwt_of_suspended_tenant_is_rejected(client, monkeypatch):
    monkeypatch.setattr(gw, "validate_keycloak_token", lambda token: dict(PAYLOAD))
    monkeypatch.setattr(gw, "_is_tenant_suspended", lambda tenant: tenant == "acme")
    resp = client.get("/api/geocode?q=x", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 401


def test_jwt_of_active_tenant_passes(client, monkeypatch):
    monkeypatch.setattr(gw, "validate_keycloak_token", lambda token: dict(PAYLOAD))
    monkeypatch.setattr(gw, "_is_tenant_suspended", lambda tenant: False)
    with patch.object(gw.requests, "get", return_value=_photon_ok()):
        resp = client.get("/api/geocode?q=x", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200


def test_create_session_keeps_explicit_suspended_error(client, monkeypatch):
    monkeypatch.setattr(gw, "validate_keycloak_token", lambda token: dict(PAYLOAD))
    monkeypatch.setattr(gw, "_is_tenant_suspended", lambda tenant: True)
    resp = client.post("/api/auth/session", json={"token": "t"})
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "TENANT_SUSPENDED"


def test_pat_of_suspended_tenant_is_rejected(client, monkeypatch):
    monkeypatch.setattr(
        gw, "resolve_pat_info", lambda tok, base: {"tenant_id": "acme", "scopes": ["entities"]}
    )
    monkeypatch.setattr(gw, "_is_tenant_suspended", lambda tenant: True)
    resp = client.get(
        "/ngsi-ld/v1/entities", headers={"Authorization": "Bearer nkz_pat_" + "a" * 40}
    )
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "TENANT_SUSPENDED"
