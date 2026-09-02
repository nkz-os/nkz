"""Every field the broker publishes must reach the parcel weather response.

The agro panel showed "Humedad suelo: N/A" while `soilMoistureTop` sat in the
broker: the `orion-cache` mapper simply did not carry the field, and the second
mapper hardcoded it — along with solar radiation and accumulated GDD — to None.
A dropped field is indistinguishable downstream from a field that was never
measured, which is how the panel ended up reporting nothing with no error.

Units are converted once, here: the broker publishes soil moisture as a
volumetric fraction (`unitCode: M3`) and every consumer compares against
percentages.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "weather-api"))

from app.routers.parcels import _soil_percent  # noqa: E402


@pytest.mark.parametrize(
    "raw,expected",
    [
        (0.106, 10.6),
        (0.16, 16.0),
        (0, 0.0),
        (1, 100.0),
        ("0.25", 25.0),
    ],
    ids=["typical-top", "typical-sub", "zero", "saturated", "numeric-string"],
)
def test_volumetric_fraction_becomes_percent(raw, expected):
    assert _soil_percent(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", [None, "", "n/a", object()], ids=["none", "empty", "text", "object"])
def test_unusable_input_stays_absent_rather_than_zero(raw):
    """A missing reading must stay missing: 0 % is 'permanent severe stress'."""
    assert _soil_percent(raw) is None


def test_the_reading_that_the_panel_showed_as_na():
    """The exact value in the broker when the panel reported N/A."""
    assert _soil_percent(0.106) == pytest.approx(10.6)
