"""X-Tenant-Plan header injection for tier-gated modules.

The api-gateway injects tenant context into module-proxy headers. The carbon
module gates on the X-Tenant-Plan header; without this injection every tenant
resolved to the fail-closed "basic" default and got 402 regardless of plan.
"""


def _clear_plan_cache(gw):
    gw._tenant_plan_cache.clear()


def test_build_authenticated_proxy_headers_injects_tenant_plan(monkeypatch):
    import fiware_api_gateway as gw

    monkeypatch.setattr(gw, "_get_tenant_plan", lambda t: "premium")
    monkeypatch.setattr(gw, "generate_hmac_signature", lambda t, ten: "sig")
    with gw.app.test_request_context("/", method="GET"):
        headers = gw._build_authenticated_proxy_headers("tok", {"sub": "u-1"}, "montiko")
    assert headers["X-Tenant-Plan"] == "premium"
    assert headers["X-Tenant-ID"] == "montiko"


def test_get_tenant_plan_reads_plan_type(monkeypatch):
    import fiware_api_gateway as gw

    _clear_plan_cache(gw)
    monkeypatch.setenv("POSTGRES_URL", "postgresql://x")

    class _Cur:
        def execute(self, sql, params):
            pass

        def fetchone(self):
            return ("enterprise",)

        def close(self):
            pass

    class _Conn:
        def cursor(self):
            return _Cur()

        def close(self):
            pass

    monkeypatch.setattr(gw.psycopg2, "connect", lambda *a, **k: _Conn())
    assert gw._get_tenant_plan("montiko") == "enterprise"


def test_get_tenant_plan_fails_open_on_db_error(monkeypatch):
    import fiware_api_gateway as gw

    _clear_plan_cache(gw)
    monkeypatch.setenv("POSTGRES_URL", "postgresql://x")

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(gw.psycopg2, "connect", boom)
    assert gw._get_tenant_plan("montiko") == "enterprise"


def test_get_tenant_plan_caches_within_ttl(monkeypatch):
    import fiware_api_gateway as gw

    _clear_plan_cache(gw)
    monkeypatch.setenv("POSTGRES_URL", "postgresql://x")
    calls = {"n": 0}

    class _Cur:
        def execute(self, sql, params):
            pass

        def fetchone(self):
            return ("pro",)

        def close(self):
            pass

    class _Conn:
        def cursor(self):
            return _Cur()

        def close(self):
            pass

    def connect(*a, **k):
        calls["n"] += 1
        return _Conn()

    monkeypatch.setattr(gw.psycopg2, "connect", connect)
    assert gw._get_tenant_plan("montiko") == "pro"
    assert gw._get_tenant_plan("montiko") == "pro"
    assert calls["n"] == 1
