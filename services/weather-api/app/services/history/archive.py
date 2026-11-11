"""Open-Meteo ERA5 archive fetch: one calendar year of hourly data per request."""
import logging
from datetime import date
from typing import Callable, Optional

import requests

from app.config import settings

logger = logging.getLogger(__name__)

HOURLY_VARS = "temperature_2m,dew_point_2m,precipitation,shortwave_radiation,wind_speed_10m"
DEFAULT_TIMEOUT = 60.0
VARIANTS = ("corrected", "grid")


class ArchiveError(RuntimeError):
    """The archive could not provide the requested year.

    The message may contain coordinates or upstream detail: log it, never echo it to clients.
    """


def _col(hourly: dict, key: str, n: int) -> list:
    col = hourly.get(key)
    if not isinstance(col, list) or len(col) != n:
        raise ArchiveError(f"archive response lacks a complete column {key!r}")
    return [None if (isinstance(v, bool) or not isinstance(v, (int, float))) else float(v) for v in col]


PROBE_DAY = "2020-01-01"


def probe_cell(
    lat: float,
    lon: float,
    elev: Optional[float],
    variant: str,
    http_get: Callable = requests.get,
    timeout=DEFAULT_TIMEOUT,
) -> dict:
    """One-day request that only learns the cell the archive answers with.

    variant "grid": `elevation=nan`; "corrected": the parcel elevation. Returns
    {"lat", "lon", "grid_elevation"} (the elevation the archive used).
    """
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}")
    base = settings.openmeteo_archive_url
    if not base:
        raise ArchiveError("OPENMETEO_ARCHIVE_URL is not configured")
    if variant == "corrected" and elev is None:
        raise ValueError("corrected variant needs the parcel elevation")
    params = {
        "latitude": lat, "longitude": lon, "start_date": PROBE_DAY, "end_date": PROBE_DAY,
        "hourly": "temperature_2m", "models": "era5", "timeformat": "unixtime", "timezone": "auto",
        "elevation": "nan" if variant == "grid" else elev,
    }
    try:
        resp = http_get(f"{base}/archive", params=params, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        raise ArchiveError(f"archive probe failed: {type(exc).__name__}") from None
    if resp.status_code != 200:
        raise ArchiveError(f"archive probe HTTP {resp.status_code}")
    try:
        body = resp.json()
        return {
            "lat": float(body["latitude"]),
            "lon": float(body["longitude"]),
            "grid_elevation": None if body.get("elevation") is None else float(body["elevation"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ArchiveError(f"malformed archive probe response: {type(exc).__name__}") from None


def fetch_year(
    lat: float,
    lon: float,
    elev: Optional[float],
    year: int,
    variant: str,
    http_get: Callable = requests.get,
    timeout: float = DEFAULT_TIMEOUT,
    today: Optional[date] = None,
) -> dict:
    """Hourly ERA5 for one calendar year.

    variant "grid": `elevation=nan` (the cell's own elevation); "corrected": the parcel
    elevation. Times are UTC epoch seconds; `tz` is the parcel's IANA zone name.
    """
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}")
    today = today or date.today()
    if year > today.year:
        raise ValueError(f"year {year} is in the future")
    base = settings.openmeteo_archive_url
    if not base:
        raise ArchiveError("OPENMETEO_ARCHIVE_URL is not configured")
    if variant == "corrected" and elev is None:
        raise ValueError("corrected variant needs the parcel elevation")
    end = min(date(year, 12, 31), today)
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": f"{year}-01-01",
        "end_date": end.isoformat(),
        "hourly": HOURLY_VARS,
        "models": "era5",
        "wind_speed_unit": "ms",
        "timeformat": "unixtime",
        "timezone": "auto",
        "elevation": "nan" if variant == "grid" else elev,
    }
    try:
        resp = http_get(f"{base}/archive", params=params, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        # the exception text carries the request URL (coordinates): type only
        raise ArchiveError(f"archive request failed: {type(exc).__name__}") from None
    if resp.status_code != 200:
        raise ArchiveError(f"archive HTTP {resp.status_code}")
    try:
        body = resp.json()
        hourly = body["hourly"]
        times = hourly["time"]
        n = len(times)
        out = {
            "lat": float(body["latitude"]),
            "lon": float(body["longitude"]),
            "grid_elevation": None if body.get("elevation") is None else float(body["elevation"]),
            "tz": str(body["timezone"]),
            "times": [int(t) for t in times],
            "t": _col(hourly, "temperature_2m", n),
            "td": _col(hourly, "dew_point_2m", n),
            "precip": _col(hourly, "precipitation", n),
            "rad": _col(hourly, "shortwave_radiation", n),
            "wind10": _col(hourly, "wind_speed_10m", n),
        }
    except ArchiveError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ArchiveError(f"malformed archive response: {type(exc).__name__}") from None
    return out
