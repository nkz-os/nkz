"""Profiles saved with the old term still match what Orion returns."""
from common.context_aliases import canonical_attribute

from blueprints.measurements import build_measurements

DEVICE = "urn:ngsi-ld:Device:tenant-a:S1"


def test_bridge_matches_profile_term_through_alias():
    entity = {"id": DEVICE, "type": "Device",
              "airTemperature": {"type": "Property", "value": 21.5}}
    profile = {"mapping": {"measurements": [
        {"type": "t", "sdmAttribute": "temperature", "unit": "°C"}]}}
    out = build_measurements(entity, profile)
    assert len(out) == 1
    assert out[0]["numValue"]["value"] == 21.5


def test_bridge_matches_entity_term_through_alias():
    entity = {"id": DEVICE, "type": "Device",
              "temperature": {"type": "Property", "value": 3}}
    profile = {"mapping": {"measurements": [
        {"type": "t", "sdmAttribute": "airTemperature", "unit": "°C"}]}}
    assert len(build_measurements(entity, profile)) == 1


def test_canonical_attribute():
    assert canonical_attribute("temperature") == "airTemperature"
    assert canonical_attribute("humidity") == "relativeHumidity"
    assert canonical_attribute("soilMoisture") == "soilMoisture"
