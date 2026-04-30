#!/usr/bin/env python3
# =============================================================================
# Risk Orchestrator
# =============================================================================
# Consumes risk events from Redis Streams and updates Orion-LD
# Separates risk calculation from Orion-LD updates to avoid circular dependencies

import hashlib
import hmac
import os
import sys
import logging
import json
import time
from typing import Dict, Any, List

import psycopg2
from psycopg2.extras import RealDictCursor
import requests

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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis-service:6379")
CONSUMER_GROUP = "risk-orchestrator"
CONSUMER_NAME = f"risk-orchestrator-{os.getenv('HOSTNAME', 'default')}"

# PostgreSQL (for webhook dispatch)
_POSTGRES_USER = os.getenv("POSTGRES_USER", "nekazari")
_POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
_POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgresql-service")
_POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
_POSTGRES_DB = os.getenv("POSTGRES_DB", "nekazari")
POSTGRES_URL = (
    f"postgresql://{_POSTGRES_USER}:{_POSTGRES_PASSWORD}@{_POSTGRES_HOST}:{_POSTGRES_PORT}/{_POSTGRES_DB}"
    if _POSTGRES_PASSWORD
    else None
)
EMAIL_SERVICE_URL = os.getenv("EMAIL_SERVICE_URL", "http://email-service:5000")

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class RiskOrchestrator:
    """Orchestrator for risk events - updates Orion-LD based on risk evaluations"""

    def __init__(self):
        self.redis_queue = None
        self.postgres = None
        self.context_url = os.getenv(
            "CONTEXT_URL", "http://api-gateway-service:5000/ngsi-ld-context.json"
        )
        self._init_connections()

    def _init_connections(self):
        """Initialize Redis and optional PostgreSQL connections"""
        try:
            self.redis_queue = TaskQueue(stream_name="risk:events")
            logger.info("Redis Streams initialized")
        except Exception as e:
            logger.error(f"Redis Streams not available: {e}")
            raise

        if POSTGRES_URL:
            try:
                self.postgres = psycopg2.connect(
                    POSTGRES_URL, cursor_factory=RealDictCursor
                )
                self.postgres.autocommit = True
                logger.info("PostgreSQL connected (webhook dispatch)")
            except Exception as e:
                logger.warning(f"PostgreSQL not available for webhook dispatch: {e}")

    def _get_active_webhooks(
        self, tenant_id: str, severity: str, event_type: str
    ) -> List[Dict[str, Any]]:
        """Fetch active webhooks for this tenant that match the event severity"""
        if not self.postgres:
            return []
        try:
            cursor = self.postgres.cursor()
            cursor.execute(
                """
                SELECT id, url, secret, events, min_severity
                FROM tenant_risk_webhooks
                WHERE tenant_id = %s AND is_active = true
            """,
                (tenant_id,),
            )
            rows = cursor.fetchall()
            cursor.close()
        except Exception as e:
            logger.error(f"Failed to query webhooks: {e}")
            return []

        event_level = SEVERITY_ORDER.get(severity, 0)
        matching = []
        for row in rows:
            row = dict(row)
            if event_type not in (row.get("events") or []):
                continue
            min_sev = row.get("min_severity", "medium")
            if event_level < SEVERITY_ORDER.get(min_sev, 1):
                continue
            matching.append(row)
        return matching

    def _dispatch_webhooks(
        self, webhooks: List[Dict[str, Any]], payload: Dict[str, Any]
    ) -> None:
        """Fire-and-forget: POST payload to each webhook URL"""
        body = json.dumps(payload)
        for webhook in webhooks:
            url = webhook["url"]
            secret = webhook.get("secret")
            headers = {"Content-Type": "application/json"}
            if secret:
                sig = hmac.new(
                    secret.encode(), body.encode(), hashlib.sha256
                ).hexdigest()
                headers["X-Nekazari-Signature"] = f"sha256={sig}"
            try:
                resp = requests.post(url, data=body, headers=headers, timeout=5)
                logger.info(f"Webhook {url} → {resp.status_code}")
            except Exception as e:
                logger.warning(f"Webhook delivery failed for {url}: {e}")

    def _update_orion_entity(
        self, tenant_id: str, entity_id: str, risk_status: Dict[str, Any]
    ) -> bool:
        """Update entity in Orion-LD with risk status"""
        try:
            headers = {
                "Content-Type": "application/ld+json",
                "Fiware-Service": tenant_id,
                "Fiware-ServicePath": "/",
            }

            # Prepare riskStatus attribute with required NGSI-LD @context
            update_payload = {
                "@context": self.context_url,
                "riskStatus": {
                    "type": "Property",
                    "value": risk_status.get("severity", "NORMAL").upper(),
                    "metadata": {
                        "riskType": {
                            "type": "Property",
                            "value": risk_status.get("risk_code", ""),
                        },
                        "probability": {
                            "type": "Property",
                            "value": risk_status.get("probability_score", 0),
                        },
                        "timestamp": {
                            "type": "DateTime",
                            "value": risk_status.get("timestamp", ""),
                        },
                    },
                },
            }

            # PATCH entity attributes
            response = requests.patch(
                f"{ORION_URL}/ngsi-ld/v1/entities/{entity_id}/attrs",
                headers=headers,
                json=update_payload,
                timeout=10,
            )

            if response.status_code in [200, 204]:
                logger.info(
                    f"Updated Orion entity {entity_id} with risk status: {risk_status.get('severity')}"
                )
                return True
            else:
                logger.warning(
                    f"Failed to update Orion entity: {response.status_code} - {response.text}"
                )
                return False

        except Exception as e:
            logger.error(f"Error updating Orion entity: {e}")
            return False

    def _get_tenant_email_subscription(
        self, tenant_id: str, risk_code: str
    ) -> dict | None:
        """Look up tenant email and risk notification preferences."""
        if not self.postgres:
            return None
        try:
            cursor = self.postgres.cursor()
            cursor.execute(
                """
                SELECT t.email, trs.notification_channels
                FROM tenants t
                LEFT JOIN tenant_risk_subscriptions trs
                  ON trs.tenant_id = t.tenant_id
                 AND trs.risk_code = %(risk_code)s
                 AND trs.is_active = true
                WHERE t.tenant_id = %(tenant_id)s
                LIMIT 1
                """,
                {"tenant_id": tenant_id, "risk_code": risk_code},
            )
            row = cursor.fetchone()
            cursor.close()
            if row and row.get("email"):
                channels = row.get("notification_channels") or {}
                return {
                    "email": row["email"],
                    "email_enabled": channels.get("email", True),
                }
        except Exception as e:
            logger.warning(f"Could not query tenant email for {tenant_id}: {e}")
        return None

    def _send_email_notification(
        self,
        email: str,
        farmer_name: str,
        risk_code: str,
        probability_score: float,
        severity: str,
        entity_id: str,
    ) -> None:
        """POST notification to the email service."""
        try:
            message = (
                f"Riesgo: {risk_code}\n"
                f"Severidad: {severity}\n"
                f"Probabilidad: {probability_score:.0f}%\n"
                f"Entidad: {entity_id}"
            )
            resp = requests.post(
                f"{EMAIL_SERVICE_URL}/send/notification",
                json={
                    "email": email,
                    "farmer_name": farmer_name,
                    "notification_type": "risk_alert",
                    "message": message,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                logger.info(
                    "Email notification sent to %s for risk %s (severity=%s)",
                    email,
                    risk_code,
                    severity,
                )
            else:
                logger.warning(
                    "Email service returned %d for %s: %s",
                    resp.status_code,
                    email,
                    resp.text,
                )
        except Exception as e:
            logger.warning(f"Email notification dispatch error (non-fatal): {e}")

    def _process_risk_event(self, event: Dict[str, Any]) -> bool:
        """Process a single risk event"""
        tenant_id = event.get("tenant_id")
        entity_id = event.get("entity_id")
        risk_code = event.get("risk_code")
        probability_score = event.get("probability_score", 0)
        severity = event.get("severity", "low")
        timestamp = event.get("timestamp", "")

        if not tenant_id or not entity_id:
            logger.warning("Invalid event: missing tenant_id or entity_id")
            return False

        orion_updated = True
        # Only update Orion-LD for significant risks (>= 50% or high/critical severity)
        if probability_score >= 50 or severity in ["high", "critical"]:
            risk_status = {
                "risk_code": risk_code,
                "probability_score": probability_score,
                "severity": severity,
                "timestamp": timestamp,
            }
            orion_updated = self._update_orion_entity(tenant_id, entity_id, risk_status)
        else:
            logger.debug(
                f"Skipping low-risk Orion update: {risk_code} (score: {probability_score:.1f})"
            )

        # Dispatch webhooks (fire-and-forget, does not affect return value)
        try:
            webhooks = self._get_active_webhooks(tenant_id, severity, "risk_evaluation")
            if webhooks:
                webhook_payload = {
                    "event": "risk_evaluation",
                    "tenant_id": tenant_id,
                    "entity_id": entity_id,
                    "risk_code": risk_code,
                    "probability_score": probability_score,
                    "severity": severity,
                    "timestamp": timestamp,
                }
                self._dispatch_webhooks(webhooks, webhook_payload)
        except Exception as e:
            logger.warning(f"Webhook dispatch error (non-fatal): {e}")

        # Dispatch email notification (fire-and-forget)
        try:
            sub = self._get_tenant_email_subscription(tenant_id, risk_code)
            if sub and sub.get("email_enabled"):
                self._send_email_notification(
                    email=sub["email"],
                    farmer_name=tenant_id,
                    risk_code=risk_code,
                    probability_score=probability_score,
                    severity=severity,
                    entity_id=entity_id,
                )
        except Exception as e:
            logger.warning(f"Email notification error (non-fatal): {e}")

        return orion_updated

    def run(self):
        """Main loop: consume events from Redis Streams"""
        logger.info(f"Risk Orchestrator started (consumer: {CONSUMER_NAME})")

        while True:
            try:
                # Consume events from stream
                events = self.redis_queue.consume_tasks(
                    consumer_group=CONSUMER_GROUP, consumer_name=CONSUMER_NAME, count=10
                )

                if not events:
                    # No events, sleep briefly
                    time.sleep(1)
                    continue

                for event in events:
                    try:
                        # Extract payload if nested
                        payload = event.get("payload", event)
                        if isinstance(payload, str):
                            payload = json.loads(payload)

                        # Process event
                        task_id = event.get("id")
                        if self._process_risk_event(payload):
                            # Acknowledge successful processing
                            if task_id:
                                self.redis_queue.acknowledge_task(
                                    CONSUMER_GROUP, task_id
                                )
                        else:
                            logger.warning(
                                f"Failed to process event: {event.get('risk_code')}"
                            )

                    except Exception as e:
                        logger.error(f"Error processing event: {e}")
                        continue

            except KeyboardInterrupt:
                logger.info("Risk Orchestrator stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in orchestrator loop: {e}")
                time.sleep(5)  # Wait before retrying


def main():
    """Main entry point"""
    orchestrator = RiskOrchestrator()
    orchestrator.run()


if __name__ == "__main__":
    main()
