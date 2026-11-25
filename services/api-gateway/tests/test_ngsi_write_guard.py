"""Shape of the entity writes the gateway forwards."""

import json

import pytest
from ngsi_write_guard import valid_context, valid_write

LIST = "/ngsi-ld/v1/entities"
ENTITY = LIST + "/urn:ngsi-ld:Device:1"
IRI = "https://example.org/n"
VALUE = {"type": "Property", "value": 1}
DEEP: object = [{"n": IRI}]
for _ in range(20):
    DEEP = [DEEP]

CONTEXTS = [
    "https://example.org/c.jsonld", "http://example.org/c.jsonld", [], {}, [{}], ["https://example.org/a", {"n": IRI}], [[{"n": IRI}]],
    {"n": IRI}, {"n": {"@id": IRI}}, {"n": {"@id": IRI, "@type": "@vocab"}}, {"n": {"@id": IRI, "@container": ["@set"]}},
    {"n": {"@id": IRI, "@container": "@list"}}, {"x": "https://example.org/", "n": "x:n"}, {"@vocab": "https://example.org/", "@version": 1.1},
    {"@base": IRI, "@protected": True, "@language": "es"},
]
OTHER_CONTEXTS = [
    None, 1, True, "", "foo", [1], [None], [""], ["foo"], {"n": 1}, {"n": None}, {"n": ""}, {"n": []}, {"n": {}}, {"n": {"@id": 1}},
    {"n": {"@id": None}}, {"n": {"@id": ""}}, {"n": {"@id": IRI, "@type": 1}}, {"n": {"@id": IRI, "@type": None}}, {"n": {"@type": "@id"}},
    {"n": {"@id": IRI, "foo": 1}}, {"n": {"@id": IRI, "@context": {}}}, {"n": {"@id": IRI, "@container": [1]}}, {"n": {"@id": IRI, "@container": []}},
    {"@vocab": 1}, {"@version": "1.1"}, {"@version": True}, {"@protected": 1}, {"@context": {"n": IRI}}, {"n\x00": IRI}, {"n": IRI + "\x00"}, {"": IRI},
    [{"n": IRI}, {"n": {"@id": 1}}], DEEP, {f"t{i}": IRI for i in range(2001)}, "https://example.org/" + "a" * 2048,
]


@pytest.mark.parametrize("context", CONTEXTS)
def test_known_contexts(context):
    assert valid_context(context) is True


@pytest.mark.parametrize("context", OTHER_CONTEXTS)
def test_other_contexts(context):
    assert valid_context(context) is False


@pytest.mark.parametrize("body", [{}, {"n": VALUE}, {"n": VALUE, "@context": {"n": IRI}}])
def test_objects_are_writes(body):
    assert valid_write(body) is True


@pytest.mark.parametrize("body", [None, [], [1], [{}], ["x"], "x", 1, True, {"n": VALUE, "@context": {"n": {"@id": 1}}}])
def test_other_bodies_are_not_writes(body):
    assert valid_write(body) is False


class _Upstream:
    content = b""
    status_code = 204
    headers = {}
    text = ""


@pytest.fixture
def calls(monkeypatch):
    import fiware_api_gateway as gw

    seen = []
    for method in ("get", "post", "put", "patch", "delete"):
        monkeypatch.setattr(gw.requests, method, lambda url, **kw: seen.append(kw.get("json")) or _Upstream())
    monkeypatch.setattr(gw, "rate_limit", lambda t: True)
    monkeypatch.setattr(
        gw, "validate_jwt_token",
        lambda t, **k: {"tenant_id": "acme", "realm_access": {"roles": []}} if t == "user" else None,
    )
    monkeypatch.setattr(gw, "extract_tenant_id", lambda p: "acme")
    monkeypatch.setattr(gw, "has_role", lambda *a, **k: False)
    return seen


USER = {"Authorization": "Bearer user"}
WRITES = pytest.mark.parametrize(
    "method, path", [("post", LIST), ("post", "/api" + LIST), ("put", ENTITY), ("patch", ENTITY), ("patch", ENTITY + "/attrs"), ("patch", "/api" + ENTITY + "/attrs/n")]
)


def _send(client, method, path, body, headers=USER, content_type="application/ld+json"):
    return getattr(client, method)(path, data=json.dumps(body), content_type=content_type, headers=headers)


@WRITES
@pytest.mark.parametrize("content_type", ["application/json", "application/ld+json"])
@pytest.mark.parametrize("body", [[1], [{}], ["x"], [], "x", None, {"n": VALUE, "@context": {"n": {"@id": 1}}}, {"n": VALUE, "@context": ["foo"]}])
def test_other_writes_are_refused(client, calls, method, path, content_type, body):
    resp = _send(client, method, path, body, content_type=content_type)
    assert resp.status_code == 400
    assert calls == []


@WRITES
def test_known_writes_are_forwarded(client, calls, method, path):
    body = {"id": "urn:ngsi-ld:Device:1", "type": "Device", "n": VALUE, "@context": {"n": IRI}}
    assert _send(client, method, path, body).status_code == 204
    assert calls == [body]


@WRITES
def test_credentials_come_first(client, calls, method, path):
    assert _send(client, method, path, [1], headers={}).status_code == 401


@pytest.mark.parametrize("prefix", ["", "/api"])
@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_the_collection_is_only_read_or_added_to(client, calls, prefix, method):
    body = {"id": "urn:ngsi-ld:Device:1", "type": "Device", "n": VALUE}
    assert _send(client, method, prefix + LIST, body).status_code == 405
    assert calls == []


def test_deletes_need_no_body(client, calls):
    assert client.delete(ENTITY, headers=USER).status_code == 204
    assert client.delete(ENTITY + "/attrs/n", headers=USER).status_code == 204
