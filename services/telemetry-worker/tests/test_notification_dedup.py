"""Tests for NotificationDedup (Redis-backed telemetry notification dedup)."""

import os
import sys
from datetime import datetime, timezone

import pytest

# ── Path setup (mirrors other telemetry-worker tests) ──────────────────────
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")

for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import telemetry_worker.dedup as dedup_module
from telemetry_worker.dedup import NotificationDedup

OBSERVED_AT = datetime(2026, 8, 4, 10, 0, 0, tzinfo=timezone.utc)
ENTITY_ID = "urn:ngsi-ld:Device:sensor-1"
TENANT_ID = "tenant1"


class FakeRedis:
    """
    Minimal in-memory stand-in for redis.asyncio.Redis that reproduces the
    SET key value NX EX semantics NotificationDedup relies on:
    - returns True and stores the key if it did not exist
    - returns None (falsy) without overwriting if the key already exists
    TTL expiry is not simulated (not needed for these tests).
    """

    def __init__(self):
        self.store = {}

    async def ping(self):
        return True

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def close(self):
        pass


class BrokenRedis:
    """Simulates a Redis connection/instance that errors on every call."""

    async def ping(self):
        raise ConnectionError("redis down")

    async def set(self, *args, **kwargs):
        raise ConnectionError("redis down")

    async def close(self):
        raise ConnectionError("redis down")


def _dedup_with_fake_redis(enabled: bool = True) -> NotificationDedup:
    dedup = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=enabled)
    dedup._redis = FakeRedis()
    return dedup


@pytest.mark.asyncio
async def test_first_event_is_new():
    """A never-before-seen event must NOT be flagged as a duplicate."""
    dedup = _dedup_with_fake_redis()
    result = await dedup.is_duplicate(
        TENANT_ID, ENTITY_ID, OBSERVED_AT, {"temperature": 21.5}
    )
    assert result is False


@pytest.mark.asyncio
async def test_second_identical_event_is_duplicate():
    """The exact same (tenant, entity, observedAt, measurements) seen twice
    must be flagged as a duplicate on the second delivery."""
    dedup = _dedup_with_fake_redis()
    measurements = {"temperature": 21.5, "humidity": 60}

    first = await dedup.is_duplicate(TENANT_ID, ENTITY_ID, OBSERVED_AT, measurements)
    second = await dedup.is_duplicate(TENANT_ID, ENTITY_ID, OBSERVED_AT, measurements)

    assert first is False
    assert second is True


@pytest.mark.asyncio
async def test_different_measurements_same_timestamp_not_collapsed():
    """Two DIFFERENT readings sharing the same observedAt must both be
    treated as new — the measurement hash must prevent collapsing them."""
    dedup = _dedup_with_fake_redis()

    first = await dedup.is_duplicate(
        TENANT_ID, ENTITY_ID, OBSERVED_AT, {"temperature": 21.5}
    )
    second = await dedup.is_duplicate(
        TENANT_ID, ENTITY_ID, OBSERVED_AT, {"temperature": 30.0}
    )

    assert first is False
    assert second is False


@pytest.mark.asyncio
async def test_redis_error_on_set_fails_open():
    """If the Redis SET call itself raises, dedup must fail open: return
    False (not a duplicate) so the write is never dropped."""
    dedup = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=True)
    dedup._redis = BrokenRedis()

    result = await dedup.is_duplicate(
        TENANT_ID, ENTITY_ID, OBSERVED_AT, {"temperature": 21.5}
    )

    assert result is False


@pytest.mark.asyncio
async def test_redis_never_connected_fails_open():
    """If Redis was unreachable at start() time (_redis stays None), dedup
    must fail open rather than blocking every subsequent write."""
    dedup = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=True)
    assert dedup._redis is None

    result = await dedup.is_duplicate(
        TENANT_ID, ENTITY_ID, OBSERVED_AT, {"temperature": 21.5}
    )

    assert result is False


@pytest.mark.asyncio
async def test_start_failure_is_non_fatal():
    """start() must never raise even if Redis is completely unreachable —
    the worker must still boot without a working dedup cache."""
    original_from_url = dedup_module.aioredis.from_url
    dedup_module.aioredis.from_url = lambda url, decode_responses=True: BrokenRedis()
    try:
        dedup = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=True)
        await dedup.start()  # must not raise
        assert dedup._redis is None
    finally:
        dedup_module.aioredis.from_url = original_from_url


@pytest.mark.asyncio
async def test_start_connects_and_pings_on_success():
    """start() should wire up the aioredis client, mirroring HealthChecker's
    connect-and-ping pattern."""
    fake = FakeRedis()
    original_from_url = dedup_module.aioredis.from_url
    dedup_module.aioredis.from_url = lambda url, decode_responses=True: fake
    try:
        dedup = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=True)
        await dedup.start()
        assert dedup._redis is fake
    finally:
        dedup_module.aioredis.from_url = original_from_url


@pytest.mark.asyncio
async def test_disabled_bypasses_redis_entirely():
    """TELEMETRY_DEDUP_ENABLED=false (enabled=False) must short-circuit
    before touching Redis at all — even a broken client must not raise."""
    dedup = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=False)
    dedup._redis = BrokenRedis()  # would raise on any call if touched

    result = await dedup.is_duplicate(
        TENANT_ID, ENTITY_ID, OBSERVED_AT, {"temperature": 21.5}
    )

    assert result is False


@pytest.mark.asyncio
async def test_disabled_start_does_not_connect():
    """start() with enabled=False must not attempt a Redis connection."""
    original_from_url = dedup_module.aioredis.from_url

    def _boom(*args, **kwargs):
        raise AssertionError("from_url should not be called when disabled")

    dedup_module.aioredis.from_url = _boom
    try:
        dedup = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=False)
        await dedup.start()
        assert dedup._redis is None
    finally:
        dedup_module.aioredis.from_url = original_from_url


def test_key_scheme_includes_tenant_entity_timestamp_and_hash():
    """Sanity-check the key format documented in dedup.py."""
    dedup = NotificationDedup(redis_url="redis://localhost:6379/0")
    key = dedup._build_key(TENANT_ID, ENTITY_ID, OBSERVED_AT, {"temperature": 21.5})
    assert key.startswith("telemetry:dedup:")
    assert TENANT_ID in key
    assert ENTITY_ID in key
    assert OBSERVED_AT.isoformat() in key


def test_key_differs_for_different_measurement_values():
    dedup = NotificationDedup(redis_url="redis://localhost:6379/0")
    key_a = dedup._build_key(TENANT_ID, ENTITY_ID, OBSERVED_AT, {"temperature": 21.5})
    key_b = dedup._build_key(TENANT_ID, ENTITY_ID, OBSERVED_AT, {"temperature": 22.0})
    assert key_a != key_b
