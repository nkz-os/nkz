"""WeatherObserved attributes must resolve through telemetry_events.

weather_observations is deprecated (no longer populated by weather-worker);
live data arrives via Orion-LD subscription into telemetry_events. The reader
must therefore serve any whitelisted NGSI-LD measurement attribute even when
the entity plan resolves a weather key, and the whitelist must cover the real
WeatherObserved measurement keys (soilMoistureTop, tempCurrent, ...).
"""
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

# auth_middleware must be stubbed BEFORE importing app: the real one validates
# JWTs against Keycloak and rejects test_client requests.
if "auth_middleware" not in sys.modules:
    _auth_stub = MagicMock()
    _auth_stub.require_auth = lambda f: f
    _auth_stub.inject_fiware_headers = lambda headers, tenant=None: dict(headers or {})
    _auth_stub.internal_error = lambda e: ({"error": "internal"}, 500)
    sys.modules["auth_middleware"] = _auth_stub

import pytest  # noqa: E402

import app  # noqa: E402

URN = "urn:ngsi-ld:WeatherObserved:testtenant:parcel-0001"
WEATHER_PLAN = {
    "mode": "weather",
    "weather_key": "31013",
    "weather_source": "municipality",
    "device_candidates": [URN, "parcel-0001"],
}
TELEMETRY_PLAN = {
    "mode": "telemetry",
    "weather_key": None,
    "weather_source": "",
    "device_candidates": [URN, "parcel-0001"],
}
QUERY = (
    f"/api/timeseries/v2/entities/{URN}/data"
    "?time_from=2026-09-01T00:00:00Z&time_to=2026-09-19T00:00:00Z"
)
HEADERS = {"X-Tenant-ID": "testtenant"}


def _telemetry_row(measurements, when=None):
    return {
        "observed_at": when or datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc),
        "payload": {"measurements": measurements},
        "quality_flag": None,
    }


# ---------------------------------------------------------------------------
# Whitelist: real WeatherObserved measurement keys must resolve as telemetry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attr",
    [
        "soilMoistureTop",
        "soilMoistureSub",
        "tempCurrent",
        "windGusts",
        "windSpeedMax",
        "gddAccumulated",
        "gustSpeed",
    ],
)
def test_weather_station_keys_in_telemetry_whitelist(attr):
    assert app._resolve_telemetry_measurement_key(attr) == attr


def test_unknown_attribute_still_rejected():
    assert app._resolve_telemetry_measurement_key("fooBar") is None


# ---------------------------------------------------------------------------
# GET /v2/entities/<urn>/data — weather-mode plan must fall through to telemetry
# ---------------------------------------------------------------------------


@patch("app._fetch_telemetry_rows")
@patch("app._weather_query_columnar")
@patch("app.get_db_connection")
@patch("app.plan_timeseries_read")
def test_get_weather_mode_unmapped_attr_falls_back_to_telemetry(
    plan, db_conn, weather_q, fetch
):
    plan.return_value = dict(WEATHER_PLAN)
    db_conn.return_value.__enter__.return_value = MagicMock()
    weather_q.return_value = {"timestamps": [], "attributes": {}}
    fetch.return_value = [_telemetry_row({"airTemperature": 15.03})]

    with app.app.test_client() as c:
        r = c.get(QUERY + "&attrs=airTemperature", headers=HEADERS)

    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["series_kind"] == "telemetry_fallback"
    assert body["attributes"]["airTemperature"] == [15.03]
    weather_q.assert_not_called()  # deprecated table skipped entirely


@patch("app._fetch_telemetry_rows")
@patch("app._weather_query_columnar")
@patch("app.get_db_connection")
@patch("app.plan_timeseries_read")
def test_get_weather_mode_mapped_attr_uses_weather_table_when_populated(
    plan, db_conn, weather_q, fetch
):
    plan.return_value = dict(WEATHER_PLAN)
    db_conn.return_value.__enter__.return_value = MagicMock()
    weather_q.return_value = {"timestamps": ["2026-09-18T12:00:00+00:00"], "attributes": {"temp_avg": [21.5]}}
    fetch.return_value = []

    with app.app.test_client() as c:
        r = c.get(QUERY + "&attrs=temperature", headers=HEADERS)

    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["series_kind"] == "weather"
    assert body["attributes"]["temp_avg"] == [21.5]
    weather_q.assert_called_once()
    fetch.assert_not_called()


@patch("app._fetch_telemetry_rows")
@patch("app._weather_query_columnar")
@patch("app.get_db_connection")
@patch("app.plan_timeseries_read")
def test_get_weather_mode_unknown_attr_still_400(plan, db_conn, weather_q, fetch):
    plan.return_value = dict(WEATHER_PLAN)
    db_conn.return_value.__enter__.return_value = MagicMock()

    with app.app.test_client() as c:
        r = c.get(QUERY + "&attrs=fooBar", headers=HEADERS)

    assert r.status_code == 400
    assert "fooBar" in r.get_json()["error"]


@patch("app._fetch_telemetry_rows")
@patch("app.get_db_connection")
@patch("app.plan_timeseries_read")
def test_get_telemetry_mode_serves_station_keys(plan, db_conn, fetch):
    plan.return_value = dict(TELEMETRY_PLAN)
    db_conn.return_value.__enter__.return_value = MagicMock()
    fetch.return_value = [_telemetry_row({"soilMoistureTop": 0.102})]

    with app.app.test_client() as c:
        r = c.get(QUERY + "&attrs=soilMoistureTop", headers=HEADERS)

    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["series_kind"] == "telemetry"
    assert body["attributes"]["soilMoistureTop"] == [0.102]


# ---------------------------------------------------------------------------
# POST /v2/query — weather-mode series with unmapped attr must become telemetry
# ---------------------------------------------------------------------------


def _batch_body(attr):
    return {
        "time_from": "2026-09-01T00:00:00Z",
        "time_to": "2026-09-19T00:00:00Z",
        "resolution": 1000,
        "series": [{"entity_urn": URN, "attribute": attr}],
    }


@patch("app.get_db_connection")
@patch("app._execute_v2_align_unified_sql")
@patch("app.plan_timeseries_read")
def test_batch_weather_mode_unmapped_attr_becomes_telemetry(plan, align, db_conn):
    plan.return_value = dict(WEATHER_PLAN)
    db_conn.return_value.__enter__.return_value = MagicMock()
    import pyarrow as pa

    align.return_value = pa.table({"timestamp": [1758210000.0], "value_0": [15.03]})

    with app.app.test_client() as c:
        r = c.post("/api/timeseries/v2/query", json=_batch_body("airTemperature"), headers=HEADERS)

    assert r.status_code == 200, r.get_data(as_text=True)
    specs = align.call_args[0][5]
    assert specs[0]["kind"] == "telemetry"
    assert specs[0]["attr"] == "airTemperature"


@patch("app.get_db_connection")
@patch("app._execute_v2_align_unified_sql")
@patch("app.plan_timeseries_read")
def test_batch_weather_mode_mapped_ngsi_name_still_weather(plan, align, db_conn):
    plan.return_value = dict(WEATHER_PLAN)
    db_conn.return_value.__enter__.return_value = MagicMock()
    import pyarrow as pa

    align.return_value = pa.table({"timestamp": [1758210000.0], "value_0": [21.5]})

    with app.app.test_client() as c:
        r = c.post("/api/timeseries/v2/query", json=_batch_body("temperature"), headers=HEADERS)

    assert r.status_code == 200, r.get_data(as_text=True)
    specs = align.call_args[0][5]
    assert specs[0]["kind"] == "weather"
    assert specs[0]["attr"] == "temp_avg"


@patch("app.plan_timeseries_read")
def test_batch_unknown_attr_still_400(plan):
    plan.return_value = dict(WEATHER_PLAN)

    with app.app.test_client() as c:
        r = c.post("/api/timeseries/v2/query", json=_batch_body("fooBar"), headers=HEADERS)

    assert r.status_code == 400
