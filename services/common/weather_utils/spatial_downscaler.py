"""
Spatial Downscaler — corrects municipality-level weather for individual parcels.

Applies four corrections in cascade:
1. Altitude (environmental lapse rate, 6.5 C/km)
2. Aspect + slope (solar radiation adjustment, FAO tilted-surface model)
3. Inverse Distance Weighting (when multiple stations available)
4. Derived metric recalculation (Delta-T from corrected T + RH)

Without this module, a parcel at 1200m on a north-facing slope gets the
same weather data as one at 200m on a south-facing slope — up to 6 C
temperature error and 50% radiation error.
"""

import logging
import math
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

# Environmental lapse rate: temperature drop per meter of altitude gain (C/m)
# Source: WMO standard atmosphere, practical agrometeorology value
LAPSE_RATE_C_PER_M = 0.0065  # 6.5 C per 1000m

# Dry adiabatic lapse rate for hot/dry conditions (C/m) — fallback
DRY_LAPSE_RATE_C_PER_M = 0.0098  # 9.8 C per 1000m

# Earth axial tilt (radians) for solar declination
EARTH_TILT_RAD = math.radians(23.45)


def _haversine_distance_km(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Haversine distance in kilometers between two lat/lon points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(
        math.radians(lat2)
    ) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _solar_declination(doy: int) -> float:
    """Solar declination angle in radians for a given day of year (1-366)."""
    return EARTH_TILT_RAD * math.sin(2 * math.pi * (284 + doy) / 365.0)


def _sunset_hour_angle(lat_rad: float, decl_rad: float) -> float:
    """Sunset hour angle in radians. Returns pi for polar day, 0 for polar night."""
    cos_omega = -math.tan(lat_rad) * math.tan(decl_rad)
    cos_omega = max(-1.0, min(1.0, cos_omega))
    return math.acos(cos_omega)


def _extraterrestrial_radiation(lat_rad: float, decl_rad: float, omega_s: float) -> float:
    """Extraterrestrial daily radiation (MJ/m2/day)."""
    G_sc = 0.0820  # solar constant in MJ/m2/min
    d_r = 1 + 0.033 * math.cos(2 * math.pi * 1 / 365.0)  # inverse relative distance
    return (
        (24 * 60 / math.pi)
        * G_sc
        * d_r
        * (omega_s * math.sin(lat_rad) * math.sin(decl_rad)
           + math.cos(lat_rad) * math.cos(decl_rad) * math.sin(omega_s))
    )


def correct_temperature_altitude(
    temp_celsius: float,
    station_altitude_m: float,
    parcel_altitude_m: float,
) -> float:
    """
    Adjust temperature for altitude difference using environmental lapse rate.

    T_parcel = T_station - LAPSE_RATE * (alt_parcel - alt_station)

    Positive delta means parcel is higher → colder.
    """
    delta_alt = parcel_altitude_m - station_altitude_m
    return temp_celsius - LAPSE_RATE_C_PER_M * delta_alt


def correct_solar_radiation_aspect(
    solar_rad_w_m2: Optional[float],
    parcel_lat: float,
    aspect_deg: float,
    slope_deg: float,
    doy: int,
) -> Optional[float]:
    """
    Adjust solar radiation for terrain aspect and slope.

    Uses the ratio of radiation on a tilted surface to radiation on a flat
    surface (FAO approach), derived from the sunset hour angle on the slope.

    Args:
        solar_rad_w_m2: Global horizontal irradiance at station (W/m2)
        parcel_lat: Parcel latitude
        aspect_deg: Slope aspect in degrees (0=N, 90=E, 180=S, 270=W)
        slope_deg: Slope steepness in degrees (0=flat, 90=vertical)
        doy: Day of year (1-366)

    Returns:
        Corrected radiation in W/m2, or original value if any input is invalid.
    """
    if solar_rad_w_m2 is None or solar_rad_w_m2 < 0:
        return solar_rad_w_m2
    if slope_deg < 1.0:
        return solar_rad_w_m2  # flat terrain, no correction

    try:
        lat_rad = math.radians(parcel_lat)
        slope_rad = math.radians(slope_deg)
        aspect_rad = math.radians(aspect_deg)
        decl = _solar_declination(doy)

        # Flat-surface sunset hour angle
        omega_s_flat = _sunset_hour_angle(lat_rad, decl)

        # Sunset hour angle on the tilted surface
        # Simplified FAO approach: adjust by the minimum of flat sunset and
        # the angle where the sun sets behind the slope
        tan_term = math.tan(lat_rad - slope_rad * math.cos(aspect_rad)) * math.tan(decl)
        tan_term = max(-1.0, min(1.0, tan_term))
        omega_s_slope = math.acos(-tan_term)
        omega_s = min(omega_s_flat, omega_s_slope)

        if omega_s <= 0:
            return 0.0  # no direct sun on this slope today

        # Extraterrestrial radiation on flat surface
        Ra_flat = _extraterrestrial_radiation(lat_rad, decl, omega_s_flat)

        # Extraterrestrial radiation on tilted surface (simplified)
        # Ratio of tilted to flat is approximately omega_s_slope / omega_s_flat
        # weighted by the slope angle relative to the sun
        if Ra_flat > 0:
            # Radiation on slope using the geometric factor
            Ra_slope = _extraterrestrial_radiation(
                lat_rad - slope_rad * math.cos(aspect_rad), decl, omega_s
            )
            ratio = Ra_slope / Ra_flat if Ra_flat > 0 else 1.0
        else:
            ratio = 1.0

        # Clamp to physically reasonable range
        # North-facing steep slopes in winter can get <20% of flat radiation
        # South-facing slopes in winter can get >120% of flat radiation
        ratio = max(0.1, min(2.0, ratio))

        return round(solar_rad_w_m2 * ratio, 1)

    except (ValueError, OverflowError, ZeroDivisionError):
        return solar_rad_w_m2


def interpolate_idw(
    parcel_lat: float,
    parcel_lon: float,
    stations: List[Dict[str, Any]],
    power: float = 2.0,
) -> Dict[str, float]:
    """
    Inverse Distance Weighting interpolation from multiple weather stations.

    value_parcel = Σ(value_i / d_i^power) / Σ(1 / d_i^power)

    Stations closer to the parcel have exponentially more weight.

    Args:
        parcel_lat, parcel_lon: Target parcel coordinates
        stations: List of dicts, each with 'latitude', 'longitude', and
                  numeric weather fields (temp_avg, humidity_avg, etc.)
        power: Distance exponent (2 = inverse square, standard)

    Returns:
        Dict with interpolated values for all numeric weather fields.
    """
    if not stations:
        return {}

    weights = []
    for s in stations:
        d = _haversine_distance_km(parcel_lat, parcel_lon,
                                   s.get('latitude', 0), s.get('longitude', 0))
        d = max(d, 0.1)  # avoid division by zero for co-located station
        weights.append(1.0 / (d ** power))

    total_weight = sum(weights)
    if total_weight <= 0:
        return {}

    # Fields to interpolate
    fields = [
        'temp_avg', 'temp_min', 'temp_max', 'humidity_avg', 'precip_mm',
        'wind_speed_ms', 'pressure_hpa', 'eto_mm',
        'solar_rad_w_m2', 'solar_rad_ghi_w_m2', 'solar_rad_dni_w_m2',
        'soil_moisture_0_10cm', 'soil_moisture_10_40cm',
    ]

    result = {}
    for field in fields:
        values = []
        for i, s in enumerate(stations):
            val = s.get(field)
            if val is not None:
                values.append((val, weights[i]))

        if values:
            weighted_sum = sum(v * w for v, w in values)
            weight_sum = sum(w for _, w in values)
            result[field] = round(weighted_sum / weight_sum, 4) if weight_sum > 0 else None

    return result


def recalculate_delta_t(temp_celsius: float, relative_humidity_percent: float) -> float:
    """
    Recalculate Delta-T from corrected temperature and humidity.

    Uses the Magnus psychrometric formula (same as MetricsCalculator).
    """
    try:
        if relative_humidity_percent < 0 or relative_humidity_percent > 100:
            return 0.0

        # Saturation vapor pressure
        e_sat = 6.112 * math.exp((17.67 * temp_celsius) / (temp_celsius + 243.5))
        # Actual vapor pressure
        e_act = e_sat * (relative_humidity_percent / 100.0)
        # Dew point
        vapor_ratio = max(e_act / 6.112, 0.01)
        dew_point = (243.5 * math.log(vapor_ratio)) / (17.67 - math.log(vapor_ratio))
        # Wet bulb approximation
        wet_bulb = temp_celsius - (temp_celsius - dew_point) * 0.4
        # Delta-T
        return round(temp_celsius - wet_bulb, 2)
    except (ValueError, OverflowError, ZeroDivisionError):
        return 0.0


def downscale_for_parcel(
    weather_data: Dict[str, Any],
    parcel_lat: float,
    parcel_lon: float,
    parcel_altitude_m: float,
    station_altitude_m: float,
    parcel_aspect_deg: float = 0.0,
    parcel_slope_deg: float = 0.0,
    doy: Optional[int] = None,
    nearby_stations: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Downscale weather data from station/municipality level to a specific parcel.

    Applies corrections in order:
    1. IDW interpolation (if multiple stations available)
    2. Altitude correction on temperature fields
    3. Aspect/slope correction on solar radiation
    4. Delta-T recalculation from corrected T and RH

    Args:
        weather_data: Weather observation from the primary station/municipality.
                      Expected keys: temp_avg, temp_min, temp_max, humidity_avg,
                      solar_rad_w_m2, wind_speed_ms, precip_mm, delta_t, etc.
        parcel_lat, parcel_lon: Parcel centroid coordinates
        parcel_altitude_m: Parcel altitude in meters above sea level
        station_altitude_m: Weather station/municipality altitude in meters
        parcel_aspect_deg: Slope aspect (0=N, 180=S) — from DEM
        parcel_slope_deg: Slope steepness in degrees — from DEM
        doy: Day of year for solar calculations (defaults to current)
        nearby_stations: Optional list of additional station data for IDW.

    Returns:
        Weather data dict with all temperature, radiation, and delta_t fields
        corrected for the parcel's specific location and terrain.
    """
    if doy is None:
        doy = datetime.utcnow().timetuple().tm_yday

    result = dict(weather_data)

    # Step 1: IDW interpolation from multiple stations if available
    if nearby_stations and len(nearby_stations) > 0:
        all_stations = [weather_data] + nearby_stations
        idw_values = interpolate_idw(parcel_lat, parcel_lon, all_stations)
        for field, value in idw_values.items():
            if value is not None:
                result[field] = value
        logger.debug(
            f"IDW interpolation from {len(all_stations)} stations: "
            f"T_avg={result.get('temp_avg')}"
        )

    # Step 2: Altitude correction on temperatures
    alt_delta = parcel_altitude_m - station_altitude_m
    if abs(alt_delta) > 10:  # only correct if meaningful difference (>10m)
        for temp_field in ['temp_avg', 'temp_min', 'temp_max']:
            val = result.get(temp_field)
            if val is not None:
                result[temp_field] = round(
                    correct_temperature_altitude(val, station_altitude_m, parcel_altitude_m), 2
                )

        if abs(alt_delta) > 50:
            logger.debug(
                f"Altitude correction: {alt_delta:+.0f}m → "
                f"T_correction={-LAPSE_RATE_C_PER_M * alt_delta:+.1f}C"
            )

    # Step 3: Solar radiation correction for aspect/slope
    if parcel_slope_deg >= 1.0:
        corrected_rad = correct_solar_radiation_aspect(
            result.get('solar_rad_w_m2'),
            parcel_lat, parcel_aspect_deg, parcel_slope_deg, doy,
        )
        if corrected_rad is not None:
            result['solar_rad_w_m2'] = corrected_rad
            # Also correct GHI and DNI if present
            if result.get('solar_rad_ghi_w_m2') is not None:
                ratio = corrected_rad / max(weather_data.get('solar_rad_w_m2', 1), 1)
                result['solar_rad_ghi_w_m2'] = round(
                    result['solar_rad_ghi_w_m2'] * ratio, 1
                )
            if result.get('solar_rad_dni_w_m2') is not None:
                result['solar_rad_dni_w_m2'] = round(
                    result['solar_rad_dni_w_m2'] * ratio, 1
                )

    # Step 4: Recalculate Delta-T from corrected T and RH
    temp_avg = result.get('temp_avg')
    humidity = result.get('humidity_avg')
    if temp_avg is not None and humidity is not None:
        result['delta_t'] = recalculate_delta_t(temp_avg, humidity)

    return result


def extract_parcel_terrain(parcel_entity: Dict[str, Any]) -> Tuple[float, float, float]:
    """
    Extract altitude, aspect, and slope from a parcel entity.

    Returns (altitude_m, aspect_deg, slope_deg).
    Defaults to (0, 0, 0) if terrain data is not attached to the entity.
    """
    altitude = 0.0
    aspect = 0.0
    slope = 0.0

    # Check for elevation property (from EU Elevation module or survey)
    elev = parcel_entity.get('elevation', {})
    if isinstance(elev, dict):
        altitude = float(elev.get('value', 0) or 0)

    # Check for terrain aspect
    terrain_aspect = parcel_entity.get('terrainAspect', {})
    if isinstance(terrain_aspect, dict):
        aspect = float(terrain_aspect.get('value', 0) or 0)

    # Check for terrain slope
    terrain_slope = parcel_entity.get('terrainSlope', {})
    if isinstance(terrain_slope, dict):
        slope = float(terrain_slope.get('value', 0) or 0)

    return altitude, aspect, slope
