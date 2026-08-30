"""`sdm.py` must emit UN/CEFACT `unitCode`, never the raw catalogue symbol.

Bug fixed here: `_build_ngsi_ld_updates` used to copy `measurement.unit` /
`mapping['unit']` (raw symbols like '°C', '%', 'µm') straight into `unitCode`. A test that
only asserted "not equal to the raw symbol" would pass for any wrong code, including a
fabricated one — so this asserts the exact UN/CEFACT value.
"""

from datetime import datetime, timezone

from telemetry_worker.models import Measurement, TelemetryPayload
from telemetry_worker.sdm import _build_ngsi_ld_updates


def _payload(measurement: Measurement) -> TelemetryPayload:
    return TelemetryPayload(
        tenant="tenant-test",
        deviceId="device-test",
        profile="profile-test",
        measurements=[measurement],
    )


def test_celsius_profile_produces_the_un_cefact_code_not_the_symbol():
    measurement = Measurement(
        type="airTemperature",
        value=21.5,
        unit="°C",
        observedAt=datetime.now(timezone.utc),
    )
    mapping = {
        "sdm_entity_type": "Device",
        "mapping": {
            "measurements": [
                {"type": "airTemperature", "sdmAttribute": "temperature", "unit": "°C"}
            ]
        },
    }

    updates = _build_ngsi_ld_updates(_payload(measurement), mapping)

    assert updates["temperature"]["unitCode"] == "CEL"


def test_unit_from_catalogue_mapping_is_also_translated_when_payload_omits_it():
    """The elif branch (catalogue-provided unit, no unit on the wire payload) must be
    translated too — not just the measurement.unit branch."""
    measurement = Measurement(
        type="relativeHumidity",
        value=55.0,
        unit=None,
        observedAt=datetime.now(timezone.utc),
    )
    mapping = {
        "sdm_entity_type": "Device",
        "mapping": {
            "measurements": [
                {"type": "relativeHumidity", "sdmAttribute": "humidity", "unit": "%"}
            ]
        },
    }

    updates = _build_ngsi_ld_updates(_payload(measurement), mapping)

    assert updates["humidity"]["unitCode"] == "P1"


def test_empty_unit_omits_unitcode_entirely_instead_of_fabricating_one():
    measurement = Measurement(
        type="soilMoisture",
        value=33.2,
        unit=None,
        observedAt=datetime.now(timezone.utc),
    )
    mapping = {
        "sdm_entity_type": "Device",
        "mapping": {
            "measurements": [
                {"type": "soilMoisture", "sdmAttribute": "soilMoisture", "unit": ""}
            ]
        },
    }

    updates = _build_ngsi_ld_updates(_payload(measurement), mapping)

    assert "unitCode" not in updates["soilMoisture"]
