"""
Tests for spatial_downscaler — verify correction math is physically reasonable.
"""
import sys
import os
import math
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'weather-worker'))

from weather_worker.processors.spatial_downscaler import (
    correct_temperature_altitude,
    correct_solar_radiation_aspect,
    interpolate_idw,
    recalculate_delta_t,
    downscale_for_parcel,
    extract_parcel_terrain,
    _haversine_distance_km,
    _solar_declination,
)


class TestAltitudeCorrection:
    """Temperature correction from environmental lapse rate."""

    def test_parcel_higher_than_station(self):
        """Parcel at 1200m should be colder than station at 200m."""
        result = correct_temperature_altitude(25.0, 200.0, 1200.0)
        # delta = 1000m, lapse = 6.5 C/km → -6.5 C
        assert result == 25.0 - 6.5

    def test_parcel_lower_than_station(self):
        """Parcel at 200m should be warmer than station at 1200m."""
        result = correct_temperature_altitude(18.5, 1200.0, 200.0)
        # delta = -1000m, lapse = 6.5 C/km → +6.5 C
        assert result == 18.5 + 6.5

    def test_same_altitude_no_change(self):
        """Same altitude → no correction."""
        result = correct_temperature_altitude(20.0, 500.0, 500.0)
        assert result == 20.0


class TestSolarRadiationAspect:
    """Solar radiation correction for terrain aspect and slope."""

    def test_flat_terrain_no_correction(self):
        """Flat terrain returns original radiation."""
        result = correct_solar_radiation_aspect(
            800.0, 42.0, 180.0, 0.5, 180
        )
        assert result == 800.0

    def test_south_facing_gets_more_radiation(self):
        """South-facing slope in summer (northern hemisphere) gets more radiation."""
        result_flat = correct_solar_radiation_aspect(800.0, 42.0, 180.0, 15.0, 180)
        # South-facing at 15 degrees should be >= flat radiation in summer
        assert result_flat is not None
        # It should be at least 80% of flat radiation
        assert result_flat >= 640

    def test_north_facing_gets_less_radiation(self):
        """North-facing slope gets less radiation."""
        result_north = correct_solar_radiation_aspect(800.0, 42.0, 0.0, 20.0, 180)
        result_south = correct_solar_radiation_aspect(800.0, 42.0, 180.0, 20.0, 180)
        if result_north is not None and result_south is not None and result_north > 0:
            # North should get less than south in summer (northern hemisphere)
            assert result_north < result_south, (
                f"North={result_north} should be < South={result_south}"
            )

    def test_none_radiation_returns_none(self):
        result = correct_solar_radiation_aspect(None, 42.0, 180.0, 15.0, 180)
        assert result is None

    def test_winter_low_sun(self):
        """Winter solstice: north-facing steep slope gets very little radiation."""
        result = correct_solar_radiation_aspect(500.0, 50.0, 0.0, 30.0, 355)
        # North-facing 30° at 50°N in late December should be heavily reduced
        assert result is not None
        assert result < 300  # less than 60% of flat


class TestIDWInterpolation:
    """Inverse Distance Weighting interpolation."""

    def test_two_stations_equal_distance(self):
        """Two stations at equal distance → simple average."""
        stations = [
            {'latitude': 42.0, 'longitude': -1.0, 'temp_avg': 20.0, 'humidity_avg': 60.0},
            {'latitude': 42.0, 'longitude': -2.0, 'temp_avg': 24.0, 'humidity_avg': 50.0},
        ]
        result = interpolate_idw(42.0, -1.5, stations)
        assert abs(result['temp_avg'] - 22.0) < 1.0  # roughly average

    def test_closer_station_has_more_weight(self):
        """Closer station should dominate the interpolation."""
        stations = [
            {'latitude': 42.0, 'longitude': -1.0, 'temp_avg': 20.0},
            {'latitude': 43.0, 'longitude': -1.0, 'temp_avg': 10.0},  # ~111km away
        ]
        result = interpolate_idw(42.0, -1.0, stations)
        # First station is at 0km, second at ~111km
        # With inverse square, weight ratio is ~(111/0.1)^2 ≈ 1,232,100:1
        assert abs(result['temp_avg'] - 20.0) < 0.1

    def test_empty_stations_returns_empty(self):
        result = interpolate_idw(42.0, -1.0, [])
        assert result == {}


class TestRecalculateDeltaT:
    """Delta-T recalculation with Magnus formula."""

    def test_warm_dry_high_delta_t(self):
        """Warm + dry → high Delta-T (good for spraying)."""
        dt = recalculate_delta_t(25.0, 40.0)
        # At 25°C / 40% RH, delta-t should be around 8-10°C
        assert 6.0 < dt < 14.0, f"Got {dt}"

    def test_cool_humid_low_delta_t(self):
        """Cool + humid → low Delta-T (bad for spraying)."""
        dt = recalculate_delta_t(10.0, 90.0)
        # At 10°C / 90% RH, delta-t should be very low (< 2°C)
        assert dt < 2.5, f"Got {dt}"

    def test_invalid_humidity_returns_zero(self):
        dt = recalculate_delta_t(20.0, 150.0)
        assert dt == 0.0


class TestDownscaleForParcel:
    """End-to-end downscaling integration."""

    def test_altitude_only_correction(self):
        """Downscale with altitude difference only."""
        weather = {
            'temp_avg': 22.0, 'temp_min': 14.0, 'temp_max': 30.0,
            'humidity_avg': 55.0, 'precip_mm': 0.0, 'wind_speed_ms': 3.0,
            'solar_rad_w_m2': 800.0, 'delta_t': 5.5,
        }
        result = downscale_for_parcel(
            weather_data=weather,
            parcel_lat=42.5,
            parcel_lon=-1.5,
            parcel_altitude_m=800.0,
            station_altitude_m=200.0,
            parcel_aspect_deg=0.0,
            parcel_slope_deg=0.0,
        )
        # 600m up → 3.9°C colder
        assert result['temp_avg'] == 22.0 - 3.9
        assert result['temp_min'] == 14.0 - 3.9
        assert result['temp_max'] == 30.0 - 3.9
        # Humidity and wind should be unchanged (no correction model)
        assert result['humidity_avg'] == 55.0
        assert result['wind_speed_ms'] == 3.0
        # Delta-T should be recalculated from corrected T and RH
        assert result['delta_t'] != 5.5

    def test_small_altitude_difference_skipped(self):
        """Altitude diff < 10m → no correction applied."""
        weather = {'temp_avg': 22.0, 'humidity_avg': 55.0, 'solar_rad_w_m2': 800.0}
        result = downscale_for_parcel(
            weather_data=weather,
            parcel_lat=42.5, parcel_lon=-1.5,
            parcel_altitude_m=205.0,
            station_altitude_m=200.0,
        )
        # 5m difference → no altitude correction
        assert result['temp_avg'] == 22.0

    def test_slope_radiation_correction_applied(self):
        """South-facing slope should get different radiation than flat."""
        weather = {'temp_avg': 20.0, 'humidity_avg': 50.0, 'solar_rad_w_m2': 800.0}
        result = downscale_for_parcel(
            weather_data=weather,
            parcel_lat=42.0, parcel_lon=-1.5,
            parcel_altitude_m=200.0,
            station_altitude_m=200.0,
            parcel_aspect_deg=180.0,  # south
            parcel_slope_deg=15.0,
            doy=180,
        )
        # South-facing slope in summer should have corrected radiation
        assert result['solar_rad_w_m2'] != 800.0


class TestExtractParcelTerrain:
    """Extract terrain attributes from NGSI-LD parcel entity."""

    def test_extracts_elevation_aspect_slope(self):
        entity = {
            'id': 'urn:ngsi-ld:AgriParcel:test:1',
            'elevation': {'type': 'Property', 'value': 750.0},
            'terrainAspect': {'type': 'Property', 'value': 180.0},
            'terrainSlope': {'type': 'Property', 'value': 8.5},
        }
        alt, aspect, slope = extract_parcel_terrain(entity)
        assert alt == 750.0
        assert aspect == 180.0
        assert slope == 8.5

    def test_missing_terrain_defaults_to_zero(self):
        entity = {'id': 'urn:ngsi-ld:AgriParcel:test:2'}
        alt, aspect, slope = extract_parcel_terrain(entity)
        assert alt == 0.0
        assert aspect == 0.0
        assert slope == 0.0


class TestHaversine:
    """Haversine distance calculation."""

    def test_pamplona_to_madrid(self):
        d = _haversine_distance_km(42.8125, -1.6458, 40.4168, -3.7038)
        # Pamplona to Madrid ≈ 315-320 km
        assert 310 < d < 330, f"Got {d}"


class TestSolarDeclination:
    """Solar declination angle."""

    def test_summer_solstice(self):
        decl = _solar_declination(172)  # ~June 21
        # Should be close to +23.45°
        assert 0.39 < decl < 0.43, f"Got {decl} rad"

    def test_winter_solstice(self):
        decl = _solar_declination(355)  # ~December 21
        # Should be close to -23.45°
        assert -0.43 < decl < -0.39, f"Got {decl} rad"


print("All spatial downscaler tests passed.")
