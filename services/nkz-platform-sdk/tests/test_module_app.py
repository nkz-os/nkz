"""Unit tests for ModuleApp — uses FastAPI TestClient (sync)."""

import logging

from fastapi.testclient import TestClient

from nkz_platform_sdk import ModuleApp, AuthContext


def make_client(**kwargs) -> TestClient:
    # configure_logging=False to avoid mutating root logger between tests
    app = ModuleApp(id="testmod", configure_logging=False, **kwargs)

    @app.get("/protected")
    async def protected(ctx: AuthContext = app.auth()):
        return {"tenant": ctx.tenant_id, "user": ctx.user_id, "roles": list(ctx.roles)}

    @app.get("/farmer-only")
    async def farmer_only(ctx: AuthContext = app.auth(roles=["Farmer"])):
        return {"ok": True}

    return TestClient(app)


def test_health_no_auth() -> None:
    c = make_client()
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "module": "testmod"}


def test_ready_no_auth() -> None:
    c = make_client()
    r = c.get("/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_protected_requires_tenant_header() -> None:
    c = make_client()
    r = c.get("/protected")
    assert r.status_code == 401
    assert "X-Tenant-ID" in r.json()["detail"]


def test_protected_requires_user_header() -> None:
    c = make_client()
    r = c.get("/protected", headers={"X-Tenant-ID": "acme"})
    assert r.status_code == 401
    assert "X-User-ID" in r.json()["detail"]


def test_protected_rejects_invalid_tenant_format() -> None:
    c = make_client()
    r = c.get("/protected", headers={"X-Tenant-ID": "BadTenant!", "X-User-ID": "u"})
    assert r.status_code == 401


def test_protected_success() -> None:
    c = make_client()
    r = c.get(
        "/protected",
        headers={
            "X-Tenant-ID": "acme",
            "X-User-ID": "u-1",
            "X-User-Roles": "Farmer,TenantAdmin",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["tenant"] == "acme"
    assert body["user"] == "u-1"
    assert body["roles"] == ["Farmer", "TenantAdmin"]


def test_role_check_blocks_when_missing() -> None:
    c = make_client()
    r = c.get(
        "/farmer-only",
        headers={"X-Tenant-ID": "acme", "X-User-ID": "u", "X-User-Roles": "Random"},
    )
    assert r.status_code == 403


def test_role_check_allows_when_present() -> None:
    c = make_client()
    r = c.get(
        "/farmer-only",
        headers={"X-Tenant-ID": "acme", "X-User-ID": "u", "X-User-Roles": "Farmer"},
    )
    assert r.status_code == 200


def test_cors_origin_allowed_when_configured() -> None:
    c = make_client(allowed_origins=["https://example.test"])
    r = c.options(
        "/protected",
        headers={
            "Origin": "https://example.test",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "https://example.test"


def test_request_id_header_present_in_response() -> None:
    c = make_client()
    r = c.get("/health")
    assert "x-request-id" in {k.lower() for k in r.headers}


def test_request_id_passthrough_when_provided() -> None:
    c = make_client()
    r = c.get("/health", headers={"X-Request-ID": "trace-abc"})
    assert r.headers["x-request-id"] == "trace-abc"


def test_orion_factory_returns_scoped_client() -> None:
    app = ModuleApp(id="testmod", configure_logging=False)
    ctx = AuthContext(tenant_id="acme", user_id="u", roles=("Farmer",))
    orion = app.orion(ctx)
    assert orion.tenant_id == "acme"


def test_unhandled_exception_returns_500_json() -> None:
    app = ModuleApp(id="testmod", configure_logging=False)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("oops")

    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/boom")
    assert r.status_code == 500
    assert r.json()["error"] == "internal"


def test_logging_filter_does_not_break_when_enabled() -> None:
    # Just construct with logging on, ensure no exceptions
    ModuleApp(id="testmod", configure_logging=True)
    logging.getLogger("nkz.access").info("smoke")


# ==========================================================================
# HMAC signature verification tests
# ==========================================================================

import hashlib
import hmac as hmac_lib
import os as _os
import time as _time


def _make_hmac_signature(
    secret: str, token: str, tenant_id: str, timestamp: int | None = None
) -> str:
    """Generate canonical HMAC signature matching keycloak_auth.py format."""
    ts = timestamp if timestamp is not None else int(_time.time())
    payload = f"{token}|{tenant_id}|{ts}"
    sig = hmac_lib.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{sig}:{ts}"


def test_hmac_disabled_by_default() -> None:
    """Without REQUIRE_HMAC_SIGNATURE, requests succeed without X-Auth-Signature."""
    c = make_client()
    r = c.get(
        "/protected",
        headers={
            "X-Tenant-ID": "acme",
            "X-User-ID": "u-1",
            "X-User-Roles": "Farmer",
        },
    )
    assert r.status_code == 200


def test_hmac_rejects_missing_signature_when_required(monkeypatch) -> None:
    monkeypatch.setenv("REQUIRE_HMAC_SIGNATURE", "true")
    monkeypatch.setenv("HMAC_SECRET", "test-secret")
    # Re-import to pick up env vars
    from importlib import reload
    import nkz_platform_sdk.auth as auth_mod
    reload(auth_mod)

    from nkz_platform_sdk import ModuleApp, AuthContext

    app = ModuleApp(id="testmod-hmac", configure_logging=False)

    @app.get("/hmac-protected")
    async def hmac_protected(ctx: AuthContext = app.auth()):
        return {"tenant": ctx.tenant_id}

    c = TestClient(app)
    r = c.get(
        "/hmac-protected",
        headers={"X-Tenant-ID": "acme", "X-User-ID": "u-1"},
    )
    assert r.status_code == 401
    assert "X-Auth-Signature" in r.json()["detail"]


def test_hmac_accepts_valid_signature(monkeypatch) -> None:
    secret = "test-secret"
    monkeypatch.setenv("REQUIRE_HMAC_SIGNATURE", "true")
    monkeypatch.setenv("HMAC_SECRET", secret)

    from importlib import reload
    import nkz_platform_sdk.auth as auth_mod
    reload(auth_mod)

    from nkz_platform_sdk import ModuleApp, AuthContext

    app = ModuleApp(id="testmod-hmac", configure_logging=False)

    @app.get("/hmac-protected")
    async def hmac_protected(ctx: AuthContext = app.auth()):
        return {"tenant": ctx.tenant_id}

    c = TestClient(app)
    sig = _make_hmac_signature(secret, "", "acme")
    r = c.get(
        "/hmac-protected",
        headers={
            "X-Tenant-ID": "acme",
            "X-User-ID": "u-1",
            "X-Auth-Signature": sig,
        },
    )
    assert r.status_code == 200
    assert r.json()["tenant"] == "acme"


def test_hmac_rejects_invalid_signature(monkeypatch) -> None:
    secret = "test-secret"
    monkeypatch.setenv("REQUIRE_HMAC_SIGNATURE", "true")
    monkeypatch.setenv("HMAC_SECRET", secret)

    from importlib import reload
    import nkz_platform_sdk.auth as auth_mod
    reload(auth_mod)

    from nkz_platform_sdk import ModuleApp, AuthContext

    app = ModuleApp(id="testmod-hmac", configure_logging=False)

    @app.get("/hmac-protected")
    async def hmac_protected(ctx: AuthContext = app.auth()):
        return {"tenant": ctx.tenant_id}

    c = TestClient(app)
    # Wrong secret
    wrong_sig = _make_hmac_signature("wrong-secret", "", "acme")
    r = c.get(
        "/hmac-protected",
        headers={
            "X-Tenant-ID": "acme",
            "X-User-ID": "u-1",
            "X-Auth-Signature": wrong_sig,
        },
    )
    assert r.status_code == 401
    assert "Invalid HMAC" in r.json()["detail"]


def test_hmac_rejects_expired_timestamp(monkeypatch) -> None:
    secret = "test-secret"
    monkeypatch.setenv("REQUIRE_HMAC_SIGNATURE", "true")
    monkeypatch.setenv("HMAC_SECRET", secret)

    from importlib import reload
    import nkz_platform_sdk.auth as auth_mod
    reload(auth_mod)

    from nkz_platform_sdk import ModuleApp, AuthContext

    app = ModuleApp(id="testmod-hmac", configure_logging=False)

    @app.get("/hmac-protected")
    async def hmac_protected(ctx: AuthContext = app.auth()):
        return {"tenant": ctx.tenant_id}

    c = TestClient(app)
    # 10 minutes old
    sig = _make_hmac_signature(secret, "", "acme", timestamp=int(_time.time()) - 600)
    r = c.get(
        "/hmac-protected",
        headers={
            "X-Tenant-ID": "acme",
            "X-User-ID": "u-1",
            "X-Auth-Signature": sig,
        },
    )
    assert r.status_code == 401
    assert "outside" in r.json()["detail"]


def test_hmac_rejects_malformed_signature(monkeypatch) -> None:
    monkeypatch.setenv("REQUIRE_HMAC_SIGNATURE", "true")
    monkeypatch.setenv("HMAC_SECRET", "test-secret")

    from importlib import reload
    import nkz_platform_sdk.auth as auth_mod
    reload(auth_mod)

    from nkz_platform_sdk import ModuleApp, AuthContext

    app = ModuleApp(id="testmod-hmac", configure_logging=False)

    @app.get("/hmac-protected")
    async def hmac_protected(ctx: AuthContext = app.auth()):
        return {"tenant": ctx.tenant_id}

    c = TestClient(app)
    r = c.get(
        "/hmac-protected",
        headers={
            "X-Tenant-ID": "acme",
            "X-User-ID": "u-1",
            "X-Auth-Signature": "not-even-a-hmac",
        },
    )
    assert r.status_code == 401
    assert "format" in r.json()["detail"]
