"""The /ngsi-ld proxy is the @context chokepoint for FE writes (regression lock).

On a mutation whose JSON body has no `@context`, the gateway MUST inject
`@context = [ngsi-ld-core-context, CONTEXT_URL]`, set Content-Type to
`application/ld+json`, and drop any `Link` header. This prevents the
"false-zero" bug where entities stored under an unexpanded type can't be
queried by short type name.
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


class _R:
    content = b"{}"
    status_code = 201
    headers = {}


def test_entity_post_gets_context_injected(monkeypatch):
    import fiware_api_gateway as gw

    captured = {}

    def fake_post(url, headers=None, json=None, params=None, **kw):
        captured.update(url=url, headers=headers or {}, json=json)
        return _R()

    _auth(monkeypatch, gw)
    monkeypatch.setattr(gw.requests, "post", fake_post)
    with gw.app.test_request_context(
        "/ngsi-ld/v1/entities",
        method="POST",
        json={"id": "urn:ngsi-ld:AgriParcel:x", "type": "AgriParcel"},
    ):
        gw.entities()

    assert "@context" in captured["json"], f"no @context injected: {captured.get('json')}"
    ctx = captured["json"]["@context"]
    ctx_list = ctx if isinstance(ctx, list) else [ctx]
    # Platform CONTEXT_URL must be among the injected contexts.
    assert any(str(gw.CONTEXT_URL) == str(c) for c in ctx_list), (
        f"CONTEXT_URL {gw.CONTEXT_URL!r} not in {ctx_list!r}"
    )
    assert any("context" in str(c).lower() for c in ctx_list)
    assert captured["headers"].get("Content-Type") == "application/ld+json"
    assert "Link" not in captured["headers"]
