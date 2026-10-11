"""/api/storage/* must take the tenant from the validated token, never from a
client-supplied header: otherwise any caller can list or presign another
tenant's objects."""
from unittest.mock import MagicMock, patch

import fiware_api_gateway as gw
import storage_routes

PAYLOAD = {"sub": "u1", "tenant_id": "acme", "exp": 9999999999}
FORGED = {"X-Tenant-ID": "victim", "X-Module-Id": "mod1"}


def _s3():
    s3 = MagicMock()
    s3.generate_presigned_url.return_value = "http://x"
    s3.list_objects_v2.return_value = {"Contents": []}
    return s3


def test_presigned_url_requires_authentication(client):
    s3 = _s3()
    with patch.object(storage_routes, "_s3_client", return_value=s3):
        resp = client.post(
            "/api/storage/presigned-url", json={"path": "a.txt"}, headers=FORGED
        )
    assert resp.status_code == 401
    assert not s3.generate_presigned_url.called


def test_list_requires_authentication(client):
    s3 = _s3()
    with patch.object(storage_routes, "_s3_client", return_value=s3):
        resp = client.get("/api/storage/list", headers=FORGED)
    assert resp.status_code == 401
    assert not s3.list_objects_v2.called


def test_presigned_url_scopes_key_to_token_tenant(client, monkeypatch):
    monkeypatch.setattr(gw, "validate_keycloak_token", lambda token: dict(PAYLOAD))
    monkeypatch.setattr(gw, "_is_tenant_suspended", lambda tenant: False)
    s3 = _s3()
    with patch.object(storage_routes, "_s3_client", return_value=s3):
        resp = client.post(
            "/api/storage/presigned-url",
            json={"path": "a.txt"},
            headers={**FORGED, "Authorization": "Bearer t"},
        )
    assert resp.status_code == 200
    key = s3.generate_presigned_url.call_args.kwargs["Params"]["Key"]
    assert key.startswith("tenants/acme/modules/mod1/")
    assert "victim" not in key


def test_list_scopes_prefix_to_token_tenant(client, monkeypatch):
    monkeypatch.setattr(gw, "validate_keycloak_token", lambda token: dict(PAYLOAD))
    monkeypatch.setattr(gw, "_is_tenant_suspended", lambda tenant: False)
    s3 = _s3()
    with patch.object(storage_routes, "_s3_client", return_value=s3):
        resp = client.get(
            "/api/storage/list", headers={**FORGED, "Authorization": "Bearer t"}
        )
    assert resp.status_code == 200
    prefix = s3.list_objects_v2.call_args.kwargs["Prefix"]
    assert prefix.startswith("tenants/acme/")
    assert "victim" not in prefix
