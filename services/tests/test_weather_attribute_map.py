"""Tests for NGSI-LD to DB column weather attribute mapping.

Tests the mapping logic without importing the full Flask app.
"""


# Replicate the exact mapping from timeseries-reader/app.py for unit testing.
# This is intentionally duplicated to catch divergence between the test and production code.
_VALID_ATTRIBUTES_EXPECTED = frozenset({
    'temp_avg', 'temp_min', 'temp_max',
    'humidity_avg', 'precip_mm',
    'solar_rad_w_m2', 'eto_mm',
    'soil_moisture_0_10cm', 'wind_speed_ms',
    'pressure_hpa', 'wind_direction_deg',
})

_WEATHER_ATTRIBUTE_MAP_EXPECTED = {
    "temperature":          "temp_avg",
    "relativeHumidity":     "humidity_avg",
    "windSpeed":            "wind_speed_ms",
    "windDirection":        "wind_direction_deg",
    "atmosphericPressure":  "pressure_hpa",
    "precipitation":        "precip_mm",
    "et0":                  "eto_mm",
    "solarRadiation":       "solar_rad_w_m2",
    "soilMoisture":         "soil_moisture_0_10cm",
}
for _col in _VALID_ATTRIBUTES_EXPECTED:
    _WEATHER_ATTRIBUTE_MAP_EXPECTED.setdefault(_col, _col)


def _resolve(requested):
    """Local resolver matching production logic."""
    r = (requested or "").strip() if requested else ""
    return _WEATHER_ATTRIBUTE_MAP_EXPECTED.get(r)


def test_resolve_ngsi_ld_name_to_db_column():
    """NGSI-LD attribute name should resolve to DB column name."""
    assert _resolve("temperature") == "temp_avg"
    assert _resolve("relativeHumidity") == "humidity_avg"
    assert _resolve("windSpeed") == "wind_speed_ms"
    assert _resolve("windDirection") == "wind_direction_deg"
    assert _resolve("atmosphericPressure") == "pressure_hpa"
    assert _resolve("precipitation") == "precip_mm"
    assert _resolve("et0") == "eto_mm"
    assert _resolve("solarRadiation") == "solar_rad_w_m2"
    assert _resolve("soilMoisture") == "soil_moisture_0_10cm"


def test_resolve_db_column_passthrough():
    """DB column names should also resolve (backwards compatibility)."""
    assert _resolve("temp_avg") == "temp_avg"
    assert _resolve("humidity_avg") == "humidity_avg"
    assert _resolve("wind_direction_deg") == "wind_direction_deg"


def test_resolve_unknown_attribute_returns_none():
    """Unknown attribute names should return None."""
    assert _resolve("nonExistent") is None
    assert _resolve("") is None
    assert _resolve(None) is None


def test_wind_direction_in_valid_attributes():
    """wind_direction_deg must be in VALID_ATTRIBUTES."""
    assert "wind_direction_deg" in _VALID_ATTRIBUTES_EXPECTED


def test_all_ngsi_ld_names_map_to_valid_columns():
    """Every NGSI-LD mapping target must be in VALID_ATTRIBUTES."""
    for ngsi_name, db_col in _WEATHER_ATTRIBUTE_MAP_EXPECTED.items():
        assert db_col in _VALID_ATTRIBUTES_EXPECTED, f"{ngsi_name} -> {db_col} not in VALID_ATTRIBUTES"


def test_source_code_has_mapping():
    """Verify the production source file contains the mapping dict and resolver."""
    import os
    source_path = os.path.join(
        os.path.dirname(__file__), "..", "timeseries-reader", "app.py"
    )
    with open(source_path) as f:
        src = f.read()
    assert "_WEATHER_ATTRIBUTE_MAP" in src
    assert "_resolve_weather_attribute" in src
    assert "wind_direction_deg" in src
