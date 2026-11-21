"""Tests for NotificationDedup (Redis-backed telemetry notification dedup).

Async coroutines are driven with ``asyncio.run`` in sync test functions —
the telemetry-worker smoke lane has no pytest-asyncio plugin (mirrors the
sensor-health-beat test pattern).
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

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
        self.set_calls = []

    async def ping(self):
        return True

    async def set(self, key, value, nx=False, ex=None):
        self.set_calls.append((key, value, nx, ex))
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def close(self):
        pass


class BrokenRedis:
    """Simulates a Redis connection/instance that errors on every call."""

    async def ping(self):
        raise ConnectionError("redis down")

    async def set(self, *args, **kwargs):
        raise ConnectionError("redis down")

    async def get(self, *args, **kwargs):
        raise ConnectionError("redis down")

    async def close(self):
        raise ConnectionError("redis down")


def _dedup_with_fake_redis(enabled: bool = True) -> NotificationDedup:
    dedup = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=enabled)
    dedup._redis = FakeRedis()
    return dedup


def test_first_event_is_new():
    """A never-before-seen event must NOT be flagged as a duplicate."""
    dedup = _dedup_with_fake_redis()
    result = asyncio.run(
        dedup.is_duplicate(TENANT_ID, ENTITY_ID, OBSERVED_AT, {"temperature": 21.5})
    )
    assert result is False


def test_second_identical_event_is_duplicate():
    """The exact same (tenant, entity, observedAt, measurements) seen twice
    must be flagged as a duplicate on the second delivery."""
    dedup = _dedup_with_fake_redis()
    measurements = {"temperature": 21.5, "humidity": 60}

    first = asyncio.run(
        dedup.is_duplicate(TENANT_ID, ENTITY_ID, OBSERVED_AT, measurements)
    )
    second = asyncio.run(
        dedup.is_duplicate(TENANT_ID, ENTITY_ID, OBSERVED_AT, measurements)
    )

    assert first is False
    assert second is True


def test_different_measurements_same_timestamp_not_collapsed():
    """Two DIFFERENT readings sharing the same observedAt must both be
    treated as new — the measurement hash must prevent collapsing them."""
    dedup = _dedup_with_fake_redis()

    first = asyncio.run(
        dedup.is_duplicate(TENANT_ID, ENTITY_ID, OBSERVED_AT, {"temperature": 21.5})
    )
    second = asyncio.run(
        dedup.is_duplicate(TENANT_ID, ENTITY_ID, OBSERVED_AT, {"temperature": 30.0})
    )

    assert first is False
    assert second is False


def test_redis_error_on_set_fails_open():
    """If the Redis SET call itself raises, dedup must fail open: return
    False (not a duplicate) so the write is never dropped."""
    dedup = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=True)
    dedup._redis = BrokenRedis()

    result = asyncio.run(
        dedup.is_duplicate(TENANT_ID, ENTITY_ID, OBSERVED_AT, {"temperature": 21.5})
    )

    assert result is False


def test_redis_never_connected_fails_open():
    """If Redis was unreachable at start() time (_redis stays None), dedup
    must fail open rather than blocking every subsequent write."""
    dedup = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=True)
    assert dedup._redis is None

    result = asyncio.run(
        dedup.is_duplicate(TENANT_ID, ENTITY_ID, OBSERVED_AT, {"temperature": 21.5})
    )

    assert result is False


def test_start_failure_is_non_fatal():
    """start() must never raise even if Redis is completely unreachable —
    the worker must still boot without a working dedup cache."""
    original_from_url = dedup_module.aioredis.from_url
    dedup_module.aioredis.from_url = lambda url, decode_responses=True: BrokenRedis()
    try:
        dedup = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=True)
        asyncio.run(dedup.start())  # must not raise
        assert dedup._redis is None
    finally:
        dedup_module.aioredis.from_url = original_from_url


def test_start_connects_and_pings_on_success():
    """start() should wire up the aioredis client, mirroring HealthChecker's
    connect-and-ping pattern."""
    fake = FakeRedis()
    original_from_url = dedup_module.aioredis.from_url
    dedup_module.aioredis.from_url = lambda url, decode_responses=True, **kw: fake
    try:
        dedup = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=True)
        asyncio.run(dedup.start())
        assert dedup._redis is fake
    finally:
        dedup_module.aioredis.from_url = original_from_url


def test_disabled_bypasses_redis_entirely():
    """TELEMETRY_DEDUP_ENABLED=false (enabled=False) must short-circuit
    before touching Redis at all — even a broken client must not raise."""
    dedup = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=False)
    dedup._redis = BrokenRedis()  # would raise on any call if touched

    result = asyncio.run(
        dedup.is_duplicate(TENANT_ID, ENTITY_ID, OBSERVED_AT, {"temperature": 21.5})
    )

    assert result is False


def test_disabled_start_does_not_connect():
    """start() with enabled=False must not attempt a Redis connection."""
    original_from_url = dedup_module.aioredis.from_url

    def _boom(*args, **kwargs):
        raise AssertionError("from_url should not be called when disabled")

    dedup_module.aioredis.from_url = _boom
    try:
        dedup = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=False)
        asyncio.run(dedup.start())
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


# ── DeviceMeasurement replay guard (design A.3) ────────────────────────────

import logging  # noqa: E402

DM_ID = "urn:ngsi-ld:DeviceMeasurement:tenant1:sensor-1:airTemperature"
T1 = datetime(2026, 8, 4, 10, 0, 0, tzinfo=timezone.utc)
T0 = datetime(2026, 8, 4, 9, 0, 0, tzinfo=timezone.utc)


def _replay(dedup, at, value, entity_id=DM_ID, tenant=TENANT_ID):
    return asyncio.run(dedup.is_replay(tenant, entity_id, at, value))


def _record(dedup, at, value, entity_id=DM_ID, tenant=TENANT_ID):
    asyncio.run(dedup.record_processed(tenant, entity_id, at, value))


def test_replay_nothing_recorded_is_not_a_replay():
    assert _replay(_dedup_with_fake_redis(), T1, 21.5) is False


def test_replay_identical_after_record_is_a_replay():
    dedup = _dedup_with_fake_redis()
    _record(dedup, T1, 21.5)
    assert _replay(dedup, T1, 21.5) is True


def test_replay_record_has_no_ttl_and_is_keyed_by_tenant_and_entity():
    dedup = _dedup_with_fake_redis()
    _record(dedup, T1, 21.5)
    (key, _value, nx, ex), = dedup._redis.set_calls
    assert key == f"telemetry:dm_last:{TENANT_ID}:{DM_ID}"
    assert ex is None and nx is False
    assert _replay(dedup, T1, 21.5, tenant="other-tenant") is False
    assert _replay(dedup, T1, 21.5, entity_id=DM_ID + "Min") is False


def test_replay_same_value_different_date_observed_is_not_a_replay():
    dedup = _dedup_with_fake_redis()
    _record(dedup, T1, 21.5)
    assert _replay(dedup, T0, 21.5) is False   # late reading, same value
    assert _replay(dedup, T1.replace(hour=11), 21.5) is False


def test_replay_same_date_different_value_is_not_a_replay():
    dedup = _dedup_with_fake_redis()
    _record(dedup, T1, 21.5)
    assert _replay(dedup, T1, 21.6) is False
    assert _replay(dedup, T1, "21.5") is False


def test_replay_compares_values_and_instants_not_strings():
    dedup = _dedup_with_fake_redis()
    _record(dedup, T1, 21)
    assert _replay(dedup, T1, 21.0) is True
    other_tz = datetime.fromisoformat("2026-08-04T12:00:00+02:00")
    assert _replay(dedup, other_tz, 21) is True
    _record(dedup, T1, 1)
    assert _replay(dedup, T1, True) is False   # a bool is never equal to a number here


def test_replay_last_record_wins():
    dedup = _dedup_with_fake_redis()
    _record(dedup, T0, 20.0)
    _record(dedup, T1, 21.5)
    assert _replay(dedup, T1, 21.5) is True
    assert _replay(dedup, T0, 20.0) is False


def test_replay_corrupt_record_is_not_a_replay():
    dedup = _dedup_with_fake_redis()
    dedup._redis.store[f"telemetry:dm_last:{TENANT_ID}:{DM_ID}"] = "not json"
    assert _replay(dedup, T1, 21.5) is False


def test_replay_redis_errors_fail_open_and_log_once(caplog):
    dedup = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=True)
    dedup._redis = BrokenRedis()
    with caplog.at_level(logging.WARNING):
        for i in range(3):
            assert _replay(dedup, T1, 21.5, entity_id=f"{DM_ID}{i}") is False
            _record(dedup, T1, 21.5, entity_id=f"{DM_ID}{i}")  # must not raise
    lines = [r for r in caplog.records if "replay_guard_unavailable" in r.getMessage()]
    assert len(lines) == 1
    assert lines[0].levelno == logging.WARNING


def test_replay_redis_never_connected_fails_open_and_logs_once(caplog):
    dedup = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=True)
    assert dedup._redis is None
    with caplog.at_level(logging.WARNING):
        assert _replay(dedup, T1, 21.5) is False
        assert _replay(dedup, T1, 21.5) is False
        _record(dedup, T1, 21.5)
    lines = [r for r in caplog.records if "replay_guard_unavailable" in r.getMessage()]
    assert len(lines) == 1


def test_replay_disabled_bypasses_redis_and_logs_nothing(caplog):
    dedup = _dedup_with_fake_redis(enabled=False)
    with caplog.at_level(logging.DEBUG):
        _record(dedup, T1, 21.5)
        assert _replay(dedup, T1, 21.5) is False
    assert dedup._redis.store == {}
    assert not [r for r in caplog.records if "replay_guard" in r.getMessage()]


# ── seen / mark split (retry safety: mark only after the write) ─────────────


def test_seen_is_read_only():
    """seen() never writes: a reading whose write then fails must not be marked."""
    dedup = _dedup_with_fake_redis()
    m = {"temperature": 21.5}
    assert asyncio.run(dedup.seen(TENANT_ID, ENTITY_ID, OBSERVED_AT, m)) is False
    assert asyncio.run(dedup.seen(TENANT_ID, ENTITY_ID, OBSERVED_AT, m)) is False
    assert dedup._redis.set_calls == []


def test_mark_then_seen_is_true_with_ttl():
    dedup = _dedup_with_fake_redis()
    m = {"temperature": 21.5}
    asyncio.run(dedup.mark(TENANT_ID, ENTITY_ID, OBSERVED_AT, m))
    assert asyncio.run(dedup.seen(TENANT_ID, ENTITY_ID, OBSERVED_AT, m)) is True
    assert asyncio.run(dedup.seen(TENANT_ID, ENTITY_ID, OBSERVED_AT, {"temperature": 30.0})) is False
    (_key, _value, _nx, ex), = dedup._redis.set_calls
    assert ex == dedup_module.DEFAULT_TTL_SECONDS


def test_mark_shares_the_is_duplicate_key():
    """A reading marked by mark() is a duplicate for is_duplicate() and vice versa."""
    dedup = _dedup_with_fake_redis()
    m = {"temperature": 21.5}
    asyncio.run(dedup.mark(TENANT_ID, ENTITY_ID, OBSERVED_AT, m))
    assert asyncio.run(dedup.is_duplicate(TENANT_ID, ENTITY_ID, OBSERVED_AT, m)) is True


def test_seen_and_mark_fail_open():
    dedup = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=True)
    dedup._redis = BrokenRedis()
    m = {"temperature": 21.5}
    assert asyncio.run(dedup.seen(TENANT_ID, ENTITY_ID, OBSERVED_AT, m)) is False
    asyncio.run(dedup.mark(TENANT_ID, ENTITY_ID, OBSERVED_AT, m))  # must not raise
    never = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=True)
    assert asyncio.run(never.seen(TENANT_ID, ENTITY_ID, OBSERVED_AT, m)) is False
    asyncio.run(never.mark(TENANT_ID, ENTITY_ID, OBSERVED_AT, m))


def test_seen_and_mark_disabled_do_not_touch_redis():
    dedup = NotificationDedup(redis_url="redis://localhost:6379/0", enabled=False)
    dedup._redis = BrokenRedis()
    m = {"temperature": 21.5}
    assert asyncio.run(dedup.seen(TENANT_ID, ENTITY_ID, OBSERVED_AT, m)) is False
    asyncio.run(dedup.mark(TENANT_ID, ENTITY_ID, OBSERVED_AT, m))
