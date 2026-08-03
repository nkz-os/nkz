"""ModuleConfig must reuse a single lazily-created httpx.AsyncClient across
requests instead of opening a new connection per call, and must expose
aclose() for graceful shutdown. Timeout is configurable via
MODULE_CONFIG_TIMEOUT (default 10.0).
"""

import httpx
import pytest

from nkz_platform_sdk.config import ModuleConfig


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)

    def json(self):
        return self._json_body


class _FakeAsyncClient:
    instances: list["_FakeAsyncClient"] = []

    def __init__(self, timeout=None):
        self.timeout = timeout
        self.closed = False
        _FakeAsyncClient.instances.append(self)

    async def get(self, url, headers=None):
        return _FakeResponse(200, {"keys": []})

    async def post(self, url, json=None, headers=None):
        return _FakeResponse(200, {})

    async def delete(self, url, headers=None):
        return _FakeResponse(200, {})

    async def aclose(self):
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()


@pytest.fixture(autouse=True)
def _reset_fake_instances():
    _FakeAsyncClient.instances = []
    yield


@pytest.mark.asyncio
async def test_client_created_lazily_and_reused_across_calls(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    config = ModuleConfig(module_id="soil", tenant_id="acme")
    assert _FakeAsyncClient.instances == []  # not created at construction time

    await config.get("k1")
    await config.get("k2")
    await config.list_keys()

    assert len(_FakeAsyncClient.instances) == 1


@pytest.mark.asyncio
async def test_default_timeout_is_10_seconds(monkeypatch) -> None:
    monkeypatch.delenv("MODULE_CONFIG_TIMEOUT", raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    config = ModuleConfig(module_id="soil", tenant_id="acme")
    await config.get("k1")
    assert _FakeAsyncClient.instances[0].timeout == 10.0


@pytest.mark.asyncio
async def test_timeout_configurable_via_env(monkeypatch) -> None:
    monkeypatch.setenv("MODULE_CONFIG_TIMEOUT", "5.5")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    config = ModuleConfig(module_id="soil", tenant_id="acme")
    await config.get("k1")
    assert _FakeAsyncClient.instances[0].timeout == 5.5


@pytest.mark.asyncio
async def test_aclose_closes_underlying_client(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    config = ModuleConfig(module_id="soil", tenant_id="acme")
    await config.get("k1")
    client = _FakeAsyncClient.instances[0]
    await config.aclose()
    assert client.closed is True


@pytest.mark.asyncio
async def test_aclose_is_noop_when_client_never_created() -> None:
    config = ModuleConfig(module_id="soil", tenant_id="acme")
    await config.aclose()  # must not raise


@pytest.mark.asyncio
async def test_new_client_created_after_aclose(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    config = ModuleConfig(module_id="soil", tenant_id="acme")
    await config.get("k1")
    await config.aclose()
    await config.get("k2")
    assert len(_FakeAsyncClient.instances) == 2
