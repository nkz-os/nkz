"""Daily archive of parcel forecasts.

Once a day, for every parcel of every active tenant, the 16-day daily forecast
of two Open-Meteo models (automatic selection and ``ecmwf_ifs025``) is saved as
one gzipped JSON object per tenant in MinIO. The archive exists only so a later
validation can compare forecasts with measurements: nothing reads it here, and
nothing is written to Orion or to any timeseries.

Object: ``<bucket>/<tenant>/<YYYY-MM-DD>.json.gz`` (UTC date of the run).

Run: ``python3 -m weather_worker.forecast_archive``
"""

import gzip
import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

# Same in-pod path setup as parcel_engine (common/ lives next to the service).
for _p in ("/app", "/app/weather-worker"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from weather_worker.parcel_engine import ParcelWeatherEngine

logger = logging.getLogger(__name__)

MODELS = ("best_match", "ecmwf_ifs025")
FORECAST_DAYS = 16
REQUEST_TIMEOUT_S = 60
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 5
NIGHT_HOURS = 14  # local 18:00 of day D .. 07:00 of D+1 (08:00 excluded)
DEFAULT_BUCKET = "weather-forecast-archive"
ORION_ATTRS = "location,name,elevation,timeZone"


class ConfigError(Exception):
    """A setting required for the core function is missing."""


# ----------------------------------------------------------------------
# Open-Meteo
# ----------------------------------------------------------------------

def _request_params(lat: float, lon: float) -> Dict[str, Any]:
    return {
        "latitude": lat,
        "longitude": lon,
        "models": ",".join(MODELS),
        "forecast_days": FORECAST_DAYS,
        "daily": "temperature_2m_min,temperature_2m_max",
        "hourly": "cloud_cover,wind_speed_10m",
        "wind_speed_unit": "ms",
        "timezone": "auto",
    }


def _fetch_forecast(openmeteo_url: str, lat: float, lon: float) -> Dict[str, Any]:
    """One Open-Meteo call, up to MAX_ATTEMPTS with backoff. Raises on failure."""
    last: Optional[Exception] = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(
                f"{openmeteo_url}/forecast",
                params=_request_params(lat, lon),
                timeout=REQUEST_TIMEOUT_S,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Open-Meteo returned HTTP {resp.status_code}")
            data = resp.json()
            if not isinstance(data, dict):
                raise RuntimeError("Open-Meteo returned an unexpected body")
            return data
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised below
            last = exc
            logger.warning(
                "Open-Meteo attempt %d/%d failed: %s", attempt, MAX_ATTEMPTS, exc
            )
            if attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_BASE_S * attempt)
    raise RuntimeError(f"Open-Meteo unavailable after {MAX_ATTEMPTS} attempts: {last}")


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _daily_value(daily: Dict[str, Any], var: str, model: str, idx: int) -> Optional[float]:
    series = daily.get(f"{var}_{model}")
    if not isinstance(series, list) or idx >= len(series):
        return None
    return _num(series[idx])


def _next_date(date: str) -> str:
    from datetime import date as _d, timedelta

    return (_d.fromisoformat(date) + timedelta(days=1)).isoformat()


def _night_mean(
    hourly_index: Dict[str, int], series: Any, date: str
) -> Optional[float]:
    """Mean of the 14 night hours of ``date``; None if any hour is missing/null."""
    if not isinstance(series, list):
        return None
    keys = [f"{date}T{h:02d}:00" for h in range(18, 24)]
    nxt = _next_date(date)
    keys += [f"{nxt}T{h:02d}:00" for h in range(0, 8)]
    values: List[float] = []
    for key in keys:
        i = hourly_index.get(key)
        if i is None or i >= len(series):
            return None
        v = _num(series[i])
        if v is None:
            return None
        values.append(v)
    return sum(values) / NIGHT_HOURS


def _model_days(response: Dict[str, Any], model: str) -> List[Dict[str, Any]]:
    daily = response.get("daily") or {}
    hourly = response.get("hourly") or {}
    dates = daily.get("time") or []
    hourly_index = {t: i for i, t in enumerate(hourly.get("time") or [])}
    cloud = hourly.get(f"cloud_cover_{model}")
    wind = hourly.get(f"wind_speed_10m_{model}")
    days = []
    for i, date in enumerate(dates[:FORECAST_DAYS]):
        days.append(
            {
                "date": date,
                "tmin_c": _daily_value(daily, "temperature_2m_min", model, i),
                "tmax_c": _daily_value(daily, "temperature_2m_max", model, i),
                "night_cloud_pct": _night_mean(hourly_index, cloud, date),
                "night_wind_ms": _night_mean(hourly_index, wind, date),
            }
        )
    return days


def _models_block(response: Dict[str, Any]) -> Dict[str, Any]:
    return {m: {"days": _model_days(response, m)} for m in MODELS}


# ----------------------------------------------------------------------
# Discovery (reuses the parcel engine)
# ----------------------------------------------------------------------

def _fetch_tenant_parcels(engine: ParcelWeatherEngine, tenant: str) -> List[Dict[str, Any]]:
    """AgriParcel entities of one tenant (read-only). Raises on Orion errors."""
    resp = requests.get(
        f"{engine.orion_url}/ngsi-ld/v1/entities",
        params={
            "type": "AgriParcel",
            "attrs": ORION_ATTRS,
            "limit": min(engine.max_parcels, 1000),
        },
        headers=engine._make_headers(tenant),
        timeout=30,
    )
    if resp.status_code == 404:
        return []
    if resp.status_code != 200:
        raise RuntimeError(f"Orion returned HTTP {resp.status_code}")
    entities = resp.json()
    if not isinstance(entities, list):
        raise RuntimeError("Orion returned an unexpected body")
    return entities


def _prepare_parcels(engine: ParcelWeatherEngine, parcels: List[Dict[str, Any]]):
    usable = []
    for p in parcels:
        centroid, altitude = engine._extract_centroid_and_altitude(p)
        if centroid is None:
            logger.warning("Skipping parcel %s: no resolvable location", p.get("id"))
            continue
        p["_centroid"] = centroid
        p["_altitude"] = altitude
        usable.append(p)
    return usable


# ----------------------------------------------------------------------
# Archive build + upload
# ----------------------------------------------------------------------

def _archive_tenant(
    engine: ParcelWeatherEngine, tenant: str, parcels: List[Dict[str, Any]]
) -> Tuple[Dict[str, Any], int]:
    """Returns (parcel entries, failed count) for one tenant."""
    usable = _prepare_parcels(engine, parcels)
    failed = len(parcels) - len(usable)
    entries: Dict[str, Any] = {}
    for ci, cluster in enumerate(engine._cluster_parcels(usable, engine.cluster_radius_km)):
        lat = sum(p["_centroid"][1] for p in cluster) / len(cluster)
        lon = sum(p["_centroid"][0] for p in cluster) / len(cluster)
        try:
            response = _fetch_forecast(engine.openmeteo_url, lat, lon)
            models = _models_block(response)
        except Exception as exc:  # noqa: BLE001 - isolate the cluster
            failed += len(cluster)
            logger.warning(
                "Tenant %s: cluster %d skipped (%d parcels: %s): %s",
                tenant, ci + 1, len(cluster),
                ", ".join(str(p.get("id")) for p in cluster), exc,
            )
            continue
        grid_elevation = _num(response.get("elevation"))
        for p in cluster:
            entries[p["id"]] = {
                "lat": p["_centroid"][1],
                "lon": p["_centroid"][0],
                "elevation": _num(p["_altitude"]),
                "grid_elevation": grid_elevation,
                "models": models,
            }
    return entries, failed


def _make_minio_client():
    endpoint = os.getenv("MINIO_ENDPOINT", "").strip()
    access = os.getenv("MINIO_ACCESS_KEY", "").strip()
    secret = os.getenv("MINIO_SECRET_KEY", "").strip()
    missing = [
        name
        for name, val in (
            ("MINIO_ENDPOINT", endpoint),
            ("MINIO_ACCESS_KEY", access),
            ("MINIO_SECRET_KEY", secret),
        )
        if not val
    ]
    if missing:
        raise ConfigError("missing required environment: " + ", ".join(missing))
    from minio import Minio

    secure = os.getenv("MINIO_SECURE", "").strip().lower() == "true"
    return Minio(endpoint, access, secret, secure=secure)


def _upload(client, bucket: str, name: str, document: Dict[str, Any]) -> None:
    payload = gzip.compress(json.dumps(document, separators=(",", ":")).encode("utf-8"))
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    client.put_object(
        bucket, name, io.BytesIO(payload), len(payload), content_type="application/gzip"
    )


def run(now: Optional[datetime] = None) -> int:
    """Archive today's forecasts. Returns the process exit code."""
    now = now or datetime.now(timezone.utc)
    try:
        client = _make_minio_client()
    except ConfigError as exc:
        logger.error("Forecast archive cannot start: %s", exc)
        return 2

    bucket = os.getenv("FORECAST_ARCHIVE_BUCKET", "").strip() or DEFAULT_BUCKET
    object_date = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
    engine = ParcelWeatherEngine()
    tenants = engine._get_active_tenants()
    logger.info("Forecast archive %s: %d tenants", object_date, len(tenants))

    total_parcels = total_archived = total_failed = 0
    exit_code = 0
    for tenant in tenants:
        try:
            parcels = _fetch_tenant_parcels(engine, tenant)
            if not parcels:
                logger.info("Tenant %s: parcels=0 archived=0 failed=0", tenant)
                continue
            entries, failed = _archive_tenant(engine, tenant, parcels)
            total_parcels += len(parcels)
            total_failed += failed
            if not entries:
                logger.error(
                    "Tenant %s: parcels=%d archived=0 failed=%d (nothing to upload)",
                    tenant, len(parcels), failed,
                )
                exit_code = 1
                continue
            document = {"issued_at": now.astimezone(timezone.utc).isoformat(), "parcels": entries}
            _upload(client, bucket, f"{tenant}/{object_date}.json.gz", document)
            total_archived += len(entries)
            logger.info(
                "Tenant %s: parcels=%d archived=%d failed=%d",
                tenant, len(parcels), len(entries), failed,
            )
        except Exception as exc:  # noqa: BLE001 - isolate the tenant
            logger.error("Tenant %s failed: %s", tenant, exc)
            exit_code = 1

    logger.info(
        "Forecast archive done: parcels=%d archived=%d failed=%d exit=%d",
        total_parcels, total_archived, total_failed, exit_code,
    )
    return exit_code


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return run()


if __name__ == "__main__":
    sys.exit(main())
