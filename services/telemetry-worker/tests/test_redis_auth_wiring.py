"""Redis auth wiring for telemetry-worker (2026-09: redis requires a password).

The cluster's redis runs with --requirepass (gitops redis-secret/password).
Before this change every redis client in telemetry-worker connected with
`from_url(settings.redis_url)` — no credentials — so dedup, profiles cache,
calibration cache and health-checker cache all silently ran in degraded
DB-only / fail-open mode ("Authentication required").

These tests pin the wiring: the configured REDIS_PASSWORD must reach every
`from_url` call, and an empty password must stay `None` (sending AUTH to a
passwordless redis is an error, not a no-op).
"""

import asyncio
import os
import sys
from unittest.mock import patch

# ── Path setup (mirrors other telemetry-worker tests) ──────────────────────
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")

for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import telemetry_worker.dedup as dedup_module
from telemetry_worker.config import Settings
from telemetry_worker.dedup import NotificationDedup


class _StubAsyncRedis:
    async def ping(self):
        return True

    async def close(self):
        pass


def test_dedup_start_passes_password():
    captured = {}

    def fake_from_url(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _StubAsyncRedis()

    with patch.object(dedup_module.aioredis, "from_url", side_effect=fake_from_url):
        d = NotificationDedup(
            redis_url="redis://redis-service:6379/0",
            redis_password="s3cret",
        )
        asyncio.run(d.start())

    assert captured.get("password") == "s3cret"


def test_dedup_start_without_password_sends_none():
    captured = {}

    def fake_from_url(url, **kwargs):
        captured.update(kwargs)
        return _StubAsyncRedis()

    with patch.object(dedup_module.aioredis, "from_url", side_effect=fake_from_url):
        d = NotificationDedup(redis_url="redis://redis-service:6379/0")
        asyncio.run(d.start())

    assert captured.get("password") is None


def test_settings_reads_redis_password_env(monkeypatch):
    monkeypatch.setenv("REDIS_PASSWORD", "env-secret")
    monkeypatch.setenv("POSTGRES_URL", "postgresql://t:t@localhost:5/t")
    s = Settings()
    assert s.redis_password == "env-secret"


def test_profiles_init_passes_password():
    import telemetry_worker.profiles as profiles_module
    from telemetry_worker.profiles import ProfileService

    captured = {}

    class _StubSyncRedis:
        def ping(self):
            return True

    def fake_from_url(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _StubSyncRedis()

    settings = Settings(
        postgres_url="postgresql://t:t@localhost:5/t", redis_password="pw123"
    )
    with patch.object(profiles_module.redis, "from_url", side_effect=fake_from_url):
        ProfileService(settings)

    assert captured.get("password") == "pw123"
