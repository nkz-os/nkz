"""/api/observations* is proxied to timeseries-reader like /api/timeseries (contract 8.14)."""
from unittest.mock import MagicMock

import pytest

import fiware_api_gateway as gw

READER = "http://timeseries-reader-service:5000"


class _Upstream:
    """Minimal requests.Response stand-in for a streamed upstream reply."""

    def __init__(self, status_code=200, chunks=(b'{"items": []}',), headers=None):
        self.status_code = status_code
        self._chunks = list(chunks)
        self.headers = headers if headers is not None else {"Content-Type": "application/json"}
        self.closed = False
        self.iterated = False

    def iter_content(self, chunk_size=None):
        self.iterated = True
        yield from self._chunks

    @property
    def content(self):
        return b"".join(self._chunks)

    def close(self):
        self.closed = True


def _patch_auth(monkeypatch, tenant="acme"):
    monkeypatch.setattr(gw, "get_request_token", lambda: "tok")
    monkeypatch.setattr(
        gw, "validate_jwt_token",
        lambda t: {"tenant_id": tenant, "sub": "u1", "realm_access": {"roles": []}},
    )
    monkeypatch.setattr(gw, "extract_tenant_id", lambda p: tenant)
    monkeypatch.setattr(gw, "rate_limit", lambda t: True)
    monkeypatch.setattr(gw, "generate_hmac_signature", lambda t, ten: "sig123")


@pytest.fixture
def client():
    gw.app.config["TESTING"] = True
    return gw.app.test_client()


@pytest.fixture
def upstream(monkeypatch):
    fake = {"get": MagicMock(return_value=_Upstream()),
            "post": MagicMock(return_value=_Upstream())}
    monkeypatch.setattr(gw.requests, "get", fake["get"])
    monkeypatch.setattr(gw.requests, "post", fake["post"])
    return fake


def test_routes_are_registered():
    rules = {r.rule: r for r in gw.app.url_map.iter_rules()}
    for rule in ("/api/observations", "/api/observations/<path:path>"):
        assert {"GET", "POST"} <= rules[rule].methods


@pytest.mark.parametrize("method,path", [
    ("get", "/api/observations/properties"),
    ("post", "/api/observations/best"),
    ("post", "/api/observations"),
])
def test_auth_required(client, upstream, monkeypatch, method, path):
    monkeypatch.setattr(gw, "get_request_token", lambda: None)
    resp = getattr(client, method)(path, json={})
    assert resp.status_code == 401
    upstream["get"].assert_not_called()
    upstream["post"].assert_not_called()


def test_invalid_token_is_rejected(client, upstream, monkeypatch):
    monkeypatch.setattr(gw, "get_request_token", lambda: "tok")
    monkeypatch.setattr(gw, "validate_jwt_token", lambda t: None)
    resp = client.get("/api/observations/properties")
    assert resp.status_code == 401
    upstream["get"].assert_not_called()


def test_tenant_comes_from_the_token_not_the_client(client, upstream, monkeypatch):
    _patch_auth(monkeypatch, tenant="acme")
    resp = client.post(
        "/api/observations/best", json={"property": "airTemperature"},
        headers={"X-Tenant-ID": "evil", "NGSILD-Tenant": "evil", "Fiware-Service": "evil",
                 "X-Delegated-Tenant-ID": "evil", "X-Auth-Signature": "forged"},
    )
    assert resp.status_code == 200
    _, kwargs = upstream["post"].call_args
    h = kwargs["headers"]
    assert h["X-Tenant-ID"] == h["NGSILD-Tenant"] == h["Fiware-Service"] == "acme"
    assert h["Authorization"] == "Bearer tok"
    # Same shape as /api/timeseries: no client delegation or signature passes through.
    assert "X-Delegated-Tenant-ID" not in h
    assert "X-Auth-Signature" not in h


def test_get_path_and_query_pass_through(client, upstream, monkeypatch):
    _patch_auth(monkeypatch)
    resp = client.get("/api/observations/best?feature=urn:ngsi-ld:AgriParcel:acme:p1&mode=latest")
    assert resp.status_code == 200
    args, kwargs = upstream["get"].call_args
    assert args[0] == f"{READER}/api/observations/best"
    assert kwargs["params"]["feature"] == "urn:ngsi-ld:AgriParcel:acme:p1"
    assert kwargs["params"]["mode"] == "latest"
    assert kwargs["stream"] is True
    assert kwargs["timeout"] == 60


def test_raw_post_hits_the_bare_path_with_body_and_long_timeout(client, upstream, monkeypatch):
    _patch_auth(monkeypatch)
    body = {"feature": "urn:ngsi-ld:AgriParcel:acme:p1", "limit": 10}
    resp = client.post("/api/observations?x=1", json=body)
    assert resp.status_code == 200
    args, kwargs = upstream["post"].call_args
    assert args[0] == f"{READER}/api/observations"
    assert kwargs["json"] == body
    assert kwargs["params"]["x"] == "1"
    assert kwargs["timeout"] == 120
    assert kwargs["stream"] is True


def test_csv_streams_with_its_headers(client, upstream, monkeypatch):
    _patch_auth(monkeypatch)
    up = _Upstream(
        chunks=[b"t,value\n", b"2026-01-01T00:00:00Z,1.5\n", b"2026-01-01T01:00:00Z,2\n"],
        headers={"Content-Type": "text/csv; charset=utf-8",
                 "Content-Disposition": 'attachment; filename="observations.csv"',
                 "X-Internal": "nope", "Transfer-Encoding": "chunked"},
    )
    upstream["post"].return_value = up
    resp = client.post("/api/observations", json={}, headers={"Accept": "text/csv"})
    _, kwargs = upstream["post"].call_args
    assert kwargs["headers"]["Accept"] == "text/csv"  # the reader picks CSV from Accept
    assert resp.status_code == 200
    assert resp.is_streamed
    assert resp.headers["Content-Type"] == "text/csv; charset=utf-8"
    assert resp.headers["Content-Disposition"] == 'attachment; filename="observations.csv"'
    assert "X-Internal" not in resp.headers
    assert resp.get_data() == b"t,value\n2026-01-01T00:00:00Z,1.5\n2026-01-01T01:00:00Z,2\n"
    resp.close()
    assert up.iterated and up.closed


@pytest.mark.parametrize("status", [400, 404, 422])
def test_upstream_4xx_passes_through_with_body(client, upstream, monkeypatch, status):
    _patch_auth(monkeypatch)
    up = _Upstream(status_code=status, chunks=[b'{"error": "unknown property"}'])
    upstream["post"].return_value = up
    resp = client.post("/api/observations/best", json={})
    assert resp.status_code == status
    assert resp.get_json() == {"error": "unknown property"}
    assert up.closed


@pytest.mark.parametrize("status", [500, 502, 504])
def test_upstream_5xx_keeps_status_hides_body(client, upstream, monkeypatch, status):
    _patch_auth(monkeypatch)
    up = _Upstream(status_code=status, chunks=[b"Traceback: secret detail"])
    upstream["get"].return_value = up
    resp = client.get("/api/observations/properties")
    assert resp.status_code == status
    assert b"secret detail" not in resp.get_data()
    assert up.closed


def test_upstream_unreachable_is_an_error(client, upstream, monkeypatch):
    _patch_auth(monkeypatch)
    upstream["get"].side_effect = gw.requests.exceptions.ConnectionError("down")
    resp = client.get("/api/observations/properties")
    assert resp.status_code == 500
