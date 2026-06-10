# nkz_platform_sdk/subscriptions.py
"""SubscriptionRegistrar — declarative Orion-LD subscription management.

Registers NGSI-LD subscriptions at service startup and auto-heals
them periodically. One subscription per entity type per tenant
(not per parcel — filtering happens in the notification handler).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

DESCRIPTION_PREFIX = "nkz-module"


@dataclass
class SubscriptionDef:
    type: str
    throttling: int = 30


class SubscriptionRegistrar:
    """Idempotent Orion-LD subscription manager.

    Usage at service startup::

        registrar = SubscriptionRegistrar(
            orion_url="http://orion-ld-service:1026",
            notification_url="http://my-backend:8000/api/my-module/webhooks/orion",
            subscriptions=[
                {"type": "EOProduct", "throttling": 30},
                {"type": "WeatherObserved", "throttling": 60},
            ],
            module_name="my-module",
        )
        await registrar.ensure_all(tenant_ids)

        # Optional auto-heal
        asyncio.create_task(registrar.periodic_heal(tenant_ids, interval_minutes=60))
    """

    def __init__(
        self,
        orion_url: str,
        notification_url: str,
        subscriptions: list[dict],
        context_url: str | None = None,
        module_name: str = "unknown",
    ):
        self.orion_url = orion_url.rstrip("/")
        self.notification_url = notification_url
        self.context_url = context_url or "http://api-gateway-service:5000/ngsi-ld-context.json"
        self.module_name = module_name
        self._subs: list[SubscriptionDef] = [
            SubscriptionDef(**s) if isinstance(s, dict) else s for s in subscriptions
        ]

    def _make_headers(self, tenant_id: str, content_type: str = "application/json") -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": content_type,
            "NGSILD-Tenant": tenant_id,
            "Fiware-Service": tenant_id,
            "Fiware-ServicePath": "/",
        }
        headers["Link"] = (
            f'<{self.context_url}>; rel="http://www.w3.org/ns/json-ld#context";'
            f' type="application/ld+json"'
        )
        return headers

    def _sub_description(self, sub: SubscriptionDef) -> str:
        return f"{DESCRIPTION_PREFIX}: {sub.type} -> {self.module_name}"

    async def ensure_all(self, tenant_ids: list[str]) -> dict:
        """Ensure subscriptions exist for all tenants. Idempotent."""
        created = 0
        skipped = 0
        errors: list[str] = []

        async with httpx.AsyncClient(timeout=15.0) as client:
            for tenant_id in tenant_ids:
                hdrs = self._make_headers(tenant_id)
                try:
                    # List existing subscriptions
                    resp = await client.get(
                        f"{self.orion_url}/ngsi-ld/v1/subscriptions",
                        headers=hdrs,
                    )
                    resp.raise_for_status()
                    existing = resp.json() if isinstance(resp.json(), list) else []
                    existing_descriptions = {s.get("description", "") for s in existing}

                    # Create missing ones
                    for sub in self._subs:
                        desc = self._sub_description(sub)
                        if desc in existing_descriptions:
                            skipped += 1
                            logger.debug("Subscription exists: %s for %s", desc, tenant_id)
                            continue

                        body = {
                            "type": "Subscription",
                            "description": desc,
                            "entities": [{"type": sub.type}],
                            "notification": {
                                "endpoint": {
                                    "uri": self.notification_url,
                                    "accept": "application/json",
                                },
                                "format": "normalized",
                            },
                            "throttling": sub.throttling,
                            "isActive": True,
                        }

                        post_resp = await client.post(
                            f"{self.orion_url}/ngsi-ld/v1/subscriptions",
                            json=body,
                            headers=self._make_headers(tenant_id, "application/ld+json"),
                        )
                        if post_resp.status_code in (200, 201):
                            created += 1
                            logger.info("Subscription created: %s for tenant %s", desc, tenant_id)
                        else:
                            error_msg = f"{desc}: HTTP {post_resp.status_code} {post_resp.text[:200]}"
                            errors.append(error_msg)
                            logger.error("Subscription failed: %s", error_msg)
                except Exception as e:
                    error_msg = f"tenant {tenant_id}: {e}"
                    errors.append(error_msg)
                    logger.warning("Subscription check failed for tenant %s: %s", tenant_id, e)

        return {"created": created, "skipped": skipped, "errors": errors}

    async def periodic_heal(
        self,
        tenant_ids: list[str],
        interval_minutes: int = 60,
    ) -> None:
        """Reconcile subscriptions periodically. Never exits."""
        while True:
            await asyncio.sleep(interval_minutes * 60)
            try:
                result = await self.ensure_all(tenant_ids)
                logger.info(
                    "Subscription heal: created=%d skipped=%d errors=%d",
                    result["created"], result["skipped"], len(result["errors"]),
                )
            except Exception as e:
                logger.warning("Subscription heal cycle failed: %s", e)
