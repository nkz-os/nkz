"""
Thread liveness heartbeats for weather-worker's daemon threads.

Problem: main.py starts long-running loops (parcel engine, meteoalarm
engine) as daemon threads. If one of those threads dies (unhandled
exception outside the loop's own try/except) or hangs forever inside a
blocking call, the process keeps running and the Prometheus /metrics
endpoint keeps returning 200 -- Kubernetes never restarts the pod, and
that thread's work (per-parcel weather, official alerts) silently stops.

Fix: each daemon thread calls heartbeat(name) once per loop iteration
(and once right after startup). check_threads_healthy() compares each
registered thread's last beat against a generous per-thread staleness
threshold; the health server's /readyz endpoint calls it to decide
whether Kubernetes should consider the pod not-ready/dead.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional, Tuple

_lock = threading.Lock()
_last_beats: Dict[str, float] = {}  # thread_name -> time.monotonic() of last beat
_process_start = time.monotonic()  # baseline for the "not started yet" grace period


def heartbeat(name: str) -> None:
    """Record that the thread `name` is alive right now.

    Call this at the top of every loop iteration (and once right after the
    thread starts, before any slow startup work) -- not after a blocking
    call, otherwise a hang inside that call would never be detected.
    """
    with _lock:
        _last_beats[name] = time.monotonic()


def get_last_beat(name: str) -> Optional[float]:
    """Return the time.monotonic() of `name`'s last heartbeat, or None if it
    has never beaten."""
    with _lock:
        return _last_beats.get(name)


def reset() -> None:
    """Clear all recorded heartbeats and reset the process-start baseline.

    Test-only: production code never needs to reset the registry.
    """
    global _process_start
    with _lock:
        _last_beats.clear()
        _process_start = time.monotonic()


def check_threads_healthy(
    max_staleness_per_thread: Dict[str, float],
    *,
    startup_grace_seconds: float = 180.0,
) -> Tuple[bool, Dict[str, dict]]:
    """Evaluate whether every registered thread has beaten recently enough.

    Args:
        max_staleness_per_thread: {thread_name: max_seconds_since_last_beat}.
            Only threads present in this dict are checked -- callers should
            include only the threads actually started (e.g. skip
            "parcel-engine" if PARCEL_ENGINE_ENABLED=false).
        startup_grace_seconds: how long a thread is allowed to go without
            ever having beaten (still inside its startup delay / first
            cycle) before it's reported stale instead of "starting". Measured
            from process start (module import time of this file), not from
            when the thread object was created.

    Returns:
        (all_healthy, details) where details[name] = {
            "status": "healthy" | "starting" | "stale",
            "last_beat_seconds_ago": float | None,
            "max_staleness_seconds": float,
        }
    """
    now = time.monotonic()
    details: Dict[str, dict] = {}
    all_healthy = True

    with _lock:
        beats_snapshot = dict(_last_beats)
        start = _process_start

    for name, max_staleness in max_staleness_per_thread.items():
        last_beat = beats_snapshot.get(name)

        if last_beat is None:
            since_process_start = now - start
            if since_process_start <= startup_grace_seconds:
                details[name] = {
                    "status": "starting",
                    "last_beat_seconds_ago": None,
                    "max_staleness_seconds": max_staleness,
                }
            else:
                all_healthy = False
                details[name] = {
                    "status": "stale",
                    "last_beat_seconds_ago": None,
                    "max_staleness_seconds": max_staleness,
                }
            continue

        age = now - last_beat
        if age > max_staleness:
            all_healthy = False
            details[name] = {
                "status": "stale",
                "last_beat_seconds_ago": round(age, 1),
                "max_staleness_seconds": max_staleness,
            }
        else:
            details[name] = {
                "status": "healthy",
                "last_beat_seconds_ago": round(age, 1),
                "max_staleness_seconds": max_staleness,
            }

    return all_healthy, details
