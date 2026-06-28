"""Terrain derivatives (slope, aspect) via Horn (1981) finite-difference gradients."""

from __future__ import annotations

import numpy as np

_METERS_PER_DEGREE = 111_320.0


def _horn_gradient(elevations: np.ndarray, pixel_size_deg: float):
    rows, cols = elevations.shape
    dzdx = np.zeros_like(elevations)
    dzdy = np.zeros_like(elevations)
    if rows > 2 and cols > 2:
        scale = 8.0 * pixel_size_deg * _METERS_PER_DEGREE
        dzdx[1:-1, 1:-1] = (
            (elevations[:-2, 2:] + 2 * elevations[1:-1, 2:] + elevations[2:, 2:])
            - (elevations[:-2, :-2] + 2 * elevations[1:-1, :-2] + elevations[2:, :-2])
        ) / scale
        dzdy[1:-1, 1:-1] = (
            (elevations[2:, :-2] + 2 * elevations[2:, 1:-1] + elevations[2:, 2:])
            - (elevations[:-2, :-2] + 2 * elevations[:-2, 1:-1] + elevations[:-2, 2:])
        ) / scale
    return dzdx, dzdy


def slope_degrees(elevations: np.ndarray, pixel_size_deg: float) -> np.ndarray:
    """Return slope in degrees for each cell (Horn 1981)."""
    dzdx, dzdy = _horn_gradient(elevations, pixel_size_deg)
    slope = np.degrees(np.arctan(np.sqrt(dzdx**2 + dzdy**2)))
    np.nan_to_num(slope, nan=0.0, copy=False)
    return slope


def aspect_degrees(elevations: np.ndarray, pixel_size_deg: float) -> np.ndarray:
    """Return aspect in degrees (0–360, clockwise from north) for each cell."""
    dzdx, dzdy = _horn_gradient(elevations, pixel_size_deg)
    aspect = np.degrees(np.arctan2(-dzdx, dzdy))
    aspect = np.where(aspect < 0, aspect + 360, aspect)
    np.nan_to_num(aspect, nan=0.0, copy=False)
    return aspect
