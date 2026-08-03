"""Async httpx-backed clients (OrionClient, TimescaleClient) must construct
their AsyncClient with an httpx.AsyncHTTPTransport carrying connection-level
retries — this only retries connection errors (safe for all HTTP methods,
including non-idempotent POST), NOT a response-status retry loop. Retry
count is configurable via NKZ_HTTP_RETRIES, default 3.
"""

from nkz_platform_sdk.orion import OrionClient
from nkz_platform_sdk.timescale import TimescaleClient


def _configured_retries(client) -> int:
    transport = client._client._transport
    assert transport is not None
    return transport._pool._retries


def test_orion_client_default_retries_is_3(monkeypatch) -> None:
    monkeypatch.delenv("NKZ_HTTP_RETRIES", raising=False)
    client = OrionClient("acme")
    assert _configured_retries(client) == 3


def test_orion_client_retries_configurable_via_env(monkeypatch) -> None:
    monkeypatch.setenv("NKZ_HTTP_RETRIES", "5")
    client = OrionClient("acme")
    assert _configured_retries(client) == 5


def test_timescale_client_default_retries_is_3(monkeypatch) -> None:
    monkeypatch.delenv("NKZ_HTTP_RETRIES", raising=False)
    client = TimescaleClient("acme")
    assert _configured_retries(client) == 3


def test_timescale_client_retries_configurable_via_env(monkeypatch) -> None:
    monkeypatch.setenv("NKZ_HTTP_RETRIES", "7")
    client = TimescaleClient("acme")
    assert _configured_retries(client) == 7


def test_orion_client_timeout_unchanged() -> None:
    # Existing timeout behavior (30s) must not regress.
    client = OrionClient("acme")
    assert client._client.timeout.connect == 30.0


def test_timescale_client_timeout_unchanged() -> None:
    client = TimescaleClient("acme")
    assert client._client.timeout.connect == 30.0
