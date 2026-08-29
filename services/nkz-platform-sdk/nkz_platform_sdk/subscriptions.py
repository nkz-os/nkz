"""SubscriptionRegistrar — declarative Orion-LD subscription management.

One subscription per entity type per tenant (not per parcel — filtering
happens in the module's notification handler). Idempotent via a
deterministic subscription id (`urn:ngsi-ld:Subscription:{module}:{type}`,
see `_subscription_id`): every process that wants this subscription POSTs
the same id, so Orion's own duplicate-id rejection (409) arbitrates
concurrent heal cycles instead of a check-then-create read that two
processes can both pass at once. All Orion-LD I/O goes through OrionClient
(NGSI-LD compliance at SDK level).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from nkz_platform_sdk.orion import OrionClient

logger = logging.getLogger(__name__)

DESCRIPTION_PREFIX = "nkz-module"

# NGSI-LD URNs are free-form after the type segment, but we keep the id
# segments to the RFC 3986 "unreserved" character set so the id is safe to
# embed literally and never needs escaping.
_URN_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_.~-]")


def _sanitize_urn_segment(value: str) -> str:
    """Make `value` safe to embed as an NGSI-LD URN segment.

    Deterministic: every character outside [A-Za-z0-9_.~-] is replaced with
    '-'. Same input always sanitises to the same output, which is what
    makes the derived subscription id stable across runs and processes.
    """
    return _URN_UNSAFE_CHARS.sub("-", value)


@dataclass
class SubscriptionDef:
    type: str
    throttling: int = 30
    watched_attributes: list[str] | None = None
    condition: dict | None = None


class SubscriptionRegistrar:
    """Idempotent Orion-LD subscription manager.

    Usage at service startup::

        registrar = SubscriptionRegistrar(
            orion_url=settings.orion_ld_url,
            notification_url="http://crop-health-api-service:8000/api/crop-health/webhooks/orion",
            subscriptions=[{"type": "EOProduct", "throttling": 30}],
            module_name="crop-health",
        )
        await registrar.ensure_all(["tenant-a", "tenant-b"])
        asyncio.create_task(registrar.periodic_heal(get_tenants, interval_minutes=60))
    """

    def __init__(
        self,
        orion_url: str,
        notification_url: str,
        subscriptions: list[dict],
        module_name: str,
        context_url: str | None = None,
    ):
        self.orion_url = orion_url.rstrip("/")
        self.notification_url = notification_url
        self.context_url = context_url
        self.module_name = module_name
        self._subs: list[SubscriptionDef] = [
            SubscriptionDef(**s) if isinstance(s, dict) else s for s in subscriptions
        ]

    def _description(self, sub: SubscriptionDef) -> str:
        return f"{DESCRIPTION_PREFIX}: {sub.type} -> {self.module_name}"

    def _subscription_id(self, sub: SubscriptionDef) -> str:
        """Deterministic id for this logical subscription.

        `urn:ngsi-ld:Subscription:{module_name}:{type}`, with module_name
        and type each run through `_sanitize_urn_segment`. Stable across
        runs and processes — tenant is deliberately not part of it, since
        each tenant has its own subscription store and module+type is
        already unique within one.
        """
        module = _sanitize_urn_segment(self.module_name)
        sub_type = _sanitize_urn_segment(sub.type)
        return f"urn:ngsi-ld:Subscription:{module}:{sub_type}"

    def _body(self, sub: SubscriptionDef) -> dict:
        body = {
            "id": self._subscription_id(sub),
            "type": "Subscription",
            "description": self._description(sub),
            "entities": [{"type": sub.type}],
            "notification": {
                "endpoint": {"uri": self.notification_url, "accept": "application/json"},
                "format": "normalized",
            },
            "throttling": sub.throttling,
            "isActive": True,
        }
        if sub.watched_attributes:
            body["watchedAttributes"] = sub.watched_attributes
        if sub.condition:
            body["condition"] = sub.condition
        return body

    async def _purge_legacy_duplicates(
        self,
        client: OrionClient,
        sub: SubscriptionDef,
        created_id: str,
        tenant_id: str,
        errors: list[str],
    ) -> None:
        """Delete pre-deterministic-id duplicates of the subscription just created.

        Only called after a 201 (never after a 409 — see `ensure_all`): a
        201 means this process's create is the one that just succeeded, so
        it is the right point to sweep for older subscriptions carrying the
        same description under a random legacy id. Self-disables once no
        legacy duplicates remain, since then every create for this
        subscription hits 409 and this method is never invoked.
        """
        description = self._description(sub)
        try:
            existing = await client.query_all_subscriptions()
        except Exception as e:
            errors.append(f"{tenant_id}/{sub.type} legacy-purge list: {e}")
            logger.warning(
                "Legacy subscription listing failed for %s/%s: %s", tenant_id, sub.type, e
            )
            return

        legacy_ids = [
            s.get("id")
            for s in existing
            if s.get("description") == description and s.get("id") != created_id
        ]
        for legacy_id in legacy_ids:
            try:
                await client.delete_subscription(legacy_id)
                logger.info(
                    "Legacy subscription purged: %s (tenant=%s, id=%s)",
                    description, tenant_id, legacy_id,
                )
            except httpx.HTTPStatusError as e:
                if e.response is not None and e.response.status_code == 404:
                    continue  # already removed by someone else
                errors.append(f"{tenant_id}/{sub.type} legacy-purge delete {legacy_id}: {e}")
                logger.warning(
                    "Legacy subscription delete failed: %s", errors[-1]
                )
            except Exception as e:
                errors.append(f"{tenant_id}/{sub.type} legacy-purge delete {legacy_id}: {e}")
                logger.warning(
                    "Legacy subscription delete failed: %s", errors[-1]
                )

    async def ensure_all(self, tenant_ids: list[str]) -> dict:
        """Ensure subscriptions exist for all tenants. Idempotent, never raises.

        No listing is done to decide whether to create: each subscription
        POSTs straight away with its deterministic id (`_subscription_id`)
        and the create either succeeds (201, this process made it exist)
        or collides (409, it already existed — the expected outcome when a
        concurrent heal cycle won the race, not an error). Legacy
        subscriptions from before this scheme are converged away after a
        201 only, via `_purge_legacy_duplicates`.
        """
        created, skipped, errors = 0, 0, []
        for tenant_id in tenant_ids:
            client = OrionClient(
                tenant_id, base_url=self.orion_url, context_url=self.context_url
            )
            try:
                for sub in self._subs:
                    sub_id = self._subscription_id(sub)
                    try:
                        await client.create_subscription(self._body(sub))
                    except httpx.HTTPStatusError as e:
                        status = e.response.status_code if e.response is not None else None
                        if status == 409:
                            skipped += 1
                            logger.debug(
                                "Subscription already exists (concurrent create won): "
                                "%s (tenant=%s, id=%s)",
                                self._description(sub), tenant_id, sub_id,
                            )
                        else:
                            errors.append(f"{tenant_id}/{sub.type}: {e}")
                            logger.error("Subscription create failed: %s", errors[-1])
                    except Exception as e:
                        errors.append(f"{tenant_id}/{sub.type}: {e}")
                        logger.error("Subscription create failed: %s", errors[-1])
                    else:
                        created += 1
                        logger.info(
                            "Subscription created: %s (tenant=%s, id=%s)",
                            self._description(sub), tenant_id, sub_id,
                        )
                        await self._purge_legacy_duplicates(
                            client, sub, sub_id, tenant_id, errors
                        )
            finally:
                await client.close()
        return {"created": created, "skipped": skipped, "errors": errors}

    async def periodic_heal(
        self,
        tenant_provider: Callable[[], Awaitable[list[str]]] | list[str],
        interval_minutes: int = 60,
    ) -> None:
        """Reconcile subscriptions periodically. Never exits, never raises.

        `tenant_provider` may be a static list or an async callable returning
        the current tenant list (so tenants activated after startup are healed).
        """
        while True:
            await asyncio.sleep(interval_minutes * 60)
            try:
                tenants = (
                    await tenant_provider()
                    if callable(tenant_provider)
                    else tenant_provider
                )
                result = await self.ensure_all(tenants)
                logger.info(
                    "Subscription heal: created=%d skipped=%d errors=%d",
                    result["created"], result["skipped"], len(result["errors"]),
                )
            except Exception as e:
                logger.warning("Subscription heal cycle failed: %s", e)
