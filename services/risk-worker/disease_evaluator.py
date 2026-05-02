"""Disease Risk Evaluator — runs epidemiological models and publishes to Orion-LD.

Called from the risk-worker batch evaluation cycle.
Consumes weather data from the same sources as other risk models.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")
CONTEXT_URL = os.getenv(
    "CONTEXT_URL", "http://api-gateway-service:5000/ngsi-ld-context.json"
)


def evaluate_disease_risks(
    tenant_id: str,
    weather_data: dict[str, Any],
    parcel_id: str = "",
    fidelity: str = "regional_proxy",
) -> list[dict[str, Any]]:
    """Run all applicable disease models and publish results to Orion-LD.

    Returns list of published DiseaseRiskAssessment entity IDs.
    """
    published: list[dict[str, Any]] = []

    # Only run if we have basic weather data
    temp_avg = weather_data.get("temp_avg")
    humidity = weather_data.get("humidity_avg")
    precip = weather_data.get("precip_mm")
    if temp_avg is None or humidity is None:
        return published

    # ── Gubler-Thomas: Powdery Mildew (temperature only, no LWD needed) ──
    try:
        from risk_models.gubler_pm import evaluate_gubler_pm

        daily_means = [temp_avg]  # Simplified: single reading
        result = evaluate_gubler_pm(daily_means=daily_means, fidelity=fidelity)
        if result.risk_level != "LOW":
            entity = _publish_disease_risk(tenant_id, result, parcel_id)
            if entity:
                published.append(entity)
    except Exception as e:
        logger.debug("Gubler-Thomas skipped: %s", e)

    # ── Magarey: Downy Mildew (needs LWD) ──
    try:
        from risk_models.lwd_estimator import estimate_lwd_nhrh
        from risk_models.magarey_mildew import evaluate_magarey_mildew

        hourly_rh = [humidity]  # Simplified: single reading proxy
        lwd_hours = estimate_lwd_nhrh(hourly_rh)
        if lwd_hours > 0:
            precip_48h = precip or 0
            result = evaluate_magarey_mildew(
                precip_mm_48h=precip_48h,
                mean_temp_48h=temp_avg,
                lwd_hours=lwd_hours,
                fidelity=fidelity,
            )
            if result.risk_level != "LOW":
                entity = _publish_disease_risk(tenant_id, result, parcel_id)
                if entity:
                    published.append(entity)
    except Exception as e:
        logger.debug("Magarey skipped: %s", e)

    # ── Mills: Apple Scab (needs LWD) ──
    try:
        from risk_models.mills_scab import evaluate_mills_scab

        hourly_rh = [humidity]
        lwd_hours = estimate_lwd_nhrh(hourly_rh)
        if lwd_hours > 0:
            result = evaluate_mills_scab(
                mean_temp=temp_avg,
                lwd_hours=lwd_hours,
                fidelity=fidelity,
            )
            if result.risk_level != "LOW":
                entity = _publish_disease_risk(tenant_id, result, parcel_id)
                if entity:
                    published.append(entity)
    except Exception as e:
        logger.debug("Mills skipped: %s", e)

    # ── TomCast: Alternaria (needs LWD) ──
    try:
        from risk_models.tomcast_alternaria import evaluate_tomcast

        hourly_rh = [humidity]
        lwd_hours = estimate_lwd_nhrh(hourly_rh)
        if lwd_hours > 0:
            result = evaluate_tomcast(
                mean_temp=temp_avg,
                lwd_hours=lwd_hours,
                fidelity=fidelity,
            )
            if result.risk_level != "LOW":
                entity = _publish_disease_risk(tenant_id, result, parcel_id)
                if entity:
                    published.append(entity)
    except Exception as e:
        logger.debug("TomCast skipped: %s", e)

    return published


def _publish_disease_risk(
    tenant_id: str, result: Any, parcel_id: str = ""
) -> dict[str, Any] | None:
    """Publish a DiseaseRiskAssessment entity to Orion-LD."""
    try:
        entity_id = f"urn:ngsi-ld:DiseaseRiskAssessment:{result.disease}-{tenant_id}"
        if parcel_id:
            entity_id += f"-{parcel_id}"

        entity: dict[str, Any] = {
            "id": entity_id,
            "type": "DiseaseRiskAssessment",
            "@context": CONTEXT_URL,
            "disease": {"type": "Property", "value": result.disease},
            "crop": {"type": "Property", "value": result.crop},
            "riskLevel": {"type": "Property", "value": result.risk_level},
            "conditions": {"type": "Property", "value": result.conditions},
            "confidence": {"type": "Property", "value": result.confidence},
            "sourceModel": {"type": "Property", "value": result.source_model},
            "recommendedAction": {
                "type": "Property",
                "value": result.recommended_action,
            },
            "dataFidelity": {"type": "Property", "value": result.data_fidelity},
        }
        if hasattr(result, "lwd_method") and result.lwd_method != "none":
            entity["lwdMethod"] = {"type": "Property", "value": result.lwd_method}

        headers = {
            "Content-Type": "application/ld+json",
            "NGSILD-Tenant": tenant_id,
        }
        resp = requests.post(
            f"{ORION_URL}/ngsi-ld/v1/entityOperations/upsert",
            json=[entity],
            headers=headers,
            timeout=10,
        )
        if resp.status_code in (200, 201, 204):
            logger.info(
                "Published DiseaseRiskAssessment: %s (%s)",
                result.disease,
                result.risk_level,
            )
            return entity
        else:
            logger.warning(
                "Orion-LD returned %d for DiseaseRiskAssessment", resp.status_code
            )
    except Exception as e:
        logger.error("Failed to publish DiseaseRiskAssessment: %s", e)
    return None
