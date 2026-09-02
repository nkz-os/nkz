"""The broker is the weather source, and the mapping must not invent inputs.

`weather_observations` in PostgreSQL lost its writer in June, and two of the
columns the models depend on — `soil_moisture_0_10cm` and `gdd_accumulated` —
were NULL in all 20k rows the table ever held. These tests pin the replacement:
the per-parcel WeatherObserved from Orion-LD, converted once at this boundary.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_SERVICE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_SERVICE_DIR, os.path.dirname(_SERVICE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from weather_source import (  # noqa: E402
    fetch_parcel_weather,
    resolve_parcel_id,
    flatten_weather_observed,
    merge_forecast,
    weather_forecast_id,
    weather_observed_id,
)

TENANT = "montiko"
PARCEL = "urn:ngsi-ld:AgriParcel:da36ccd2-85d2-4c76-b552-c5c835a987c1"
ORION = "http://orion-ld-service:1026"
HEADERS = {"NGSILD-Tenant": TENANT, "Link": "<ctx>; rel=..."}


def _observed(**overrides):
    entity = {
        "id": weather_observed_id(TENANT, PARCEL),
        "type": "WeatherObserved",
        "airTemperature": {"type": "Property", "value": 21.9},
        "tempCurrent": {"type": "Property", "value": 20.2},
        "humidity": {"type": "Property", "value": 61},
        "precipitation": {"type": "Property", "value": 0},
        "et0": {"type": "Property", "value": 5.04},
        "deltaT": {"type": "Property", "value": 3.14},
        "windSpeed": {"type": "Property", "value": 4.1},
        "windDirection": {"type": "Property", "value": 59},
        "solarRadiation": {"type": "Property", "value": 268.63},
        "atmosphericPressure": {"type": "Property", "value": 956.9},
        "soilMoistureTop": {"type": "Property", "value": 0.106, "unitCode": "M3"},
        "soilMoistureSub": {"type": "Property", "value": 0.16, "unitCode": "M3"},
        "gddAccumulated": {"type": "Property", "value": 11.9},
        "dateObserved": {
            "type": "Property",
            "value": {"@type": "DateTime", "@value": "2026-09-02T12:25:29Z"},
        },
    }
    entity.update(overrides)
    return entity


def test_ids_match_the_producer_convention():
    assert weather_observed_id(TENANT, PARCEL) == (
        "urn:ngsi-ld:WeatherObserved:montiko:parcel-"
        "da36ccd2-85d2-4c76-b552-c5c835a987c1"
    )
    assert weather_forecast_id(TENANT, PARCEL).startswith(
        "urn:ngsi-ld:WeatherForecast:montiko:parcel-"
    )


def test_every_key_the_models_read_is_produced():
    """The flat dict is a contract with risk_models/; a dropped key is a silent
    loss of a whole model's input."""
    flat = flatten_weather_observed(_observed())
    for key in (
        "temp_avg", "temperature", "humidity_avg", "humidity",
        "precip_mm", "precipitation", "eto_mm", "delta_t",
        "wind_speed_ms", "wind_direction_deg",
        "solar_rad_w_m2", "solar_rad_ghi_w_m2", "radiation",
        "soil_moisture_0_10cm", "gdd_accumulated", "observed_at",
    ):
        assert key in flat, f"{key} missing from the flattened weather"


def test_soil_moisture_is_converted_from_fraction_to_percent():
    """The broker publishes m³/m³; the models compare against percentages.

    Passing 0.106 straight through reads as 0.1 % — permanent severe stress on a
    parcel that is only mildly dry.
    """
    flat = flatten_weather_observed(_observed())
    assert flat["soil_moisture_0_10cm"] == pytest.approx(10.6)
    assert flat["soil_moisture_10_40cm"] == pytest.approx(16.0)


def test_water_balance_is_derived_from_precipitation_and_et0():
    flat = flatten_weather_observed(_observed())
    assert flat["water_balance"] == pytest.approx(0 - 5.04)


def test_absent_attributes_are_omitted_not_defaulted():
    entity = _observed()
    for gone in ("soilMoistureTop", "et0", "solarRadiation"):
        del entity[gone]
    flat = flatten_weather_observed(entity)
    for key in ("soil_moisture_0_10cm", "eto_mm", "solar_rad_w_m2", "radiation"):
        assert key not in flat, f"{key} was invented from a missing input"
    # And a balance that cannot be computed is not reported as zero.
    assert "water_balance" not in flat


def test_keyvalues_representation_is_accepted_too():
    flat = flatten_weather_observed(
        {"id": "x", "type": "WeatherObserved", "airTemperature": 21.9, "humidity": 61}
    )
    assert flat["temp_avg"] == 21.9
    assert flat["humidity_avg"] == 61


def test_forecast_supplies_the_daily_minimum_for_frost():
    """temp_min is not on WeatherObserved: the SDM models it as instantaneous."""
    flat = merge_forecast(
        flatten_weather_observed(_observed()),
        {
            "dayMinimum": {"type": "Property", "value": {"temperature": 15.2}},
            "dayMaximum": {"type": "Property", "value": {"temperature": 31.9}},
        },
    )
    assert flat["temp_min"] == 15.2
    assert flat["temp_max"] == 31.9


def _response(status, payload=None):
    resp = MagicMock(status_code=status, text="")
    resp.json.return_value = payload
    return resp


def test_fetch_reads_observation_and_forecast_and_labels_fidelity():
    with patch("weather_source.requests.get") as get:
        get.side_effect = [
            _response(200, _observed()),
            _response(200, {"dayMinimum": {"value": {"temperature": 15.2}}}),
        ]
        flat = fetch_parcel_weather(ORION, HEADERS, TENANT, PARCEL)

    assert flat["temp_avg"] == 21.9
    assert flat["temp_min"] == 15.2
    assert flat["data_fidelity"] == "parcel_weather"
    assert flat["parcel_id"] == PARCEL


def test_missing_forecast_still_returns_the_observation():
    with patch("weather_source.requests.get") as get:
        get.side_effect = [_response(200, _observed()), _response(404)]
        flat = fetch_parcel_weather(ORION, HEADERS, TENANT, PARCEL)

    assert flat["temp_avg"] == 21.9
    assert "temp_min" not in flat


def test_no_observation_returns_none_rather_than_a_stale_fallback():
    """There is deliberately no fallback: a table that answers with June data is
    worse than one that does not answer."""
    with patch("weather_source.requests.get") as get:
        get.return_value = _response(404)
        assert fetch_parcel_weather(ORION, HEADERS, TENANT, PARCEL) is None


def test_broker_error_is_logged_not_mistaken_for_no_data(caplog):
    """A 400 here is what a missing @context Link looks like."""
    with patch("weather_source.requests.get") as get:
        get.return_value = _response(400, None)
        get.return_value.text = "context not found"
        with caplog.at_level("ERROR"):
            assert fetch_parcel_weather(ORION, HEADERS, TENANT, PARCEL) is None
    assert any("400" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "entity,expected",
    [
        ({"id": PARCEL, "type": "AgriParcel"}, PARCEL),
        (
            {"id": "urn:ngsi-ld:Device:d1", "type": "Device",
             "hasAgriParcel": {"type": "Relationship", "object": PARCEL}},
            PARCEL,
        ),
        (
            {"id": "urn:ngsi-ld:Device:d1", "type": "Device",
             "refAgriParcel": {"type": "Relationship", "object": PARCEL}},
            PARCEL,
        ),
        ({"id": "urn:ngsi-ld:Device:d1", "type": "Device"}, None),
    ],
    ids=["parcel-itself", "hasAgriParcel", "legacy-refAgriParcel", "unlinked"],
)
def test_resolve_parcel_id_accepts_both_relationship_names(entity, expected):
    """Weather is per parcel, so a non-parcel entity has to point at one.

    Both names are live during the ref<Type> migration window.
    """
    assert resolve_parcel_id(entity) == expected
