"""The gateway forwards entity reads only in their accepted form."""

import json

import pytest

LIST = "/ngsi-ld/v1/entities"
QUERY = "/ngsi-ld/v1/entityOperations/query"
TYPES = "/ngsi-ld/v1/types"
DEVICE = "urn:ngsi-ld:Device:1"
BODY = {"type": "Query", "entities": [{"type": "Device"}]}


class _Upstream:
    content = b"[]"
    status_code = 200
    headers = {"Content-Type": "application/json"}
    text = "[]"

    def json(self):
        return []


@pytest.fixture
def calls(monkeypatch):
    import fiware_api_gateway as gw

    seen = []

    def fake(url, headers=None, params=None, json=None, **kw):
        seen.append({"url": url, "headers": dict(headers or {}), "params": params, "json": json})
        return _Upstream()

    for method in ("get", "post", "put", "patch", "delete"):
        monkeypatch.setattr(gw.requests, method, fake)
    monkeypatch.setattr(gw, "rate_limit", lambda t: True)
    monkeypatch.setattr(gw, "_is_tenant_suspended", lambda t: False)
    monkeypatch.setattr(gw, "obtain_gateway_service_jwt", lambda: "svc")
    monkeypatch.setattr(
        gw, "resolve_pat_info", lambda t, b: {"tenant_id": "acme", "scopes": ["entities", "export"]}
    )
    monkeypatch.setattr(
        gw,
        "validate_jwt_token",
        lambda t, **k: {"tenant_id": "acme", "realm_access": {"roles": []}} if t == "user" else None,
    )
    monkeypatch.setattr(gw, "extract_tenant_id", lambda p: "acme")
    monkeypatch.setattr(gw, "has_role", lambda *a, **k: False)
    return seen


USER = {"Authorization": "Bearer user"}
PAT = {"Authorization": "Bearer nkz_pat_" + "a" * 40}
BOTH = pytest.mark.parametrize("headers", [USER, PAT], ids=["user", "pat"])


@BOTH
def test_list_forwards_the_given_parameters(client, calls, headers):
    args = {"type": "Device", "q": 'name=="Viña Norte"', "attrs": "name", "options": "keyValues", "limit": "7", "offset": "2", "count": "true"}
    assert client.get(LIST, query_string=args, headers=headers).status_code == 200
    assert calls[0]["params"] == args


def test_both_list_prefixes_forward(client, calls):
    for prefix in ("", "/api"):
        assert client.get(prefix + LIST + "?type=Device", headers=USER).status_code == 200
    assert [c["params"] for c in calls] == [{"type": "Device"}] * 2


def test_geo_lists_are_forwarded(client, calls):
    args = {"type": "Device", "georel": "near;maxDistance==100", "geometry": "Point", "coordinates": "[0,0]"}
    assert client.get(LIST, query_string=args, headers=USER).status_code == 200
    assert calls[0]["params"] == args


BAD_READS = [
    (LIST + "?q=a%3D%3D", "q"),
    (LIST + "?q=a%3D%3D1&q=a%3D%3D1", "q"),
    (LIST + "?foo=1", "foo"),
    (LIST + "?limit%00=1000", "limit\x00"),
    (LIST + "?attrs=" + "a" * 129, "attrs"),
    (LIST + "?limit=5&limit=6", "limit"),
    (LIST + "?geometry=Point", "georel"),
    (f"{LIST}/{DEVICE}?q=a%3D%3D1", "q"),
    (f"{LIST}/{DEVICE}?attrs=" + "a" * 129, "attrs"),
    (TYPES + "?q=a%3D%3D1", "q"),
    (TYPES + "/Device?foo=1", "foo"),
]


@BOTH
@pytest.mark.parametrize("path, parameter", BAD_READS)
def test_other_reads_are_refused(client, calls, headers, path, parameter):
    resp = client.get(path, headers=headers)
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Invalid query parameter", "parameter": parameter}
    assert calls == []


@pytest.mark.parametrize("path, parameter", BAD_READS + [("/api/sensors?foo=1", "foo"), ("/api" + LIST + "?foo=1", "foo")])
def test_credentials_come_first(client, calls, path, parameter):
    assert client.get(path).status_code == 401
    assert calls == []


def test_head_is_judged_like_get(client, calls):
    assert client.head(LIST + "?foo=1", headers=USER).status_code == 400
    assert client.head("/api/sensors?foo=1", headers=USER).status_code == 400
    assert calls == []


def test_the_answer_names_the_parameter_briefly(client, calls):
    resp = client.get(LIST + "?" + "x" * 500 + "=secret-value", headers=USER)
    assert resp.get_json()["parameter"] == "x" * 64
    assert b"secret-value" not in resp.data


def test_entity_and_types_forward_their_parameters(client, calls):
    client.get(f"{LIST}/{DEVICE}?attrs=name&options=keyValues", headers=USER)
    client.get(TYPES + "?details=true", headers=USER)
    client.get(TYPES + "/Device", headers=USER)
    assert [c["params"] for c in calls] == [{"attrs": "name", "options": "keyValues"}, {"details": "true"}, {}]


def test_sensors_keep_their_types(client, calls):
    assert client.get("/api/sensors", headers=USER).status_code == 200
    assert client.get("/api/sensors?limit=5&options=keyValues", headers=USER).status_code == 200
    assert calls[0]["params"] == {"type": "Device,AgriSensor,AgriDevice"}
    assert calls[1]["params"] == {"type": "Device,AgriSensor,AgriDevice", "limit": "5", "options": "keyValues"}


@pytest.mark.parametrize("query", ["q=a%3D%3D", "foo=1", "attrs=" + "a" * 129])
def test_other_sensor_reads_are_refused(client, calls, query):
    assert client.get("/api/sensors?" + query, headers=USER).status_code == 400
    assert calls == []


@BOTH
def test_query_forwards_body_and_paging(client, calls, headers):
    body = {**BODY, "q": "a==1", "attrs": ["name"]}
    resp = client.post(QUERY + "?limit=7&offset=2&count=true", json=body, headers=headers)
    assert resp.status_code == 200
    assert calls[0]["json"] == body
    assert calls[0]["params"] == {"limit": "7", "offset": "2", "count": "true"}


@BOTH
@pytest.mark.parametrize("content_type", ["application/json", "application/ld+json"])
def test_queries_are_sent_as_json(client, calls, headers, content_type):
    client.post(QUERY, data=json.dumps(BODY), content_type=content_type, headers=headers)
    assert calls[0]["headers"]["Content-Type"] == "application/json"


@BOTH
@pytest.mark.parametrize(
    "body, parameter",
    [
        ({**BODY, "limit": 1}, "limit"),
        ({**BODY, "q": "a=="}, "q"),
        ({**BODY, "@context": ["https://example.org/c.jsonld"]}, "@context"),
        ({**BODY, "q\x00": "a==1"}, "q\x00"),
        ({**BODY, "datasetId": [1]}, "datasetId"),
        ([], "body"),
        (None, "body"),
        ("x", "body"),
    ],
)
def test_other_query_bodies_are_refused(client, calls, headers, body, parameter):
    resp = client.post(QUERY, data=json.dumps(body), content_type="application/json", headers=headers)
    assert resp.status_code == 400
    assert resp.get_json()["parameter"] == parameter
    assert calls == []


@BOTH
@pytest.mark.parametrize("query", ["q=a%3D%3D1", "foo=1", "limit=1&limit%00=2"])
def test_other_query_parameters_are_refused(client, calls, headers, query):
    assert client.post(f"{QUERY}?{query}", json=BODY, headers=headers).status_code == 400
    assert calls == []


@BOTH
def test_query_without_a_json_body_is_refused(client, calls, headers):
    resp = client.post(QUERY + "?limit=9999", data=json.dumps(BODY), content_type="text/plain", headers=headers)
    assert resp.status_code == 400
    assert calls == []


def test_query_is_read_only_by_post(client, calls):
    assert client.get(QUERY, headers=USER).status_code == 405
    assert calls == []


@pytest.mark.parametrize(
    "args, limit",
    [("", "100"), ("limit=7", "7"), ("limit=500", "500"), ("limit=9999", "500"), ("limit=0", "100"), ("limit=-1", "100"), ("limit=abc", "100")],
)
def test_pat_pages_are_capped(client, calls, args, limit):
    assert client.post(f"{QUERY}?{args}", json=BODY, headers=PAT).status_code == 200
    assert client.get(f"{LIST}?type=Device&{args}", headers=PAT).status_code == 200
    assert [c["params"]["limit"] for c in calls] == [limit, limit]


def test_pat_single_entity_gets_no_limit(client, calls):
    assert client.get(f"{LIST}/{DEVICE}", headers=PAT).status_code == 200
    assert calls[0]["params"] == {}


def test_user_pages_are_not_invented(client, calls):
    client.post(QUERY, json=BODY, headers=USER)
    client.get(LIST + "?type=Device", headers=USER)
    assert "limit" not in calls[0]["params"] and "limit" not in calls[1]["params"]


def test_user_pages_have_the_broker_maximum(client, calls):
    assert client.get(LIST + "?type=Device&limit=1000", headers=USER).status_code == 200
    assert client.get(LIST + "?type=Device&limit=2000", headers=USER).status_code == 400
    assert len(calls) == 1
