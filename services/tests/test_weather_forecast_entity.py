"""WeatherForecast lleva los agregados diarios que WeatherObserved no puede llevar.

El SDM modela WeatherObserved como observación instantánea y no define dayMinimum ni
dayMaximum; WeatherForecast sí. FAO-56 necesita tMin y tMax, así que meterlos en la
observación sería usar mal el modelo.
"""

import ast
import os
import sys

_SERVICES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_SERVICES, os.path.join(_SERVICES, "weather-worker")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from weather_worker.storage.orion_writer import build_weather_forecast_entity  # noqa: E402

DAILY = {
    "temp_min": 9.9,
    "temp_max": 28.4,
    "humidity_avg": 55.0,
    "precip_probability": 20.0,
}


def _entity():
    return build_weather_forecast_entity(
        parcel_id="urn:ngsi-ld:AgriParcel:t:p1",
        tenant_id="t",
        location=(-2.07, 42.63, 450.0),
        daily=DAILY,
        valid_from="2026-09-01T00:00:00Z",
        valid_to="2026-09-01T23:59:59Z",
    )


def test_id_is_deterministic_per_parcel():
    assert _entity()["id"] == "urn:ngsi-ld:WeatherForecast:t:parcel-p1"


def test_type_is_the_sdm_type():
    assert _entity()["type"] == "WeatherForecast"


def test_day_minimum_and_maximum_carry_temperature_and_humidity():
    e = _entity()
    assert e["dayMinimum"]["value"]["temperature"] == 9.9
    assert e["dayMaximum"]["value"]["temperature"] == 28.4
    assert e["dayMaximum"]["value"]["relativeHumidity"] == 55.0


def test_carries_precipitation_probability():
    assert _entity()["precipitationProbability"]["value"] == 20.0


def test_carries_the_validity_window():
    e = _entity()
    assert e["validFrom"]["value"]["@value"] == "2026-09-01T00:00:00Z"
    assert e["validTo"]["value"]["@value"] == "2026-09-01T23:59:59Z"


def test_location_keeps_the_reference_elevation():
    assert _entity()["location"]["value"]["coordinates"] == [-2.07, 42.63, 450.0]


def test_omits_precipitation_probability_when_missing():
    e = build_weather_forecast_entity(
        parcel_id="urn:ngsi-ld:AgriParcel:t:p1",
        tenant_id="t",
        location=(-2.07, 42.63),
        daily={"temp_min": 9.9, "temp_max": 28.4},
        valid_from="2026-09-01T00:00:00Z",
        valid_to="2026-09-01T23:59:59Z",
    )
    assert "precipitationProbability" not in e, "no se inventa un valor ausente"


def test_omits_day_minimum_when_temp_min_missing():
    e = build_weather_forecast_entity(
        parcel_id="urn:ngsi-ld:AgriParcel:t:p1",
        tenant_id="t",
        location=(-2.07, 42.63),
        daily={"temp_max": 28.4, "humidity_avg": 55.0},
        valid_from="2026-09-01T00:00:00Z",
        valid_to="2026-09-01T23:59:59Z",
    )
    assert "dayMinimum" not in e, "no se inventa dayMinimum sin temp_min"


def test_omits_day_maximum_when_temp_max_missing():
    e = build_weather_forecast_entity(
        parcel_id="urn:ngsi-ld:AgriParcel:t:p1",
        tenant_id="t",
        location=(-2.07, 42.63),
        daily={"temp_min": 9.9, "humidity_avg": 55.0},
        valid_from="2026-09-01T00:00:00Z",
        valid_to="2026-09-01T23:59:59Z",
    )
    assert "dayMaximum" not in e, "no se inventa dayMaximum sin temp_max"


def test_omits_relative_humidity_from_day_extremes_when_missing():
    e = build_weather_forecast_entity(
        parcel_id="urn:ngsi-ld:AgriParcel:t:p1",
        tenant_id="t",
        location=(-2.07, 42.63),
        daily={"temp_min": 9.9, "temp_max": 28.4},
        valid_from="2026-09-01T00:00:00Z",
        valid_to="2026-09-01T23:59:59Z",
    )
    assert "dayMinimum" in e, "dayMinimum debe existir si temp_min existe"
    assert "dayMaximum" in e, "dayMaximum debe existir si temp_max existe"
    assert "relativeHumidity" not in e["dayMinimum"]["value"], "no se inventa relativeHumidity"
    assert "relativeHumidity" not in e["dayMaximum"]["value"], "no se inventa relativeHumidity"


def test_telemetry_worker_subscribes_to_weather_forecast():
    """Sin suscripción, la entidad existe en el broker pero nunca llega a TimescaleDB.

    Cannot import subscription_manager directly (Prometheus metrics are registered
    at import time and reject duplicates). Verify via source code inspection.
    """
    source_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "telemetry-worker",
        "telemetry_worker",
        "subscription_manager.py",
    )
    tree = ast.parse(open(source_path, encoding="utf-8").read())

    types = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", None) == "SUBSCRIPTIONS" for t in node.targets):
            continue
        # Extract all "type" values from entity dicts
        for entities in _values_for_key(node.value, "entities"):
            for entity in getattr(entities, "elts", []):
                for value in _values_for_key(entity, "type"):
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        types.add(value.value)

    assert "WeatherForecast" in types, f"WeatherForecast not found in subscription types: {types}"


def _values_for_key(node, key: str):
    """Helper to extract dict values by key from AST nodes."""
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Dict):
            continue
        for k, v in zip(sub.keys, sub.values):
            if isinstance(k, ast.Constant) and k.value == key:
                yield v
