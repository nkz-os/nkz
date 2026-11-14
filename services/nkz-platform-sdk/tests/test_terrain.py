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


# --- axis scaling (x and y metres per degree differ) ---------------------

import math
import warnings

import pytest

_M_DEG = 111_320.0
_LAT = 42.8
_PX = 0.0001


def _plane(gx_m: float, gy_south_m: float, px_deg=_PX, py_deg=_PX, lat=_LAT, n=7):
    """Rows north-first. gx_m: rise per metre eastward; gy_south_m: rise per metre southward."""
    dx = px_deg * _M_DEG * math.cos(math.radians(lat))
    dy = py_deg * _M_DEG
    rr, cc = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
    return 500.0 + gx_m * dx * cc + gy_south_m * dy * rr


def test_east_west_plane_slope_with_latitude():
    z = _plane(gx_m=0.30, gy_south_m=0.0)
    expected = math.degrees(math.atan(0.30))
    slope = slope_degrees(z, _PX, latitude_deg=_LAT)
    assert slope[3, 3] == pytest.approx(expected, abs=0.5)


def test_north_south_plane_unchanged_by_latitude():
    z = _plane(gx_m=0.0, gy_south_m=0.30)
    expected = math.degrees(math.atan(0.30))
    with_lat = slope_degrees(z, _PX, latitude_deg=_LAT)
    assert with_lat[3, 3] == pytest.approx(expected, abs=0.5)
    with pytest.warns(DeprecationWarning):
        legacy = slope_degrees(z, _PX)
    assert with_lat[3, 3] == pytest.approx(legacy[3, 3], abs=1e-9)


def test_non_square_pixel_uses_row_height():
    z = _plane(gx_m=0.0, gy_south_m=0.30, py_deg=0.00007)
    expected = math.degrees(math.atan(0.30))
    slope = slope_degrees(z, _PX, latitude_deg=_LAT, pixel_height_deg=0.00007)
    assert slope[3, 3] == pytest.approx(expected, abs=0.5)


def test_aspect_east_facing_is_90():
    z = _plane(gx_m=-0.2, gy_south_m=0.0)  # falls toward the east
    asp = aspect_degrees(z, _PX, latitude_deg=_LAT)
    assert asp[3, 3] == pytest.approx(90.0, abs=0.5)


def test_aspect_south_facing_is_180_north_first_rows():
    z = _plane(gx_m=0.0, gy_south_m=-0.2)  # falls toward the south (down the rows)
    asp = aspect_degrees(z, _PX, latitude_deg=_LAT)
    assert asp[3, 3] == pytest.approx(180.0, abs=0.5)


def test_without_latitude_keeps_old_values_and_warns():
    z = _plane(gx_m=0.30, gy_south_m=0.10)
    with pytest.warns(DeprecationWarning, match="latitude"):
        slope = slope_degrees(z, _PX)
    with pytest.warns(DeprecationWarning, match="latitude"):
        aspect = aspect_degrees(z, _PX)
    # legacy: both axes scaled by pixel_size_deg * 111320 (no cos phi)
    s = _PX * _M_DEG
    gx = 0.30 * math.cos(math.radians(_LAT))
    gy = 0.10
    assert slope[3, 3] == pytest.approx(math.degrees(math.atan(math.hypot(gx, gy))), abs=1e-9)
    assert aspect[3, 3] == pytest.approx(
        math.degrees(math.atan2(-gx, gy)) % 360, abs=1e-9
    )


def test_with_latitude_does_not_warn():
    z = _plane(gx_m=0.3, gy_south_m=0.0)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        slope_degrees(z, _PX, latitude_deg=_LAT)
        aspect_degrees(z, _PX, latitude_deg=_LAT)
