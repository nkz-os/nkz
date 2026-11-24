"""HealthChecker: absence cache, shared HTTP client, platform context on the GET."""

import asyncio
import logging
import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_TEST_DIR, _SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import httpx
import pytest

from _lookup_fakes import TIMEOUT, CacheRedis, DownRedis, FakeClient, FakeResponse, health_property
from telemetry_worker import health_checker as hcm
from telemetry_worker.health_checker import CACHE_TTL, HealthChecker

CTX = "http://localhost/context.json"
TENANT = "tenant-a"
ENTITY = "urn:ngsi-ld:Device:tenant-a:dev-1"
CONFIG = {"temperature": {"minValid": -20, "maxValid": 60}}


def _hc(redis=None, client=None):
    hc = HealthChecker(orion_url="http://orion:1026", redis_url="redis://x/0", context_url=CTX)
    hc._redis = redis
    hc._client = client
    return hc


def _get(hc, **kw):
    return asyncio.run(hc.get_health_config(ENTITY, TENANT, **kw))


def _twice(hc, **kw):
    async def go():
        a = await hc.get_health_config(ENTITY, TENANT, **kw)
        b = await hc.get_health_config(ENTITY, TENANT, **kw)
        return a, b
    return asyncio.run(go())


def test_404_cached_as_absent_second_call_makes_no_request():
    client, redis = FakeClient([FakeResponse(404)]), CacheRedis()
    assert _twice(_hc(redis, client)) == (None, None)
    assert len(client.gets) == 1
    assert redis.setex_calls[0][1] == CACHE_TTL


def test_200_without_health_config_cached_as_absent():
    client, redis = FakeClient([FakeResponse(200, {"id": ENTITY, "type": "Device"})]), CacheRedis()
    assert _twice(_hc(redis, client)) == (None, None)
    assert len(client.gets) == 1


def test_500_is_not_cached_and_is_retried():
    client = FakeClient([FakeResponse(500), FakeResponse(200, health_property(CONFIG))])
    redis = CacheRedis()
    assert _twice(_hc(redis, client)) == (None, CONFIG)
    assert len(client.gets) == 2


def test_other_status_not_cached():
    client, redis = FakeClient([FakeResponse(401), FakeResponse(404)]), CacheRedis()
    _twice(_hc(redis, client))
    assert len(client.gets) == 2


def test_timeout_is_not_cached_and_is_retried():
    client = FakeClient([TIMEOUT, FakeResponse(200, health_property(CONFIG))])
    assert _twice(_hc(CacheRedis(), client)) == (None, CONFIG)
    assert len(client.gets) == 2


def test_generic_exception_is_not_cached():
    client = FakeClient([RuntimeError("boom"), FakeResponse(404)])
    _twice(_hc(CacheRedis(), client))
    assert len(client.gets) == 2


def test_force_refresh_skips_both_cache_reads():
    client, redis = FakeClient([FakeResponse(404), FakeResponse(200, health_property(CONFIG))]), CacheRedis()
    hc = _hc(redis, client)
    assert _get(hc) is None
    assert _get(hc, force_refresh=True) == CONFIG
    assert len(client.gets) == 2
    # the refreshed config replaces the absence marker
    assert _get(hc) == CONFIG
    assert len(client.gets) == 2


def test_force_refresh_ignores_a_cached_config():
    client, redis = FakeClient([FakeResponse(200, health_property(CONFIG))] * 2), CacheRedis()
    hc = _hc(redis, client)
    _get(hc)
    _get(hc, force_refresh=True)
    assert len(client.gets) == 2


def test_redis_down_works_uncached():
    client = FakeClient([FakeResponse(404), FakeResponse(200, health_property(CONFIG))])
    assert _twice(_hc(DownRedis(), client)) == (None, CONFIG)
    assert len(client.gets) == 2


def test_no_redis_works_uncached():
    client = FakeClient([FakeResponse(200, health_property(CONFIG))] * 2)
    assert _twice(_hc(None, client)) == (CONFIG, CONFIG)
    assert len(client.gets) == 2


def test_sentinel_is_never_returned_as_a_config():
    client, redis = FakeClient([FakeResponse(404)]), CacheRedis()
    hc = _hc(redis, client)
    _get(hc)
    (marker,) = redis.store.values()
    assert _get(hc) is None
    # whatever the marker is, a config round-trips as a dict and the marker is not one
    client2, redis2 = FakeClient([FakeResponse(200, health_property(CONFIG))]), CacheRedis()
    _get(_hc(redis2, client2))
    (cfg,) = redis2.store.values()
    assert cfg != marker


def test_cached_config_is_served_without_request():
    client, redis = FakeClient([FakeResponse(200, health_property(CONFIG))]), CacheRedis()
    assert _twice(_hc(redis, client)) == (CONFIG, CONFIG)
    assert len(client.gets) == 1


def test_get_sends_platform_context_link_and_json_accept():
    client = FakeClient([FakeResponse(404)])
    _get(_hc(CacheRedis(), client))
    url, headers, timeout = client.gets[0]
    assert url == f"http://orion:1026/ngsi-ld/v1/entities/{ENTITY}?attrs=healthConfig"
    assert headers["Accept"] == "application/json"
    assert headers["Link"] == f'<{CTX}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"'
    assert headers["NGSILD-Tenant"] == TENANT and headers["Fiware-Service"] == TENANT
    assert timeout == 10


def test_404_logs_debug_other_status_logs_warning(caplog):
    with caplog.at_level(logging.DEBUG, logger=hcm.__name__):
        _get(_hc(CacheRedis(), FakeClient([FakeResponse(404)])))
        assert [r.levelno for r in caplog.records if "healthConfig" in r.getMessage()] == [logging.DEBUG]
        caplog.clear()
        _get(_hc(CacheRedis(), FakeClient([FakeResponse(500)])))
        assert [r.levelno for r in caplog.records if "healthConfig" in r.getMessage()] == [logging.WARNING]


def test_patch_uses_the_shared_client():
    client = FakeClient()
    hc = _hc(None, client)
    assert asyncio.run(hc.update_reliability_status(ENTITY, TENANT, "error")) is True
    url, body, headers, timeout = client.patches[0]
    assert url.endswith(f"/entities/{ENTITY}/attrs")
    assert body["reliabilityStatus"]["value"] == "error"
    assert headers["Content-Type"] == "application/json" and "Link" in headers
    assert timeout == 10


def test_client_created_in_start_and_closed_in_stop(monkeypatch):
    monkeypatch.setattr(hcm.aioredis, "from_url", lambda *a, **k: CacheRedis())
    hc = HealthChecker(orion_url="http://orion:1026", redis_url="redis://x/0", context_url=CTX)

    async def go():
        await hc.start()
        client = hc._client
        assert isinstance(client, httpx.AsyncClient) and not client.is_closed
        await hc.stop()
        return client

    client = asyncio.run(go())
    assert client.is_closed
    assert hc._client is None


def test_client_is_created_lazily_without_start_and_reused():
    hc = HealthChecker(orion_url="http://orion:1026", redis_url="redis://x/0", context_url=CTX)

    async def go():
        a, b = hc._get_client(), hc._get_client()
        await hc.stop()
        return a, b

    a, b = asyncio.run(go())
    assert a is b and a.is_closed


def test_tenant_is_part_of_the_cache_key():
    client = FakeClient([FakeResponse(404), FakeResponse(200, health_property(CONFIG))])
    hc = _hc(CacheRedis(), client)

    async def go():
        a = await hc.get_health_config(ENTITY, "tenant-a")
        b = await hc.get_health_config(ENTITY, "tenant-b")
        a2 = await hc.get_health_config(ENTITY, "tenant-a")
        return a, b, a2

    assert asyncio.run(go()) == (None, CONFIG, None)
    assert len(client.gets) == 2


def test_patch_404_returns_false_and_warns(caplog):
    client = FakeClient(patch_status=404)
    with caplog.at_level(logging.WARNING, logger=hcm.__name__):
        assert asyncio.run(_hc(None, client).update_reliability_status(ENTITY, TENANT, "error")) is False
    assert any("reliabilityStatus" in r.getMessage() for r in caplog.records)


def test_stop_closes_redis_even_if_aclose_raises():
    class Boom(FakeClient):
        async def aclose(self):
            raise RuntimeError("boom")

    redis = CacheRedis()
    closed = []

    async def close():
        closed.append(True)

    redis.close = close
    hc = _hc(redis, Boom())
    with pytest.raises(RuntimeError):
        asyncio.run(hc.stop())
    assert closed == [True]


def test_get_client_after_stop_raises_instead_of_leaking_a_client():
    hc = _hc(None, FakeClient())
    asyncio.run(hc.stop())
    with pytest.raises(RuntimeError):
        hc._get_client()
    # and lookups degrade to "no config", logged, never an unclosed client
    assert asyncio.run(hc.get_health_config(ENTITY, TENANT)) is None
    assert hc._client is None


def test_start_after_stop_reopens(monkeypatch):
    monkeypatch.setattr(hcm.aioredis, "from_url", lambda *a, **k: CacheRedis())
    hc = HealthChecker(orion_url="http://orion:1026", redis_url="redis://x/0", context_url=CTX)

    async def go():
        await hc.start()
        await hc.stop()
        await hc.start()
        c = hc._client
        await hc.stop()
        return c

    assert asyncio.run(go()).is_closed


def test_ref_device_with_reserved_characters_is_quoted_in_patch_url():
    client = FakeClient()
    asyncio.run(_hc(None, client).update_reliability_status("urn:x:a/b?c", TENANT, "error"))
    assert client.patches[0][0].endswith("/entities/urn:x:a%2Fb%3Fc/attrs")


# --- evaluate_measurement is fail-safe ---

@pytest.mark.parametrize("cfg", [
    None, [], "x", 5,
    {"t": "oops"}, {"t": 3}, {"t": ["a"]},
    {"t": {"minValid": "1"}}, {"t": {"maxValid": "9"}},
    {"t": {"minValid": True}}, {"t": {"maxValid": False}},
    {"t": {"minValid": float("nan")}}, {"t": {"maxValid": float("nan")}},
])
def test_evaluate_never_raises_on_malformed_config(cfg):
    hc = _hc(None, None)
    assert hc.evaluate_measurement("t", 0.5, cfg) == "valid"


def test_evaluate_one_bad_threshold_makes_the_variable_valid():
    hc = _hc(None, None)
    assert hc.evaluate_measurement("t", 100.0, {"t": {"minValid": 0, "maxValid": "9"}}) == "valid"


def test_evaluate_warns_once_per_tenant_device_window(caplog):
    hc = _hc(None, None)
    with caplog.at_level(logging.WARNING, logger=hcm.__name__):
        for _ in range(4):
            hc.evaluate_measurement("t", 5.0, {"t": "oops"}, context=("tenant-a", "dev-1"))
        assert len(caplog.records) == 1
        hc.evaluate_measurement("t", 5.0, {"t": "oops"}, context=("tenant-a", "dev-2"))
        hc.evaluate_measurement("t", 5.0, {"t": "oops"}, context=("tenant-b", "dev-1"))
        assert len(caplog.records) == 3
    assert hc._config_warnings._window_s == CACHE_TTL


def test_evaluate_alias_only_when_asked():
    hc = _hc(None, None)
    cfg = {"temperature": {"maxValid": 40}}
    assert hc.evaluate_measurement("airTemperature", 99.0, cfg) == "valid"
    assert hc.evaluate_measurement("airTemperature", 99.0, cfg, aliases=True) == "out_of_bounds"
