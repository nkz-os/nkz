"""Unit tests for TimescaleClient — fakes the httpx layer, no network."""

from typing import Any

import pytest

from nkz_platform_sdk.timescale import TimescaleClient


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse):
        self.response = response
        self.last_get: dict[str, Any] | None = None
        self.last_post: dict[str, Any] | None = None

    async def get(self, url: str, params: dict[str, Any], headers: dict[str, str]):
        self.last_get = {"url": url, "params": params, "headers": headers}
        return self.response

    async def post(self, url: str, json: dict[str, Any], headers: dict[str, str]):
        self.last_post = {"url": url, "json": json, "headers": headers}
        return self.response

    async def aclose(self) -> None:
        pass


def _make(payload: Any, status: int = 200) -> tuple[TimescaleClient, _FakeAsyncClient]:
    client = TimescaleClient(tenant_id="acme", base_url="http://ts-test:5000")
    fake = _FakeAsyncClient(_FakeResponse(status, payload))
    client._client = fake  # type: ignore[assignment]
    return client, fake


@pytest.mark.asyncio
async def test_query_normalises_points() -> None:
    client, fake = _make({"points": [{"ts": "2026-05-01T00:00:00Z", "value": "42.5"}]})
    pts = await client.query(
        entity_id="urn:ngsi-ld:AgriParcel:p1",
        attribute="soilMoisture",
        since="2026-05-01T00:00:00Z",
    )
    assert pts == [{"ts": "2026-05-01T00:00:00Z", "value": 42.5}]
    assert fake.last_get is not None
    assert fake.last_get["headers"]["X-Tenant-ID"] == "acme"
    assert fake.last_get["headers"]["NGSILD-Tenant"] == "acme"
    assert fake.last_get["headers"]["Fiware-Service"] == "acme"
    assert fake.last_get["params"]["entityId"] == "urn:ngsi-ld:AgriParcel:p1"
    assert fake.last_get["params"]["attribute"] == "soilMoisture"


@pytest.mark.asyncio
async def test_query_filters_non_numeric() -> None:
    client, _ = _make(
        {
            "points": [
                {"ts": "2026-05-01T00:00:00Z", "value": "42"},
                {"ts": "2026-05-02T00:00:00Z", "value": "not-a-number"},
                {"ts": "2026-05-03T00:00:00Z"},  # missing value
                {"ts": "2026-05-04T00:00:00Z", "value": 7},
            ]
        }
    )
    pts = await client.query(
        entity_id="urn:ngsi-ld:AgriParcel:p1",
        attribute="x",
        since="2026-05-01T00:00:00Z",
    )
    assert [p["value"] for p in pts] == [42.0, 7.0]


@pytest.mark.asyncio
async def test_query_accepts_bare_list_payload() -> None:
    client, _ = _make([{"ts": "2026-05-01T00:00:00Z", "value": 1}])
    pts = await client.query(
        entity_id="urn:ngsi-ld:AgriParcel:p1",
        attribute="x",
        since="2026-05-01T00:00:00Z",
    )
    assert pts == [{"ts": "2026-05-01T00:00:00Z", "value": 1.0}]


@pytest.mark.asyncio
async def test_query_aggregate_posts_to_v2() -> None:
    client, fake = _make({"series": []})
    body = {"entityIds": ["a", "b"], "attributes": ["x"], "aggrPeriod": "PT1H"}
    out = await client.query_aggregate(body)
    assert out == {"series": []}
    assert fake.last_post is not None
    assert fake.last_post["url"].endswith("/api/v2/query")
    assert fake.last_post["json"] == body
    assert fake.last_post["headers"]["X-Tenant-ID"] == "acme"


@pytest.mark.asyncio
async def test_http_error_propagates() -> None:
    client, _ = _make({}, status=500)
    with pytest.raises(RuntimeError):
        await client.query(
            entity_id="urn:ngsi-ld:AgriParcel:p1",
            attribute="x",
            since="2026-05-01T00:00:00Z",
        )
