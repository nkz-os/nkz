"""installed_tenants — the platform's answer, never an empty one by mistake."""

import httpx
import pytest
import respx
from httpx import Response

from nkz_platform_sdk.installations import (
    InstalledTenantsUnavailable,
    TenantInstallations,
    installed_tenants,
)

EM = "http://em:5000"
URL = f"{EM}/api/internal/modules/bioorchestrator/tenants"


async def _call(**kw):
    return await installed_tenants("bioorchestrator", base_url=EM, internal_secret="s3", **kw)


@pytest.mark.asyncio
@respx.mock
async def test_parses_states_sorted():
    route = respx.get(URL).mock(return_value=Response(200, json={
        "module_id": "bioorchestrator",
        "tenants": {"z": "enabled", "a": "enabled", "b": "disabled", "c": "not_installed"},
    }))
    got = await _call()
    assert got == TenantInstallations("bioorchestrator", ("a", "z"), ("b",), ("c",))
    assert route.calls[0].request.headers["X-Internal-Service-Secret"] == "s3"


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("status", [401, 404, 500, 503])
async def test_non_200_raises(status):
    respx.get(URL).mock(return_value=Response(status, json={"error": "x"}))
    with pytest.raises(InstalledTenantsUnavailable):
        await _call()


@pytest.mark.asyncio
@respx.mock
async def test_network_error_raises():
    respx.get(URL).mock(side_effect=httpx.ConnectTimeout("t"))
    with pytest.raises(InstalledTenantsUnavailable):
        await _call()


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("body", [
    "not json",
    [],
    {"module_id": "bioorchestrator"},
    {"module_id": "other", "tenants": {}},
    {"module_id": "bioorchestrator", "tenants": {"a": "maybe"}},
    {"module_id": "bioorchestrator", "tenants": ["a"]},
])
async def test_malformed_body_raises(body):
    if isinstance(body, str):
        respx.get(URL).mock(return_value=Response(200, text=body))
    else:
        respx.get(URL).mock(return_value=Response(200, json=body))
    with pytest.raises(InstalledTenantsUnavailable):
        await _call()


@pytest.mark.asyncio
async def test_missing_secret_raises_without_calling(monkeypatch):
    monkeypatch.delenv("INTERNAL_SERVICE_SECRET", raising=False)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(URL)
        with pytest.raises(InstalledTenantsUnavailable):
            await installed_tenants("bioorchestrator", base_url=EM)
        assert not route.called


@pytest.mark.asyncio
@respx.mock
async def test_env_defaults(monkeypatch):
    monkeypatch.setenv("ENTITY_MANAGER_URL", "http://em-env:1/")
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "envsecret")
    route = respx.get("http://em-env:1/api/internal/modules/risk/tenants").mock(
        return_value=Response(200, json={"module_id": "risk", "tenants": {}})
    )
    got = await installed_tenants("risk")
    assert got == TenantInstallations("risk", (), (), ())
    assert route.calls[0].request.headers["X-Internal-Service-Secret"] == "envsecret"


@pytest.mark.asyncio
@respx.mock
async def test_module_id_is_quoted():
    route = respx.get(f"{EM}/api/internal/modules/a%2Fb/tenants").mock(
        return_value=Response(200, json={"module_id": "a/b", "tenants": {}})
    )
    await installed_tenants("a/b", base_url=EM, internal_secret="s3")
    assert route.called
