"""Tests for weather-worker's daemon-thread heartbeat registry.

Covers the registry used to detect a dead/stuck daemon thread (parcel
engine, meteoalarm engine, main loop) so /readyz can report it instead of
the process silently staying "healthy" forever.
"""

import os
import sys
import time

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # services/weather-worker
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))  # services/

import pytest

import weather_worker.heartbeat as hb


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test gets an empty registry and a fresh process-start baseline."""
    hb.reset()
    yield
    hb.reset()


def test_heartbeat_updates_registry():
    assert hb.get_last_beat("worker-a") is None
    hb.heartbeat("worker-a")
    assert hb.get_last_beat("worker-a") is not None


def test_heartbeat_refreshes_existing_entry():
    hb.heartbeat("worker-a")
    first = hb.get_last_beat("worker-a")
    time.sleep(0.01)
    hb.heartbeat("worker-a")
    second = hb.get_last_beat("worker-a")
    assert second > first


def test_check_threads_healthy_all_fresh():
    hb.heartbeat("parcel-engine")
    hb.heartbeat("meteoalarm-engine")

    healthy, details = hb.check_threads_healthy(
        {"parcel-engine": 100.0, "meteoalarm-engine": 100.0}
    )

    assert healthy is True
    assert details["parcel-engine"]["status"] == "healthy"
    assert details["meteoalarm-engine"]["status"] == "healthy"
    assert details["parcel-engine"]["last_beat_seconds_ago"] < 1.0


def test_check_threads_healthy_flags_stale_thread_by_name():
    """A thread that stopped beating must be named individually -- the
    other, healthy thread must not be dragged down with it."""
    hb.heartbeat("parcel-engine")
    with hb._lock:  # simulate a dead/hung thread: beat recorded far in the past
        hb._last_beats["meteoalarm-engine"] = time.monotonic() - 500.0

    healthy, details = hb.check_threads_healthy(
        {"parcel-engine": 100.0, "meteoalarm-engine": 100.0}
    )

    assert healthy is False
    assert details["parcel-engine"]["status"] == "healthy"
    assert details["meteoalarm-engine"]["status"] == "stale"
    assert details["meteoalarm-engine"]["last_beat_seconds_ago"] >= 500.0
    assert details["meteoalarm-engine"]["max_staleness_seconds"] == 100.0


def test_check_threads_healthy_not_yet_started_gets_grace_period():
    """A thread that has never beaten (still inside its startup delay) must
    not instant-fail -- it should read as 'starting', not 'stale'."""
    healthy, details = hb.check_threads_healthy(
        {"parcel-engine": 100.0}, startup_grace_seconds=180.0
    )

    assert healthy is True
    assert details["parcel-engine"]["status"] == "starting"
    assert details["parcel-engine"]["last_beat_seconds_ago"] is None


def test_check_threads_healthy_not_yet_started_past_grace_period_is_stale():
    """Once the grace period elapses with no first heartbeat, it's a real
    problem (e.g. the thread crashed before its first loop iteration)."""
    with hb._lock:
        hb._process_start = time.monotonic() - 1000.0

    healthy, details = hb.check_threads_healthy(
        {"parcel-engine": 100.0}, startup_grace_seconds=180.0
    )

    assert healthy is False
    assert details["parcel-engine"]["status"] == "stale"
    assert details["parcel-engine"]["last_beat_seconds_ago"] is None


def test_check_threads_healthy_only_evaluates_registered_threads():
    """A thread not passed in max_staleness_per_thread (e.g. disabled via
    config, so its background thread was never started) must never be
    evaluated -- not even if it happens to be present in the registry."""
    hb.heartbeat("some-other-thread-not-checked")
    healthy, details = hb.check_threads_healthy({})
    assert healthy is True
    assert details == {}


def test_check_threads_healthy_close_to_but_under_threshold_is_healthy():
    """A cycle that's taking a while but hasn't crossed the threshold yet
    must not be flagged -- avoids false positives on a slow-but-alive
    iteration."""
    with hb._lock:
        hb._last_beats["parcel-engine"] = time.monotonic() - 90.0

    healthy, details = hb.check_threads_healthy({"parcel-engine": 100.0})

    assert healthy is True
    assert details["parcel-engine"]["status"] == "healthy"
