"""Shared fakes for the lookup-cache tests (Redis with setex, scripted httpx client)."""

import httpx


class CacheRedis:
    """In-memory stand-in for redis.asyncio.Redis (get / setex / keys / delete)."""

    def __init__(self):
        self.store = {}
        self.setex_calls = []

    async def ping(self):
        return True

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl, value))
        self.store[key] = value

    async def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in self.store if k.startswith(prefix)]

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)

    async def close(self):
        pass


class DownRedis:
    """Every call fails, as an unreachable Redis does."""

    async def get(self, *a, **k):
        raise ConnectionError("redis down")

    setex = keys = delete = get

    async def close(self):
        pass


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {}

    def json(self):
        return self._body


class FakeClient:
    """Scripted stand-in for httpx.AsyncClient: `get_script` is a list of responses or exceptions."""

    def __init__(self, get_script=None, patch_status=204):
        self.get_script = list(get_script or [])
        self.patch_status = patch_status
        self.gets = []
        self.patches = []
        self.closed = False

    async def get(self, url, headers=None, timeout=None):
        self.gets.append((url, headers, timeout))
        item = self.get_script.pop(0) if self.get_script else FakeResponse(404)
        if isinstance(item, Exception):
            raise item
        return item

    async def patch(self, url, json=None, headers=None, timeout=None):
        self.patches.append((url, json, headers, timeout))
        return FakeResponse(self.patch_status)

    async def aclose(self):
        self.closed = True


def health_property(config):
    return {"id": "x", "type": "Device", "healthConfig": {"type": "Property", "value": config}}


TIMEOUT = httpx.ReadTimeout("slow")
