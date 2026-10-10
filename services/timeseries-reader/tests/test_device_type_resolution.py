"""Canonical devices (Device, ManufacturingMachine) read telemetry like AgriSensor did."""
import os
import sys

import pytest

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from urn_resolution import _is_device_type  # noqa: E402


@pytest.mark.parametrize("etype", [
    "Device", "ManufacturingMachine", "AgriSensor", "AgriDevice",
    "https://smartdatamodels.org/dataModel.Device/Device",
    "https://smartdatamodels.org/dataModel.ManufacturingMachine/ManufacturingMachine",
    "https://nkz-os.org/ns/AgriDevice",
    "nkz:AgriSensor",
])
def test_device_types_read_telemetry(etype):
    assert _is_device_type(etype)


@pytest.mark.parametrize("etype", ["AgriParcel", "WeatherObserved", "DeviceMeasurement", "", None])
def test_other_types_do_not(etype):
    assert not _is_device_type(etype)
