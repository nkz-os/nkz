"""Page caps of PATs, and the content type of entity queries."""

import json

import pytest

QUERY = "/ngsi-ld/v1/entityOperations/query"
BODY = {"type": "Query", "entities": [{"type": "Device"}]}


class _Upstream:
    content = b"[]"
    status_code = 200
    headers = {"Content-Type": "application/json"}
    text = "[]"

    def json(self):
        return []


@pytest.fixture
def upstream(monkeypatch):
    import fiware_api_gateway as gw

    calls = []

    def fake(url, headers=None, params=None, json=None, **kw):
        calls.append(
            {"url": url, "headers": dict(headers or {}), "params": params, "json": json}
        )
        return _Upstream()

    monkeypatch.setattr(gw.requests, "post", fake)
    monkeypatch.setattr(gw.requests, "get", fake)
    monkeypatch.setattr(gw, "rate_limit", lambda t: True)
    return calls


@pytest.fixture
def pat(client, monkeypatch, upstream):
    import fiware_api_gateway as gw

    monkeypatch.setattr(
        gw,
        "resolve_pat_info",
        lambda tok, base: {"tenant_id": "acme", "scopes": ["entities", "export"]},
    )
    monkeypatch.setattr(gw, "_is_tenant_suspended", lambda tenant: False)
    monkeypatch.setattr(gw, "obtain_gateway_service_jwt", lambda: "svc")
    headers = {"Authorization": "Bearer nkz_pat_" + "a" * 40}

    def post(path, data=None, content_type="application/json"):
        body = data if isinstance(data, str) else json.dumps(BODY if data is None else data)
        return client.post(path, data=body, content_type=content_type, headers=headers)

    post.client, post.headers, post.calls = client, headers, upstream
    return post


@pytest.fixture
def user(client, monkeypatch, upstream):
    import fiware_api_gateway as gw

    monkeypatch.setattr(gw, "get_request_token", lambda: "tok")
    monkeypatch.setattr(
        gw, "validate_jwt_token", lambda t: {"tenant_id": "acme", "realm_access": {"roles": []}}
    )
    monkeypatch.setattr(gw, "extract_tenant_id", lambda p: "acme")
    monkeypatch.setattr(gw, "has_role", lambda *a, **k: False)
    monkeypatch.setattr(gw, "is_pat_token", lambda t: False)

    def post(path, data=None, content_type="application/json"):
        return client.post(
            path, data=json.dumps(BODY if data is None else data), content_type=content_type
        )

    post.calls = upstream
    return post


def test_pat_parameter_names_must_be_plain_in_lists(pat):
    resp = pat.client.get("/ngsi-ld/v1/entities?limit=1&limit%00=1000", headers=pat.headers)
    assert resp.status_code == 400
    assert pat.calls == []


def test_pat_entity_list_keeps_its_cap(pat):
    resp = pat.client.get("/ngsi-ld/v1/entities?type=Device&limit=9999", headers=pat.headers)
    assert resp.status_code == 200
    assert pat.calls[0]["params"]["limit"] == "500"


def test_pat_export_keeps_its_cap(pat):
    pat("/api/datahub/export", data={"max_rows": 999999})
    assert pat.calls[0]["json"]["max_rows"] == 10000


@pytest.mark.parametrize("caller", ["pat", "user"])
@pytest.mark.parametrize("content_type", ["application/ld+json", "application/json"])
def test_query_bodies_are_sent_as_json(request, caller, content_type):
    post = request.getfixturevalue(caller)
    body = {**BODY, "@context": ["https://example.org/c.jsonld"]}
    post(QUERY, data=body, content_type=content_type)
    assert post.calls[0]["headers"]["Content-Type"] == "application/json"
