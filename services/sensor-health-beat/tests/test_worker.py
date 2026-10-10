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


def test_fetch_sensors_queries_canonical_and_legacy_types():
    """Device is the canonical sensor; AgriSensor and AgriDevice are still read."""
    import asyncio
    from unittest.mock import patch

    seen = []

    class _Resp:
        status_code = 200
        headers = {}

        def json(self):
            return []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, timeout=None):
            seen.append(url)
            return _Resp()

    beat = SensorHealthBeat(Settings())
    with patch("sensor_health_beat.worker.httpx.AsyncClient", return_value=_Client()):
        asyncio.run(beat._fetch_sensors("t"))
    assert "type=Device,AgriSensor,AgriDevice&" in seen[0]


def test_telemetry_lookups_use_the_device_key_not_the_entity_urn():
    """Canonical readings are stored under the DeviceMeasurement URN; the device
    is only in device_id (last URN segment), for legacy and canonical alike."""
    import asyncio

    calls = []

    class _Conn:
        async def fetchrow(self, sql, *args):
            calls.append((sql, args))
            return None

        async def fetch(self, sql, *args):
            calls.append((sql, args))
            return []

    class _Acquire:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def acquire(self):
            return _Acquire()

    beat = SensorHealthBeat(Settings())
    beat._pg_pool = _Pool()
    urn = "urn:ngsi-ld:Device:t:S1"

    async def run():
        await beat._get_last_observed("t", urn)
        await beat._check_stagnation("t", urn, "airTemperature", 6)
        await beat._check_recovery("t", urn)

    asyncio.run(run())
    assert len(calls) == 3
    for sql, args in calls:
        assert "device_id = $2" in sql
        assert args[1] == "S1"
