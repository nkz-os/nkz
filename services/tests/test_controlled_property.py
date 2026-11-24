import re
from pathlib import Path

from common import controlled_property as cp
from common.context_aliases import canonical_attribute
from common.window_statistics import base_attribute

ROOT = Path(__file__).resolve().parents[2]
MIG_DIR = ROOT / "config" / "timescaledb" / "migrations"

# Migration 099 rewrites these seed entity types to Device; every other type is a machine.
DEVICE_SOURCE_TYPES = {"AgriSensor", "WeatherObserved", "Device"}

_PROFILE_START = re.compile(r"^\s*\(NULL, '[^']+', '[^']*', '[^']*', '(\w+)',", re.MULTILINE)
_SDM_ATTRIBUTE = re.compile(r"'sdmAttribute',\s*'([^']+)'")


def _device_seed_attributes() -> set[str]:
    names: set[str] = set()
    for filename in (
        "010_sensor_ingestion_schema.sql",
        "029_extended_sensor_profiles.sql",
        "110_water_meter_profile.sql",
    ):
        sql = (MIG_DIR / filename).read_text(encoding="utf-8")
        starts = list(_PROFILE_START.finditer(sql))
        if filename.startswith("110"):
            starts = list(re.finditer(r"'water_meter', '[^']*', '[^']*', '(\w+)',", sql))
        assert starts, f"no profile rows parsed in {filename}"
        for i, match in enumerate(starts):
            end = starts[i + 1].start() if i + 1 < len(starts) else len(sql)
            chunk = sql[match.end():end].split(");\n")[0]
            if match.group(1) in DEVICE_SOURCE_TYPES:
                names.update(_SDM_ATTRIBUTE.findall(chunk))
    return names


def _observation_property_names() -> set[str]:
    sql = (MIG_DIR / "113_observation_properties.sql").read_text(encoding="utf-8")
    block = sql.split("FROM (VALUES", 1)[1].split(") AS v", 1)[0]
    return set(re.findall(r"^\s*\('(\w+)',", block, re.MULTILINE))


def test_enum_has_the_official_51_values():
    assert len(cp.CONTROLLED_PROPERTY_TYPES) == 51


def test_every_value_is_in_the_official_enum():
    for name, row in cp.MAPPING.items():
        if row.kind == "none":
            assert row.value is None, name
        else:
            assert row.value in cp.CONTROLLED_PROPERTY_TYPES, name
        assert row.kind in {"exact", "broader", "none"}, name
        assert row.reason.strip(), name


def test_exact_rows_map_to_themselves_except_ph():
    for name, row in cp.MAPPING.items():
        if row.kind == "exact":
            assert row.value == ("pH" if name == "ph" else name), name


def test_every_device_seed_attribute_has_a_row():
    seeds = _device_seed_attributes() | _observation_property_names()
    assert len(seeds) > 40
    missing = sorted(
        n for n in seeds
        if not ({n, canonical_attribute(n), base_attribute(canonical_attribute(n))} & set(cp.MAPPING))
    )
    assert not missing, f"seed attributes without a row: {missing}"


def test_observed_property_wins_over_sdm_attribute():
    m = {"sdmAttribute": "soilMoisture", "observedProperty": "relativeHumidity"}
    assert cp.controlled_properties([m]) == ["humidity"]


def test_none_row_stops_the_search():
    # `voltage` is a decision (none); it must not fall through to the sdmAttribute.
    m = {"sdmAttribute": "windSpeed", "observedProperty": "voltage"}
    assert cp.controlled_properties([m]) == []


def test_statistics_channels_use_their_base():
    assert cp.controlled_properties([{"sdmAttribute": "airTemperatureMin"}]) == ["temperature"]
    assert cp.controlled_properties([{"sdmAttribute": "windSpeedMax"}]) == ["windSpeed"]


def test_alias_is_canonicalised():
    assert cp.controlled_properties([{"sdmAttribute": "humidity"}]) == ["humidity"]


def test_duplicates_collapse_and_sort():
    ms = [{"sdmAttribute": "soilTemperature"}, {"sdmAttribute": "windSpeed"},
          {"sdmAttribute": "airTemperature"}, {"sdmAttribute": "ph"}]
    assert cp.controlled_properties(ms) == ["pH", "temperature", "windSpeed"]


def test_unmapped_returns_empty_list():
    assert cp.controlled_properties([{"sdmAttribute": "sapFlow"}, {"sdmAttribute": "nonsense"}, {}]) == []
    assert cp.controlled_properties([]) == []


def test_malformed_measurements_are_skipped():
    ms = [
        None, "windSpeed", 3, ["windSpeed"],
        {"observedProperty": 5, "sdmAttribute": "soilMoisture"},
        {"observedProperty": ["x"], "sdmAttribute": ["y"]},
        {"sdmAttribute": {"a": 1}},
        {"sdmAttribute": "windSpeed"},
    ]
    assert cp.controlled_properties(ms) == ["soilMoisture", "windSpeed"]


def test_non_list_input_returns_empty_list():
    assert cp.controlled_properties(None) == []
    assert cp.controlled_properties({"sdmAttribute": "windSpeed"}) == []


def test_soil_salinity_and_drain_flow_map_to_broader_values():
    assert cp.controlled_properties([{"sdmAttribute": "soilSalinity"}]) == ["salinity"]
    assert cp.controlled_properties([{"sdmAttribute": "drainFlow"}]) == ["waterFlow"]
    assert cp.MAPPING["soilSalinity"].kind == "broader"
    assert cp.MAPPING["drainFlow"].kind == "broader"
