"""Tests for weather-api agro_status — semaphores, PTFs, fusion (pure logic).

Characterization suite for app/services/agro_status.py (weather-api).
Delta-T is controlled via monkeypatching the unified psychrometrics function
(its math is already covered by test_psychrometrics.py).
"""

import sys
import os
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "weather-api"))

from app.services.agro_status import (  # noqa: E402
    _calc_water_balance,
    _crop_spraying_sensitivity,
    _estimate_recovery_hours,
    _extract_crop_stage,
    _extract_float,
    _saxton_rawls_2006,
    _scs_hydrologic_group,
    _texture_workability,
    _usda_texture_class,
    calculate_agro_status,
)

PARCEL = {
    "id": "urn:ngsi-ld:AgriParcel:t1:p1",
    "type": "AgriParcel",
    "name": {"type": "Property", "value": "Viña Norte"},
}


def make_observation(**overrides):
    obs = {
        "observed_at": datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc),
        "temp_avg": 22.0,
        "temp_min": 14.0,
        "temp_max": 27.0,
        "humidity_avg": 55.0,
        "precip_mm": 0.0,
        "precip_probability": 10,
        "wind_speed_ms": 3.0,  # 10.8 km/h — inside optimal band
        "wind_gusts_ms": 4.0,  # 14.4 km/h — below gust threshold
        "wind_direction_deg": 180,
        "pressure_hpa": 1013.0,
        "eto_mm": 4.0,
    }
    obs.update(overrides)
    return obs


def run_agro(obs_overrides=None, *, weather_3d=None, sensor_data=None,
             soil_texture=None, parcel=None, **kwargs):
    return calculate_agro_status(
        lat=42.49,
        lon=-1.64,
        parcel_entity=parcel or PARCEL,
        weather_observation=make_observation(**(obs_overrides or {})),
        weather_3d=weather_3d,
        sensor_data=sensor_data,
        soil_texture=soil_texture,
        **kwargs,
    )


def make_sensor(payload):
    return {
        "external_id": "sensor-1",
        "name": "Estación parcela",
        "distance_m": 250.0,
        "observed_at": "2026-06-11T10:00:00Z",
        "payload": dict(payload),
    }


@pytest.fixture
def delta_t(monkeypatch):
    """Pin the Delta-T the orchestrator sees (psychrometrics is unit-tested elsewhere)."""

    def _set(value):
        monkeypatch.setattr(
            "weather_utils.psychrometrics.calculate_delta_t",
            lambda t, h: value,
        )

    return _set


# ---------------------------------------------------------------------------
# Saxton & Rawls (2006) pedotransfer functions
# ---------------------------------------------------------------------------
class TestSaxtonRawls:
    @pytest.mark.parametrize(
        "sand,clay",
        [(85.0, 5.0), (10.0, 60.0), (40.0, 20.0)],
        ids=["sand", "clay", "loam"],
    )
    def test_physical_bounds(self, sand, clay):
        ptf = _saxton_rawls_2006(sand, clay, 1.0)
        assert 0.0 < ptf["wilting_point"] < ptf["field_capacity"] < 0.6
        assert ptf["ksat"] > 0.0

    def test_sand_drains_faster_than_clay(self):
        sand = _saxton_rawls_2006(85.0, 5.0, 1.0)
        clay = _saxton_rawls_2006(10.0, 60.0, 1.0)
        assert sand["ksat"] > clay["ksat"]

    def test_clay_holds_more_water_than_sand(self):
        sand = _saxton_rawls_2006(85.0, 5.0, 1.0)
        clay = _saxton_rawls_2006(10.0, 60.0, 1.0)
        assert clay["field_capacity"] > sand["field_capacity"]
        assert clay["wilting_point"] > sand["wilting_point"]

    @pytest.mark.parametrize(
        "sand,clay,lo,hi",
        [
            (88.0, 5.0, 50.0, 200.0),  # sand — paper Table 3 ≈ 108 mm/h
            (40.0, 20.0, 3.0, 20.0),  # loam ≈ 13 mm/h
            (20.0, 15.0, 2.0, 15.0),  # silt loam ≈ 7 mm/h
            (20.0, 55.0, 0.1, 3.0),  # clay ≈ 1.5 mm/h
        ],
        ids=["sand", "loam", "silt_loam", "clay"],
    )
    def test_ksat_reference_ranges(self, sand, clay, lo, hi):
        """Regression: Ksat must be non-zero and in the ballpark of Saxton &
        Rawls 2006 Table 3. The pre-fix formula returned 0.0 for every
        non-sandy texture, forcing hydrologic group D and inflated
        post-rain recovery hours."""
        ksat = _saxton_rawls_2006(sand, clay, 1.0)["ksat"]
        assert lo <= ksat <= hi


# ---------------------------------------------------------------------------
# USDA texture triangle
# ---------------------------------------------------------------------------
class TestUsdaTextureClass:
    @pytest.mark.parametrize(
        "sand,clay,expected",
        [
            (92.0, 3.0, "sand"),
            (82.0, 6.0, "loamy_sand"),
            (65.0, 10.0, "sandy_loam"),
            (40.0, 20.0, "loam"),
            (20.0, 15.0, "silt_loam"),
            (10.0, 5.0, "silt"),
            (60.0, 25.0, "sandy_clay_loam"),
            (35.0, 33.0, "clay_loam"),
            (10.0, 33.0, "silty_clay_loam"),
            (50.0, 40.0, "sandy_clay"),
            (5.0, 45.0, "silty_clay"),
            (20.0, 50.0, "clay"),
        ],
    )
    def test_canonical_points(self, sand, clay, expected):
        assert _usda_texture_class(sand, clay) == expected


# ---------------------------------------------------------------------------
# SCS hydrologic groups + recovery hours
# ---------------------------------------------------------------------------
class TestScsHydrologicGroup:
    @pytest.mark.parametrize(
        "ksat,expected",
        [(40.0, "A"), (36.0, "B"), (10.0, "B"), (3.6, "C"), (1.0, "C"),
         (0.36, "D"), (0.1, "D")],
    )
    def test_boundaries(self, ksat, expected):
        assert _scs_hydrologic_group(ksat) == expected


class TestRecoveryHours:
    @pytest.mark.parametrize(
        "group,base", [("A", 6), ("B", 18), ("C", 36), ("D", 60)]
    )
    def test_base_per_group(self, group, base):
        assert _estimate_recovery_hours(group, 0.0) == base

    def test_rain_extra_is_capped_at_48h(self):
        assert _estimate_recovery_hours("D", 200.0) == 60 + 48

    def test_unknown_group_defaults_to_24h(self):
        assert _estimate_recovery_hours("X", 0.0) == 24


# ---------------------------------------------------------------------------
# Texture-aware workability (direct)
# ---------------------------------------------------------------------------
class TestTextureWorkability:
    FC, WP = 0.32, 0.12  # margins → too_wet > 0.27, too_dry < 0.17

    @pytest.mark.parametrize(
        "moisture,expected",
        [(0.30, "too_wet"), (0.10, "too_dry"), (0.20, "optimal")],
    )
    def test_sensor_thresholds_relative_to_fc_pwp(self, moisture, expected):
        assert _texture_workability(moisture, self.FC, self.WP) == expected

    @pytest.mark.parametrize(
        "precip,humidity,expected",
        [(6.0, 50.0, "too_wet"), (0.0, 30.0, "too_dry"),
         (2.0, 60.0, "optimal"), (0.0, 60.0, "caution")],
    )
    def test_no_sensor_heuristic(self, precip, humidity, expected):
        assert _texture_workability(None, self.FC, self.WP, precip, humidity) == expected


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
class TestSmallHelpers:
    def test_water_balance(self):
        assert _calc_water_balance(10.0, 4.0) == 6.0
        assert _calc_water_balance(None, 4.0) is None
        assert _calc_water_balance(10.0, None) is None

    def test_extract_float_ngsild_attribute(self):
        assert _extract_float({"value": "3.5"}) == 3.5
        assert _extract_float({"value": "not-a-number"}) == 0.0
        assert _extract_float(7) == 7.0
        assert _extract_float(None, default=0.5) == 0.5

    def test_extract_crop_stage_normalizes(self):
        assert _extract_crop_stage({"cropStatus": {"value": " Flowering "}}) == "flowering"
        assert _extract_crop_stage({}) is None

    @pytest.mark.parametrize(
        "stage,expected",
        [("floracion plena", "high"), ("fruiting", "high"), ("espigado", "high"),
         ("vegetative", "normal"), (None, "normal")],
    )
    def test_crop_spraying_sensitivity(self, stage, expected):
        assert _crop_spraying_sensitivity(stage) == expected


# ---------------------------------------------------------------------------
# Spraying semaphore matrix
# ---------------------------------------------------------------------------
class TestSprayingSemaphore:
    def test_optimal(self, delta_t):
        delta_t(5.0)
        r = run_agro()
        assert r["semaphores"]["spraying"] == "optimal"
        assert r["metrics"]["spraying_reason"] is None

    def test_gusts_block_first(self, delta_t):
        delta_t(5.0)
        r = run_agro({"wind_gusts_ms": 8.0})  # 28.8 km/h > 25
        assert r["semaphores"]["spraying"] == "not_suitable"
        assert r["metrics"]["spraying_reason"] == "wind_gusts"

    def test_thermal_inversion_blocks(self, delta_t):
        delta_t(5.0)
        # day-night ΔT = 17 > 15 and wind 3.6 km/h < 5
        r = run_agro({"temp_min": 5.0, "wind_speed_ms": 1.0})
        assert r["semaphores"]["spraying"] == "not_suitable"
        assert r["metrics"]["spraying_reason"] == "inversion_risk"
        assert r["inversion_risk"] is True

    def test_wind_speed_blocks(self, delta_t):
        delta_t(5.0)
        r = run_agro({"wind_speed_ms": 6.0})  # 21.6 km/h > 20
        assert r["semaphores"]["spraying"] == "not_suitable"
        assert r["metrics"]["spraying_reason"] == "wind_speed"

    def test_high_delta_t_blocks(self, delta_t):
        delta_t(12.0)
        r = run_agro()
        assert r["semaphores"]["spraying"] == "not_suitable"
        assert r["metrics"]["spraying_reason"] == "delta_t"

    def test_precipitation_blocks(self, delta_t):
        delta_t(9.0)  # outside optimal band but not > 10
        r = run_agro({"precip_mm": 1.0})
        assert r["semaphores"]["spraying"] == "not_suitable"
        assert r["metrics"]["spraying_reason"] == "precipitation"

    def test_caution_band(self, delta_t):
        delta_t(5.0)
        r = run_agro({"wind_speed_ms": 4.5})  # 16.2 km/h: not optimal, not blocked
        assert r["semaphores"]["spraying"] == "caution"

    def test_rain_risk_degrades_optimal(self, delta_t):
        delta_t(5.0)
        r = run_agro({"precip_probability": 60})
        assert r["semaphores"]["spraying"] == "caution"
        assert r["metrics"]["spraying_reason"] == "rain_risk"

    def test_sensitive_crop_stage_degrades_optimal(self, delta_t):
        delta_t(5.0)
        parcel = dict(PARCEL, cropStatus={"type": "Property", "value": "Flowering"})
        r = run_agro(parcel=parcel)
        assert r["semaphores"]["spraying"] == "caution"
        assert r["metrics"]["spraying_reason"] == "crop_sensitive"
        assert r["crop"] == {"stage": "flowering", "spraying_sensitivity": "high"}

    def test_unknown_without_temperature(self):
        r = run_agro({"temp_avg": None})
        assert r["semaphores"]["spraying"] == "unknown"


# ---------------------------------------------------------------------------
# Workability + irrigation semaphores (orchestrator level)
# ---------------------------------------------------------------------------
class TestWorkabilitySemaphore:
    @pytest.mark.parametrize(
        "moisture,expected",
        [(20, "optimal"), (30, "too_wet"), (5, "too_dry"), (12, "caution")],
    )
    def test_generic_sensor_thresholds(self, delta_t, moisture, expected):
        delta_t(5.0)
        r = run_agro(sensor_data=make_sensor({"soil_moisture": moisture}))
        assert r["semaphores"]["workability"] == expected

    def test_no_sensor_heuristic_too_wet(self, delta_t):
        delta_t(5.0)
        r = run_agro(weather_3d=[{"precip_mm": 6.0, "eto_mm": 1.0}])
        assert r["semaphores"]["workability"] == "too_wet"

    def test_precomputed_ptf_takes_priority(self, delta_t):
        delta_t(5.0)
        soil = {"sand": 40.0, "clay": 20.0, "field_capacity": 0.32,
                "wilting_point": 0.12, "ksat": 2.0, "texture_class": "loam",
                "source": "soil-module"}
        r = run_agro(sensor_data=make_sensor({"soil_moisture": 0.20}),
                     soil_texture=soil)
        assert r["semaphores"]["workability"] == "optimal"
        # Pre-computed values used verbatim — no Saxton-Rawls recompute
        assert r["soil"]["field_capacity"] == 0.32
        assert r["soil"]["wilting_point"] == 0.12
        assert r["soil"]["texture_applied"] is True
        assert r["soil"]["texture_class"] == "loam"

    def test_on_the_fly_ptf_from_raw_texture(self, delta_t):
        delta_t(5.0)
        soil = {"sand": 40.0, "clay": 20.0, "organic_carbon": 1.0}
        r = run_agro(sensor_data=make_sensor({"soil_moisture": 0.55}),
                     soil_texture=soil)
        assert r["soil"]["texture_applied"] is True
        assert r["soil"]["field_capacity"] is not None
        assert r["semaphores"]["workability"] == "too_wet"  # 0.55 above any FC

    def test_recovery_hours_when_too_wet_after_rain(self, delta_t):
        delta_t(5.0)
        soil = {"sand": 40.0, "clay": 20.0, "field_capacity": 0.32,
                "wilting_point": 0.12, "ksat": 2.0}
        r = run_agro(
            sensor_data=make_sensor({"soil_moisture": 0.30}),
            soil_texture=soil,
            weather_3d=[{"precip_mm": 10.0, "eto_mm": 2.0}],
        )
        assert r["semaphores"]["workability"] == "too_wet"
        assert r["soil"]["hydrologic_group"] == "C"  # ksat 2.0 → group C
        assert r["soil"]["recovery_hours"] == 36 + 5  # base C + 10mm * 0.5


class TestIrrigationSemaphore:
    @pytest.mark.parametrize(
        "precip,eto,expected",
        [(10.0, 4.0, "satisfied"), (2.0, 5.0, "alert"), (0.0, 6.0, "deficit")],
    )
    def test_water_balance_bands(self, delta_t, precip, eto, expected):
        delta_t(5.0)
        r = run_agro(weather_3d=[{"precip_mm": precip, "eto_mm": eto}])
        assert r["semaphores"]["irrigation"] == expected
        assert r["metrics"]["water_balance"] == round(precip - eto, 2)

    def test_unknown_without_eto(self, delta_t):
        delta_t(5.0)
        r = run_agro(weather_3d=[{"precip_mm": 1.0, "eto_mm": None}])
        assert r["semaphores"]["irrigation"] == "unknown"


# ---------------------------------------------------------------------------
# Sensor fusion
# ---------------------------------------------------------------------------
class TestSensorFusion:
    def test_sensor_overrides_observation_per_field(self, delta_t):
        delta_t(5.0)
        r = run_agro(sensor_data=make_sensor({"temperature": 19.5, "humidity": 70.0}))
        assert r["weather"]["temperature"] == 19.5
        assert r["weather"]["humidity"] == 70.0
        assert r["weather"]["sources"]["temperature"] == "SENSOR_REAL"
        assert r["weather"]["sources"]["humidity"] == "SENSOR_REAL"
        # Fields the sensor doesn't report stay on the observation
        assert r["weather"]["sources"]["wind_speed"] == "WEATHER-OBS"
        assert r["source_confidence"] == "SENSOR_REAL"
        assert r["weather"]["sensor"]["external_id"] == "sensor-1"
        assert r["weather"]["sensor"]["distance_m"] == 250.0


# ---------------------------------------------------------------------------
# Spatial downscaling wiring
# ---------------------------------------------------------------------------
class TestDownscaling:
    def test_applied_on_altitude_difference(self, monkeypatch, delta_t):
        delta_t(5.0)

        def fake_downscale(weather_data, **kwargs):
            out = dict(weather_data)
            out["temp_avg"] = weather_data["temp_avg"] - 2.0
            return out

        monkeypatch.setattr(
            "weather_utils.spatial_downscaler.downscale_for_parcel",
            fake_downscale,
        )
        r = run_agro(parcel_altitude_m=600.0, station_altitude_m=300.0)
        assert r["downscaling"] == "applied"
        assert r["weather"]["temperature"] == 20.0

    def test_skipped_when_terrain_matches_station(self, delta_t):
        delta_t(5.0)
        r = run_agro()  # altitudes 0/0, slope 0 → no trigger
        assert r["downscaling"] == "unavailable"

    def test_downscaler_error_is_non_fatal(self, monkeypatch, delta_t):
        delta_t(5.0)

        def boom(**kwargs):
            raise RuntimeError("downscaler exploded")

        monkeypatch.setattr(
            "weather_utils.spatial_downscaler.downscale_for_parcel", boom
        )
        r = run_agro(parcel_altitude_m=600.0, station_altitude_m=300.0)
        assert r["downscaling"] == "unavailable"
        assert r["semaphores"]["spraying"] == "optimal"  # calc still completed


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------
class TestOutputShape:
    def test_minimal_output(self, delta_t):
        delta_t(5.0)
        r = run_agro()
        assert r["parcel_id"] == PARCEL["id"]
        assert r["parcel_name"] == "Viña Norte"
        assert r["centroid"] == {"latitude": 42.49, "longitude": -1.64}
        assert r["soil"] is None
        assert r["crop"] is None
        assert r["source_confidence"] == "WEATHER-OBS"
        # ISO timestamp parseable
        datetime.fromisoformat(r["timestamp"])

    def test_unnamed_parcel_fallback(self, delta_t):
        delta_t(5.0)
        r = run_agro(parcel={"id": "urn:ngsi-ld:AgriParcel:t1:p2", "type": "AgriParcel"})
        assert r["parcel_name"] == "Unnamed"
