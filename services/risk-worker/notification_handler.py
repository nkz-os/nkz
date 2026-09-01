"""
Notification handler for RiskAssessment subscription.

Receives NGSI-LD entity notifications from Orion-LD subscription
and persists to risk_daily_states (TimescaleDB hypertable).
"""

import json
import logging
import os
from datetime import datetime
from typing import Any

import psycopg2
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()

POSTGRES_URL = os.getenv("POSTGRES_URL", "")


def _get_conn():
    """Get a psycopg2 connection."""
    if not POSTGRES_URL:
        raise RuntimeError("POSTGRES_URL not set")
    return psycopg2.connect(POSTGRES_URL)


def _set_tenant_context(conn, tenant_id: str) -> None:
    """Set tenant context for RLS policies."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.current_tenant', %s, true)",
                (tenant_id,),
            )
    except Exception:
        pass  # Non-fatal if RLS not configured


def _extract_prop(entity: dict, key: str) -> Any:
    """Extract Property value from NGSI-LD normalized entity."""
    prop = entity.get(key, {})
    if isinstance(prop, dict):
        return prop.get("value")
    return prop


def _compute_severity(probability_score: float) -> str:
    """Compute severity label from probability score."""
    if probability_score >= 95:
        return "critical"
    if probability_score >= 80:
        return "high"
    if probability_score >= 60:
        return "medium"
    return "low"


@router.post("/notify", status_code=204)
async def handle_notification(request: Request):
    """Receive Orion-LD subscription notification for RiskAssessment entities.

    Extracts risk evaluation data and persists to risk_daily_states.
    Tenant is extracted from NGSILD-Tenant or Fiware-Service header.

    Answers 204 with no body: Orion-LD looks for a capitalised "Content-Length:"
    with a case-sensitive strstr and only skips that check on a 204. uvicorn emits
    the header lower-cased, so any other success status is counted as a failed
    notification and deactivates the subscription after 3 consecutive hits.
    """
    try:
        tenant_id = (
            request.headers.get("NGSILD-Tenant")
            or request.headers.get("Fiware-Service")
            or "unknown"
        )
        body = await request.json()
        entities = body.get("data", [])

        if not entities:
            return None

        persisted = 0
        conn = _get_conn()
        try:
            _set_tenant_context(conn, tenant_id)
            with conn.cursor() as cur:
                for entity in entities:
                    risk_code = _extract_prop(entity, "riskCode")
                    probability_score = _extract_prop(entity, "probabilityScore")
                    target_entity_id = _extract_prop(entity, "targetEntityId")
                    target_entity_type = _extract_prop(entity, "targetEntityType")
                    evaluation_data = _extract_prop(entity, "evaluationData")
                    evaluated_by = _extract_prop(entity, "evaluatedBy")
                    evaluation_version = _extract_prop(entity, "evaluationVersion")
                    severity = _extract_prop(entity, "severity")
                    timestamp_val = _extract_prop(entity, "timestamp")

                    if not risk_code or probability_score is None:
                        continue

                    # Parse timestamp
                    ts = datetime.utcnow()
                    if timestamp_val:
                        try:
                            ts = datetime.fromisoformat(
                                str(timestamp_val).replace("Z", "+00:00")
                            )
                        except (ValueError, TypeError):
                            pass

                    cur.execute(
                        """
                        INSERT INTO risk_daily_states (
                            tenant_id, entity_id, entity_type, risk_code,
                            probability_score, severity,
                            evaluation_data, evaluation_timestamp,
                            timestamp, evaluated_by, evaluation_version
                        ) VALUES (
                            %s, %s, %s, %s,
                            %s, %s,
                            %s, %s,
                            %s, %s, %s
                        )
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            tenant_id,
                            target_entity_id or entity.get("id", ""),
                            target_entity_type or entity.get("type", ""),
                            risk_code,
                            float(probability_score),
                            severity or _compute_severity(float(probability_score)),
                            json.dumps(evaluation_data or {}),
                            ts,
                            ts,
                            evaluated_by or "risk-worker",
                            evaluation_version or "1.0.0",
                        ),
                    )
                    persisted += 1

            conn.commit()
        finally:
            conn.close()

        logger.info("Persisted %d risk evaluations for tenant=%s", persisted, tenant_id)
        return None

    except Exception as e:
        logger.error("Error processing risk notification: %s", e, exc_info=True)
        return JSONResponse(
            {"status": "error", "error": "Internal server error"},
            status_code=500,
        )
