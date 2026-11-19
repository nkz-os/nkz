"""ObservationLookup unit tests with a fake asyncpg pool (no DB)."""

import asyncio
import functools
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from asyncpg import Range

from common.context_aliases import canonical_attribute

from telemetry_worker.observation_lookup import (
    ObservationLookup,
    TenantContextMissing,
)

def _sync(fn):
    """Run an async test body (no pytest-asyncio in this suite)."""

    @functools.wraps(fn)
    def wrapper():
        asyncio.run(fn())

    return wrapper


TENANT = "tenant_a"
URN = "urn:ngsi-ld:Device:tenant_a:d1"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 2, 1, tzinfo=timezone.utc)


class FakeConn:
    def __init__(self, log, rows):
        self.log = log
        self.rows = rows

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self_):
                conn.log.append("transaction-enter")

            async def __aexit__(self_, *exc):
                conn.log.append("transaction-exit")
                return False

        return _Tx()

    async def execute(self, sql, *args):
        assert "set_config('app.current_tenant'" in sql
        self.log.append(("set_config", args[0]))

    async def fetch(self, sql, *args):
        self.log.append(("select", args))
        return list(self.rows)


class FakePool:
    def __init__(self, rows=()):
        self.log = []
        self.rows = rows

    def acquire(self):
        pool = self

        class _Acq:
            async def __aenter__(self_):
                pool.log.append("acquire")
                return FakeConn(pool.log, pool.rows)

            async def __aexit__(self_, *exc):
                return False

        return _Acq()

    @property
    def selects(self):
        return [e for e in self.log if isinstance(e, tuple) and e[0] == "select"]


def _dep_row():
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "device_urn": URN,
        "valid": Range(T0, None, lower_inc=True, upper_inc=False),
        "observes_feature": "urn:ngsi-ld:AgriParcel:p1",
        "observation_context": '{"medium": "soil"}',
        "channel_overrides": {},
        "mobility": "fixed",
        "lon": -3.7,
        "lat": 40.4,
    }


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@_sync
async def test_no_tenant_raises():
    pool = FakePool()
    lk = ObservationLookup(pool)
    for bad in ("", None):
        with pytest.raises(TenantContextMissing):
            await lk.deployment_at(bad, URN, T0)
        with pytest.raises(TenantContextMissing):
            await lk.property(bad, "airTemperature")
        with pytest.raises(TenantContextMissing):
            await lk.measurement_def(bad, "p", "a")
    assert pool.log == []


@_sync
async def test_naive_datetime_raises():
    pool = FakePool()
    with pytest.raises(ValueError):
        await ObservationLookup(pool).deployment_at(TENANT, URN, datetime(2026, 1, 2))
    assert pool.log == []


def test_ttl_above_60_rejected():
    with pytest.raises(ValueError):
        ObservationLookup(FakePool(), ttl_s=61)
    ObservationLookup(FakePool(), ttl_s=60)


@_sync
async def test_set_config_inside_transaction_before_query():
    pool = FakePool([_dep_row()])
    dep = await ObservationLookup(pool).deployment_at(TENANT, URN, T0 + timedelta(days=1))
    assert pool.log[0] == "acquire"
    assert pool.log[1] == "transaction-enter"
    assert pool.log[2] == ("set_config", TENANT)
    assert pool.log[3][0] == "select"
    assert pool.log[4] == "transaction-exit"
    assert dep.id == "11111111-1111-1111-1111-111111111111"
    assert dep.location == (-3.7, 40.4)
    assert dep.observation_context == {"medium": "soil"}


@_sync
async def test_cache_hit_within_ttl():
    pool = FakePool([_dep_row()])
    clock = Clock()
    lk = ObservationLookup(pool, ttl_s=30, clock=clock)
    await lk.deployment_at(TENANT, URN, T0 + timedelta(days=1))
    clock.t += 29
    await lk.deployment_at(TENANT, URN, T0 + timedelta(days=2))
    assert len(pool.selects) == 1


@_sync
async def test_cache_expires_after_ttl():
    pool = FakePool([_dep_row()])
    clock = Clock()
    lk = ObservationLookup(pool, ttl_s=30, clock=clock)
    await lk.deployment_at(TENANT, URN, T0 + timedelta(days=1))
    clock.t += 30
    await lk.deployment_at(TENANT, URN, T0 + timedelta(days=1))
    assert len(pool.selects) == 2


@_sync
async def test_invalidate_drops_only_that_tenant():
    pool = FakePool([_dep_row()])
    lk = ObservationLookup(pool, clock=Clock())
    at = T0 + timedelta(days=1)
    await lk.deployment_at("tenant_a", URN, at)
    await lk.deployment_at("tenant_b", URN, at)
    assert len(pool.selects) == 2
    lk.invalidate("tenant_a")
    await lk.deployment_at("tenant_b", URN, at)
    assert len(pool.selects) == 2
    await lk.deployment_at("tenant_a", URN, at)
    assert len(pool.selects) == 3


@_sync
async def test_in_memory_interval_selection():
    r1 = dict(_dep_row(), id="a", valid=Range(T0, T1, lower_inc=True, upper_inc=False))
    r2 = dict(_dep_row(), id="b", valid=Range(T1, None, lower_inc=True, upper_inc=False))
    pool = FakePool([r1, r2])
    lk = ObservationLookup(pool)
    assert (await lk.deployment_at(TENANT, URN, T0)).id == "a"
    assert (await lk.deployment_at(TENANT, URN, T1 - timedelta(microseconds=1))).id == "a"
    assert (await lk.deployment_at(TENANT, URN, T1)).id == "b"
    assert await lk.deployment_at(TENANT, URN, T0 - timedelta(seconds=1)) is None
    assert len(pool.selects) == 1


@_sync
async def test_measurement_def_matches_alias_to_canonical():
    row = {
        "mapping": {"measurements": [
            {"sdmAttribute": "rainfall", "counterMax": 255},
            {"sdmAttribute": "temperature"},
        ]}
    }
    lk = ObservationLookup(FakePool([row]))
    canon = canonical_attribute("temperature")
    assert canon != "temperature"  # the alias really maps elsewhere
    got = await lk.measurement_def(TENANT, "prof", canon)
    assert got == {"sdmAttribute": "temperature"}
    assert await lk.measurement_def(TENANT, "prof", "nonexistent") is None


@_sync
async def test_miss_revalidates_after_floor():
    pool = FakePool([])
    clock = Clock()
    lk = ObservationLookup(pool, ttl_s=60, clock=clock)
    at = T0 + timedelta(days=1)
    assert await lk.deployment_at(TENANT, URN, at) is None
    pool.rows = [_dep_row()]  # device registered after the empty list was cached
    clock.t += 6
    dep = await lk.deployment_at(TENANT, URN, at)
    assert dep is not None
    assert len(pool.selects) == 2
    clock.t += 1  # now a hit inside the refreshed entry: no further DB call
    assert await lk.deployment_at(TENANT, URN, at) is not None
    assert len(pool.selects) == 2


@_sync
async def test_miss_within_floor_uses_cache():
    pool = FakePool([])
    clock = Clock()
    lk = ObservationLookup(pool, ttl_s=60, clock=clock)
    at = T0 + timedelta(days=1)
    assert await lk.deployment_at(TENANT, URN, at) is None
    pool.rows = [_dep_row()]
    clock.t += 1
    assert await lk.deployment_at(TENANT, URN, at) is None
    assert len(pool.selects) == 1


def test_miss_revalidate_bounds():
    with pytest.raises(ValueError):
        ObservationLookup(FakePool(), miss_revalidate_s=0)
    with pytest.raises(ValueError):
        ObservationLookup(FakePool(), ttl_s=10, miss_revalidate_s=11)
