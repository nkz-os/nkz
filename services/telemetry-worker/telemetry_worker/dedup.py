"""
Notification deduplication for Orion-LD subscription notifications.

Orion-LD can redeliver the same notification (network retries, subscription
replay after a restart, etc.), which would otherwise cause duplicate rows in
TimescaleDB and inflate historical aggregations. This module tracks
recently-seen (tenant, entity, observedAt, measurement-hash) tuples in Redis
with a short TTL and flags repeats as duplicates so the caller can skip the
write.

FAIL OPEN (mandatory): if Redis is unavailable, unreachable at startup, or
any Redis call raises, the event is treated as NOT a duplicate and the write
proceeds. A dropped telemetry reading is strictly worse than an occasional
duplicate row — never let a Redis outage silently discard data.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

KEY_PREFIX = "telemetry:dedup:"
DEFAULT_TTL_SECONDS = 300  # 5 minutes — covers Orion's typical retry/replay window
HASH_LENGTH = 16


def _measurement_hash(measurements: Dict[str, Any]) -> str:
    """
    Short stable hash of measurement values.

    Included in the dedup key so two DIFFERENT readings delivered with the
    same observedAt (e.g. clock-quantized sensors) are NOT collapsed into a
    single key — only byte-identical repeats are deduplicated.
    """
    canonical = json.dumps(measurements, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:HASH_LENGTH]


def _observed_at_key(observed_at: datetime) -> str:
    """Stable string form of observedAt for the dedup key."""
    try:
        return observed_at.isoformat()
    except AttributeError:
        return str(observed_at)


class NotificationDedup:
    """
    Redis-backed deduplication of Orion-LD notification events.

    Key scheme: telemetry:dedup:{tenant_id}:{entity_id}:{observed_at}:{hash}
    - tenant_id + entity_id: scope the key to a specific sensor/tenant
    - observed_at: the measurement timestamp Orion reports — this is the
      natural per-event identity for telemetry (Orion notifications don't
      carry a stable notification/message id we can rely on across retries)
    - hash: sha256(measurements)[:16] — guards against two distinct readings
      sharing an observedAt being wrongly collapsed

    Uses `SET key 1 NX EX <ttl>`: atomic set-if-not-exists. Redis returns a
    falsy result if the key already existed (duplicate); truthy if it just
    set the key (new event).

    Reuses the exact aioredis pattern from HealthChecker/CalibrationService
    (`redis.asyncio.from_url` + `decode_responses=True`) and is intended to
    share the same `settings.redis_url` those services use.
    """

    def __init__(
        self,
        redis_url: str,
        enabled: bool = True,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ):
        self._redis_url = redis_url
        self.enabled = enabled
        self._ttl = ttl_seconds
        self._redis: Optional[aioredis.Redis] = None

    async def start(self) -> None:
        """Initialize Redis connection. Never raises — fail-open on connect error."""
        if not self.enabled:
            logger.info("NotificationDedup disabled via TELEMETRY_DEDUP_ENABLED=false")
            return
        try:
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
            await self._redis.ping()
            logger.info("NotificationDedup connected to Redis")
        except Exception as e:
            logger.warning(
                f"Redis unavailable, notification dedup runs fail-open (no dedup): {e}"
            )
            self._redis = None

    async def stop(self) -> None:
        """Close Redis connection."""
        if self._redis:
            try:
                await self._redis.close()
            except Exception as e:
                logger.warning(f"Error closing dedup Redis connection: {e}")

    def _build_key(
        self,
        tenant_id: Optional[str],
        entity_id: str,
        observed_at: datetime,
        measurements: Dict[str, Any],
    ) -> str:
        tenant = tenant_id or "unknown"
        ts = _observed_at_key(observed_at)
        h = _measurement_hash(measurements)
        return f"{KEY_PREFIX}{tenant}:{entity_id}:{ts}:{h}"

    async def is_duplicate(
        self,
        tenant_id: Optional[str],
        entity_id: str,
        observed_at: datetime,
        measurements: Dict[str, Any],
    ) -> bool:
        """
        Returns True if this exact event was already seen within the TTL
        window (caller should skip the write), False otherwise (new event,
        or dedup disabled/unavailable — proceed with the write).

        FAIL OPEN: any exception talking to Redis results in False.
        """
        if not self.enabled:
            return False

        if not self._redis:
            # Redis was never connected (down at startup) — fail open.
            return False

        key = self._build_key(tenant_id, entity_id, observed_at, measurements)
        try:
            was_set = await self._redis.set(key, "1", nx=True, ex=self._ttl)
            return not was_set  # falsy (None/False) => key pre-existed => duplicate
        except Exception as e:
            logger.warning(
                f"Redis error during dedup check for entity={entity_id}, "
                f"failing open (treating as new event): {e}"
            )
            return False
