# nkz_platform_sdk/activation.py
"""ModuleActivation — parcel-level entity lifecycle for module backends.

Provides ensure_entities() for idempotent placeholder creation and
get_status() for source health queries. Uses httpx internally
with proper FIWARE headers and @context injection.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ModuleActivation:
    """Manage Orion-LD entities for a module's per-parcel activation."""

    def __init__(
        self,
        tenant_id: str,
        orion_url: str | None = None,
        context_url: str | None = None,
    ):
        self.tenant_id = tenant_id
        self.orion_url = (orion_url or "http://orion-ld-service:1026").rstrip("/")
        self.context_url = context_url or "http://api-gateway-service:5000/ngsi-ld-context.json"

    def _headers(self, content_type: str = "application/ld+json") -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": content_type,
            "NGSILD-Tenant": self.tenant_id,
            "Fiware-Service": self.tenant_id,
            "Fiware-ServicePath": "/",
        }
        if content_type == "application/json":
            headers["Accept"] = "application/ld+json"
        headers["Link"] = (
            f'<{self.context_url}>; rel="http://www.w3.org/ns/json-ld#context";'
            f' type="application/ld+json"'
        )
        return headers

    def _build_entity(
        self, parcel_urn: str, entity_type: str, id_suffix: str
    ) -> dict[str, Any]:
        parcel_short = parcel_urn.split(":")[-1]
        entity_id = f"urn:ngsi-ld:{entity_type}:{self.tenant_id}:{parcel_short}-{id_suffix}"
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        base: dict[str, Any] = {
            "id": entity_id,
            "type": entity_type,
            "@context": self.context_url,
            "hasAgriParcel": {
                "type": "Relationship",
                "object": parcel_urn,
            },
            "status": {
                "type": "Property",
                "value": "pending",
            },
            "dateCreated": {
                "type": "Property",
                "value": {"@type": "DateTime", "@value": now},
            },
        }

        # Type-specific attributes
        if entity_type == "AgriCrop":
            base["plantingDate"] = {
                "type": "Property",
                "value": {"@type": "Date", "@value": datetime.now(timezone.utc).strftime("%Y-03-01")},
            }
            base["harvestDate"] = {
                "type": "Property",
                "value": {"@type": "Date", "@value": datetime.now(timezone.utc).strftime("%Y-06-30")},
            }
            base["provenance"] = {"type": "Property", "value": "placeholder"}

        elif entity_type == "CropHealthAssessment":
            base["assessedAt"] = {
                "type": "Property",
                "value": {"@type": "DateTime", "@value": now},
            }
            base["dataFidelity"] = {"type": "Property", "value": "pending"}

        elif entity_type == "AgriSoil":
            base["dataSource"] = {"type": "Property", "value": "pending_analysis"}

        return base

    async def ensure_entities(
        self,
        parcel_id: str,
        entities: list[dict],
    ) -> dict:
        """Idempotently create Orion-LD placeholder entities for a parcel.

        Args:
            parcel_id: Full URN (e.g. urn:ngsi-ld:AgriParcel:montiko:Montiko)
            entities: List of {type: str, id_suffix: str} dicts

        Returns:
            {created: int, skipped: int, errors: list[str], entity_ids: list[str]}
        """
        created = 0
        skipped = 0
        errors: list[str] = []
        entity_ids: list[str] = []

        async with httpx.AsyncClient(timeout=15.0) as client:
            for ent_def in entities:
                entity_type = ent_def["type"]
                id_suffix = ent_def["id_suffix"]
                body = self._build_entity(parcel_id, entity_type, id_suffix)
                eid = body["id"]
                entity_ids.append(eid)

                try:
                    # Try POST (create)
                    resp = await client.post(
                        f"{self.orion_url}/ngsi-ld/v1/entities",
                        json=body,
                        headers=self._headers("application/ld+json"),
                    )

                    if resp.status_code in (200, 201):
                        created += 1
                        logger.info("Created %s for parcel %s", eid, parcel_id)

                    elif resp.status_code == 409:
                        # Entity already exists — PATCH to update placeholder
                        patch_body = {
                            k: v for k, v in body.items()
                            if k not in ("id", "type", "@context")
                        }
                        patch_resp = await client.patch(
                            f"{self.orion_url}/ngsi-ld/v1/entities/{eid}/attrs",
                            json=patch_body,
                            headers=self._headers("application/ld+json"),
                        )
                        if patch_resp.status_code in (200, 204):
                            skipped += 1
                            logger.debug("Skipped (exists, patched): %s", eid)
                        else:
                            errors.append(
                                f"{entity_type} PATCH: HTTP {patch_resp.status_code} {patch_resp.text[:200]}"
                            )

                    else:
                        errors.append(
                            f"{entity_type} POST: HTTP {resp.status_code} {resp.text[:200]}"
                        )
                        logger.error("Failed to create %s: %s", eid, errors[-1])

                except Exception as e:
                    errors.append(f"{entity_type}: {e}")
                    logger.error("Exception creating %s: %s", eid, e)

        return {
            "created": created,
            "skipped": skipped,
            "errors": errors,
            "entity_ids": entity_ids,
        }

    async def get_status(self, parcel_id: str) -> dict[str, str]:
        """Check which entity types exist for a parcel.

        Returns dict like: {"AgriCrop": "ok", "CropHealthAssessment": "unavailable"}
        """
        types_to_check = [
            "AgriCrop", "CropHealthAssessment", "AgriSoil",
            "EOProduct", "DeviceMeasurement",
        ]
        status: dict[str, str] = {}

        async with httpx.AsyncClient(timeout=10.0) as client:
            for entity_type in types_to_check:
                try:
                    # Try new relationship name first
                    resp = await client.get(
                        f"{self.orion_url}/ngsi-ld/v1/entities",
                        params={
                            "type": entity_type,
                            "q": f'hasAgriParcel=="{parcel_id}"',
                            "limit": 1,
                            "options": "keyValues",
                        },
                        headers=self._headers("application/json"),
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, list) and len(data) > 0:
                            status[entity_type] = "ok"
                            continue

                    # Fallback: try legacy refAgriParcel
                    resp2 = await client.get(
                        f"{self.orion_url}/ngsi-ld/v1/entities",
                        params={
                            "type": entity_type,
                            "q": f'refAgriParcel=="{parcel_id}"',
                            "limit": 1,
                            "options": "keyValues",
                        },
                        headers=self._headers("application/json"),
                    )
                    if resp2.status_code == 200:
                        data2 = resp2.json()
                        status[entity_type] = "ok" if (isinstance(data2, list) and len(data2) > 0) else "unavailable"
                    else:
                        status[entity_type] = "unavailable"

                except Exception:
                    status[entity_type] = "error"

        return status
