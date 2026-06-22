"""Tests for HealthChecker."""

import os
import sys

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")

for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from telemetry_worker.health_checker import HealthChecker, _severity


def test_evaluate_valid_value():
    """Test that a value within bounds returns valid."""
    hc = HealthChecker(orion_url="http://localhost:1026", redis_url="redis://localhost:6379/0", context_url="http://localhost/context.json")
    result = hc.evaluate_measurement("temperature", 25.0, {"temperature": {"minValid": -20, "maxValid": 60}})
    assert result == "valid"

def test_evaluate_out_of_bounds_low():
    """Test that a value below min returns out_of_bounds."""
    hc = HealthChecker(orion_url="http://localhost:1026", redis_url="redis://localhost:6379/0", context_url="http://localhost/context.json")
    result = hc.evaluate_measurement("temperature", -30.0, {"temperature": {"minValid": -20, "maxValid": 60}})
    assert result == "out_of_bounds"

def test_evaluate_out_of_bounds_high():
    """Test that a value above max returns out_of_bounds."""
    hc = HealthChecker(orion_url="http://localhost:1026", redis_url="redis://localhost:6379/0", context_url="http://localhost/context.json")
    result = hc.evaluate_measurement("temperature", 100.0, {"temperature": {"minValid": -20, "maxValid": 60}})
    assert result == "out_of_bounds"

def test_evaluate_nan():
    """Test that None value returns nan."""
    hc = HealthChecker(orion_url="http://localhost:1026", redis_url="redis://localhost:6379/0", context_url="http://localhost/context.json")
    result = hc.evaluate_measurement("temperature", None, {"temperature": {"minValid": -20, "maxValid": 60}})
    assert result == "nan"

def test_evaluate_no_config():
    """Test that no config for a variable returns valid."""
    hc = HealthChecker(orion_url="http://localhost:1026", redis_url="redis://localhost:6379/0", context_url="http://localhost/context.json")
    result = hc.evaluate_measurement("windSpeed", 100.0, {"temperature": {"minValid": -20}})
    assert result == "valid"

def test_severity_ordering():
    """Test severity ordering."""
    assert _severity("valid") == 0
    assert _severity("nan") == 1
    assert _severity("out_of_bounds") == 2
    assert _severity("stale") == 3
