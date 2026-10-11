"""The Orion subscriptions proxy must not exist on the gateway.

It returned each tenant's subscriptions verbatim, including the
receiverInfo headers that carry the platform's internal service secret,
and it let any user point Orion at arbitrary internal URLs.
"""
import fiware_api_gateway as gw


def test_subscriptions_view_removed():
    assert not hasattr(gw, "subscriptions")


def test_subscriptions_path_is_not_routed(client, monkeypatch):
    monkeypatch.setattr(
        gw, "validate_keycloak_token", lambda token: {"sub": "u1", "tenant_id": "acme"}
    )
    monkeypatch.setattr(gw, "N8N_PROXY_HOST", "")
    for method in ("get", "post", "patch", "delete"):
        resp = getattr(client, method)(
            "/ngsi-ld/v1/subscriptions",
            headers={"Authorization": "Bearer user-token"},
        )
        assert resp.status_code == 404, method
