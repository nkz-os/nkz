"""POST /ngsi-ld/v1/entities checks the declared type against the closed list.

Enforcement is off by default; when on, an unaccepted type answers 422 and
nothing reaches the broker. Only creation (POST) is judged.
"""

import json
import logging

import pytest
from entity_type_guard import EntityTypeGuard, load_accepted

PREFIXES = ["/ngsi-ld/v1/entities", "/api/ngsi-ld/v1/entities"]
SAREF_PARCEL = "https://saref.etsi.org/saref4agri/AgriParcel"
ERROR_BODY = {
    "error": "Entity type not accepted",
    "hint": "Accepted types are listed in config/entity-types of the platform repository",
}


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
    for method in ("get", "post", "put", "patch", "delete"):
        monkeypatch.setattr(gw.requests, method, _fake(method))
    return gw, calls


def _set_guard(monkeypatch, gw, enforce, accepted="load"):
    accepted = load_accepted() if accepted == "load" else accepted
    monkeypatch.setattr(gw, "ENTITY_TYPE_GUARD", EntityTypeGuard("api-gateway", accepted, enforce))


def _call(gw, prefix, method, body, headers=None):
    with gw.app.test_request_context(prefix, method=method, json=body, headers=headers):
        return gw.make_response(gw.entities())


def _guard_records(caplog):
    return [r for r in caplog.records if "entity_type_guard" in r.getMessage()]


@pytest.mark.parametrize("prefix", PREFIXES)
def test_not_enforcing_forwards_and_warns(forwarded, monkeypatch, caplog, prefix):
    gw, calls = forwarded
    _set_guard(monkeypatch, gw, enforce=False)
    with caplog.at_level(logging.INFO):
        resp = _call(gw, prefix, "POST", {"id": "urn:ngsi-ld:FooAsset:tenant-a:1", "type": "FooAsset"})
    assert resp.status_code == 204
    assert len(calls) == 1
    records = _guard_records(caplog)
    assert len(records) == 1 and records[0].levelno == logging.WARNING


@pytest.mark.parametrize("prefix", PREFIXES)
def test_enforcing_rejects_unknown_type(forwarded, monkeypatch, prefix):
    gw, calls = forwarded
    _set_guard(monkeypatch, gw, enforce=True)
    resp = _call(gw, prefix, "POST", {"id": "urn:ngsi-ld:FooAsset:tenant-a:1", "type": "FooAsset"})
    assert resp.status_code == 422
    assert resp.get_json() == {**ERROR_BODY, "types": ["FooAsset"]}
    assert calls == []


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("declared", ["AgriParcel", "AgriPlant", "nkz:AgriPlant", SAREF_PARCEL])
def test_enforcing_accepts_listed_types(forwarded, monkeypatch, prefix, declared):
    gw, calls = forwarded
    _set_guard(monkeypatch, gw, enforce=True)
    resp = _call(gw, prefix, "POST", {"id": "urn:ngsi-ld:X:tenant-a:1", "type": declared})
    assert resp.status_code == 204
    assert len(calls) == 1


@pytest.mark.parametrize("prefix", PREFIXES)
def test_enforcing_rejects_code_block_type(forwarded, monkeypatch, prefix):
    gw, calls = forwarded
    _set_guard(monkeypatch, gw, enforce=True)
    resp = _call(gw, prefix, "POST", {"id": "urn:ngsi-ld:X:tenant-a:1", "type": "AgriEnergyTracker"})
    assert resp.status_code == 422
    assert resp.get_json()["types"] == ["AgriEnergyTracker"]
    assert calls == []


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize(
    "body,expected",
    [
        ({"@type": "FooAsset"}, ["FooAsset"]),
        ({"type": ["AgriParcel", "FooAsset"]}, ["FooAsset"]),
        ({"type": "AgriParcel", "@type": "FooAsset"}, ["FooAsset"]),
        ({"type": "nkz:Device"}, ["nkz:Device"]),
    ],
)
def test_enforcing_rejects_alternate_declarations(forwarded, monkeypatch, prefix, body, expected):
    gw, calls = forwarded
    _set_guard(monkeypatch, gw, enforce=True)
    resp = _call(gw, prefix, "POST", body)
    assert resp.status_code == 422
    assert resp.get_json()["types"] == expected
    assert calls == []


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("body", [[1, 2], {"id": "urn:ngsi-ld:X:tenant-a:1"}])
def test_enforcing_forwards_what_it_cannot_judge(forwarded, monkeypatch, prefix, body):
    gw, calls = forwarded
    _set_guard(monkeypatch, gw, enforce=True)
    resp = _call(gw, prefix, "POST", body)
    assert resp.status_code == 204
    assert len(calls) == 1


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("method", ["GET", "PUT", "PATCH", "DELETE"])
def test_only_creation_is_judged(forwarded, monkeypatch, caplog, prefix, method):
    gw, calls = forwarded
    _set_guard(monkeypatch, gw, enforce=True)
    with caplog.at_level(logging.INFO):
        resp = _call(gw, prefix, method, {"type": "FooAsset"})
    assert resp.status_code == 204
    assert len(calls) == 1
    assert _guard_records(caplog) == []


@pytest.mark.parametrize("prefix", PREFIXES)
def test_unavailable_lists_let_everything_through(forwarded, monkeypatch, prefix):
    gw, calls = forwarded
    _set_guard(monkeypatch, gw, enforce=True, accepted=None)
    resp = _call(gw, prefix, "POST", {"id": "urn:ngsi-ld:FooAsset:tenant-a:1", "type": "FooAsset"})
    assert resp.status_code == 204
    assert len(calls) == 1


@pytest.mark.parametrize("prefix", PREFIXES)
def test_foreign_context_is_logged_and_forwarded(forwarded, monkeypatch, caplog, prefix):
    gw, calls = forwarded
    _set_guard(monkeypatch, gw, enforce=True)
    body = {"id": "urn:ngsi-ld:X:tenant-a:1", "type": "AgriParcel", "@context": ["https://example.org/ctx.jsonld"]}
    with caplog.at_level(logging.INFO):
        resp = _call(gw, prefix, "POST", body, headers={"X-Module-Id": "demo-module"})
    assert resp.status_code == 204
    assert len(calls) == 1
    records = _guard_records(caplog)
    assert len(records) == 1 and records[0].levelno == logging.INFO
    payload = json.loads(records[0].getMessage().split(" ", 1)[1])
    assert payload["foreign_context"] is True
    assert payload["module_id"] == "demo-module"
    assert payload["tenant"] == "tenant-a"


@pytest.mark.parametrize("prefix", PREFIXES)
def test_platform_context_still_injected(forwarded, monkeypatch, prefix):
    gw, calls = forwarded
    _set_guard(monkeypatch, gw, enforce=True)
    resp = _call(gw, prefix, "POST", {"id": "urn:ngsi-ld:X:tenant-a:1", "type": "AgriParcel"})
    assert resp.status_code == 204
    sent = calls[0][2]
    assert sent["@context"] == [
        "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld",
        gw.CONTEXT_URL,
    ]


@pytest.mark.parametrize("prefix", PREFIXES)
def test_guard_judges_the_body_as_sent_not_after_context_injection(forwarded, monkeypatch, caplog, prefix):
    # If the guard ran after the platform @context was injected, every body
    # would look like it carried its own context.
    gw, calls = forwarded
    _set_guard(monkeypatch, gw, enforce=False)
    with caplog.at_level(logging.INFO):
        resp = _call(gw, prefix, "POST", {"id": "urn:ngsi-ld:FooAsset:tenant-a:1", "type": "FooAsset"})
    assert resp.status_code == 204
    records = _guard_records(caplog)
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert json.loads(records[0].getMessage().split(" ", 1)[1])["foreign_context"] is False
    assert calls[0][2]["@context"][1] == gw.CONTEXT_URL
