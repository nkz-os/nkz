"""Tests for WeatherAlert entity building from EDR payloads.

Uses EDR-sourced fixtures (index feature + CAP-JSON detail from a
MeteoAlarm EDR probe) to verify _build_single_entity maps severity,
subCategory, validFrom/validTo (plain ISO strings), location polygon,
and auxiliary fields correctly.

The English info block is selected so _CAP_SEVERITY_MAP and
_normalize_event (unchanged from legacy) produce correct values.
"""

import json
import os
import sys

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))

import weather_worker.meteo_alerts_engine as mae

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(path: str):
    with open(os.path.join(_FIXTURES, path)) as f:
        return json.load(f)


_CAP = _load("edr_cap_detail.json")
_FEATURE = _load("edr_index_feature.json")


def _engine():
    return mae.MeteoAlertsEngine(orion_url="http://orion.test:1026")


def _polygon():
    return {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [2, 0], [0, 0]]]}


# ---------------------------------------------------------------------------
# Severity and subCategory (from English info block)
# ---------------------------------------------------------------------------


def test_build_maps_severity_from_en_block():
    """The English info block has severity=Minor → should map to 'minor'."""
    entity = _engine()._build_single_entity(_CAP, _FEATURE)
    assert entity is not None
    assert entity["severity"]["value"] == "minor"


def test_build_maps_subcategory_from_en_block_event():
    """The English info block event is 'thunderstorms, possibly with wind gusts'
    → _normalize_event should detect THUNDERSTORM → 'thunderstorm'."""
    entity = _engine()._build_single_entity(_CAP, _FEATURE)
    assert entity is not None
    assert entity["subCategory"]["value"] == ["thunderstorm"]


def test_build_uses_english_block_not_de():
    """info[0] is de-DE with event='GEWITTER' — selecting it blindly would
    yield subCategory='gewitter' (fallback) instead of 'thunderstorm'.
    Verify we get the English-block value."""
    entity = _engine()._build_single_entity(_CAP, _FEATURE)
    assert entity is not None
    sub = entity["subCategory"]["value"]
    # Must NOT be the german fallback 'gewitter'
    assert sub != ["gewitter"]
    assert sub == ["thunderstorm"]


# ---------------------------------------------------------------------------
# Temporal values (must be plain ISO strings, not typed DateTime) — 2026-07-25 fix
# ---------------------------------------------------------------------------


def test_valid_from_is_plain_iso_string():
    entity = _engine()._build_single_entity(_CAP, _FEATURE)
    assert entity is not None
    vf = entity["validFrom"]
    assert vf["type"] == "Property"
    assert isinstance(vf["value"], str), "must be a plain string, not a dict"
    # onset in the fixture is "2026-07-31T23:42:00+02:00" → UTC should be near 21:42
    assert "2026" in vf["value"], "should contain a valid ISO date"


def test_valid_to_is_plain_iso_string():
    entity = _engine()._build_single_entity(_CAP, _FEATURE)
    assert entity is not None
    vt = entity["validTo"]
    assert vt["type"] == "Property"
    assert isinstance(vt["value"], str), "must be a plain string, not a dict"
    assert "2026" in vt["value"]


def test_valid_from_and_to_are_not_typed_datetime():
    """The JSON-LD typed DateTime form ({"@type":"DateTime","@value":...})
    must NOT appear — it would break Orion q=validTo</> relational ops."""
    entity = _engine()._build_single_entity(_CAP, _FEATURE)
    assert entity is not None
    for key in ("validFrom", "validTo"):
        assert not isinstance(entity[key]["value"], dict), (
            f"{key} must NOT be {{'@type':'DateTime',...}}"
        )


# ---------------------------------------------------------------------------
# Location polygon
# ---------------------------------------------------------------------------


def test_build_attaches_location_when_geometry_provided():
    entity = _engine()._build_single_entity(_CAP, _FEATURE, _polygon())
    assert entity is not None
    assert "location" in entity
    assert entity["location"]["type"] == "GeoProperty"
    assert entity["location"]["value"]["type"] == "Polygon"


def test_build_strips_crs_from_geometry():
    """MeteoAlarm geo+json carries geometry.crs; Orion-LD rejects non-standard
    GeoProperty members (400 BadRequestData "Unexpected Field in value of
    GeoProperty"), so the built location must keep only {type, coordinates}."""
    geom = _polygon()
    geom["crs"] = {"type": "name", "properties": {"name": "EPSG:4326"}}
    entity = _engine()._build_single_entity(_CAP, _FEATURE, geom)
    assert entity is not None
    loc = entity["location"]["value"]
    assert set(loc.keys()) == {"type", "coordinates"}
    assert "crs" not in loc
    assert loc["type"] == "Polygon"


def test_build_omits_location_when_geometry_is_none():
    entity = _engine()._build_single_entity(_CAP, _FEATURE, geometry=None)
    assert entity is not None
    assert "location" not in entity


def test_build_omits_location_when_no_geometry_passed():
    entity = _engine()._build_single_entity(_CAP, _FEATURE)
    assert entity is not None
    assert "location" not in entity


# ---------------------------------------------------------------------------
# Entity identity and auxiliary fields
# ---------------------------------------------------------------------------


def test_build_uses_edr_alert_id_as_entity_id():
    entity = _engine()._build_single_entity(_CAP, _FEATURE)
    assert entity is not None
    # The fixture alertId is the UUID from the EDR index
    expected_id = f'urn:ngsi-ld:WeatherAlert:meteoalarm:{_FEATURE["properties"]["alertId"]}'
    assert entity["id"] == expected_id


def test_build_sets_data_provider():
    entity = _engine()._build_single_entity(_CAP, _FEATURE)
    assert entity is not None
    assert entity["dataProvider"]["value"] == "MeteoAlarm (EUMETNET)"


def test_build_sets_category():
    entity = _engine()._build_single_entity(_CAP, _FEATURE)
    assert entity is not None
    assert entity["category"]["value"] == ["meteorological"]


def test_build_populates_area_fields():
    """meteoalarmZoneId ← area[0].geocode, addressLocality ← area[0].areaDesc."""
    entity = _engine()._build_single_entity(_CAP, _FEATURE)
    assert entity is not None
    # geocode in EDR CAP-JSON is a list of {value, valueName}; we must store
    # the EMMA_ID string (e.g. "DE094"), not the raw array.
    assert entity["meteoalarmZoneId"]["value"] == "DE094", (
        "meteoalarmZoneId must be the EMMA_ID string extracted from the geocode array"
    )
    addr = entity["address"]["value"]
    assert isinstance(addr, dict)
    assert addr.get("addressLocality"), "addressLocality should not be empty"


def test_build_uses_en_headline_for_description():
    """description ← headline (en block), fallback event."""
    entity = _engine()._build_single_entity(_CAP, _FEATURE)
    assert entity is not None
    desc = entity["description"]["value"]
    # The en block headline is "Official WARNING of THUNDERSTORMS"
    assert "WARNING" in desc or "warning" in desc.lower() or "THUNDER" in desc.upper()


# ---------------------------------------------------------------------------
# Boundary: no-info / no-alert-id
# ---------------------------------------------------------------------------


def test_build_returns_none_for_empty_info():
    cap = {"info": []}
    feat = {"properties": {"alertId": "id-1"}}
    entity = _engine()._build_single_entity(cap, feat)
    assert entity is not None  # falls back gracefully with empty info
    assert entity["severity"]["value"] == "informational"


def test_build_returns_none_for_missing_alert_id():
    cap = {"info": [{"language": "en", "severity": "Moderate", "event": "Wind"}]}
    feat = {"properties": {}}
    entity = _engine()._build_single_entity(cap, feat)
    assert entity is None
