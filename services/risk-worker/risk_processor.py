#!/usr/bin/env python3
# =============================================================================
# Risk Worker - Evaluate Risks for Entities
# =============================================================================
# Batch worker that evaluates risks for all active entities
# Runs hourly via CronJob for batch risks (agronomic, energy)
# Real-time risks are handled via webhook/event-driven flow

import os
import sys
import logging
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

import requests
import psycopg2
from psycopg2.extras import RealDictCursor

# Add paths for imports
sys.path.insert(0, "/app/task-queue")
sys.path.insert(0, "/app/common")
sys.path.insert(0, "/app")

# Import task_queue module
try:
    import importlib.util

    task_queue_file = "/app/task-queue/task_queue.py"
    if os.path.exists(task_queue_file):
        spec = importlib.util.spec_from_file_location("task_queue", task_queue_file)
        task_queue_module = importlib.util.module_from_spec(spec)
        sys.modules["task_queue"] = task_queue_module
        spec.loader.exec_module(task_queue_module)
        TaskQueue = task_queue_module.TaskQueue
        logger = logging.getLogger(__name__)
        logger.info("TaskQueue module loaded successfully")
    else:
        raise ImportError(f"task_queue.py not found at {task_queue_file}")
except Exception as e:
    logger = logging.getLogger(__name__)
    logger.error(f"Failed to load TaskQueue module: {e}")
    raise

from db_helper import set_tenant_context
from common.ngsi_headers import inject_fiware_headers
from weather_source import (
    fetch_parcel_weather,
    fetch_season_gdd,
    resolve_parcel_id as _resolve_parcel_id,
)

# Import risk models
from risk_models.factory import RiskModelFactory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
POSTGRES_URL = os.getenv("POSTGRES_URL")
POSTGRES_USER = os.getenv("POSTGRES_USER", "nekazari")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgresql-service")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "nekazari")

ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")
CONTEXT_URL = os.getenv(
    "CONTEXT_URL", "http://api-gateway-service:5000/ngsi-ld-context.json"
)
REDIS_URL = os.getenv("REDIS_URL", "redis://redis-service:6379")
WEATHER_API_URL = os.getenv("WEATHER_API_URL", "http://weather-api-service:8000")


def _make_headers(tenant_id: str) -> dict:
    """Build Orion-LD headers with normalized tenant ID."""
    return inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False)


def _make_orion_headers(
    tenant_id: str, content_type: str = "application/ld+json"
) -> dict:
    """Build Orion-LD headers for entity creation (POST)."""
    return inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=(content_type == "application/ld+json"))


# Build PostgreSQL URL
if POSTGRES_PASSWORD:
    POSTGRES_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
    logger.info(
        f"PostgreSQL URL built from components (host: {POSTGRES_HOST}, db: {POSTGRES_DB})"
    )
elif POSTGRES_URL:
    logger.warning("PostgreSQL URL provided but no password - connection may fail")
else:
    POSTGRES_URL = None
    logger.warning("PostgreSQL configuration not available")


class RiskProcessor:
    """Process risk evaluations for entities"""

    def __init__(self):
        self.postgres = None
        self.redis_queue = None
        self._init_connections()

    def _init_connections(self):
        """Initialize database and queue connections"""
        if POSTGRES_URL:
            try:
                self.postgres = psycopg2.connect(
                    POSTGRES_URL, cursor_factory=RealDictCursor
                )
                self.postgres.autocommit = False
                logger.info("PostgreSQL connected")
            except Exception as e:
                logger.error(f"PostgreSQL connection failed: {e}")

        try:
            self.redis_queue = TaskQueue(stream_name="risk:events")
            logger.info("Redis Streams initialized")
        except Exception as e:
            logger.warning(f"Redis Streams not available: {e}")

    def _get_risk_catalog(self, evaluation_mode: str = "batch") -> List[Dict[str, Any]]:
        """Get active risks from catalog"""
        if not self.postgres:
            return []

        try:
            cursor = self.postgres.cursor()
            cursor.execute(
                """
                SELECT 
                    risk_code, risk_name, target_sdm_type, target_subtype,
                    data_sources, risk_domain, evaluation_mode,
                    model_type, model_config, severity_levels
                FROM admin_platform.risk_catalog
                WHERE is_active = TRUE
                  AND evaluation_mode = %s
                ORDER BY risk_code
            """,
                (evaluation_mode,),
            )

            risks = cursor.fetchall()
            cursor.close()
            return [dict(r) for r in risks]
        except Exception as e:
            logger.error(f"Failed to get risk catalog: {e}")
            return []

    def _get_entities_from_orion(
        self, tenant_id: str, entity_type: str
    ) -> List[Dict[str, Any]]:
        """Get entities from Orion-LD, paginating through all results."""
        all_entities: List[Dict[str, Any]] = []
        headers = _make_headers(tenant_id)
        page_size = 200
        offset = 0

        try:
            while True:
                params = {
                    "type": entity_type,
                    "limit": page_size,
                    "offset": offset,
                }

                response = requests.get(
                    f"{ORION_URL}/ngsi-ld/v1/entities",
                    headers=headers,
                    params=params,
                    timeout=30,
                )

                if response.status_code != 200:
                    logger.warning(
                        f"Failed to get entities from Orion: {response.status_code} "
                        f"(tenant={tenant_id}, type={entity_type}, offset={offset})"
                    )
                    break

                batch = response.json()
                if not isinstance(batch, list) or not batch:
                    break

                all_entities.extend(batch)

                if len(batch) < page_size:
                    break  # last page

                offset += page_size

        except Exception as e:
            logger.error(f"Error getting entities from Orion: {e}")

        return all_entities

    def _get_weather_data(
        self, tenant_id: str, parcel_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Latest downscaled weather for a parcel, read from the broker.

        This used to read `weather_observations` in PostgreSQL. That table lost
        its writer in June, and the lookup fell back to a 'platform' tenant that
        no longer receives anything either, so every weather-driven risk had been
        silently evaluating on nothing. There is deliberately no fallback to it
        now: a stale table that answers is worse than one that does not.
        """
        if not parcel_id:
            logger.debug(
                "No parcel for tenant %s; weather is per-parcel and cannot be "
                "resolved without one.",
                tenant_id,
            )
            return None

        return fetch_parcel_weather(ORION_URL, tenant_id, parcel_id)

    def _get_gdd_accumulated(
        self,
        tenant_id: str,
        season_start_doy: int = 1,
        parcel_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Season-to-date GDD, summed from the timeseries the broker feeds.

        The previous query summed `weather_observations.gdd_accumulated`, a column
        that was NULL in every row the table ever held — so the pest-cycle models
        that depend on it have never once fired.
        """
        if not self.postgres:
            return None
        return fetch_season_gdd(
            self.postgres, tenant_id, season_start_doy, parcel_id
        )

    def _get_telemetry_data(
        self, tenant_id: str, device_id: str, metric_name: str, hours: int = 24
    ) -> Optional[List[Dict[str, Any]]]:
        """Get latest telemetry data for device.

        FIWARE COMPLIANCE: Reads from telemetry_measurements which is
        populated by NGSI-LD subscription notifications (not direct writes).
        """
        if not self.postgres:
            return None

        try:
            cursor = self.postgres.cursor()
            set_tenant_context(self.postgres, tenant_id)

            cursor.execute(
                """
                SELECT attribute_name AS metric_name,
                       value,
                       NULL::text AS unit,
                       observed_at AS time,
                       '{}'::jsonb AS metadata
                FROM telemetry_measurements
                WHERE tenant_id = %s
                  AND device_id = %s
                  AND attribute_name = %s
                  AND observed_at >= NOW() - INTERVAL '%s hours'
                ORDER BY observed_at DESC
                LIMIT 100
            """,
                (tenant_id, device_id, metric_name, hours),
            )

            results = cursor.fetchall()
            cursor.close()

            if results:
                return [dict(r) for r in results]
            return None
        except Exception as e:
            logger.error(f"Failed to get telemetry data: {e}")
            return None

    def _get_weather_alerts_for_parcel(
        self, tenant_id: str, parcel_id: str
    ) -> List[Dict[str, Any]]:
        """Active WeatherAlerts covering a parcel, via weather-api.

        weather-api require_auth reads X-Tenant-ID (no HMAC); X-User-ID is added
        defensively (internal-call 401 lesson). Errors → [] (non-fatal).
        """
        try:
            resp = requests.get(
                f"{WEATHER_API_URL}/api/weather/parcel/{parcel_id}/alerts",
                headers={"X-Tenant-ID": tenant_id, "X-User-ID": "risk-worker"},
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get("alerts", []) or []
            logger.warning(
                "weather-alerts fetch %s for %s: %s",
                resp.status_code,
                parcel_id,
                resp.text[:200],
            )
        except Exception as e:
            logger.debug("weather-alerts fetch failed for %s: %s", parcel_id, e)
        return []

    def _prepare_data_sources(
        self, tenant_id: str, risk: Dict[str, Any], entity: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Prepare data sources for risk evaluation"""
        data_sources = {}
        required_sources = risk.get("data_sources", [])

        parcel_id = _resolve_parcel_id(entity)

        # Get weather data if needed
        if "weather" in required_sources:
            weather_data = self._get_weather_data(tenant_id, parcel_id)
            if weather_data:
                data_sources["weather"] = weather_data

        # Get accumulated GDD if needed (for pest cycle models)
        if "gdd" in required_sources:
            # season_start_doy from the risk's model_config (default Jan 1)
            model_config = risk.get("model_config", {})
            season_start_doy = model_config.get("season_start_doy", 1)
            gdd_data = self._get_gdd_accumulated(
                tenant_id, season_start_doy, parcel_id
            )
            if gdd_data:
                data_sources["gdd"] = gdd_data

        # Get telemetry data if needed
        if "telemetry" in required_sources:
            device_id = None
            # Extract device ID from entity
            if "id" in entity:
                entity_id = entity["id"]
                # Extract device ID (e.g., from urn:ngsi-ld:Device:robot_123)
                if "Device" in entity.get("type", "") or "Vehicle" in entity.get(
                    "type", ""
                ):
                    # Use entity ID as device ID
                    device_id = (
                        entity_id.split(":")[-1] if ":" in entity_id else entity_id
                    )

            if device_id:
                # Get battery telemetry
                battery_telemetry = self._get_telemetry_data(
                    tenant_id, device_id, "batteryLevel", hours=1
                )
                if battery_telemetry:
                    # Use latest value
                    data_sources["telemetry"] = (
                        battery_telemetry[0] if battery_telemetry else None
                    )
                else:
                    # Try alternative metric names
                    for alt_name in ["battery", "batteryLevel", "powerLevel"]:
                        alt_telemetry = self._get_telemetry_data(
                            tenant_id, device_id, alt_name, hours=1
                        )
                        if alt_telemetry:
                            data_sources["telemetry"] = alt_telemetry[0]
                            break

        # Covering weather alerts (for the weather_alert model)
        if "weather_alerts" in required_sources:
            entity_id = entity.get("id")
            if entity_id:
                data_sources["weather_alerts"] = self._get_weather_alerts_for_parcel(
                    tenant_id, entity_id
                )

        return data_sources

    def _store_risk_evaluation(
        self,
        tenant_id: str,
        entity_id: str,
        entity_type: str,
        risk_code: str,
        probability_score: float,
        evaluation_data: Dict[str, Any],
        confidence: float = 1.0,
    ) -> bool:
        """Publish RiskAssessment entity to Orion-LD.

        FIWARE COMPLIANCE: Creates RiskAssessment NGSI-LD entity in Orion-LD.
        A subscription then feeds risk_daily_states (TimescaleDB) for
        historical queries.
        """
        try:
            # Sanitize entity_id for use in URN (replace colons to avoid URN issues)
            short_id = entity_id.rsplit(":", 1)[-1] if ":" in entity_id else entity_id
            risk_entity_id = (
                f"urn:ngsi-ld:RiskAssessment:{risk_code}:{tenant_id}:{short_id}"
            )

            risk_entity = {
                "@context": CONTEXT_URL,
                "id": risk_entity_id,
                "type": "RiskAssessment",
                "riskCode": {"type": "Property", "value": risk_code},
                "probabilityScore": {
                    "type": "Property",
                    "value": probability_score,
                },
                "confidence": {"type": "Property", "value": confidence},
                "targetEntityId": {"type": "Property", "value": entity_id},
                "targetEntityType": {"type": "Property", "value": entity_type},
                "evaluationData": {
                    "type": "Property",
                    "value": evaluation_data,
                },
                "evaluatedBy": {"type": "Property", "value": "risk-worker"},
                "evaluationVersion": {"type": "Property", "value": "1.0.0"},
                "timestamp": {
                    "type": "Property",
                    "value": datetime.utcnow().isoformat(),
                },
            }

            headers = _make_orion_headers(tenant_id)
            response = requests.post(
                f"{ORION_URL}/ngsi-ld/v1/entityOperations/upsert",
                json=[risk_entity],
                headers=headers,
                timeout=10,
            )

            if response.status_code in (200, 201, 204):
                logger.info(
                    f"Created RiskAssessment: {risk_code} for {entity_id} "
                    f"(score: {probability_score:.1f})"
                )
                return True
            else:
                logger.error(
                    f"Failed to create RiskAssessment entity: "
                    f"{response.status_code} - {response.text[:200]}"
                )
                return False

        except Exception as e:
            logger.error(f"Failed to publish RiskAssessment to Orion-LD: {e}")
            return False

    def _compute_severity(self, probability_score: float, severity_levels: dict) -> str:
        """Compute severity label from probability score and catalog thresholds"""
        levels = severity_levels or {
            "low": 30,
            "medium": 60,
            "high": 80,
            "critical": 95,
        }
        if probability_score >= levels.get("critical", 95):
            return "critical"
        if probability_score >= levels.get("high", 80):
            return "high"
        if probability_score >= levels.get("medium", 60):
            return "medium"
        return "low"

    def _publish_risk_event(
        self,
        tenant_id: str,
        entity_id: str,
        entity_type: str,
        risk_code: str,
        probability_score: float,
        severity: str,
    ) -> bool:
        """Publish risk event to Redis Streams for orchestrator"""
        if not self.redis_queue:
            return False

        try:
            event = {
                "tenant_id": tenant_id,
                "entity_id": entity_id,
                "entity_type": entity_type,
                "risk_code": risk_code,
                "probability_score": probability_score,
                "severity": severity,
                "timestamp": datetime.utcnow().isoformat(),
                "event_type": "risk_evaluation",
            }

            self.redis_queue.enqueue_task(
                tenant_id=tenant_id,
                task_type="risk_evaluation",
                payload=event,
                max_retries=1,
            )

            logger.info(
                f"Published risk event: {risk_code} for {entity_id} (score: {probability_score:.1f})"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to publish risk event: {e}")
            return False

    def evaluate_risks_for_tenant(self, tenant_id: str) -> Dict[str, Any]:
        """Evaluate all batch risks for a tenant"""
        logger.info(f"Evaluating risks for tenant: {tenant_id}")

        # Get batch risks from catalog
        risks = self._get_risk_catalog(evaluation_mode="batch")
        if not risks:
            logger.info("No batch risks found in catalog")
            return {"evaluated": 0, "errors": 0}

        evaluated = 0
        errors = 0

        for risk in risks:
            risk_code = risk["risk_code"]
            target_sdm_type = risk["target_sdm_type"]
            risk_domain = risk["risk_domain"]
            model_config = risk.get("model_config", {})
            model_type = risk.get("model_type")

            logger.info(
                f"Evaluating risk: {risk_code} for entity type: {target_sdm_type}"
            )

            # Get entities of this type from Orion-LD
            entities = self._get_entities_from_orion(tenant_id, target_sdm_type)
            if not entities:
                logger.info(
                    f"No entities of type {target_sdm_type} found for tenant {tenant_id}"
                )
                continue

            # Create risk model using factory (model_type takes precedence over domain)
            model = RiskModelFactory.create_model(
                risk_code, risk_domain, model_config, model_type
            )
            if not model:
                logger.error(f"Failed to create model for risk: {risk_code}")
                errors += 1
                continue

            # Evaluate risk for each entity
            for entity in entities:
                entity_id = entity.get("id")
                if not entity_id:
                    continue

                try:
                    # Prepare data sources
                    data_sources = self._prepare_data_sources(tenant_id, risk, entity)

                    # Evaluate risk
                    result = model.evaluate(
                        entity_id=entity_id,
                        entity_type=target_sdm_type,
                        tenant_id=tenant_id,
                        data_sources=data_sources,
                    )

                    probability_score = result.get("probability_score", 0.0)
                    evaluation_data = result.get("evaluation_data", {})
                    confidence = result.get("confidence", 1.0)

                    # Store evaluation
                    if self._store_risk_evaluation(
                        tenant_id=tenant_id,
                        entity_id=entity_id,
                        entity_type=target_sdm_type,
                        risk_code=risk_code,
                        probability_score=probability_score,
                        evaluation_data=evaluation_data,
                        confidence=confidence,
                    ):
                        evaluated += 1

                        # Publish event if risk is significant (>= 50%)
                        if probability_score >= 50:
                            severity = self._compute_severity(
                                probability_score, risk.get("severity_levels", {})
                            )
                            self._publish_risk_event(
                                tenant_id=tenant_id,
                                entity_id=entity_id,
                                entity_type=target_sdm_type,
                                risk_code=risk_code,
                                probability_score=probability_score,
                                severity=severity,
                            )
                    else:
                        errors += 1

                except Exception as e:
                    logger.error(
                        f"Error evaluating risk {risk_code} for entity {entity_id}: {e}"
                    )
                    errors += 1

        # ── Evaluate disease risks (epidemiological models) ─────────────────
        # Weather is published per parcel, so disease pressure is evaluated per
        # parcel too. This previously ran once per tenant on a single regional
        # sample, which is why every result carried fidelity "regional_proxy".
        try:
            from disease_evaluator import evaluate_disease_risks

            for parcel in self._get_entities_from_orion(tenant_id, "AgriParcel"):
                parcel_id = parcel.get("id")
                if not parcel_id:
                    continue
                weather = self._get_weather_data(tenant_id, parcel_id)
                if not weather:
                    continue
                evaluate_disease_risks(
                    tenant_id=tenant_id,
                    weather_data=weather,
                    parcel_id=parcel_id,
                    fidelity=weather.get("data_fidelity", "parcel_weather"),
                )
        except Exception as e:
            logger.warning("Disease risk evaluation skipped: %s", e)

        return {"evaluated": evaluated, "errors": errors}

    def run_batch_evaluation(self):
        """Run batch evaluation for all active tenants"""
        if not self.postgres:
            logger.error("PostgreSQL not available")
            return

        try:
            cursor = self.postgres.cursor()
            cursor.execute("""
                SELECT DISTINCT tenant_id
                FROM tenants
                WHERE status = 'active'
            """)

            tenants = cursor.fetchall()
            cursor.close()

            if not tenants:
                logger.info("No active tenants found")
                return

            total_evaluated = 0
            total_errors = 0

            for tenant_row in tenants:
                tenant_id = tenant_row["tenant_id"]
                result = self.evaluate_risks_for_tenant(tenant_id)
                total_evaluated += result["evaluated"]
                total_errors += result["errors"]

            logger.info(
                f"Batch evaluation complete. Evaluated: {total_evaluated}, Errors: {total_errors}"
            )

        except Exception as e:
            logger.error(f"Error running batch evaluation: {e}")

    def run_continuous(self):
        """Run continuously: consume on-demand requests + hourly batch fallback"""
        logger.info("Starting risk worker in continuous mode")
        eval_queue = TaskQueue(stream_name="risk:eval-requests")
        consumer_group = "risk-worker"
        consumer_name = f"worker-{os.getenv('HOSTNAME', 'local')}"

        last_batch_ts = 0.0
        batch_interval = 3600  # 1 hour in seconds

        while True:
            try:
                # Pull on-demand evaluation requests
                tasks = eval_queue.consume_tasks(
                    consumer_group=consumer_group, consumer_name=consumer_name, count=5
                )
                for task in tasks or []:
                    task_id = task.get("id")
                    payload = task.get("payload", {})
                    tenant_id = payload.get("tenant_id") or task.get("tenant_id")
                    if tenant_id:
                        logger.info(
                            f"On-demand evaluation triggered for tenant: {tenant_id}"
                        )
                        self.evaluate_risks_for_tenant(tenant_id)
                    if task_id:
                        eval_queue.acknowledge_task(consumer_group, task_id)

                # Hourly batch fallback
                now = time.time()
                if now - last_batch_ts >= batch_interval:
                    logger.info("Running scheduled batch evaluation")
                    self.run_batch_evaluation()
                    last_batch_ts = now

            except Exception as e:
                logger.error(f"Error in continuous loop: {e}")

            time.sleep(10)


def main():
    """Main entry point for risk worker"""
    processor = RiskProcessor()
    if os.getenv("CONTINUOUS_MODE", "").lower() == "true":
        processor.run_continuous()
    else:
        processor.run_batch_evaluation()


if __name__ == "__main__":
    main()
