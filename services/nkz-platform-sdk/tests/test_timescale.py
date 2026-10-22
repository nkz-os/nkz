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
    client, fake = _make(
        {
            "timestamps": ["2026-05-01T00:00:00Z"],
            "attributes": {"soilMoisture": ["42.5"]},
        }
    )
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
    # The reader takes the URN in the path and names the attribute `attrs`.
    assert fake.last_get["url"].endswith(
        "/api/timeseries/v2/entities/urn:ngsi-ld:AgriParcel:p1/data"
    )
    assert fake.last_get["params"]["attrs"] == "soilMoisture"


@pytest.mark.asyncio
async def test_query_filters_non_numeric() -> None:
    client, _ = _make(
        {
            "timestamps": [
                "2026-05-01T00:00:00Z",
                "2026-05-02T00:00:00Z",
                "2026-05-03T00:00:00Z",
                "2026-05-04T00:00:00Z",
            ],
            "attributes": {"x": ["42", "not-a-number", None, 7]},
        }
    )
    pts = await client.query(
        entity_id="urn:ngsi-ld:AgriParcel:p1",
        attribute="x",
        since="2026-05-01T00:00:00Z",
    )
    assert [p["value"] for p in pts] == [42.0, 7.0]


@pytest.mark.asyncio
async def test_query_tolerates_an_unexpected_payload_shape() -> None:
    """The reader answers columnar. Anything else yields no rows, never a crash.

    (This replaces a test that asserted a bare row list was accepted — a shape
    the reader has never returned.)
    """
    client, _ = _make([{"ts": "2026-05-01T00:00:00Z", "value": 1}])
    pts = await client.query(
        entity_id="urn:ngsi-ld:AgriParcel:p1",
        attribute="x",
        since="2026-05-01T00:00:00Z",
    )
    assert pts == []


@pytest.mark.asyncio
async def test_query_aggregate_posts_to_v2() -> None:
    client, fake = _make({"series": []})
    body = {"entityIds": ["a", "b"], "attributes": ["x"], "aggrPeriod": "PT1H"}
    out = await client.query_aggregate(body)
    assert out == {"series": []}
    assert fake.last_post is not None
    assert fake.last_post["url"].endswith("/api/timeseries/v2/query")
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
