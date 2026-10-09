"""
GET /api/weather/parcel/{parcel_id} — canonical parcel weather with spatial downscaling.
GET /api/weather/parcel/{parcel_id}/agro-status — agronomic semaphores.
"""

from common.tenant_constants import SHARED_TENANT
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from psycopg2.extras import RealDictCursor

from app.auth import require_auth, require_auth_optional
from app.config import settings, with_models
from app.deps import get_db_connection
from app.routers.observations import _WEATHER_ENTITY_TYPES
from app.services.agro_status import (
    calculate_agro_status,
    _usda_texture_class,
    _extract_float,
)
from common.ngsi_headers import inject_fiware_headers

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weather", tags=["parcels"])

# Altitude weighting for regional station selection: a station 100 m higher or
# lower than the parcel is treated as ~10 km farther away. The temperature lapse
# rate is corrected by the spatial downscaler; this biases the SELECTION toward
# same-altitude stations for the variables the downscaler does not correct
# (precipitation, wind, soil moisture).
ALTITUDE_WEIGHT_KM_PER_100M = 10.0


# Live WeatherObserved measurement keys per agro-status field. The platform
# @context is not injective, so a field can arrive under several aliases; the
# first alias present (and not null) wins.
_LIVE_MEASUREMENT_KEYS = {
    "temp_avg": ("airTemperature", "temperature"),
    "humidity_avg": ("relativeHumidity", "humidity"),
    "precip_mm": ("precipitation",),
    "wind_speed_ms": ("windSpeed",),
    "wind_gusts_ms": ("windGusts",),
    "wind_direction_deg": ("windDirection",),
    "pressure_hpa": ("atmosphericPressure",),
    "solar_rad_w_m2": ("solarRadiation",),
    "eto_mm": ("et0",),
    "soil_moisture_0_10cm": ("soilMoistureTop",),
    "soil_moisture_10_40cm": ("soilMoistureSub",),
    "gdd_accumulated": ("gddAccumulated",),
    "delta_t": ("deltaT",),
}


def _measurements_dict(raw) -> dict:
    """telemetry_events payload.measurements as a dict (psycopg2 may hand a str)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _measurement(measurements: dict, *keys):
    """First non-null measurement among several aliases; None when absent."""
    for key in keys:
        value = measurements.get(key)
        if value is not None:
            return value
    return None


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _live_weather_observation(observed_at, measurements: dict) -> dict:
    """Agro-status observation dict from a parcel's live WeatherObserved row.

    Fields the row does not carry stay None: nothing is defaulted or filled.
    """
    obs = {"observed_at": observed_at}
    for field, keys in _LIVE_MEASUREMENT_KEYS.items():
        obs[field] = _measurement(measurements, *keys)
    obs["soil_moisture_0_10cm"] = _soil_percent(obs["soil_moisture_0_10cm"])
    obs["soil_moisture_10_40cm"] = _soil_percent(obs["soil_moisture_10_40cm"])
    # Live rows carry no daily extremes (those live on the closed-day series).
    obs["temp_min"] = None
    obs["temp_max"] = None
    obs["precip_probability"] = None
    obs["solar_rad_ghi_w_m2"] = obs["solar_rad_w_m2"]
    obs["solar_rad_dni_w_m2"] = None
    obs["source"] = _measurement(measurements, "sourceConfidence") or "OPEN-METEO"
    obs["data_type"] = "HISTORY"
    obs["metadata"] = {}
    return obs


def _cross_validate_sensors(sensors: list) -> dict:
    """Cross-validate multiple sensors, flag outliers >30% from median.

    Returns the primary (nearest reliable) sensor data with validation metadata.
    When one sensor diverges significantly, uses median of remaining sensors.
    """
    if len(sensors) < 2:
        return sensors[0] if sensors else None

    # Numeric metrics to cross-validate
    keys = [
        "temperature",
        "temp",
        "humidity",
        "soil_moisture",
        "moisture",
        "wind_speed",
        "pressure",
    ]

    def _median(values):
        sorted_vals = sorted(v for v in values if v is not None)
        if not sorted_vals:
            return None
        n = len(sorted_vals)
        mid = n // 2
        return (
            sorted_vals[mid] if n % 2 else (sorted_vals[mid - 1] + sorted_vals[mid]) / 2
        )

    # Flag unreliable sensors
    reliable = []
    for s in sensors:
        s["_unreliable"] = False
        reliable.append(s)

    if len(reliable) >= 2:
        for key in keys:
            values = [
                s["payload"].get(key)
                for s in reliable
                if s["payload"].get(key) is not None
            ]
            if len(values) < 2:
                continue
            med = _median(values)
            if med and med > 0:
                for s in reliable:
                    val = s["payload"].get(key)
                    if val is not None and med > 0:
                        deviation = abs(val - med) / med
                        if deviation > 0.3:
                            s["_unreliable"] = True

    # Build fused payload from reliable sensors (median of each metric)
    reliable_only = [s for s in reliable if not s["_unreliable"]]
    if not reliable_only:
        reliable_only = reliable  # fallback: all sensors unreliable, use all

    fused_payload = {}
    for key in keys:
        vals = [
            s["payload"].get(key)
            for s in reliable_only
            if s["payload"].get(key) is not None
        ]
        fused_payload[key] = _median(vals)

    # Use nearest reliable sensor as primary
    primary = reliable_only[0]
    primary["payload"] = fused_payload
    primary["validation"] = {
        "status": "cross_validated",
        "total_sensors": len(sensors),
        "reliable_sensors": len(reliable_only),
        "unreliable_ids": [s["external_id"] for s in sensors if s["_unreliable"]],
    }
    return primary


def _persist_agro_status_to_orion(tenant_id: str, parcel_id: str, result: dict):
    """Write agroStatus semaphores to the AgriParcel entity in Orion-LD.

    Non-blocking best-effort: failures are logged but never propagated.
    """
    try:
        headers = _orion_headers(tenant_id)
        headers["Content-Type"] = "application/json"
        semaphores = result.get("semaphores", {})
        soil = result.get("soil") or {}
        metrics = result.get("metrics", {})

        agro_status_value = {
            "spraying": semaphores.get("spraying", "unknown"),
            "workability": semaphores.get("workability", "unknown"),
            "irrigation": semaphores.get("irrigation", "unknown"),
            "calculatedAt": result.get("timestamp"),
            "sourceConfidence": result.get("source_confidence"),
            "downscalingApplied": result.get("downscaling") == "applied",
        }
        if soil.get("texture_applied"):
            agro_status_value["soilTexture"] = soil.get("texture_class")
            agro_status_value["fieldCapacity"] = soil.get("field_capacity")
            agro_status_value["wiltingPoint"] = soil.get("wilting_point")
        if metrics.get("delta_t") is not None:
            agro_status_value["deltaT"] = metrics["delta_t"]
        if metrics.get("water_balance") is not None:
            agro_status_value["waterBalance"] = metrics["water_balance"]
        if metrics.get("spraying_reason"):
            agro_status_value["sprayingReason"] = metrics["spraying_reason"]

        body = {
            "agroStatus": {
                "type": "Property",
                "value": agro_status_value,
            }
        }

        resp = requests.patch(
            f"{settings.orion_url}/ngsi-ld/v1/entities/{parcel_id}/attrs",
            headers=headers,
            json=body,
            timeout=3,
        )
        if resp.status_code not in (200, 201, 204):
            logger.debug(f"Orion agroStatus persist returned {resp.status_code}")
    except Exception as e:
        logger.debug(f"Could not persist agroStatus to Orion: {e}")


def _orion_headers(tenant_id: str) -> dict:
    return inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False)


def _normalize_parcel_id(parcel_id: str) -> str:
    """Callers pass either the full NGSI-LD URN or the bare id.

    Orion only resolves the URN; a bare id makes it answer 400, which the
    agro-status route then misreports as a 500. Normalize to the canonical
    AgriParcel URN shape (no tenant segment — parcel URNs are tenant-less).
    """
    if not parcel_id or not parcel_id.startswith("urn:"):
        return f"urn:ngsi-ld:AgriParcel:{parcel_id}"
    return parcel_id


def _orion_query_headers(tenant_id: str) -> dict:
    """Headers for Orion READ queries — the platform @context Link is REQUIRED.

    Attribute names in a q filter are expanded with whatever context the request
    carries. Entities are stored with the platform expansion (locatedAt lives at
    https://nkz-os.org/ns/locatedAt), so a query sent WITHOUT the Link header
    expands the term to the default vocabulary instead, matches nothing, and
    returns an empty list — a FALSE ZERO, not an error.

    Dropping the header was tried as a fix and caused exactly the failure it was
    meant to avoid; the queries returned [] for every parcel.
    """
    return inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False)


def _fetch_weather_map_stats(parcel_id: str, tenant_id: str) -> dict:
    """Parcel-level meteo from the weather-map raster (fail-safe {}).

    The weather-map raster is spatially interpolated to the parcel, so it is
    more precise than the downscaled regional station. It is the
    ``parcel_weather`` tier in the platform meteo precedence.

    Contract: GET /api/weather-map/stats/{parcel_urn}?metrics=<csv> with
    X-Tenant-ID + X-User-ID headers (401 without X-User-ID). Valid metrics:
    temperature_avg, temperature_min, solar_radiation, eto, water_balance,
    frost_risk, soil_moisture (percent scale). Returns {} on failure/no COG.
    """
    if not settings.weather_map_url:
        return {}
    parcel_urn = (
        parcel_id
        if parcel_id.startswith("urn:")
        else f"urn:ngsi-ld:AgriParcel:{parcel_id}"
    )
    try:
        resp = requests.get(
            f"{settings.weather_map_url}/api/weather-map/stats/{parcel_urn}",
            params={"metrics": "temperature_avg,temperature_min,eto,soil_moisture"},
            headers=(
                {"X-Tenant-ID": tenant_id, "X-User-ID": "weather-api-worker"}
                if tenant_id else {}
            ),
            timeout=8,
        )
        if resp.status_code != 200:
            return {}
        data = resp.json() or {}
    except Exception as exc:  # noqa: BLE001 — fail-safe
        logger.warning("weather-map stats failed for %s: %s", parcel_id, exc)
        return {}

    # The raster is a daily product regenerated every few days. A day other than
    # today must not override the parcel's own current reading: it is another
    # day's mean, not the present value.
    raster_day = data.get("date")
    today = datetime.now(timezone.utc).date().isoformat()
    if raster_day != today:
        logger.debug(
            "weather-map raster for %s is from %s, not today (%s): ignored",
            parcel_id, raster_day, today,
        )
        return {}

    metrics = data.get("metrics") or {}

    def _mean(name: str):
        m = metrics.get(name)
        if isinstance(m, dict):
            v = m.get("mean", m.get("value"))
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
        return None

    out: dict = {}
    air = _mean("temperature_avg")
    tmin = _mean("temperature_min")
    et0 = _mean("eto")
    soil = _mean("soil_moisture")
    if air is not None:
        out["temp_avg"] = air
    if tmin is not None:
        out["temp_min"] = tmin
    if et0 is not None:
        out["eto_mm"] = et0
    if soil is not None:
        # weather-map soil_moisture is already percent (dry <= 15, saturated >= 40)
        out["soil_moisture_0_10cm"] = soil
    return out


def _soil_percent(value):
    """Volumetric water content (m3/m3) -> percent.

    The broker publishes `soilMoistureTop`/`soilMoistureSub` as a volumetric
    fraction (`unitCode: M3`), while the agro panel and the risk models compare
    against percentages (optimal 15-25). Passing 0.106 straight through reads as
    0.1 %: permanent severe stress on a parcel that is only mildly dry.
    """
    if value is None:
        return None
    try:
        return float(value) * 100.0
    except (TypeError, ValueError):
        return None


def _attr_alias(entity: dict, *keys, default=None):
    """Read the first present attribute among several context aliases.

    The platform @context is not injective: `temperature` and `airTemperature`
    expand to the same IRI, as do `relativeHumidity`/`humidity` and
    `locatedAt`/`refParcel`. JSON-LD compaction returns ONE term per IRI, and it
    is not necessarily the one the writer used — the ParcelWeatherEngine writes
    `temperature` and Orion hands it back as `airTemperature`. Reading a single
    name yields None for every field.
    """
    for key in keys:
        attr = entity.get(key)
        if attr is None:
            continue
        value = attr.get("value") if isinstance(attr, dict) else attr
        if value is not None:
            return value
    return default


def _resolve_parcel_location(parcel_entity: dict) -> Optional[tuple]:
    """Extract (longitude, latitude) from a parcel entity's location attribute."""
    location_attr = parcel_entity.get("location", {})
    if isinstance(location_attr, dict):
        loc_value = location_attr.get("value", location_attr)
    else:
        loc_value = location_attr

    if isinstance(loc_value, dict):
        geom_type = loc_value.get("type", "")
        coords = loc_value.get("coordinates", [])
        if geom_type == "Point" and len(coords) >= 2:
            return (float(coords[0]), float(coords[1]))
        if geom_type in ("Polygon", "MultiPolygon") and coords:
            ring = coords[0] if geom_type == "Polygon" else coords[0][0]
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            return (sum(xs) / len(xs), sum(ys) / len(ys))
    return None


def _safe_idx(daily: dict, key: str, index: int) -> Optional[float]:
    """Safely get a value from Open-Meteo daily dict by index."""
    arr = daily.get(key, [])
    if arr and index < len(arr) and arr[index] is not None:
        return float(arr[index])
    return None


def _div_if(value: Optional[float], divisor: float) -> Optional[float]:
    """Divide value by divisor if not None."""
    if value is None:
        return None
    return round(value / divisor, 2)


@router.get("/parcel/{parcel_id}")
def get_parcel_weather(
    parcel_id: str,
    tenant_id: str = Depends(require_auth_optional),
    source: str = Query("OPEN-METEO"),
    data_type: str = Query("HISTORY"),
    limit: int = Query(1, le=72),
):
    """
    Canonical weather endpoint for a specific parcel.

    Resolves the parcel's location from Orion-LD, finds the nearest
    municipality with weather data, applies spatial downscaling (altitude,
    aspect, slope), and returns corrected observations.
    """
    if not tenant_id:
        tenant_id = SHARED_TENANT

    # Callers pass either the full NGSI-LD URN or the bare id (the host uses
    # both conventions). Orion only resolves the URN, so a bare id made this
    # endpoint 404 and the caller fell back to the legacy municipality weather
    # (which has no soil moisture) — the dashboard then showed soil moisture
    # as N/A even though the value sat in the broker.
    parcel_id = _normalize_parcel_id(parcel_id)

    try:
        # Step 1: Resolve parcel from Orion-LD
        headers = _orion_headers(tenant_id)
        parcel_resp = requests.get(
            f"{settings.orion_url}/ngsi-ld/v1/entities/{parcel_id}",
            headers=headers,
            timeout=10,
        )
        if parcel_resp.status_code != 200:
            return JSONResponse(
                {"error": f"Parcel not found: {parcel_resp.status_code}"},
                status_code=404,
            )

        parcel = parcel_resp.json()

        # Step 2: Extract location
        loc = _resolve_parcel_location(parcel)
        if loc is None:
            return JSONResponse(
                {"error": "Parcel has no resolvable location"}, status_code=400
            )
        parcel_lon, parcel_lat = loc

        # Step 3: Extract terrain attributes
        parcel_altitude = 0.0
        elev = parcel.get("elevation", {})
        if isinstance(elev, dict):
            parcel_altitude = float(elev.get("value", 0) or 0)

        parcel_aspect = 0.0
        ta = parcel.get("terrainAspect", {})
        if isinstance(ta, dict):
            parcel_aspect = float(ta.get("value", 0) or 0)

        parcel_slope = 0.0
        ts = parcel.get("terrainSlope", {})
        if isinstance(ts, dict):
            parcel_slope = float(ts.get("value", 0) or 0)

        # Step 4: Try to find linked WeatherObserved entity in Orion-LD.
        # Query by locatedAt relationship only — DO NOT filter by type.
        # (FALSE-ZERO guard: stored type URI may differ from context expansion.)
        wo_resp = requests.get(
            f"{settings.orion_url}/ngsi-ld/v1/entities",
            params={
                "q": f'locatedAt=="{parcel_id}"',
                "limit": 10,
            },
            headers=_orion_query_headers(tenant_id),
            timeout=10,
        )

        wo_entity = None
        if wo_resp.status_code == 200:
            wo_data = wo_resp.json()
            if isinstance(wo_data, dict):
                wo_data = [wo_data]
            # Skip the closed-day series entity (dailySummary true, #1043): it
            # is not the current-conditions station. The "-daily" id suffix
            # stays as a legacy guard only.
            def _is_closed_day(e):
                node = e.get("dailySummary")
                if isinstance(node, dict) and node.get("value") is True:
                    return True
                return str(e.get("id", "")).endswith("-daily")

            wo_entity = next(
                (e for e in wo_data
                 if isinstance(e, dict) and e.get("id")
                 and not _is_closed_day(e)),
                None,
            )

        # Guard: verify the entity has weather-like attributes before using it.
        # Querying without type filter avoids FALSE-ZERO when the stored type URI
        # (e.g. saref4agri:WeatherObserved) differs from context expansion.
        if wo_entity:
            has_weather_attrs = any(
                wo_entity.get(k) for k in
                ("airTemperature", "temperature", "dateObserved",
                 "humidity", "relativeHumidity", "windSpeed")
            )
            if not has_weather_attrs:
                wo_entity = None  # not a WeatherObserved entity — skip

        if wo_entity and data_type != "FORECAST":
            # Use cached WeatherObserved for HISTORY only.
            # FORECAST must reach Open-Meteo for future predictions —
            # the WeatherObserved entity only stores current conditions.
            # Normalize to same schema as on-the-fly response
            wo_attrs = wo_entity if isinstance(wo_entity, dict) else {}

            _attr = _attr_alias

            date_obs = wo_attrs.get("dateObserved", {})

            normalized_obs = {
                "observed_at": (
                    date_obs.get("value", {}).get("@value", "")
                    if isinstance(date_obs, dict)
                    else ""
                ),
                "temp_avg": _attr(wo_attrs, "airTemperature", "temperature"),
                "temp_max": None,
                "temp_min": None,
                "humidity_avg": _attr(wo_attrs, "humidity", "relativeHumidity"),
                "precip_mm": _attr(wo_attrs, "precipitation"),
                "wind_speed_ms": _attr(wo_attrs, "windSpeed"),       # current (latest hour mean)
                "wind_speed_max": _attr(wo_attrs, "windSpeedMax"),   # max of hourly means today
                "wind_gusts_ms": _attr(wo_attrs, "windGusts"),       # max gust today
                "wind_direction_deg": _attr(wo_attrs, "windDirection"),
                "pressure_hpa": _attr(wo_attrs, "atmosphericPressure"),
                "eto_mm": _attr(wo_attrs, "et0"),
                "delta_t": _attr(wo_attrs, "deltaT"),
                # Published by the ParcelWeatherEngine and, until now, dropped
                # here: the panel showed "N/A" for soil moisture while the value
                # sat in the broker.
                "soil_moisture_0_10cm": _soil_percent(_attr(wo_attrs, "soilMoistureTop")),
                "soil_moisture_10_40cm": _soil_percent(_attr(wo_attrs, "soilMoistureSub")),
                "solar_rad_w_m2": _attr(wo_attrs, "solarRadiation"),
                "solar_rad_ghi_w_m2": _attr(wo_attrs, "solarRadiation"),
                "gdd_accumulated": _attr(wo_attrs, "gddAccumulated"),
                "source": _attr(wo_attrs, "sourceConfidence", "OPEN-METEO"),
                "data_type": "HISTORY",
            }

            return {
                "parcel_id": parcel_id,
                "source": "orion-cache",
                "observations": [normalized_obs],
            }

        # Step 5: Fetch from Open-Meteo directly (FORECAST always, HISTORY on cache miss)
        from datetime import datetime, timedelta

        today = datetime.utcnow().strftime("%Y-%m-%d")
        end = (datetime.utcnow() + timedelta(days=min(limit, 14))).strftime("%Y-%m-%d")

        om_resp = requests.get(
            f"{settings.openmeteo_api_url}/forecast",
            params=with_models({
                "latitude": parcel_lat,
                "longitude": parcel_lon,
                "start_date": today,
                "end_date": end,
                "daily": [
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "temperature_2m_mean",
                    "relative_humidity_2m_mean",
                    "precipitation_sum",
                    "precipitation_probability_max",
                    "et0_fao_evapotranspiration",
                    "shortwave_radiation_sum",
                    "soil_moisture_0_to_7cm_mean",
                    "soil_moisture_7_to_28cm_mean",
                    "surface_pressure_mean",
                ],
                "hourly": [
                    "wind_speed_10m",
                    "wind_gusts_10m",
                    "wind_direction_10m",
                ],
                "timezone": "Europe/Madrid",
            }),
            timeout=10,
        )

        if om_resp.status_code != 200:
            return JSONResponse(
                {"error": f"Open-Meteo returned {om_resp.status_code}"},
                status_code=502,
            )

        raw = om_resp.json()
        station_elevation = raw.get("elevation", 0.0)
        daily = raw.get("daily", {})
        dates = daily.get("time", [])
        hourly = raw.get("hourly", {})

        # Pre-group hourly wind by date
        hourly_by_date: dict = {}
        hourly_times = hourly.get("time", [])
        if hourly_times:
            speeds = hourly.get("wind_speed_10m", [])
            gusts = hourly.get("wind_gusts_10m", [])
            dirs = hourly.get("wind_direction_10m", [])
            for hi, ht in enumerate(hourly_times):
                day = ht[:10]
                if day not in hourly_by_date:
                    hourly_by_date[day] = {"speeds": [], "gusts": [], "last_dir": None}
                hd = hourly_by_date[day]
                if hi < len(speeds) and speeds[hi] is not None:
                    hd["speeds"].append(float(speeds[hi]))
                if hi < len(gusts) and gusts[hi] is not None:
                    hd["gusts"].append(float(gusts[hi]))
                if hi < len(dirs) and dirs[hi] is not None:
                    hd["last_dir"] = float(dirs[hi])

        # Parse Open-Meteo response to observation dicts
        observations = []
        for i, date_str in enumerate(dates):
            obs = {
                "observed_at": date_str,
                "temp_max": _safe_idx(daily, "temperature_2m_max", i),
                "temp_min": _safe_idx(daily, "temperature_2m_min", i),
                "temp_avg": _safe_idx(daily, "temperature_2m_mean", i),
                "humidity_avg": _safe_idx(daily, "relative_humidity_2m_mean", i),
                "precip_mm": _safe_idx(daily, "precipitation_sum", i),
                "precip_probability": _safe_idx(daily, "precipitation_probability_max", i),
                "pressure_hpa": _safe_idx(daily, "surface_pressure_mean", i),
                "eto_mm": _safe_idx(daily, "et0_fao_evapotranspiration", i),
                "solar_rad_w_m2": _div_if(
                    _safe_idx(daily, "shortwave_radiation_sum", i), 0.0864
                ),
                "soil_moisture_0_10cm": _safe_idx(
                    daily, "soil_moisture_0_to_7cm_mean", i
                ),
                "soil_moisture_10_40cm": _safe_idx(
                    daily, "soil_moisture_7_to_28cm_mean", i
                ),
            }

            # Hourly wind — km/h → m/s
            hday = hourly_by_date.get(date_str, {})
            hspeeds = hday.get("speeds", [])
            hgusts = hday.get("gusts", [])
            if hspeeds:
                obs["wind_speed_ms"] = round(hspeeds[-1] / 3.6, 1)     # current
                obs["wind_speed_max"] = round(max(hspeeds) / 3.6, 1)   # max today
                obs["wind_direction_deg"] = hday.get("last_dir")
            if hgusts:
                obs["wind_gusts_ms"] = round(max(hgusts) / 3.6, 1)

            observations.append(obs)

        if not observations:
            return JSONResponse(
                {
                    "parcel_id": parcel_id,
                    "source": "on-the-fly",
                    "downscaling": "unavailable",
                    "observations": [],
                }
            )

        # Step 6: Apply spatial downscaling
        downscaling_applied = False
        try:
            from weather_utils.spatial_downscaler import (
                downscale_for_parcel,
            )

            corrected_observations = []
            for obs in observations:
                obs_dt_str = obs.get("observed_at")
                doy = None
                if obs_dt_str and isinstance(obs_dt_str, str):
                    try:
                        doy = datetime.fromisoformat(obs_dt_str).timetuple().tm_yday
                    except (ValueError, TypeError):
                        pass

                corrected = downscale_for_parcel(
                    weather_data=obs,
                    parcel_lat=parcel_lat,
                    parcel_lon=parcel_lon,
                    parcel_altitude_m=parcel_altitude,
                    station_altitude_m=station_elevation,
                    parcel_aspect_deg=parcel_aspect,
                    parcel_slope_deg=parcel_slope,
                    doy=doy,
                )
                corrected["observed_at"] = obs.get("observed_at")
                corrected["source"] = "OPEN-METEO"
                corrected["data_type"] = data_type
                corrected_observations.append(corrected)

            observations = corrected_observations
            downscaling_applied = parcel_altitude > 0 or parcel_slope >= 1.0

        except ImportError:
            logger.debug(
                "Spatial downscaler not available — returning raw observations"
            )
        except Exception as exc:
            logger.warning(f"Downscaling error (returning raw data): {exc}")

        return {
            "parcel_id": parcel_id,
            "source": "on-the-fly",
            "parcel_altitude_m": parcel_altitude,
            "station_altitude_m": station_elevation,
            "parcel_aspect_deg": parcel_aspect,
            "parcel_slope_deg": parcel_slope,
            "downscaling": "applied" if downscaling_applied else "unavailable",
            "observations": observations,
        }

    except requests.exceptions.Timeout:
        return JSONResponse({"error": "Orion-LD request timed out"}, status_code=504)
    except Exception as e:
        logger.error(f"Error in get_parcel_weather: {e}", exc_info=True)
        return JSONResponse(
            {"error": "Failed to fetch parcel weather"}, status_code=500
        )


@router.get("/parcel/{parcel_id}/agro-status")
def get_parcel_agro_status(
    parcel_id: str,
    tenant_id: str = Depends(require_auth),
):
    """
    Get agronomic weather status for a parcel.

    Uses the parcel's own WeatherObserved series (telemetry_events) — no direct
    Open-Meteo call.
    Fuses sensor data when available within 5km radius.
    Applies spatial downscaling for parcel-specific microclimate.
    """
    parcel_id = _normalize_parcel_id(parcel_id)
    try:
        # 1. Get parcel from Orion-LD
        headers = _orion_headers(tenant_id)
        response = requests.get(
            f"{settings.orion_url}/ngsi-ld/v1/entities/{parcel_id}",
            headers=headers,
            timeout=10,
        )
        if response.status_code == 404:
            return JSONResponse({"error": "Parcel not found"}, status_code=404)
        if response.status_code != 200:
            logger.error(f"Error fetching parcel from Orion: {response.status_code}")
            return JSONResponse({"error": "Failed to fetch parcel"}, status_code=500)

        parcel_entity = response.json()

        # 2. Calculate centroid from parcel geometry
        loc = _resolve_parcel_location(parcel_entity)
        if not loc:
            return JSONResponse(
                {
                    "error": "Parcel has no valid location/geometry",
                    "details": "Parcel location could not be determined",
                },
                status_code=400,
            )
        lon, lat = loc

        # 3. Try to get sensor data near the parcel (within 5km radius)
        #    Fetch all nearby sensors for cross-validation
        sensor_data = None
        try:
            conn = get_db_connection(tenant_id)
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute(
                    """
                    SELECT
                        s.external_id,
                        s.name,
                        ST_X(s.installation_location::geometry) as lon,
                        ST_Y(s.installation_location::geometry) as lat,
                        ST_Distance(
                            s.installation_location::geography,
                            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                        ) as distance_m,
                        te.observed_at,
                        te.payload
                    FROM sensors s
                    LEFT JOIN LATERAL (
                        SELECT observed_at, payload
                        FROM telemetry_events
                        WHERE tenant_id = %s
                        AND device_id = s.external_id
                        ORDER BY observed_at DESC
                        LIMIT 1
                    ) te ON true
                    WHERE s.tenant_id = %s
                    AND ST_Distance(
                        s.installation_location::geography,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                    ) <= 5000
                    ORDER BY distance_m ASC
                    """,
                    (lon, lat, tenant_id, tenant_id, lon, lat),
                )
                all_sensors = []
                for row in cur.fetchall():
                    if row["payload"]:
                        payload = (
                            row["payload"]
                            if isinstance(row["payload"], dict)
                            else json.loads(row["payload"])
                        )
                        all_sensors.append(
                            {
                                "external_id": row["external_id"],
                                "name": row["name"],
                                "distance_m": float(row["distance_m"])
                                if row["distance_m"]
                                else None,
                                "observed_at": row["observed_at"].isoformat()
                                if row["observed_at"]
                                else None,
                                "payload": payload,
                            }
                        )

                # Cross-validate: if 2+ sensors, compare and flag outliers (>30% from median)
                if len(all_sensors) >= 2:
                    sensor_data = _cross_validate_sensors(all_sensors)
                elif all_sensors:
                    sensor_data = all_sensors[0]
                    sensor_data["validation"] = {"status": "single"}
                else:
                    sensor_data = None
            finally:
                cur.close()
                conn.close()
        except Exception as e:
            logger.warning(f"Error fetching sensor data: {e}")

        # 4. Extract terrain attributes for spatial downscaling
        parcel_altitude = 0.0
        elev = parcel_entity.get("elevation", {})
        if isinstance(elev, dict):
            parcel_altitude = float(elev.get("value", 0) or 0)

        parcel_aspect = 0.0
        ta = parcel_entity.get("terrainAspect", {})
        if isinstance(ta, dict):
            parcel_aspect = float(ta.get("value", 0) or 0)

        parcel_slope = 0.0
        ts = parcel_entity.get("terrainSlope", {})
        if isinstance(ts, dict):
            parcel_slope = float(ts.get("value", 0) or 0)

        # 4.5. Soil of this parcel: the AgriSoilExtended (or legacy AgriSoil)
        # entity linked to it. Both types are listed: filtering by AgriSoil alone
        # is a FALSE ZERO for AgriSoilExtended. The type filter is required — many
        # entities link to the parcel (jobs, assessments, EO products) and with
        # limit=1 the first one is often not the soil.
        soil_texture = None
        try:
            soil_headers = _orion_query_headers(tenant_id)
            soil_response = requests.get(
                f"{settings.orion_url}/ngsi-ld/v1/entities",
                params={
                    "type": "AgriSoilExtended,AgriSoil",
                    # The URN must be quoted: its colons break Orion's q parser
                    # and the request comes back 400, not empty.
                    "q": f'hasAgriParcel=="{parcel_id}"',
                    "limit": 1,
                },
                headers=soil_headers,
                timeout=5,
            )
            if soil_response.status_code == 200:
                soil_entities = soil_response.json()
                if isinstance(soil_entities, list) and soil_entities:
                    soil = soil_entities[0]
                    # Guard: verify this is actually a soil entity
                    if not soil.get("horizons"):
                        soil_entities = []  # skip non-soil entity
                if isinstance(soil_entities, list) and soil_entities:
                    soil = soil_entities[0]
                    horizons = soil.get("horizons", {}).get("value", [])
                    if isinstance(horizons, list) and horizons:
                        # Use top horizon (0-30cm)
                        h = horizons[0]
                        soil_texture = {
                            "sand": _extract_float(h.get("sand")),
                            "clay": _extract_float(h.get("clay")),
                            "organic_carbon": _extract_float(
                                h.get("organicCarbon"), 0.5
                            ),
                            # Derived by the soil module (single source): the
                            # tempero limits and the hydraulics shown with them.
                            "wet_tillage_limit": _extract_float(
                                h.get("wetTillageLimit"), None
                            ),
                            "dry_tillage_limit": _extract_float(
                                h.get("dryTillageLimit"), None
                            ),
                            "field_capacity": _extract_float(h.get("fieldCapacity"), None),
                            "wilting_point": _extract_float(h.get("wiltingPoint"), None),
                            "ksat": _extract_float(h.get("ksatSaturated"), None),
                            "hydrologic_group": h.get("hydrologicGroup"),
                            "source": "soil-module",
                        }
                        # Determine USDA texture class
                        silt = 100.0 - soil_texture["sand"] - soil_texture["clay"]
                        soil_texture["silt"] = max(0.0, silt)
                        soil_texture["texture_class"] = _usda_texture_class(
                            soil_texture["sand"], soil_texture["clay"]
                        )
                        logger.debug(
                            f"Soil texture found for parcel {parcel_id}: {soil_texture['texture_class']}"
                        )
        except Exception as e:
            logger.debug(f"Could not fetch AgriSoil for parcel {parcel_id}: {e}")

        # 5. Read the parcel's OWN WeatherObserved series from telemetry_events:
        # the latest live row for current conditions and the closed-day
        # ("...-daily") rows of the last 3 days for the water balance. The
        # series is fetched at the parcel centroid, so there is no regional
        # station and no altitude gap to correct.
        weather_observation = {}
        weather_3d = []
        station_altitude = parcel_altitude
        parcel_bare = _escape_like(parcel_id.split(":")[-1])

        try:
            conn = get_db_connection(tenant_id)
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)

                # 5a/5b. Latest live observation (the trailing "-daily" series
                # entity does not match this pattern).
                cur.execute(
                    """
                    SELECT observed_at, payload->'measurements' AS measurements
                    FROM telemetry_events
                    WHERE tenant_id = %s
                      AND entity_type IN (%s, %s)
                      AND entity_id LIKE %s ESCAPE '\\'
                    ORDER BY observed_at DESC
                    LIMIT 1
                    """,
                    (tenant_id, *_WEATHER_ENTITY_TYPES, f"%:parcel-{parcel_bare}"),
                )
                row = cur.fetchone()
                if row:
                    weather_observation = _live_weather_observation(
                        row["observed_at"], _measurements_dict(row.get("measurements"))
                    )

                # 5c. Closed days D-3..D-1 (UTC day label) for water balance
                # aggregation. Days without a record are simply absent: they are
                # neither filled with zero nor interpolated.
                today = datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                cur.execute(
                    """
                    SELECT observed_at, payload->'measurements' AS measurements
                    FROM telemetry_events
                    WHERE tenant_id = %s
                      AND entity_type IN (%s, %s)
                      AND entity_id LIKE %s ESCAPE '\\'
                      AND observed_at >= %s AND observed_at < %s
                    ORDER BY observed_at DESC
                    """,
                    (
                        tenant_id,
                        *_WEATHER_ENTITY_TYPES,
                        f"%:parcel-{parcel_bare}-daily",
                        today - timedelta(days=3),
                        today,
                    ),
                )
                seen_days = set()
                for r in cur.fetchall():
                    day = r["observed_at"].date() if hasattr(r["observed_at"], "date") else None
                    if day in seen_days:
                        continue  # one record per day, latest wins
                    seen_days.add(day)
                    m = _measurements_dict(r.get("measurements"))
                    weather_3d.append({
                        "observed_at": r["observed_at"],
                        "precip_mm": _measurement(m, "precipitation"),
                        "eto_mm": _measurement(m, "et0"),
                    })
            finally:
                cur.close()
                conn.close()
        except Exception as e:
            logger.warning(f"Could not fetch parcel weather from telemetry_events: {e}")

        # 5d. Fallback: if no telemetry data, query WeatherObserved directly from Orion-LD.
        # Query by locatedAt relationship only — DO NOT filter by type.
        # The stored entity type may be expanded to a URI (e.g. saref4agri:WeatherObserved)
        # that differs from the platform context expansion (nkz:WeatherObserved).
        # Filtering by type=WeatherObserved causes FALSE-ZERO (0 results).
        if not weather_observation:
            try:
                wo_resp = requests.get(
                    f"{settings.orion_url}/ngsi-ld/v1/entities",
                    params={
                        "q": f'locatedAt=="{parcel_id}"',
                        "limit": 1,
                    },
                    headers=_orion_query_headers(tenant_id),
                    timeout=10,
                )
                if wo_resp.status_code == 200:
                    wo_data = wo_resp.json()
                    wo_entities = (
                        wo_data
                        if isinstance(wo_data, list)
                        else [wo_data]
                        if wo_data.get("id")
                        else []
                    )
                    if wo_entities:
                        wo = wo_entities[0]
                        # Guard: verify the entity has weather-like attributes.
                        # (Same FALSE-ZERO guard — query without type filter may
                        # return non-WeatherObserved entities.)
                        has_weather_attrs = any(
                            wo.get(k) for k in
                            ("airTemperature", "temperature", "dateObserved",
                             "humidity", "relativeHumidity", "windSpeed")
                        )
                        if not has_weather_attrs:
                            wo_entities = []  # skip non-weather entity
                    if wo_entities:
                        wo = wo_entities[0]
                        # Normalize NGSI-LD attribute names → internal format
                        _attr = _attr_alias

                        date_obs = wo.get("dateObserved", {})
                        obs_at = (
                            date_obs.get("value", {}).get("@value", "")
                            if isinstance(date_obs, dict)
                            else ""
                        )
                        weather_observation = {
                            "observed_at": obs_at,
                            "temp_avg": _attr(wo, "airTemperature", "temperature"),
                            "temp_min": None,
                            "temp_max": None,
                            "humidity_avg": _attr(wo, "humidity", "relativeHumidity"),
                            "precip_mm": _attr(wo, "precipitation"),
                            "precip_probability": None,
                            "wind_speed_ms": _attr(wo, "windSpeed"),
                            "wind_speed_max": _attr(wo, "windSpeedMax"),
                            "wind_gusts_ms": _attr(wo, "windGusts"),
                            "wind_direction_deg": _attr(wo, "windDirection"),
                            "pressure_hpa": _attr(wo, "atmosphericPressure"),
                            "solar_rad_w_m2": _attr(wo, "solarRadiation"),
                            "solar_rad_ghi_w_m2": _attr(wo, "solarRadiation"),
                            "solar_rad_dni_w_m2": None,
                            "eto_mm": _attr(wo, "et0"),
                            "soil_moisture_0_10cm": _soil_percent(
                                _attr(wo, "soilMoistureTop")
                            ),
                            "soil_moisture_10_40cm": _soil_percent(
                                _attr(wo, "soilMoistureSub")
                            ),
                            "gdd_accumulated": _attr(wo, "gddAccumulated"),
                            "delta_t": _attr(wo, "deltaT"),
                            "source": _attr(wo, "sourceConfidence", "OPEN-METEO"),
                            "data_type": "HISTORY",
                            "municipality_code": _attr(wo, "municipalityCode"),
                            "station_elevation_m": _attr(wo, "stationElevation"),
                        }
                        # The ParcelWeatherEngine fetches Open-Meteo AT the parcel
                        # centroid, so this reading is already local and carries no
                        # station elevation. Treating a missing value as sea level
                        # would apply a full lapse-rate correction on top of an
                        # already-local reading (~3 degC too cold at 450 m). Fall
                        # back to the parcel's own altitude, which is a no-op.
                        _station_elev = _attr(wo, "stationElevation")
                        station_altitude = (
                            float(_station_elev)
                            if _station_elev is not None
                            else parcel_altitude
                        )
                        logger.info(
                            f"Orion WeatherObserved fallback for parcel {parcel_id}"
                        )
            except Exception as e:
                logger.warning(f"Orion WeatherObserved fallback failed: {e}")

        # 5f. Weather-map per-parcel raster (parcel_weather tier) — override the
        # regional proxy for the fields the raster provides. The raster is
        # spatially interpolated to the parcel, so it is more precise than the
        # downscaled regional station. Variables the raster does not provide
        # (humidity, wind, precipitation, pressure) keep their regional values.
        wm = _fetch_weather_map_stats(parcel_id, tenant_id)
        wm_fields = []
        for _wm_key in ("temp_avg", "temp_min", "eto_mm", "soil_moisture_0_10cm"):
            if wm.get(_wm_key) is not None:
                weather_observation[_wm_key] = wm[_wm_key]
                wm_fields.append(_wm_key)
        if wm_fields:
            weather_observation["_weather_map_fields"] = wm_fields
            if "temp_avg" in wm_fields:
                # The raster temperature is already at the parcel's altitude, so
                # the lapse-rate downscaling must NOT be applied on top of it
                # (it would double-correct). Zeroing the station->parcel altitude
                # gap makes the downscaler a no-op for the lapse term.
                station_altitude = parcel_altitude

        if not weather_observation:
            # Graceful degradation: return parcel metadata + sensor data
            # without weather semaphores, rather than 503.
            parcel_name = "Unnamed"
            name_attr = parcel_entity.get("name", {})
            if isinstance(name_attr, dict):
                parcel_name = name_attr.get("value", "Unnamed")
            return JSONResponse(
                {
                    "parcel_id": parcel_id,
                    "parcel_name": parcel_name,
                    "centroid": {"latitude": lat, "longitude": lon},
                    "weather": {
                        "temperature": None,
                        "humidity": None,
                        "wind_speed": None,
                        "wind_direction": None,
                        "pressure": None,
                        "precipitation": 0,
                        "precipitation_3d": 0,
                        "eto_today": None,
                        "eto_3d": None,
                        "wind_gusts": None,
                        "water_balance": None,
                        "observed_at": None,
                        "sources": {},
                        "source_confidence": "UNAVAILABLE",
                    },
                    "semaphores": {
                        "spraying": "unknown",
                        "workability": "unknown",
                        "irrigation": "unknown",
                    },
                    "metrics": None,
                    "soil": None,
                    "crop": None,
                    "source_confidence": "UNAVAILABLE",
                    "downscaling": "unavailable",
                    "no_data_reason": "Weather data has not been ingested yet for this area. The weather-worker processes parcels periodically — data should appear within the next hour.",
                    "timestamp": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
                },
            )

        # 6. Calculate agronomic status
        result = calculate_agro_status(
            lat=lat,
            lon=lon,
            parcel_entity=parcel_entity,
            weather_observation=weather_observation,
            weather_3d=weather_3d,
            sensor_data=sensor_data,
            soil_texture=soil_texture,
            parcel_altitude_m=parcel_altitude,
            station_altitude_m=station_altitude,
            parcel_aspect_deg=parcel_aspect,
            parcel_slope_deg=parcel_slope,
        )

        # 7. Persist agroStatus to Orion-LD (non-blocking, best-effort)
        _persist_agro_status_to_orion(tenant_id, parcel_id, result)

        return result

    except requests.exceptions.Timeout:
        return JSONResponse({"error": "Orion-LD request timed out"}, status_code=504)
    except Exception as e:
        logger.error(f"Error in get_parcel_agro_status: {e}", exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@router.get("/parcel/{parcel_id}/forecast")
def get_parcel_forecast(
    parcel_id: str,
    tenant_id: str = Depends(require_auth),
    days: int = Query(7, le=14, description="Forecast days (max 14)"),
):
    """
    Direct Open-Meteo forecast for a parcel's exact centroid.

    Resolves the AgriParcel from Orion-LD, extracts the centroid from its
    location geometry (Point or Polygon), and fetches a forecast from
    Open-Meteo for that precise point. No DB dependency, no municipality
    catalog — pure coordinates.

    Use this for the dashboard forecast card with parcel dropdown.
    """
    parcel_id = _normalize_parcel_id(parcel_id)
    try:
        # 1. Resolve parcel from Orion-LD
        headers = _orion_headers(tenant_id)
        parcel_resp = requests.get(
            f"{settings.orion_url}/ngsi-ld/v1/entities/{parcel_id}",
            headers=headers,
            timeout=10,
        )
        if parcel_resp.status_code != 200:
            return JSONResponse(
                {"error": f"Parcel not found: {parcel_resp.status_code}"},
                status_code=404,
            )

        parcel = parcel_resp.json()

        # 2. Extract centroid from location
        loc = _resolve_parcel_location(parcel)
        if loc is None:
            return JSONResponse(
                {"error": "Parcel has no resolvable location"},
                status_code=400,
            )
        parcel_lon, parcel_lat = loc

        # 3. Get parcel display name
        name_attr = parcel.get("name", {})
        parcel_name = (
            name_attr.get("value", "")
            if isinstance(name_attr, dict)
            else str(name_attr or "")
        )

        # 4. Fetch forecast from Open-Meteo
        from datetime import datetime, timedelta

        today = datetime.utcnow().strftime("%Y-%m-%d")
        end = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d")

        params = {
            "latitude": parcel_lat,
            "longitude": parcel_lon,
            "start_date": today,
            "end_date": end,
            "daily": [
                "temperature_2m_max",
                "temperature_2m_min",
                "weather_code",
                "precipitation_sum",
                "precipitation_probability_max",
                "wind_speed_10m_max",
                "wind_direction_10m_dominant",
                "et0_fao_evapotranspiration",
                "shortwave_radiation_sum",
            ],
            "timezone": "auto",
        }

        resp = requests.get(
            f"{settings.openmeteo_api_url}/forecast",
            params=with_models(params),
            timeout=10,
        )
        if resp.status_code != 200:
            return JSONResponse(
                {"error": f"Open-Meteo returned {resp.status_code}"},
                status_code=502,
            )

        raw = resp.json()
        daily = raw.get("daily", {})
        dates = daily.get("time", [])

        # 5. Build forecast response
        forecast = []
        for i, date_str in enumerate(dates):
            forecast.append(
                {
                    "date": date_str,
                    "temp_max": _safe_idx(daily, "temperature_2m_max", i),
                    "temp_min": _safe_idx(daily, "temperature_2m_min", i),
                    "weather_code": _safe_idx(daily, "weather_code", i),
                    "precip_mm": _safe_idx(daily, "precipitation_sum", i),
                    "precip_probability": _safe_idx(
                        daily, "precipitation_probability_max", i
                    ),
                    # Open-Meteo returns wind_speed_10m_max in km/h — convert to m/s
                    "wind_speed_ms": _div_if(
                        _safe_idx(daily, "wind_speed_10m_max", i), 3.6
                    ),
                    "wind_direction_deg": _safe_idx(
                        daily, "wind_direction_10m_dominant", i
                    ),
                    "eto_mm": _safe_idx(daily, "et0_fao_evapotranspiration", i),
                    "solar_rad_w_m2": _div_if(
                        _safe_idx(daily, "shortwave_radiation_sum", i), 0.0864
                    ),
                }
            )

        return {
            "parcel_id": parcel_id,
            "parcel_name": parcel_name or parcel_id,
            "coordinates": {"latitude": parcel_lat, "longitude": parcel_lon},
            "elevation_m": raw.get("elevation"),
            "forecast_days": days,
            "forecast": forecast,
            "source": "OPEN-METEO",
        }

    except requests.exceptions.Timeout:
        return JSONResponse(
            {"error": "Open-Meteo request timed out"}, status_code=504
        )
    except Exception as e:
        logger.error(f"Error in get_parcel_forecast: {e}", exc_info=True)
        return JSONResponse(
            {"error": "Failed to fetch parcel forecast"}, status_code=500
        )
