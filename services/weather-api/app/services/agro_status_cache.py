"""Short-TTL Redis cache for parcel agro-status responses.

Fail-open by design: if Redis is unconfigured or unavailable, reads degrade to
cache misses and writes are skipped so the endpoint always computes normally.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from fastapi.encoders import jsonable_encoder

logger = logging.getLogger(__name__)

DEFAULT_TTL_S = 600
_KEY_PREFIX = "weather:agro_status:v1"


def cache_key(tenant_id: str, parcel_id: str) -> str:
    """Tenant-isolated cache key for a parcel's agro-status response."""
    return f"{_KEY_PREFIX}:{tenant_id}:{parcel_id}"


def _parse_ttl(raw: Optional[str], default: int = DEFAULT_TTL_S) -> int:
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        logger.warning(
            "Invalid AGRO_STATUS_CACHE_TTL_S value; using default %ds", default
        )
        return default


class AgroStatusCache:
    """Best-effort Redis cache for GET /parcel/{parcel_id}/agro-status."""

    def __init__(self, client=None, ttl_s: int = DEFAULT_TTL_S):
        self._client = client
        self.ttl_s = ttl_s

    @property
    def enabled(self) -> bool:
        return self._client is not None and self.ttl_s > 0

    def get(self, tenant_id: str, parcel_id: str) -> Optional[dict]:
        if not self.enabled:
            return None
        try:
            raw = self._client.get(cache_key(tenant_id, parcel_id))
            if raw is None:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except Exception as exc:  # noqa: BLE001 — fail-open
            logger.warning(
                "agro-status cache read failed (%s); falling back to compute",
                type(exc).__name__,
            )
            return None

    def set(self, tenant_id: str, parcel_id: str, payload: dict) -> bool:
        if not self.enabled:
            return False
        try:
            encoded = json.dumps(jsonable_encoder(payload), separators=(",", ":"))
            self._client.setex(cache_key(tenant_id, parcel_id), self.ttl_s, encoded)
            return True
        except Exception as exc:  # noqa: BLE001 — fail-open
            logger.warning(
                "agro-status cache write failed (%s); continuing without cache",
                type(exc).__name__,
            )
            return False


_cache: Optional[AgroStatusCache] = None


def get_agro_status_cache() -> AgroStatusCache:
    """Return process-wide Redis cache; disabled (fail-open) if unconfigured."""
    global _cache
    if _cache is not None:
        return _cache

    ttl_s = _parse_ttl(os.getenv("AGRO_STATUS_CACHE_TTL_S"), DEFAULT_TTL_S)
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url or ttl_s <= 0:
        if not redis_url:
            logger.warning(
                "agro-status Redis cache disabled: REDIS_URL not configured"
            )
        _cache = AgroStatusCache(None, ttl_s)
        return _cache

    try:
        import redis

        kwargs: dict = {
            "decode_responses": True,
            "socket_connect_timeout": 1.0,
            "socket_timeout": 1.0,
        }
        redis_password = os.getenv("REDIS_PASSWORD", "").strip()
        if redis_password:
            kwargs["password"] = redis_password

        client = redis.from_url(redis_url, **kwargs)
        _cache = AgroStatusCache(client, ttl_s)
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "agro-status Redis cache unavailable (%s); continuing without cache",
            type(exc).__name__,
        )
        _cache = AgroStatusCache(None, ttl_s)
    return _cache


def reset_agro_status_cache() -> None:
    """Reset the singleton cache (for tests)."""
    global _cache
    _cache = None
