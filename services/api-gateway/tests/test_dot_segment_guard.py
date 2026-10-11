"""A '.'/'..' segment must never reach an upstream proxy target."""
from unittest.mock import patch

import pytest

import fiware_api_gateway as gw

PAYLOAD = {"sub": "u1", "tenant_id": "acme", "exp": 9999999999}


@pytest.mark.parametrize("prefix", ["/ngsi-ld/v1/entities/", "/api/ngsi-ld/v1/entities/"])
@pytest.mark.parametrize(
    "tail",
    [
        "..%2Fsubscriptions",
        "%2E%2E%2Fsubscriptions",
        "%252E%252E%252Fsubscriptions",
        "../subscriptions",
        "urn:ngsi-ld:AgriParcel:x/../../subscriptions",
        "urn:ngsi-ld:AgriParcel:x/./attrs",
        "..%3F",
        "..%23",
        "..%3Fx=1",
        "urn:x/..%3F",
    ],
)
@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_dot_segments_never_reach_orion(client, monkeypatch, prefix, tail, method):
    monkeypatch.setattr(gw, "validate_keycloak_token", lambda token: dict(PAYLOAD))
    monkeypatch.setattr(gw, "_is_tenant_suspended", lambda tenant: False)
    with patch.object(gw.requests, "get") as g, patch.object(gw.requests, "patch") as p, patch.object(
        gw.requests, "delete"
    ) as d:
        resp = getattr(client, method)(prefix + tail, headers={"Authorization": "Bearer t"})
    assert resp.status_code == 400
    assert not g.called and not p.called and not d.called


def test_normal_entity_path_still_proxied(client, monkeypatch):
    monkeypatch.setattr(gw, "validate_keycloak_token", lambda token: dict(PAYLOAD))
    monkeypatch.setattr(gw, "_is_tenant_suspended", lambda tenant: False)
    with patch.object(gw.requests, "get") as g:
        g.return_value.status_code = 200
        g.return_value.content = b"{}"
        g.return_value.headers = {"Content-Type": "application/json"}
        resp = client.get(
            "/ngsi-ld/v1/entities/urn:ngsi-ld:AgriParcel:acme:1/attrs/area",
            headers={"Authorization": "Bearer t"},
        )
    assert resp.status_code == 200
    assert g.call_args.args[0].endswith("/ngsi-ld/v1/entities/urn:ngsi-ld:AgriParcel:acme:1/attrs/area")


@pytest.mark.parametrize(
    "path", ["/a/../b", "/a/./b", "/..", "/%2e%2e/x", "/%252e%252e/x", "/a/..?x", "/a/..#"]
)
def test_has_dot_segment_detects_encoded_forms(path):
    assert gw._has_dot_segment(path)


@pytest.mark.parametrize(
    "path",
    ["/a/b", "/a.b/c", "/urn:ngsi-ld:X:1/attrs/..name", "/x/.hidden", "/urn:x:a?b", "/a#frag", "/a.b..c"],
)
def test_has_dot_segment_ignores_dots_inside_names(path):
    assert not gw._has_dot_segment(path)
