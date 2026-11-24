"""CalibrationService: a definite 'no active period' is cached briefly."""

import asyncio
import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_TEST_DIR, _SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from _lookup_fakes import CacheRedis, DownRedis
from telemetry_worker import calibration as cal
from telemetry_worker.calibration import NEGATIVE_CACHE_TTL, CalibrationService

TENANT, SENSOR, VAR = "tenant-a", "urn:ngsi-ld:DeviceMeasurement:tenant-a:dev-1:temperature", "temperature"


class _Conn:
    def __init__(self, pool):
        self.pool = pool

    async def fetchrow(self, *a):
        self.pool.queries += 1
        if self.pool.error:
            raise RuntimeError("db down")
        return self.pool.row


class _Acquire:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        return _Conn(self.pool)

    async def __aexit__(self, *a):
        return False


class FakePool:
    def __init__(self, row=None, error=False):
        self.row, self.error, self.queries = row, error, 0

    def acquire(self):
        return _Acquire(self)


def _svc(pool, redis):
    s = CalibrationService(redis_url="redis://x/0", pg_pool=pool)
    s._redis = redis
    return s


def _lookup(s, n=1):
    async def go():
        return [await s.get_active_period(SENSOR, TENANT, VAR) for _ in range(n)]
    return asyncio.run(go())


def test_negative_ttl_constant():
    assert NEGATIVE_CACHE_TTL == 60


def test_no_period_cached_second_call_skips_postgres():
    pool, redis = FakePool(row=None), CacheRedis()
    assert _lookup(_svc(pool, redis), 2) == [None, None]
    assert pool.queries == 1
    assert redis.setex_calls[0][1] == NEGATIVE_CACHE_TTL


def test_error_is_not_cached():
    pool, redis = FakePool(error=True), CacheRedis()
    assert _lookup(_svc(pool, redis), 2) == [None, None]
    assert pool.queries == 2
    assert redis.setex_calls == []


def test_no_pool_is_not_cached():
    redis = CacheRedis()
    assert _lookup(_svc(None, redis), 2) == [None, None]
    assert redis.setex_calls == []


def test_invalidate_clears_the_negative_entry():
    pool, redis = FakePool(row=None), CacheRedis()
    s = _svc(pool, redis)
    _lookup(s)
    asyncio.run(s.invalidate_cache(SENSOR, TENANT, VAR))
    pool.row = {"id": "p1", "slope": 2, "offset_val": 1}
    assert _lookup(s)[0] == {"id": "p1", "slope": 2.0, "offset_val": 1.0}


def test_invalidate_all_variables_clears_negative_entry():
    pool, redis = FakePool(row=None), CacheRedis()
    s = _svc(pool, redis)
    _lookup(s)
    asyncio.run(s.invalidate_cache(SENSOR, TENANT))
    _lookup(s)
    assert pool.queries == 2


def test_found_period_still_cached_and_served():
    pool, redis = FakePool(row={"id": "p1", "slope": 2, "offset_val": 1}), CacheRedis()
    out = _lookup(_svc(pool, redis), 2)
    assert out[0] == out[1] == {"id": "p1", "slope": 2.0, "offset_val": 1.0}
    assert pool.queries == 1
    assert redis.setex_calls[0][1] == cal.CACHE_TTL


def test_marker_is_never_returned_as_a_period():
    pool, redis = FakePool(row=None), CacheRedis()
    s = _svc(pool, redis)
    _lookup(s)
    assert all(v is None for v in _lookup(s, 3))


def test_redis_down_queries_every_time():
    pool = FakePool(row=None)
    assert _lookup(_svc(pool, DownRedis()), 2) == [None, None]
    assert pool.queries == 2
