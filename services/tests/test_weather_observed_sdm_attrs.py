"""WeatherObserved debe llevar los atributos SDM que weather-map y el semáforo consumen.

solar_rad_w_m2 y wind_gusts_ms ya se parsean de Open-Meteo y se descartaban en el
escritor, así que weather-map no podía calcular ET0 y el semáforo no tenía ráfagas.
Los nombres nuevos conviven con los viejos: la retirada es una fase aparte.
"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

_SERVICES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_SERVICES, os.path.join(_SERVICES, "weather-worker")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from weather_worker.storage import orion_writer  # noqa: E402

WEATHER = {
    "temp_avg": 21.0,
    "humidity_avg": 60.0,
    "wind_speed_ms": 2.5,
    "wind_gusts_ms": 9.8,
    "solar_rad_w_m2_horizontal": 171.8,
    "precip_mm": 0.0,
    "station_elevation_m": 450.0,
}


def _captured_entity():
    """Devuelve el cuerpo que el escritor habría enviado a Orion."""
    sent = {}

    class _Resp:
        status_code = 201
        text = ""

    def _post(url, json=None, headers=None, timeout=None, **kwargs):
        sent.update(json or {})
        return _Resp()

    with patch.object(orion_writer.requests, "post", side_effect=_post):
        orion_writer.create_weather_observed_entity(
            parcel_id="urn:ngsi-ld:AgriParcel:t:p1",
            tenant_id="t",
            location=(-2.07, 42.63),
            weather_data=WEATHER,
            observed_at=datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc).replace(tzinfo=None),
            parcel_name="P1",
        )
    return sent


def test_publishes_the_horizontal_solar_radiation():
    """Debe ser la global horizontal, NO la ya corregida por aspecto.

    El engine corrige por el aspecto de la parcela para su propio uso puntual. Si esa
    corregida se publicase, weather-map la corregiría otra vez por píxel.
    """
    e = _captured_entity()
    assert "solarRadiation" in e, "weather-map no puede calcular ET0 sin radiación"
    assert e["solarRadiation"]["value"] == 171.8
    assert e["solarRadiation"]["unitCode"] == "D54"


def test_solar_radiation_ignores_the_aspect_corrected_value():
    sent = {}

    class _Resp:
        status_code = 201
        text = ""

    def _post(url, json=None, headers=None, timeout=None, **kwargs):
        sent.update(json or {})
        return _Resp()

    mixto = dict(WEATHER, solar_rad_w_m2=999.0)  # la corregida, que NO debe publicarse
    with patch.object(orion_writer.requests, "post", side_effect=_post):
        orion_writer.create_weather_observed_entity(
            parcel_id="urn:ngsi-ld:AgriParcel:t:p1", tenant_id="t",
            location=(-2.07, 42.63), weather_data=mixto, parcel_name="P1",
        )
    assert sent["solarRadiation"]["value"] == 171.8


def test_publishes_gust_speed_alongside_the_legacy_name():
    e = _captured_entity()
    assert e["gustSpeed"]["value"] == 9.8, "gustSpeed es el nombre SDM"
    assert e["windGusts"]["value"] == 9.8, "el nombre viejo sigue durante expand & contract"


def test_publishes_source_alongside_the_legacy_name():
    e = _captured_entity()
    assert e["source"]["value"] == e["sourceConfidence"]["value"]


# ---------------------------------------------------------------------------
# Update path (POST /attrs) — the create path only runs once per virtual
# station; every cycle after that goes through update_weather_observed_entity,
# so it must carry the same three attributes or they'd never refresh again.
# ---------------------------------------------------------------------------


def _captured_update_payload(weather_data=WEATHER):
    """Devuelve el payload que update_weather_observed_entity habría enviado."""
    sent = {}

    class _Resp:
        status_code = 204
        text = ""

    def _post(url, json=None, headers=None, timeout=None, **kwargs):
        sent.update(json or {})
        return _Resp()

    with patch.object(orion_writer.requests, "post", side_effect=_post):
        orion_writer.update_weather_observed_entity(
            entity_id="urn:ngsi-ld:WeatherObserved:t:parcel-p1",
            tenant_id="t",
            weather_data=weather_data,
            observed_at=datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc).replace(tzinfo=None),
        )
    return sent


def test_update_publishes_the_horizontal_solar_radiation():
    p = _captured_update_payload()
    assert "solarRadiation" in p, "el refresco periódico también debe llevar radiación"
    assert p["solarRadiation"]["value"] == 171.8
    assert p["solarRadiation"]["unitCode"] == "D54"


def test_update_solar_radiation_ignores_the_aspect_corrected_value():
    mixto = dict(WEATHER, solar_rad_w_m2=999.0)  # la corregida, que NO debe publicarse
    p = _captured_update_payload(mixto)
    assert p["solarRadiation"]["value"] == 171.8


def test_update_publishes_gust_speed_alongside_the_legacy_name():
    p = _captured_update_payload()
    assert p["gustSpeed"]["value"] == 9.8, "gustSpeed es el nombre SDM"
    assert p["windGusts"]["value"] == 9.8, "el nombre viejo sigue durante expand & contract"


def test_update_publishes_source_alongside_the_legacy_name():
    p = _captured_update_payload()
    assert p["source"]["value"] == p["sourceConfidence"]["value"]
