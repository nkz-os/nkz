"""End-to-end round-trip guard for the uniform entity-write path (Plan 2 / Task 6).

The false-zero bug bites when the @context used to WRITE an AgriParcel differs from
the @context used to READ it by short type. The /ngsi-ld proxy must use the SAME
platform CONTEXT_URL on both legs:

  - WRITE (POST/PATCH): @context injected into the body (locked in
    test_ngsild_context_injection.py).
  - READ (GET ?type=AgriParcel): platform CONTEXT_URL forwarded via the Link header
    (inject_fiware_headers), so the short type expands to the same full URI.

If both legs carry CONTEXT_URL, an AgriParcel written by short type is found by a
query on the same short type. This test pins the read leg + the write==read invariant
without needing a live broker.
"""


def _auth(monkeypatch, gw, tenant="montiko"):
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
    monkeypatch.setattr(gw, "generate_hmac_signature", lambda t, ten: "sig")


class _R:
    content = b"[]"
    status_code = 200
    headers = {}


def _ctx_in_link(link_value):
    """True if a Link header carries a json-ld context (and return is the URL string)."""
    return link_value and 'rel="http://www.w3.org/ns/json-ld#context"' in link_value


def test_get_by_short_type_forwards_platform_context(monkeypatch):
    """A GET ?type=AgriParcel forwards the platform CONTEXT_URL via the Link header."""
    import fiware_api_gateway as gw

    captured = {}

    def fake_get(url, headers=None, params=None, **kw):
        captured.update(url=url, headers=headers or {}, params=params or {})
        return _R()

    _auth(monkeypatch, gw)
    monkeypatch.setattr(gw.requests, "get", fake_get)
    with gw.app.test_request_context(
        "/ngsi-ld/v1/entities", method="GET", query_string={"type": "AgriParcel"}
    ):
        gw.entities()

    link = captured["headers"].get("Link", "")
    assert _ctx_in_link(link), f"read leg missing json-ld context Link: {captured['headers']!r}"
    assert str(gw.CONTEXT_URL) in link, (
        f"CONTEXT_URL {gw.CONTEXT_URL!r} not forwarded on read: {link!r}"
    )


def test_write_and_read_use_same_platform_context(monkeypatch):
    """The CONTEXT_URL injected on write equals the one forwarded on read."""
    import fiware_api_gateway as gw

    write_ctx = {}
    read_ctx = {}

    def fake_post(url, headers=None, json=None, params=None, **kw):
        write_ctx["body"] = (json or {}).get("@context")
        return type("W", (), {"content": b"{}", "status_code": 201, "headers": {}})()

    def fake_get(url, headers=None, params=None, **kw):
        read_ctx["link"] = (headers or {}).get("Link", "")
        return _R()

    _auth(monkeypatch, gw)
    monkeypatch.setattr(gw.requests, "post", fake_post)
    monkeypatch.setattr(gw.requests, "get", fake_get)

    with gw.app.test_request_context(
        "/ngsi-ld/v1/entities",
        method="POST",
        json={"id": "urn:ngsi-ld:AgriParcel:x", "type": "AgriParcel"},
    ):
        gw.entities()
    with gw.app.test_request_context(
        "/ngsi-ld/v1/entities", method="GET", query_string={"type": "AgriParcel"}
    ):
        gw.entities()

    ctx_list = write_ctx["body"] if isinstance(write_ctx["body"], list) else [write_ctx["body"]]
    assert any(str(gw.CONTEXT_URL) == str(c) for c in ctx_list), (
        f"write leg missing CONTEXT_URL: {write_ctx['body']!r}"
    )
    assert str(gw.CONTEXT_URL) in read_ctx["link"], (
        f"read leg missing CONTEXT_URL: {read_ctx['link']!r}"
    )
