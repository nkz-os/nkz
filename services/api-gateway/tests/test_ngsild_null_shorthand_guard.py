"""The /ngsi-ld entity proxies never forward the bare null token as an attribute value.

The broker mishandles `{"attr": "urn:ngsi-ld:null"}` on attribute-fragment
endpoints, so the gateway answers 400 itself and the body never reaches it.
Attribute removal goes through DELETE /entities/{id}/attrs/{name}.
"""

import pytest

ENTITY_ID = "urn:ngsi-ld:AgriCrop:tenant-a:c1"


def _auth(monkeypatch, gw, tenant="tenant-a"):
    monkeypatch.setattr(gw, "get_request_token", lambda: "tok")
    monkeypatch.setattr(
        gw,
        "validate_jwt_token",
        lambda t: {"tenant_id": tenant, "realm_access": {"roles": []}},
    )
    monkeypatch.setattr(gw, "extract_tenant_id", lambda p: tenant)
    monkeypatch.setattr(gw, "rate_limit", lambda t: True)
    monkeypatch.setattr(gw, "has_role", lambda *a, **k: False)
    monkeypatch.setattr(gw, "is_pat_token", lambda t: False)


class _R:
    content = b""
    status_code = 204
    headers = {}
    text = ""


@pytest.fixture
def forwarded(monkeypatch):
    import fiware_api_gateway as gw

    calls = []

    def _fake(method):
        def fake(url, headers=None, json=None, params=None, **kw):
            calls.append((method, url, json))
            return _R()

        return fake

    _auth(monkeypatch, gw)
    for method in ("post", "put", "patch"):
        monkeypatch.setattr(gw.requests, method, _fake(method))
    return gw, calls


@pytest.mark.parametrize(
    "path",
    [f"{ENTITY_ID}/attrs", f"{ENTITY_ID}/attrs/", ENTITY_ID],
)
def test_entity_patch_with_shorthand_null_is_rejected(forwarded, path):
    gw, calls = forwarded
    with gw.app.test_request_context(
        f"/ngsi-ld/v1/entities/{path}",
        method="PATCH",
        json={"refAgriParcel": "urn:ngsi-ld:null"},
    ):
        resp = gw.make_response(gw.entity_by_id(path))

    assert resp.status_code == 400
    assert resp.get_json()["attributes"] == ["refAgriParcel"]
    assert calls == []


def test_entity_put_with_shorthand_null_is_rejected(forwarded):
    gw, calls = forwarded
    with gw.app.test_request_context(
        f"/ngsi-ld/v1/entities/{ENTITY_ID}",
        method="PUT",
        json={"type": "AgriCrop", "refAgriParcel": "urn:ngsi-ld:null"},
    ):
        resp = gw.make_response(gw.entity_by_id(ENTITY_ID))

    assert resp.status_code == 400
    assert calls == []


def test_collection_mutation_with_shorthand_null_is_rejected(forwarded):
    gw, calls = forwarded
    with gw.app.test_request_context(
        "/ngsi-ld/v1/entities",
        method="POST",
        json={"id": ENTITY_ID, "type": "AgriCrop", "x": "urn:ngsi-ld:null"},
    ):
        resp = gw.make_response(gw.entities())

    assert resp.status_code == 400
    assert resp.get_json()["attributes"] == ["x"]
    assert calls == []


def test_regular_attribute_patch_is_still_forwarded(forwarded):
    gw, calls = forwarded
    body = {"hasAgriParcel": {"type": "Relationship", "object": "urn:ngsi-ld:AgriParcel:tenant-a:p1"}}
    with gw.app.test_request_context(
        f"/ngsi-ld/v1/entities/{ENTITY_ID}/attrs", method="PATCH", json=body
    ):
        resp = gw.make_response(gw.entity_by_id(f"{ENTITY_ID}/attrs"))

    assert resp.status_code == 204
    assert calls == [("patch", f"{gw.ORION_URL}/ngsi-ld/v1/entities/{ENTITY_ID}/attrs", body)]
