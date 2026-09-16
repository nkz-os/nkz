"""Redis auth wiring for entity-manager's sync cache (2026-09).

`blueprints/sync.py` built its redis client from REDIS_URL only. The in-repo
default even embedded a password in the URL (`redis://:default@...`) which the
gitops overlay then overrode with a bare URL — leaving the sync cache with no
credentials against the cluster's requirepass redis. The password now travels
in its own REDIS_PASSWORD env (same pattern as telemetry-worker and
risk-orchestrator's task queue), never embedded in the URL.
"""

import importlib.util
import os
import sys
from unittest.mock import patch

_test_dir = os.path.dirname(os.path.abspath(__file__))
_em_dir = os.path.normpath(os.path.join(_test_dir, ".."))
_services_dir = os.path.normpath(os.path.join(_em_dir, ".."))
_common_dir = os.path.join(_services_dir, "common")
for _p in (_em_dir, _services_dir, _common_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_spec = importlib.util.spec_from_file_location(
    "blueprints.sync", os.path.join(_em_dir, "blueprints", "sync.py")
)
sync = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(sync)


class _StubRedis:
    def ping(self):
        return True


def test_get_redis_passes_configured_password():
    captured = {}

    def fake_from_url(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _StubRedis()

    with patch.object(sync.redis.Redis, "from_url", side_effect=fake_from_url):
        sync._REDIS_PASSWORD = "sync-pw"
        client = sync._get_redis()

    assert client is not None
    assert captured.get("password") == "sync-pw"


def test_get_redis_without_password_sends_none():
    captured = {}

    def fake_from_url(url, **kwargs):
        captured.update(kwargs)
        return _StubRedis()

    with patch.object(sync.redis.Redis, "from_url", side_effect=fake_from_url):
        sync._REDIS_PASSWORD = ""
        client = sync._get_redis()

    assert client is not None
    assert captured.get("password") is None
