"""
Health evaluation service for the telemetry-worker.

Reads healthConfig from Orion-LD (cached in Redis), evaluates
measurement values against thresholds, and determines quality_flag.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

import httpx
import redis.asyncio as aioredis

from common.context_aliases import canonical_attribute
from .log_throttle import ThrottledWarnings

logger = logging.getLogger(__name__)

CACHE_PREFIX = "sensor_health_config:"
CACHE_TTL = 300  # 5 minutes
# Cached in place of a config when the entity definitely has none (404, or 200 without the
# attribute). Not valid JSON, so it can never be mistaken for a config.
ABSENT_MARKER = "__absent__"
_HTTP_TIMEOUT = 10


class HealthChecker:
    """Evaluates sensor measurements against configured health thresholds."""

    def __init__(self, orion_url: str, redis_url: str, context_url: str, redis_password: Optional[str] = None):
        self._orion_url = orion_url.rstrip("/")
        self._context_url = context_url
        self._redis: Optional[aioredis.Redis] = None
        self._redis_url = redis_url
        self._redis_password = redis_password
        self._client: Optional[httpx.AsyncClient] = None
        self._stopped = False
        # One line per (tenant, device) per cache lifetime of the configuration.
        self._config_warnings = ThrottledWarnings(logger, window_s=CACHE_TTL)

    def _get_client(self) -> httpx.AsyncClient:
        """Shared HTTP client (created here when start() was not called)."""
        if self._stopped:
            raise RuntimeError("HealthChecker is stopped")
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client

    async def start(self) -> None:
        """Create the shared HTTP client and initialize Redis connection."""
        self._stopped = False
        self._get_client()
        try:
            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                password=self._redis_password or None,
            )
            await self._redis.ping()
            logger.info("HealthChecker connected to Redis")
        except Exception as e:
            logger.warning(f"Redis unavailable, health checker runs uncached: {e}")

    async def stop(self) -> None:
        """Close the HTTP client and the Redis connection."""
        self._stopped = True
        client, self._client = self._client, None
        try:
            if client is not None:
                await client.aclose()
        finally:
            redis, self._redis = self._redis, None
            if redis:
                await redis.close()

    async def get_health_config(
        self, entity_id: str, tenant_id: str, force_refresh: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Fetch healthConfig from Redis cache or Orion-LD.

        A definite absence (404, or 200 without the attribute) is cached as ABSENT_MARKER
        for CACHE_TTL. Errors, timeouts and other statuses are not cached.
        """
        cache_key = f"{CACHE_PREFIX}{tenant_id}:{entity_id}"

        # Try cache first
        if self._redis and not force_refresh:
            try:
                cached = await self._redis.get(cache_key)
                if cached == ABSENT_MARKER:
                    return None
                if cached:
                    return json.loads(cached)
            except Exception:
                pass

        # Fetch from Orion-LD
        headers = {
            "NGSILD-Tenant": tenant_id,
            "Fiware-Service": tenant_id,
            "Fiware-ServicePath": "/",
            "Accept": "application/json",
            "Link": self._link_header(),
        }
        quoted_id = quote(entity_id, safe=":")
        url = f"{self._orion_url}/ngsi-ld/v1/entities/{quoted_id}?attrs=healthConfig"
        try:
            resp = await self._get_client().get(url, headers=headers, timeout=_HTTP_TIMEOUT)
            if resp.status_code == 200:
                entity = resp.json()
                hc = entity.get("healthConfig", {})
                health_config = hc.get("value") if isinstance(hc, dict) else None
                # Orion answers 404 for an existing entity that lacks the attribute and
                # 200 without it elsewhere: both are "no configuration".
                await self._cache_set(cache_key, json.dumps(health_config) if health_config else ABSENT_MARKER)
                return health_config
            if resp.status_code == 404:
                logger.debug(f"No healthConfig for {entity_id}: not found")
                await self._cache_set(cache_key, ABSENT_MARKER)
                return None
            logger.warning(
                f"Failed to fetch healthConfig for {entity_id}: {resp.status_code}"
            )
        except Exception as e:
            logger.error(f"Error fetching healthConfig: {e}")

        return None

    def _link_header(self) -> str:
        return f'<{self._context_url}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"'

    async def _cache_set(self, key: str, value: str) -> None:
        """Best effort: a Redis failure never changes the result of a lookup."""
        if not self._redis:
            return
        try:
            await self._redis.setex(key, CACHE_TTL, value)
        except Exception:
            pass

    def _invalid_config(self, context: Optional[Tuple[str, str]], variable: str, detail: str) -> str:
        tenant, device = context or ("", "")
        self._config_warnings.warning(
            (tenant, device),
            "Invalid health configuration, readings treated as valid: "
            "tenant_id=%s device=%s variable=%s: %s",
            tenant, device, variable, detail,
        )
        return "valid"

    def evaluate_measurement(
        self,
        variable: str,
        value: Any,
        health_config: Any,
        *,
        context: Optional[Tuple[str, str]] = None,
        aliases: bool = False,
    ) -> str:
        """
        Evaluate a single measurement against health thresholds.

        Returns quality_flag: 'valid', 'nan', 'out_of_bounds'. A malformed configuration
        (not a dict, entry not a dict, threshold not a real number) never raises: the
        variable is 'valid' and a throttled warning names `context` = (tenant, device).
        `aliases`: when the exact `variable` key is absent, accept a config key whose
        canonical attribute name equals it (`temperature` for channel `airTemperature`).
        """
        if value is None:
            return "nan"

        if not isinstance(value, (int, float)):
            # Non-numeric measurement cannot be evaluated
            return "valid"

        if not isinstance(health_config, dict):
            return self._invalid_config(context, variable, "configuration is not an object")

        if variable in health_config:
            var_config = health_config[variable]
        else:
            var_config = None
            if aliases:
                for key, candidate in health_config.items():
                    if isinstance(key, str) and canonical_attribute(key) == variable:
                        var_config = candidate
                        break
        if not var_config:
            return "valid"
        if not isinstance(var_config, dict):
            return self._invalid_config(context, variable, "variable entry is not an object")

        min_valid = var_config.get("minValid")
        max_valid = var_config.get("maxValid")
        for name, threshold in (("minValid", min_valid), ("maxValid", max_valid)):
            if threshold is None:
                continue
            if (
                isinstance(threshold, bool)
                or not isinstance(threshold, (int, float))
                or math.isnan(threshold)
            ):
                return self._invalid_config(context, variable, f"{name} is not a number")

        if min_valid is not None and value < min_valid:
            return "out_of_bounds"
        if max_valid is not None and value > max_valid:
            return "out_of_bounds"

        return "valid"

    async def update_reliability_status(
        self,
        entity_id: str,
        tenant_id: str,
        new_status: str,
    ) -> bool:
        """PATCH reliabilityStatus on the sensor entity in Orion-LD."""
        headers = {
            "NGSILD-Tenant": tenant_id,
            "Fiware-Service": tenant_id,
            "Fiware-ServicePath": "/",
            "Content-Type": "application/json",
            "Link": self._link_header(),
        }
        body = {
            "reliabilityStatus": {
                "type": "Property",
                "value": new_status,
                "observedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        }
        quoted_id = quote(entity_id, safe=":")
        url = f"{self._orion_url}/ngsi-ld/v1/entities/{quoted_id}/attrs"
        try:
            resp = await self._get_client().patch(
                url, json=body, headers=headers, timeout=_HTTP_TIMEOUT
            )
            if resp.status_code not in (200, 204):
                logger.warning(
                    f"Failed to update reliabilityStatus for {entity_id}: {resp.status_code}"
                )
                return False
            return True
        except Exception as e:
            logger.error(f"Error updating reliabilityStatus: {e}")
            return False


def _severity(flag: str) -> int:
    """Higher = worse quality."""
    return {"valid": 0, "nan": 1, "out_of_bounds": 2, "stale": 3}.get(flag, 0)
