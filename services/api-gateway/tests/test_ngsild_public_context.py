"""NGSI-LD responses name a @context URL that clients outside the cluster can resolve,
and /types is readable (also with a read-only PAT)."""
import pytest

import fiware_api_gateway as gw

PUBLIC = "https://api.example.org/ngsi-ld-context.json"


class _Resp:
    def __init__(self, body, status=200, headers=None):
        self.content = body
        self.status_code = status
        self.headers = headers or {}
        self.text = body.decode()


def _orion_body():
    return (
        '[{"@context":["' + gw.CONTEXT_URL + '","https://uri.etsi.org/ngsi-ld/v1/'
        'ngsi-ld-core-context-v1.6.jsonld"],"id":"urn:ngsi-ld:Device:x","type":"Device",'
        '"note":{"type":"Property","value":"see ' + gw.CONTEXT_URL + '"}}]'
    ).encode()


def _link():
    return (f'<{gw.CONTEXT_URL}>; rel="http://www.w3.org/ns/json-ld#context"; '
            'type="application/ld+json"')


def test_public_url_replaces_the_internal_one(monkeypatch):
    monkeypatch.setattr(gw, "PUBLIC_CONTEXT_URL", PUBLIC)
    body = _orion_body()
    upstream = _Resp(body, headers={"Content-Type": "application/ld+json",
                                    "Content-Length": str(len(body)), "Link": _link()})
    with gw.app.test_request_context("/ngsi-ld/v1/entities"):
        resp = gw.ngsi_proxy_response(upstream)
    out = resp.get_data()
    assert f'"{PUBLIC}"'.encode() in out
    assert f'"{gw.CONTEXT_URL}"'.encode() not in out
    # Only the exact quoted URL is replaced; free text keeps its value.
    assert f"see {gw.CONTEXT_URL}".encode() in out
    assert resp.headers["Link"].startswith(f"<{PUBLIC}>")
    # Body length changed: the upstream Content-Length must not be forwarded.
    assert int(resp.headers["Content-Length"]) == len(out)


def test_unset_public_url_leaves_the_response_untouched(monkeypatch):
    monkeypatch.setattr(gw, "PUBLIC_CONTEXT_URL", "")
    body = _orion_body()
    upstream = _Resp(body, headers={"Content-Length": str(len(body)), "Link": _link()})
    with gw.app.test_request_context("/ngsi-ld/v1/entities"):
        resp = gw.ngsi_proxy_response(upstream)
    assert resp.get_data() == body
    assert resp.headers["Link"] == _link()


def test_upstream_5xx_body_is_not_leaked(monkeypatch):
    monkeypatch.setattr(gw, "PUBLIC_CONTEXT_URL", PUBLIC)
    with gw.app.test_request_context("/ngsi-ld/v1/entities"):
        resp, status = gw.ngsi_proxy_response(_Resp(b"stack trace", status=500))
    assert status == 500
    assert b"stack trace" not in resp.get_data()


def _auth(monkeypatch, tenant="acme"):
    monkeypatch.setattr(gw, "get_request_token", lambda: "tok")
    monkeypatch.setattr(gw, "validate_jwt_token",
                        lambda t: {"tenant_id": tenant, "realm_access": {"roles": []}})
    monkeypatch.setattr(gw, "extract_tenant_id", lambda p: tenant)
    monkeypatch.setattr(gw, "rate_limit", lambda t: True)
    monkeypatch.setattr(gw, "has_role", lambda *a, **k: False)
    monkeypatch.setattr(gw, "is_pat_token", lambda t: False)


@pytest.mark.parametrize("path,upstream", [
    ("/ngsi-ld/v1/types", "/ngsi-ld/v1/types"),
    ("/ngsi-ld/v1/types/Device", "/ngsi-ld/v1/types/Device"),
])
def test_types_is_proxied_with_the_caller_tenant(monkeypatch, path, upstream):
    monkeypatch.setattr(gw, "PUBLIC_CONTEXT_URL", PUBLIC)
    captured = {}

    def fake_get(url, headers=None, params=None, **kw):
        captured.update(url=url, headers=headers or {}, params=dict(params or {}))
        return _Resp(b'{"typeList":[]}', headers={"Content-Type": "application/json"})

    _auth(monkeypatch)
    monkeypatch.setattr(gw.requests, "get", fake_get)
    with gw.app.test_request_context(path, headers={"NGSILD-Tenant": "other"},
                                     query_string={"details": "true"}):
        resp = gw.entity_types(type_name=path.rsplit("/types", 1)[1].lstrip("/") or None)
    assert resp.status_code == 200
    assert captured["url"] == f"{gw.ORION_URL}{upstream}"
    assert captured["headers"]["NGSILD-Tenant"] == "acme"
    assert captured["params"] == {"details": "true"}


@pytest.fixture
def client():
    gw.app.config["TESTING"] = True
    return gw.app.test_client()


def _pat(monkeypatch, scopes):
    monkeypatch.setattr(gw, "resolve_pat_info",
                        lambda tok, base: {"tenant_id": "acme", "scopes": scopes})
    monkeypatch.setattr(gw, "_is_tenant_suspended", lambda tenant: False)
    return {"Authorization": "Bearer nkz_pat_" + "a" * 40}


def test_entities_pat_reads_types(client, monkeypatch):
    headers = _pat(monkeypatch, ["entities"])
    monkeypatch.setattr(gw, "get_request_token", lambda: None)
    resp = client.get("/ngsi-ld/v1/types", headers=headers)
    # The scope check passed (not the PAT 403); the route then needs a token.
    assert resp.status_code != 403


@pytest.mark.parametrize("method", ["post", "patch", "delete"])
def test_entities_pat_cannot_write(client, monkeypatch, method):
    headers = _pat(monkeypatch, ["entities"])
    resp = getattr(client, method)("/ngsi-ld/v1/entities/urn:ngsi-ld:Device:x", headers=headers)
    assert resp.status_code in (403, 405)
