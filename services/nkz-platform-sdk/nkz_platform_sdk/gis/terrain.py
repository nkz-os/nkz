"""Terrain derivatives (slope, aspect) via Horn (1981) finite-difference gradients."""

from __future__ import annotations

import math
import warnings

import numpy as np

_METERS_PER_DEGREE = 111_320.0


def _cell_size_m(
    pixel_size_deg: float,
    latitude_deg: float | None,
    pixel_height_deg: float | None,
) -> tuple[float, float]:
    """Return (dx_m, dy_m). Rows are north-first; heights may differ from widths."""
    if latitude_deg is None:
        warnings.warn(
            "terrain slope/aspect without latitude_deg is wrong: a degree of "
            "longitude is shorter than a degree of latitude; pass latitude_deg "
            "(and pixel_height_deg if rows are not square in degrees)",
            DeprecationWarning,
            stacklevel=4,
        )
        legacy = pixel_size_deg * _METERS_PER_DEGREE
        return legacy, legacy
    dx_m = pixel_size_deg * _METERS_PER_DEGREE * math.cos(math.radians(latitude_deg))
    dy_m = (pixel_height_deg or pixel_size_deg) * _METERS_PER_DEGREE
    return dx_m, dy_m


def _horn_gradient(
    elevations: np.ndarray,
    pixel_size_deg: float,
    latitude_deg: float | None = None,
    pixel_height_deg: float | None = None,
):
    dx_m, dy_m = _cell_size_m(pixel_size_deg, latitude_deg, pixel_height_deg)
    rows, cols = elevations.shape
    dzdx = np.zeros_like(elevations)
    dzdy = np.zeros_like(elevations)
    if rows > 2 and cols > 2:
        # dzdx: rise per metre eastward; dzdy: rise per metre toward higher row
        # index (southward for north-first rows).
        dzdx[1:-1, 1:-1] = (
            (elevations[:-2, 2:] + 2 * elevations[1:-1, 2:] + elevations[2:, 2:])
            - (elevations[:-2, :-2] + 2 * elevations[1:-1, :-2] + elevations[2:, :-2])
        ) / (8.0 * dx_m)
        dzdy[1:-1, 1:-1] = (
            (elevations[2:, :-2] + 2 * elevations[2:, 1:-1] + elevations[2:, 2:])
            - (elevations[:-2, :-2] + 2 * elevations[:-2, 1:-1] + elevations[:-2, 2:])
        ) / (8.0 * dy_m)
    return dzdx, dzdy


def slope_degrees(
    elevations: np.ndarray,
    pixel_size_deg: float,
    latitude_deg: float | None = None,
    pixel_height_deg: float | None = None,
) -> np.ndarray:
    """Return slope in degrees for each cell (Horn 1981).

    Pass latitude_deg for correct east-west scaling; pixel_height_deg is the
    row height in degrees when it differs from pixel_size_deg.
    """
    dzdx, dzdy = _horn_gradient(elevations, pixel_size_deg, latitude_deg, pixel_height_deg)
    slope = np.degrees(np.arctan(np.sqrt(dzdx**2 + dzdy**2)))
    np.nan_to_num(slope, nan=0.0, copy=False)
    return slope


def aspect_degrees(
    elevations: np.ndarray,
    pixel_size_deg: float,
    latitude_deg: float | None = None,
    pixel_height_deg: float | None = None,
) -> np.ndarray:
    """Return aspect in degrees (0-360, clockwise from north), rows north-first."""
    dzdx, dzdy = _horn_gradient(elevations, pixel_size_deg, latitude_deg, pixel_height_deg)
    aspect = np.degrees(np.arctan2(-dzdx, dzdy))
    aspect = np.where(aspect < 0, aspect + 360, aspect)
    np.nan_to_num(aspect, nan=0.0, copy=False)
    return aspect
