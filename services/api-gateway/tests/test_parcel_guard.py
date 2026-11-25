"""Pure rules of the AgriParcel write guard."""

import json
from pathlib import Path

import pytest
from parcel_guard import (
    creates_parcel_id,
    declares_parcel,
    entity_type_from_id,
    is_parcel_id,
    local_name,
)


@pytest.mark.parametrize("name", ["AgriParcelOperation", "AgriParcelRecord", "AgriParcelZone"])
def test_other_types_are_not_parcels(name):
    assert entity_type_from_id(f"urn:ngsi-ld:{name}:tenant-a:1") == name
    assert is_parcel_id(f"urn:ngsi-ld:{name}:tenant-a:1") is False


@pytest.mark.parametrize(
    "entity_id",
    [
        "urn:ngsi-ld:AgriParcel:tenant-a:1",
        "urn%3Angsi-ld%3AAgriParcel%3A1",
        "urn%253Angsi-ld%253AAgriParcel%253A1",
        "urn:ngsi-ld:%41griParcel:1",
        "AgriParcel-1",
        "urn:ngsi-ld:AgriParcel",
        "urn:ngsi-ld:AgriParcel%3Fx:1",
    ],
)
def test_parcels_and_unreadable_ids_match(entity_id):
    assert is_parcel_id(entity_id) is True


def test_parcel_name_after_the_type_does_not_match():
    assert is_parcel_id("urn:ngsi-ld:Device:AgriParcel:1") is False


@pytest.mark.parametrize("entity_id", ["", "Device-1", "urn:ngsi-ld:Device:1"])
def test_ids_without_the_name_do_not_match(entity_id):
    assert is_parcel_id(entity_id) is False


BASE = "https://saref.etsi.org/saref4agri/"
IRI = BASE + "AgriParcel"

PARCEL_BODIES = [
    {"type": "AgriParcel"},
    {"@type": "AgriParcel"},
    {"type": IRI},
    {"@type": IRI},
    {"type": "saref4agri:AgriParcel"},
    {"type": "x:AgriParcel"},
    {"type": "Foo", "@context": {"Foo": IRI}},
    {"type": "Foo", "@context": {"Foo": {"@id": IRI}}},
    {"type": "x:Parcel", "@context": {"x": BASE + "Agri"}},
    {"type": "Foo", "@context": {"x": BASE, "Foo": "x:AgriParcel"}},
    {"type": "Foo", "@context": {"x": {"@id": BASE}, "Foo": {"@id": "x:AgriParcel"}}},
    {"type": "Foo", "@context": ["https://example.org/c.jsonld", [{"Foo": IRI}]]},
    {"type": "Foo", "@context": [{"Foo": IRI}, {"Foo": "https://example.org/Foo"}]},
]

OTHER_BODIES = [
    {"type": "Device"},
    {"type": "AgriParcelRecord"},
    {"name": {"type": "Property", "value": 1}},
    {"type": "Device", "@context": {"AgriParcel": IRI}},
    {
        "type": "Device",
        "hasAgriParcel": {"type": "Relationship", "object": "urn:ngsi-ld:AgriParcel:1"},
    },
    {"type": "a", "@context": {"a": "b", "b": "a"}},
    {"type": "x:a", "@context": {"x": "x:b"}},
    {"type": 3},
    {"type": "Device", "@context": None},
    {"type": "Device", "@context": {"Device": 3, "x": {"@type": "@id"}}},
    [],
    None,
    "x",
]


@pytest.mark.parametrize("body", PARCEL_BODIES)
def test_declares_parcel(body):
    assert declares_parcel(body) is True


@pytest.mark.parametrize("body", OTHER_BODIES)
def test_does_not_declare_parcel(body):
    assert declares_parcel(body) is False


@pytest.mark.parametrize("key", ["id", "@id"])
def test_creates_parcel_id(key):
    assert creates_parcel_id({key: "urn:ngsi-ld:AgriParcel:1", "type": "Device"}) is True
    assert creates_parcel_id({key: "urn:ngsi-ld:Device:1"}) is False
    assert creates_parcel_id({key: 3}) is False


@pytest.mark.parametrize("body", [[], None, "x", {}])
def test_creates_parcel_id_needs_an_entity(body):
    assert creates_parcel_id(body) is False


def test_deeply_nested_context_is_walked_without_recursion():
    context: object = {"Foo": IRI}
    for _ in range(5000):
        context = [context]
    assert declares_parcel({"type": "Foo", "@context": context}) is True


@pytest.mark.parametrize(
    "body",
    [
        {"type": "a" * 3000},
        {"type": ["a"] * 21},
        {"type": "Device", "@context": [{"a": "b"}] * 1001},
        {"type": "x:a", "@context": {"x": "x:" + "b" * 3000}},
    ],
)
def test_payloads_beyond_the_bounds_are_not_accepted(body):
    assert declares_parcel(body) is True


def test_payloads_within_the_bounds_are_judged():
    body = {"type": "Device", "@context": [{}] * 5000 + [{"a": "b"}] * 1000}
    assert declares_parcel(body) is False


def test_platform_context_keeps_the_guarded_name():
    """Terms are gathered from every object entry: their position may change."""
    path = Path(__file__).resolve().parents[3] / "config" / "ngsi-ld-context.json"
    entries = json.loads(path.read_text(encoding="utf-8"))["@context"]
    terms = {}
    for entry in entries if isinstance(entries, list) else [entries]:
        if isinstance(entry, dict):
            terms.update(entry)
    iris = {k: v.get("@id") if isinstance(v, dict) else v for k, v in terms.items()}
    named = {k for k, v in iris.items() if isinstance(v, str) and local_name(v) == "AgriParcel"}
    assert named == {"AgriParcel"}


class _Upstream:
    content = b""
    status_code = 201
    headers = {}
    text = ""


@pytest.fixture
def writes(client, monkeypatch):
    """Signed-in client plus the calls that reach the broker."""
    import fiware_api_gateway as gw

    calls = []
    monkeypatch.setattr(gw, "get_request_token", lambda: "tok")
    monkeypatch.setattr(
        gw, "validate_jwt_token", lambda t: {"tenant_id": "tenant-a", "realm_access": {"roles": []}}
    )
    monkeypatch.setattr(gw, "extract_tenant_id", lambda p: "tenant-a")
    monkeypatch.setattr(gw, "rate_limit", lambda t: True)
    monkeypatch.setattr(gw, "has_role", lambda *a, **k: False)
    monkeypatch.setattr(gw, "is_pat_token", lambda t: False)
    for method in ("post", "patch", "put"):
        monkeypatch.setattr(
            gw.requests, method, lambda url, json=None, **kw: calls.append(json) or _Upstream()
        )
    return client, calls


PREFIXES = ["/ngsi-ld/v1/entities", "/api/ngsi-ld/v1/entities"]
CONTENT_TYPES = ["application/json", "application/ld+json"]


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("content_type", CONTENT_TYPES)
@pytest.mark.parametrize(
    "body", PARCEL_BODIES + [{"@id": "urn:ngsi-ld:AgriParcel:1", "type": "Device"}]
)
def test_parcel_creation_is_rejected(writes, prefix, content_type, body):
    client, calls = writes
    entity = body if "@id" in body else {"id": "urn:ngsi-ld:Probe:1", **body}
    resp = client.post(prefix, data=json.dumps(entity), content_type=content_type)
    assert resp.status_code == 403
    assert calls == []


def test_parcel_creation_is_rejected_without_type_lists(writes, monkeypatch):
    import fiware_api_gateway as gw

    client, calls = writes
    monkeypatch.setattr(gw.ENTITY_TYPE_GUARD, "accepted", None)
    resp = client.post(PREFIXES[0], json={"id": "urn:ngsi-ld:Probe:1", "type": IRI})
    assert resp.status_code == 403
    assert calls == []


def test_internal_secret_is_still_exempt(writes, monkeypatch):
    client, calls = writes
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "s3cret-for-tests")
    resp = client.post(
        PREFIXES[0],
        json={"id": "urn:ngsi-ld:AgriParcel:1", "type": IRI},
        headers={"X-Internal-Service-Secret": "s3cret-for-tests"},
    )
    assert resp.status_code == 201
    assert len(calls) == 1


@pytest.mark.parametrize("prefix", PREFIXES)
def test_other_writes_are_forwarded(writes, prefix):
    client, calls = writes
    entity = {"id": "urn:ngsi-ld:Device:1", "type": "Device", "@context": {"AgriParcel": IRI}}
    assert client.post(prefix, json=entity).status_code == 201
    fragment = {"name": {"type": "Property", "value": "x"}}
    assert client.patch(f"{prefix}/urn:ngsi-ld:Device:1/attrs", json=fragment).status_code == 201
    assert len(calls) == 2
