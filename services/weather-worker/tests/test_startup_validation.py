"""Tests for weather-worker startup fail-fast validation.

MUNICIPALITY_WORKER_ENABLED is a legacy flag: the path it enables writes
through TimescaleDBWriter.write_observations(), which is now a no-op stub
(Orion-LD migration). Running it silently discards data, so the worker must
refuse to start rather than run a broken/no-op ingestion path.
"""

import importlib
import os
import sys

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # services/weather-worker
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))  # services/ — for shared imports

import pytest

import main as weather_worker_main
import weather_worker.config as config_module


def _config_with_env(monkeypatch, enabled: str):
    """Reload weather_worker.config with MUNICIPALITY_WORKER_ENABLED set, return a fresh class."""
    monkeypatch.setenv("MUNICIPALITY_WORKER_ENABLED", enabled)
    importlib.reload(config_module)
    return config_module.WeatherWorkerConfig


def test_validate_startup_config_raises_when_municipality_worker_enabled(monkeypatch):
    cfg = _config_with_env(monkeypatch, "true")
    with pytest.raises(SystemExit) as exc_info:
        weather_worker_main.validate_startup_config(cfg)
    assert "MUNICIPALITY_WORKER_ENABLED" in str(exc_info.value)


def test_validate_startup_config_passes_when_municipality_worker_disabled(monkeypatch):
    cfg = _config_with_env(monkeypatch, "false")
    # Must not raise.
    weather_worker_main.validate_startup_config(cfg)
