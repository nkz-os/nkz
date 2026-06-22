"""
Calibration service for the telemetry-worker.

Looks up active calibration periods and transforms raw values
into calibrated values.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

import asyncpg
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

CACHE_PREFIX = "sensor_calibration:"
CACHE_TTL = 300  # 5 minutes


class CalibrationService:
    """Manages calibration lookup and application for sensor measurements."""

    def __init__(self, redis_url: str, pg_pool: Optional[asyncpg.Pool] = None):
        self._redis_url = redis_url
        self._pg_pool = pg_pool
        self._redis: Optional[aioredis.Redis] = None

    def set_pool(self, pool: asyncpg.Pool) -> None:
        """Set the asyncpg pool (called after pool is created in lifespan)."""
        self._pg_pool = pool

    async def start(self) -> None:
        """Initialize Redis connection."""
        try:
            self._redis = aioredis.from_url(
                self._redis_url, decode_responses=True
            )
            await self._redis.ping()
            logger.info("CalibrationService connected to Redis")
        except Exception as e:
            logger.warning(f"Redis unavailable for calibration cache: {e}")

    async def stop(self) -> None:
        if self._redis:
            await self._redis.close()

    async def get_active_period(
        self, sensor_id: str, tenant_id: str, variable: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get the active calibration period for a sensor+variable.

        Returns dict with {id, slope, offset_val} or None.
        Results are cached in Redis.
        """
        cache_key = f"{CACHE_PREFIX}{tenant_id}:{sensor_id}:{variable}"

        # Try Redis cache
        if self._redis:
            try:
                cached = await self._redis.get(cache_key)
                if cached:
                    return json.loads(cached)
            except Exception:
                pass

        # Query PostgreSQL
        if not self._pg_pool:
            return None

        try:
            async with self._pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, slope, offset_val
                    FROM calibration_periods
                    WHERE sensor_id = $1
                      AND tenant_id = $2
                      AND variable = $3
                      AND valid_from <= NOW()
                      AND (valid_to IS NULL OR valid_to > NOW())
                    ORDER BY valid_from DESC
                    LIMIT 1
                    """,
                    sensor_id,
                    tenant_id,
                    variable,
                )
                if row:
                    result = {
                        "id": str(row["id"]),
                        "slope": float(row["slope"]),
                        "offset_val": float(row["offset_val"]),
                    }
                    # Cache in Redis
                    if self._redis:
                        try:
                            await self._redis.setex(
                                cache_key, CACHE_TTL, json.dumps(result)
                            )
                        except Exception:
                            pass
                    return result
        except Exception as e:
            logger.error(f"Error querying calibration for {sensor_id}/{variable}: {e}")

        return None

    def apply_calibration(
        self,
        raw_value: Any,
        calibration: Optional[Dict[str, Any]],
    ) -> Any:
        """
        Apply calibration to a raw value.
        If no calibration, returns raw_value unchanged.
        """
        if calibration is None or not isinstance(raw_value, (int, float)):
            return raw_value
        slope = calibration.get("slope", 1.0)
        offset_val = calibration.get("offset_val", 0.0)
        return raw_value * slope + offset_val

    async def invalidate_cache(
        self, sensor_id: str, tenant_id: str, variable: Optional[str] = None
    ) -> None:
        """Invalidate cached calibration (call after updating calibration periods)."""
        if not self._redis:
            return
        try:
            pattern = f"{CACHE_PREFIX}{tenant_id}:{sensor_id}:"
            if variable:
                pattern += variable
            else:
                pattern += "*"
            keys = await self._redis.keys(pattern)
            if keys:
                await self._redis.delete(*keys)
        except Exception as e:
            logger.warning(f"Failed to invalidate calibration cache: {e}")
