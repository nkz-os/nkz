"""Tests for nkz_platform_sdk.gis.terrain (Horn 1981 slope/aspect)."""

import numpy as np

from nkz_platform_sdk.gis.terrain import aspect_degrees, slope_degrees


def test_slope_aspect_import_and_shapes():
    elevations = np.array(
        [
            [100.0, 101.0, 102.0],
            [100.0, 105.0, 103.0],
            [100.0, 104.0, 106.0],
        ],
        dtype=float,
    )
    pixel_size_deg = 0.0001

    slope = slope_degrees(elevations, pixel_size_deg)
    aspect = aspect_degrees(elevations, pixel_size_deg)

    assert slope.shape == elevations.shape
    assert aspect.shape == elevations.shape
    assert 0.0 <= slope[1, 1] <= 90.0
    assert 0.0 <= aspect[1, 1] <= 360.0


def test_small_grid_returns_zeros():
    elevations = np.array([[1.0, 2.0], [3.0, 4.0]])
    slope = slope_degrees(elevations, 0.0001)
    aspect = aspect_degrees(elevations, 0.0001)
    np.testing.assert_array_equal(slope, 0.0)
    np.testing.assert_array_equal(aspect, 0.0)
