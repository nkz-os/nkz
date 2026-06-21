"""
Sensor Health Beat — periodic detection of stagnation and timeouts.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import asyncpg
import httpx

from .config import Settings

logger = logging.getLogger(__name__)


class SensorHealthBeat:
    """Runs periodic health checks against all AgriSensor entities."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._pg_pool: Optional[asyncpg.Pool] = None

    async def start(self) -> None:
        """Initialize PostgreSQL connection pool."""
        if self.settings.timescale_dsn:
            self._pg_pool = await asyncpg.create_pool(
                dsn=self.settings.timescale_dsn, min_size=2, max_size=5,
            )
        logger.info("SensorHealthBeat started")

    async def stop(self) -> None:
        if self._pg_pool:
            await self._pg_pool.close()

    async def run_once(self) -> None:
        """Executes one full health check cycle."""
        logger.info("Starting health beat cycle")
        tenants = await self._list_tenants_with_sensors()
        for tenant_id in tenants:
            await self._check_tenant(tenant_id)
        logger.info(f"Health beat cycle complete for {len(tenants)} tenants")

    async def _list_tenants_with_sensors(self) -> List[str]:
        """Query TimescaleDB for distinct tenants with telemetry data."""
        if not self._pg_pool:
            return []
        async with self._pg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT tenant_id FROM telemetry_events WHERE tenant_id IS NOT NULL"
            )
            return [r["tenant_id"] for r in rows]

    async def _check_tenant(self, tenant_id: str) -> None:
        sensors = await self._fetch_sensors(tenant_id)
        for sensor in sensors:
            await self._check_sensor(tenant_id, sensor)

    async def _fetch_sensors(self, tenant_id: str) -> List[Dict[str, Any]]:
        sensors = []
        url = (
            f"{self.settings.orion_url}/ngsi-ld/v1/entities"
            f"?type=AgriSensor&attrs=healthConfig,reliabilityStatus,isSilenced,location"
            f"&limit=500"
        )
        headers = {
            "NGSILD-Tenant": tenant_id,
            "Fiware-Service": tenant_id,
            "Fiware-ServicePath": "/",
            "Accept": "application/json",
            "Link": f'<{self.settings.context_url}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"',
        }
        async with httpx.AsyncClient() as client:
            while url:
                try:
                    resp = await client.get(url, headers=headers, timeout=30)
                    if resp.status_code != 200:
                        logger.warning(
                            f"Failed to fetch sensors for {tenant_id}: {resp.status_code}"
                        )
                        break
                    batch = resp.json()
                    if not isinstance(batch, list):
                        break
                    sensors.extend(batch)
                    link_header = resp.headers.get("Link", "")
                    url = None
                    if link_header:
                        for part in link_header.split(","):
                            if 'rel="next"' in part:
                                url = part.split(";")[0].strip("<> ")
                                break
                except Exception as e:
                    logger.error(f"Error fetching sensors: {e}")
                    break
        return sensors

    async def _check_sensor(self, tenant_id: str, sensor: Dict[str, Any]) -> None:
        entity_id = sensor.get("id")
        if not entity_id:
            return

        # Extract healthConfig
        health_config = sensor.get("healthConfig", {})
        if isinstance(health_config, dict):
            health_config = health_config.get("value", health_config)
        if not health_config or not isinstance(health_config, dict):
            return

        # Extract isSilenced
        is_silenced = sensor.get("isSilenced", {})
        if isinstance(is_silenced, dict):
            is_silenced = is_silenced.get("value", False)

        # Extract current reliabilityStatus
        current_status = sensor.get("reliabilityStatus", {})
        if isinstance(current_status, dict):
            current_status_val = current_status.get("value", "optimal")
        else:
            current_status_val = "optimal"

        timeout_hours = health_config.get("communicationTimeoutHours", 24)

        # Check timeout: when was the last data point?
        last_observed = await self._get_last_observed(tenant_id, entity_id)
        if last_observed:
            delta = datetime.now(timezone.utc) - last_observed.replace(tzinfo=timezone.utc)
            if delta > timedelta(hours=timeout_hours):
                logger.info(
                    f"Sensor {entity_id} timeout: {delta.total_seconds()/3600:.1f}h > {timeout_hours}h"
                )
                await self._set_status(tenant_id, entity_id, "error")
                return

        # Check stagnation per variable
        for variable, var_config in health_config.items():
            if variable == "communicationTimeoutHours":
                continue
            if not isinstance(var_config, dict):
                continue
            max_stagnant = var_config.get("maxStagnantHours")
            if not max_stagnant:
                continue
            is_stagnant = await self._check_stagnation(
                tenant_id, entity_id, variable, int(max_stagnant)
            )
            if is_stagnant:
                logger.info(
                    f"Sensor {entity_id} variable {variable}: stagnant > {max_stagnant}h"
                )
                await self._set_status(tenant_id, entity_id, "error")
                return

        # Recovery check: if currently degraded/error, check if data recovered
        if current_status_val in ("degraded", "error"):
            recovered = await self._check_recovery(tenant_id, entity_id)
            if recovered:
                logger.info(f"Sensor {entity_id}: recovered, setting to optimal")
                await self._set_status(tenant_id, entity_id, "optimal")
                return

    async def _get_last_observed(self, tenant_id: str, entity_id: str) -> Optional[datetime]:
        if not self._pg_pool:
            return None
        async with self._pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT observed_at FROM telemetry_events
                WHERE tenant_id = $1 AND entity_id = $2
                ORDER BY observed_at DESC LIMIT 1
                """,
                tenant_id,
                entity_id,
            )
            return row["observed_at"] if row else None

    async def _check_stagnation(
        self, tenant_id: str, entity_id: str, variable: str, max_hours: int
    ) -> bool:
        if not self._pg_pool:
            return False
        window_hours = max(max_hours * 2, self.settings.stagnation_query_window_hours)
        async with self._pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) AS cnt,
                    STDDEV_POP((payload->'measurements'->>$3)::numeric) AS stddev_val,
                    MAX(observed_at) AS last_ts
                FROM telemetry_events
                WHERE tenant_id = $1
                  AND entity_id = $2
                  AND observed_at > NOW() - ($4 || ' hours')::INTERVAL
                  AND payload->'measurements' ? $3
                """,
                tenant_id,
                entity_id,
                variable,
                str(window_hours),
            )
            if not row or row["cnt"] is None or row["cnt"] < 2:
                return False
            stddev_val = row["stddev_val"]
            if stddev_val is not None and float(stddev_val) == 0.0:
                last_ts = row["last_ts"]
                if last_ts:
                    stagnant_hours = (
                        datetime.now(timezone.utc) - last_ts.replace(tzinfo=timezone.utc)
                    ).total_seconds() / 3600
                    return stagnant_hours >= max_hours
        return False

    async def _check_recovery(
        self, tenant_id: str, entity_id: str
    ) -> bool:
        """Check if the last N data points are all valid."""
        if not self._pg_pool:
            return False
        async with self._pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT quality_flag FROM telemetry_events
                WHERE tenant_id = $1 AND entity_id = $2
                  AND quality_flag IS NOT NULL
                ORDER BY observed_at DESC
                LIMIT $3
                """,
                tenant_id,
                entity_id,
                self.settings.recovery_valid_count,
            )
            if len(rows) < self.settings.recovery_valid_count:
                return False
            return all(r["quality_flag"] == "valid" for r in rows)

    async def _set_status(self, tenant_id: str, entity_id: str, status: str) -> bool:
        async with httpx.AsyncClient() as client:
            headers = {
                "NGSILD-Tenant": tenant_id,
                "Fiware-Service": tenant_id,
                "Fiware-ServicePath": "/",
                "Content-Type": "application/json",
                "Link": f'<{self.settings.context_url}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"',
            }
            body = {
                "reliabilityStatus": {
                    "type": "Property",
                    "value": status,
                    "observedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            }
            url = f"{self.settings.orion_url}/ngsi-ld/v1/entities/{entity_id}/attrs"
            try:
                resp = await client.patch(url, json=body, headers=headers, timeout=10)
                return resp.status_code in (200, 204)
            except Exception as e:
                logger.error(f"Error updating status for {entity_id}: {e}")
                return False
