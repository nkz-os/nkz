"""The client must call routes the timeseries-reader actually serves.

`TimescaleClient.query()` used to GET `/api/timeseries` and `query_aggregate()`
POST `/api/v2/query`. Neither exists: the reader serves
`/api/timeseries/v2/entities/<urn>/data` and `/api/timeseries/v2/query`
(services/timeseries-reader/app.py). Every call 404'd, and the only consumer
swallowed the error as "no readings" — a silent, permanent empty result.

The v1 route `/api/timeseries/entities/<id>/data` is NOT the target: it validates
`attribute` against a weather-column whitelist, so a telemetry measurement name
is rejected with 400. v2 resolves the series kind from the URN instead.
"""

from typing import Any

import pytest

from nkz_platform_sdk.timescale import TimescaleClient


class _FakeResponse:
    def __init__(self, payload: Any):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, payload: Any):
        self._payload = payload
        self.last_get: dict[str, Any] | None = None
        self.last_post: dict[str, Any] | None = None

    async def get(self, url: str, params: dict[str, Any], headers: dict[str, str]):
        self.last_get = {"url": url, "params": params, "headers": headers}
        return _FakeResponse(self._payload)

    async def post(self, url: str, json: dict[str, Any], headers: dict[str, str]):
        self.last_post = {"url": url, "json": json, "headers": headers}
        return _FakeResponse(self._payload)


def _make(payload: Any) -> tuple[TimescaleClient, _FakeAsyncClient]:
    client = TimescaleClient(tenant_id="acme", base_url="http://ts-test:5000")
    fake = _FakeAsyncClient(payload)
    client._client = fake  # type: ignore[assignment]
    return client, fake


_COLUMNAR = {
    "entity_urn": "urn:ngsi-ld:DeviceMeasurement:acme:probe-1:leafWetness",
    "series_kind": "telemetry",
    "timestamps": ["2026-08-29T09:00:00Z", "2026-08-29T10:00:00Z"],
    "attributes": {"leafWetness": [1, 0]},
}

URN = "urn:ngsi-ld:DeviceMeasurement:acme:probe-1:leafWetness"


@pytest.mark.asyncio
async def test_query_targets_the_v2_entity_route_with_the_urn_in_the_path():
    client, fake = _make(_COLUMNAR)
    await client.query(entity_id=URN, attribute="leafWetness", since="2026-08-29T00:00:00Z")

    assert fake.last_get is not None
    assert fake.last_get["url"] == f"http://ts-test:5000/api/timeseries/v2/entities/{URN}/data"
    # The URN identifies the series in the path; there is no entityId parameter.
    assert "entityId" not in fake.last_get["params"]


@pytest.mark.asyncio
async def test_query_sends_the_parameter_names_the_reader_reads():
    client, fake = _make(_COLUMNAR)
    await client.query(
        entity_id=URN,
        attribute="leafWetness",
        since="2026-08-29T00:00:00Z",
        until="2026-08-29T12:00:00Z",
        limit=50,
    )
    params = fake.last_get["params"]
    assert params["time_from"] == "2026-08-29T00:00:00Z"
    assert params["time_to"] == "2026-08-29T12:00:00Z"
    assert params["attrs"] == "leafWetness"
    assert params["limit"] == 50


@pytest.mark.asyncio
async def test_time_to_is_always_sent_because_the_reader_requires_it():
    """The reader answers 400 without time_to; `until` is optional on this API."""
    client, fake = _make(_COLUMNAR)
    await client.query(entity_id=URN, attribute="leafWetness", since="2026-08-29T00:00:00Z")
    assert fake.last_get["params"]["time_to"]


@pytest.mark.asyncio
async def test_columnar_response_becomes_rows():
    """The v2 payload is columnar; callers are promised oldest-first rows."""
    client, _ = _make(_COLUMNAR)
    points = await client.query(
        entity_id=URN, attribute="leafWetness", since="2026-08-29T00:00:00Z"
    )
    assert points == [
        {"ts": "2026-08-29T09:00:00Z", "value": 1.0},
        {"ts": "2026-08-29T10:00:00Z", "value": 0.0},
    ]


@pytest.mark.asyncio
async def test_missing_attribute_yields_no_rows():
    client, _ = _make(
        {"timestamps": ["2026-08-29T09:00:00Z"], "attributes": {"temperature": [21.0]}}
    )
    assert await client.query(
        entity_id=URN, attribute="leafWetness", since="2026-08-29T00:00:00Z"
    ) == []


@pytest.mark.asyncio
async def test_non_numeric_and_null_points_are_dropped():
    client, _ = _make(
        {
            "timestamps": ["t1", "t2", "t3", "t4"],
            "attributes": {"leafWetness": [1, None, "nope", "7"]},
        }
    )
    points = await client.query(
        entity_id=URN, attribute="leafWetness", since="2026-08-29T00:00:00Z"
    )
    assert points == [{"ts": "t1", "value": 1.0}, {"ts": "t4", "value": 7.0}]


@pytest.mark.asyncio
async def test_ragged_columns_do_not_raise():
    """Never IndexError on a short column — drop the unpaired tail."""
    client, _ = _make({"timestamps": ["t1", "t2", "t3"], "attributes": {"x": [1]}})
    assert await client.query(entity_id=URN, attribute="x", since="s") == [
        {"ts": "t1", "value": 1.0}
    ]


@pytest.mark.asyncio
async def test_aggregate_targets_the_v2_query_route():
    client, fake = _make({"ok": True})
    await client.query_aggregate({"entityIds": ["a"], "attributes": ["x"]})
    assert fake.last_post["url"] == "http://ts-test:5000/api/timeseries/v2/query"
