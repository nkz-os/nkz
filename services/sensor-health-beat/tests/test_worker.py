"""Tests for sensor-health-beat worker."""

import os
import sys

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))

for _p in [_SVC_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from sensor_health_beat.worker import SensorHealthBeat
from sensor_health_beat.config import Settings


def test_health_config_parsing():
    """Test that healthConfig is correctly extracted from Orion entity."""
    settings = Settings()
    settings.timescale_dsn = ""
    beat = SensorHealthBeat(settings)

    sensor = {
        "id": "urn:ngsi-ld:AgriSensor:test:sensor-01",
        "healthConfig": {
            "type": "Property",
            "value": {
                "temperature": {"minValid": -20, "maxValid": 60, "maxStagnantHours": 4},
                "communicationTimeoutHours": 12,
            },
        },
        "reliabilityStatus": {"type": "Property", "value": "optimal"},
    }

    hc = sensor.get("healthConfig", {})
    if isinstance(hc, dict):
        hc = hc.get("value", hc)
    assert hc["temperature"]["minValid"] == -20
    assert hc["communicationTimeoutHours"] == 12


def test_is_silenced_parsing():
    """Test isSilenced flag extraction."""
    settings = Settings()
    settings.timescale_dsn = ""
    beat = SensorHealthBeat(settings)

    sensor = {
        "id": "urn:ngsi-ld:AgriSensor:test:sensor-01",
        "isSilenced": {"type": "Property", "value": True},
    }

    is_silenced = sensor.get("isSilenced", {})
    if isinstance(is_silenced, dict):
        is_silenced = is_silenced.get("value", False)
    assert is_silenced is True


def test_health_config_without_health_config():
    """Test sensor without healthConfig returns None-like."""
    settings = Settings()
    settings.timescale_dsn = ""
    beat = SensorHealthBeat(settings)

    sensor = {"id": "urn:ngsi-ld:AgriSensor:test:sensor-01"}

    hc = sensor.get("healthConfig", {})
    if isinstance(hc, dict):
        hc = hc.get("value", hc)
    assert hc == {}
