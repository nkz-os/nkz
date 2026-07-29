"""Tests for WeatherAlert entity representation.

Temporal values (validFrom/validTo) MUST be stored as plain ISO8601 strings so
Orion-LD supports relational `q` filtering (validTo</> comparisons). The JSON-LD
typed form {"@type":"DateTime","@value":...} is stored by Orion-LD as a compound
object that its `q` relational operators cannot compare into, which returned 0 for
every temporal query — breaking both the active-alert lookup (weather-api
`q=validTo>now`) and the expired-alert prune (`q=validTo<now`). Incident 2026-07-25.
"""

import os
import sys

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))

import weather_worker.meteo_alerts_engine as mae


def _engine():
    return mae.MeteoAlertsEngine(orion_url="http://orion.test:1026")


def _alert():
    return {
        "alert_id": "x1",
        "severity": "Severe",
        "event": "Wind",
        "onset": "2026-07-02T06:00:00Z",
        "expires": "2026-07-02T21:00:00Z",
        "area_desc": "Zone A",
        "emma_id": "EMMA-1",
    }


_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "emma_zones_sample.geojson")


def test_build_attaches_location_for_known_zone():
    eng = mae.MeteoAlertsEngine(orion_url="http://orion.test:1026", emma_source=_FIXTURE)
    alert = _alert()
    alert["emma_id"] = "ES190"
    entity = eng._build_single_entity(alert)

    assert entity["location"]["type"] == "GeoProperty"
    assert entity["location"]["value"]["type"] in ("Polygon", "MultiPolygon")
    assert isinstance(entity["location"]["value"]["coordinates"], list)


def test_build_omits_location_for_unknown_zone():
    eng = mae.MeteoAlertsEngine(orion_url="http://orion.test:1026", emma_source=_FIXTURE)
    alert = _alert()
    alert["emma_id"] = "ZZ999"
    entity = eng._build_single_entity(alert)

    assert "location" not in entity


def test_build_omits_location_when_no_zone_source():
    entity = _engine()._build_single_entity(_alert())

    assert "location" not in entity


def test_validto_stored_as_plain_iso_string_for_orion_q():
    entity = _engine()._build_single_entity(_alert())

    assert entity["validTo"]["type"] == "Property"
    assert isinstance(entity["validTo"]["value"], str), (
        "validTo.value must be a plain ISO string, not a typed object"
    )
    assert entity["validTo"]["value"] == "2026-07-02T21:00:00Z"

    assert isinstance(entity["validFrom"]["value"], str)
    assert entity["validFrom"]["value"] == "2026-07-02T06:00:00Z"
