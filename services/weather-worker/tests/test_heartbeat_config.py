"""Tests for weather-worker heartbeat threshold config: derived defaults +
env overrides.

Follows the same monkeypatch-and-reload pattern as
test_startup_validation.py, since WeatherWorkerConfig reads env vars at
class-definition (import) time.
"""

import importlib
import os
import sys

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # services/weather-worker
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))  # services/

import weather_worker.config as config_module

_ENV_KEYS = (
    "PARCEL_ENGINE_INTERVAL_HOURS",
    "AEMET_ALERTS_INTERVAL_HOURS",
    "PARCEL_ENGINE_HEARTBEAT_MAX_STALENESS_SECONDS",
    "METEOALARM_HEARTBEAT_MAX_STALENESS_SECONDS",
    "MAIN_LOOP_HEARTBEAT_MAX_STALENESS_SECONDS",
    "HEARTBEAT_STARTUP_GRACE_SECONDS",
    "METRICS_PORT",
    "HEALTH_PORT",
    "HEALTH_HOST",
)


def _reload_config(monkeypatch, **env):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    importlib.reload(config_module)
    return config_module.WeatherWorkerConfig


def test_default_staleness_derived_from_loop_interval_3x_plus_margin(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        PARCEL_ENGINE_INTERVAL_HOURS="2",
        AEMET_ALERTS_INTERVAL_HOURS="1",
    )

    assert cfg.PARCEL_ENGINE_HEARTBEAT_MAX_STALENESS_SECONDS == 3 * 2 * 3600 + 600
    assert cfg.METEOALARM_HEARTBEAT_MAX_STALENESS_SECONDS == 3 * 1 * 3600 + 600


def test_default_staleness_scales_with_a_longer_configured_interval(monkeypatch):
    """The threshold must track the actual configured interval, not a fixed
    constant -- e.g. a deployment running PARCEL_ENGINE_INTERVAL_HOURS=6
    must get a proportionally larger default, not the 2h-derived one."""
    cfg = _reload_config(monkeypatch, PARCEL_ENGINE_INTERVAL_HOURS="6")
    assert cfg.PARCEL_ENGINE_HEARTBEAT_MAX_STALENESS_SECONDS == 3 * 6 * 3600 + 600


def test_staleness_threshold_env_override_wins_over_derived_default(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        PARCEL_ENGINE_INTERVAL_HOURS="2",
        PARCEL_ENGINE_HEARTBEAT_MAX_STALENESS_SECONDS="42",
    )
    assert cfg.PARCEL_ENGINE_HEARTBEAT_MAX_STALENESS_SECONDS == 42.0


def test_startup_grace_seconds_default_and_override(monkeypatch):
    cfg = _reload_config(monkeypatch)
    assert cfg.HEARTBEAT_STARTUP_GRACE_SECONDS == 180.0

    cfg = _reload_config(monkeypatch, HEARTBEAT_STARTUP_GRACE_SECONDS="30")
    assert cfg.HEARTBEAT_STARTUP_GRACE_SECONDS == 30.0


def test_health_port_defaults_to_metrics_port_plus_one(monkeypatch):
    cfg = _reload_config(monkeypatch, METRICS_PORT="9106")
    assert cfg.HEALTH_PORT == 9107


def test_health_port_env_override_wins(monkeypatch):
    cfg = _reload_config(monkeypatch, METRICS_PORT="9106", HEALTH_PORT="9200")
    assert cfg.HEALTH_PORT == 9200
