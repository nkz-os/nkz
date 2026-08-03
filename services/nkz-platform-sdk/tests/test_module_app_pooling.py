"""ModuleApp must cache OrionClient/TimescaleClient per tenant_id so the
underlying httpx client (and its connection pool) is reused across requests
instead of being recreated on every call. aclose() must close all cached
clients and clear the cache.
"""

import pytest

from nkz_platform_sdk import ModuleApp, AuthContext


def _ctx(tenant_id: str) -> AuthContext:
    return AuthContext(tenant_id=tenant_id, user_id="u", roles=("Farmer",))


async def _noop() -> None:
    return None


def test_orion_same_tenant_returns_cached_instance() -> None:
    app = ModuleApp(id="testmod", configure_logging=False)
    ctx = _ctx("acme")
    o1 = app.orion(ctx)
    o2 = app.orion(ctx)
    assert o1 is o2


def test_orion_different_tenants_get_different_instances() -> None:
    app = ModuleApp(id="testmod", configure_logging=False)
    o1 = app.orion(_ctx("acme"))
    o2 = app.orion(_ctx("other-tenant"))
    assert o1 is not o2
    assert o1.tenant_id == "acme"
    assert o2.tenant_id == "other-tenant"


def test_timescale_same_tenant_returns_cached_instance() -> None:
    app = ModuleApp(id="testmod", configure_logging=False)
    ctx = _ctx("acme")
    t1 = app.timescale(ctx)
    t2 = app.timescale(ctx)
    assert t1 is t2


def test_timescale_different_tenants_get_different_instances() -> None:
    app = ModuleApp(id="testmod", configure_logging=False)
    t1 = app.timescale(_ctx("acme"))
    t2 = app.timescale(_ctx("other-tenant"))
    assert t1 is not t2


@pytest.mark.asyncio
async def test_aclose_closes_all_cached_clients() -> None:
    app = ModuleApp(id="testmod", configure_logging=False)
    o1 = app.orion(_ctx("acme"))
    t1 = app.timescale(_ctx("acme"))

    closed = {"orion": False, "timescale": False}

    async def fake_orion_close() -> None:
        closed["orion"] = True

    async def fake_timescale_close() -> None:
        closed["timescale"] = True

    o1.close = fake_orion_close
    t1.close = fake_timescale_close

    await app.aclose()

    assert closed == {"orion": True, "timescale": True}


@pytest.mark.asyncio
async def test_aclose_clears_cache_so_new_client_created_after() -> None:
    app = ModuleApp(id="testmod", configure_logging=False)
    ctx = _ctx("acme")
    o1 = app.orion(ctx)
    o1.close = _noop
    await app.aclose()
    o2 = app.orion(ctx)
    assert o2 is not o1


@pytest.mark.asyncio
async def test_aclose_is_noop_when_nothing_cached() -> None:
    app = ModuleApp(id="testmod", configure_logging=False)
    await app.aclose()  # must not raise
