"""SubscriptionRegistrar — declarative Orion-LD subscription management.

One subscription per entity type per tenant (not per parcel — filtering
happens in the module's notification handler). Idempotent via a
deterministic subscription id (`urn:ngsi-ld:Subscription:{module}:{type}`,
see `_subscription_id`): every process that wants this subscription POSTs
the same id, so Orion's own duplicate-id rejection (409) arbitrates
concurrent heal cycles instead of a check-then-create read that two
processes can both pass at once. A 409 is followed by a PATCH that
converges the existing subscription onto the declared one (endpoint,
receiverInfo, watched attributes, throttling) and re-arms it if Orion has
paused it — otherwise a changed declaration never reaches the broker.
All Orion-LD I/O goes through OrionClient (NGSI-LD compliance at SDK level).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from nkz_platform_sdk.notify_auth import (
    MASTER_HEADER,
    TOKEN_HEADER,
    notification_token,
    receiver_host_from_url,
)
from nkz_platform_sdk.installations import InstalledTenantsUnavailable, installed_tenants
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


def _short_type(value: object) -> str:
    """Entity type as declared: Orion may echo it expanded to a URI."""
    text = value if isinstance(value, str) else ""
    return text.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


@dataclass
class SubscriptionDef:
    type: str
    # Seconds between notifications; None sends no throttling, so none is applied.
    # Orion-LD rejects 0 (400 on create and on update), hence None for "no throttling".
    throttling: int | None = 30
    watched_attributes: list[str] | None = None
    condition: dict | None = None

    def __post_init__(self) -> None:
        if self.throttling is not None and self.throttling <= 0:
            raise ValueError(
                f"{self.type}: throttling must be positive, or None for no throttling "
                f"(got {self.throttling})"
            )


class SubscriptionRegistrar:
    """Idempotent Orion-LD subscription manager.

    Usage at service startup::

        registrar = SubscriptionRegistrar(
            orion_url=settings.orion_ld_url,
            notification_url="http://crop-health-api-service:8000/api/crop-health/webhooks/orion",
            subscriptions=[{"type": "EOProduct", "throttling": 30}],
            module_name="crop-health",
            internal_secret=os.environ["INTERNAL_SERVICE_SECRET"],
            derived_tokens=derived_tokens_from_env(),
        )
        await registrar.ensure_all(["tenant-a", "tenant-b"])
        asyncio.create_task(registrar.periodic_heal(get_tenants, interval_minutes=60))

    Receiver auth (``notification.endpoint.receiverInfo``): with ``internal_secret``
    the registrar owns the auth header. ``derived_tokens=False`` sends the master
    secret as ``X-Internal-Service-Secret`` (legacy); ``True`` sends
    ``X-Notification-Token``, a token bound to the tenant and the receiver host
    (see ``notify_auth``), so the broker never stores the master secret and the
    subscription body becomes tenant-dependent. Any master entry in
    ``notification_headers`` is dropped in both modes; other headers pass through.
    """

    def __init__(
        self,
        orion_url: str,
        notification_url: str,
        subscriptions: list[dict],
        module_name: str,
        context_url: str | None = None,
        notification_headers: dict[str, str] | None = None,
        internal_secret: str | None = None,
        derived_tokens: bool = False,
    ):
        self.orion_url = orion_url.rstrip("/")
        self.notification_url = notification_url
        self.context_url = context_url
        self.module_name = module_name
        self.notification_headers = notification_headers or {}
        self.internal_secret = internal_secret or ""
        self.derived_tokens = bool(derived_tokens)
        if self.derived_tokens and not self.internal_secret:
            raise ValueError(
                f"{module_name}: derived_tokens=True requires internal_secret "
                "(the master secret the token is derived from)"
            )
        # Resolved once: a notification URL without a host is a declaration bug,
        # and in derived mode it would make every token unverifiable.
        self.receiver_host = (
            receiver_host_from_url(notification_url) if self.derived_tokens else None
        )
        self._subs: list[SubscriptionDef] = [
            SubscriptionDef(**s) if isinstance(s, dict) else s for s in subscriptions
        ]
        self._reconcile_lock = asyncio.Lock()

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

    def _receiver_info(self, tenant_id: str | None) -> list[dict]:
        """Headers Orion injects into every delivery of this subscription.

        With ``internal_secret`` the auth header is the registrar's to emit, so a
        master entry in ``notification_headers`` is dropped rather than sent twice
        or next to the derived token.
        """
        headers = dict(self.notification_headers)
        if self.internal_secret:
            headers = {k: v for k, v in headers.items() if k.lower() != MASTER_HEADER.lower()}
            if self.derived_tokens:
                if not tenant_id:
                    raise ValueError(
                        f"{self.module_name}: derived tokens need the tenant to bind to"
                    )
                headers[TOKEN_HEADER] = notification_token(
                    self.internal_secret, tenant_id, self.receiver_host
                )
            else:
                headers[MASTER_HEADER] = self.internal_secret
        return [{"key": key, "value": value} for key, value in headers.items()]

    def _body(self, sub: SubscriptionDef, tenant_id: str | None = None) -> dict:
        """The declared subscription for ``tenant_id`` (needed only in derived-token mode)."""
        endpoint = {"uri": self.notification_url, "accept": "application/json"}
        receiver_info = self._receiver_info(tenant_id)
        if receiver_info:
            endpoint["receiverInfo"] = receiver_info
        body = {
            "id": self._subscription_id(sub),
            "type": "Subscription",
            "description": self._description(sub),
            "entities": [{"type": sub.type}],
            "notification": {
                "endpoint": endpoint,
                "format": "normalized",
            },
            "isActive": True,
        }
        if sub.throttling is not None:
            body["throttling"] = sub.throttling
        if sub.watched_attributes:
            body["watchedAttributes"] = sub.watched_attributes
        if sub.condition:
            body["condition"] = sub.condition
        return body

    def _fragment(self, sub: SubscriptionDef, tenant_id: str | None = None) -> dict:
        """The declared subscription as an update fragment: the body minus its id.

        Orion-LD rejects the id inside a PATCH. Carrying `isActive: True` is
        what re-arms a subscription Orion paused after 3 consecutive
        notification failures (verified live: 204, `paused` -> `active`).
        The whole `notification` member travels, so a receiverInfo that still
        carries the master secret is replaced by the declared one.
        """
        return {k: v for k, v in self._body(sub, tenant_id).items() if k != "id"}

    async def _converge_existing(
        self,
        client: OrionClient,
        sub: SubscriptionDef,
        sub_id: str,
        tenant_id: str,
        errors: list[str],
    ) -> bool:
        """PATCH the existing subscription onto the declared one. True when converged.

        A 409 only proves the id exists, not that it matches: a subscription
        created before a declaration change (new auth header, new watched
        attribute) or paused by Orion would otherwise stay wrong forever.
        Unconditional rather than read-compare-patch: reads are eventually
        consistent on this platform (see `_reconcile_ambiguous_create_failure`),
        and the PATCH is idempotent.

        404 is not an error: the 409 came from a create still settling in
        Orion's subscription cache, and that create carried this same body.
        Attributes the declaration no longer has (e.g. a dropped
        watchedAttributes) are not removed by a PATCH.
        """
        try:
            await client.update_subscription(sub_id, self._fragment(sub, tenant_id))
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 404:
                logger.debug(
                    "Subscription not yet readable for update: %s (tenant=%s, id=%s)",
                    self._description(sub), tenant_id, sub_id,
                )
                return False
            errors.append(f"{tenant_id}/{sub.type} update {sub_id}: {e}")
            logger.warning("Subscription update failed: %s", errors[-1])
            return False
        except Exception as e:
            errors.append(f"{tenant_id}/{sub.type} update {sub_id}: {e}")
            logger.warning("Subscription update failed: %s", errors[-1])
            return False
        logger.debug(
            "Subscription converged: %s (tenant=%s, id=%s)",
            self._description(sub), tenant_id, sub_id,
        )
        return True

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

        This listing is subject to the same eventually-consistent read
        cache as any other Orion subscription GET (measured ~10s+ lag on
        this platform's 60s subscription-cache interval), so it can miss a
        duplicate created only seconds ago. That is fine and deliberately
        left as-is: legacy duplicates are old and long since settled in the
        cache, and a genuinely fresh duplicate — one created moments before
        this sweep runs — simply gets caught on a later heal cycle instead.
        Do not "fix" this by reading the id back to confirm freshness; see
        `_reconcile_ambiguous_create_failure` for why an immediate read
        cannot be trusted here.
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

    async def _reconcile_ambiguous_create_failure(
        self,
        client: OrionClient,
        sub: SubscriptionDef,
        sub_id: str,
        tenant_id: str,
        status: int | None,
        error: Exception,
        errors: list[str],
    ) -> str:
        """Disambiguate a non-409 create failure by retrying the create, never by reading.

        Orion's subscription reads are eventually consistent on this
        platform (the broker runs with a 60s subscription-cache interval,
        set deliberately) -- measured against the live broker, a `GET` for
        a just-created subscription answered 404 at ~0s and ~2s after the
        create, and only turned 200 at ~10s and ~30s. A read taken right
        after an ambiguous failure can therefore say "does not exist" for a
        subscription that in fact exists, which makes a read useless for
        resolving a race inside that window. **No code should verify a
        subscription by reading it back immediately after creating one —
        the cache has not caught up yet.**

        The create path, unlike reads, is strongly consistent: across every
        concurrent round observed against the live broker, exactly one
        caller received 201 and every other caller was rejected. So the
        create itself is the oracle here: retry the POST once, immediately,
        no sleep (the create path doesn't touch the read cache, so waiting
        buys nothing).

        - retry -> 409: someone else holds the id -> skipped, like a plain 409
          (the caller then converges it, same as a plain 409).
        - retry -> 201: we hold it -> created (caller runs the legacy purge,
          same as the primary 201 path).
        - retry -> anything else: a real error. The message names both the
          original and the retry status.

        Returns "skipped", "created", or "error" (in the "error" case the
        message is already appended to `errors` and logged).
        """
        try:
            await client.create_subscription(self._body(sub, tenant_id))
        except httpx.HTTPStatusError as e:
            retry_status = e.response.status_code if e.response is not None else None
            if retry_status == 409:
                logger.debug(
                    "Subscription create retry collided (concurrent create won): "
                    "%s (tenant=%s, id=%s, first_status=%s)",
                    self._description(sub), tenant_id, sub_id, status,
                )
                return "skipped"
            errors.append(
                f"{tenant_id}/{sub.type}: create failed twice "
                f"(status={status}, retry_status={retry_status}): {e}"
            )
            logger.error("Subscription create failed: %s", errors[-1])
            return "error"
        except Exception as e:
            errors.append(
                f"{tenant_id}/{sub.type}: create failed twice "
                f"(status={status}, retry_status=None): {e}"
            )
            logger.error("Subscription create failed: %s", errors[-1])
            return "error"
        else:
            logger.info(
                "Subscription created on retry: %s (tenant=%s, id=%s, first_status=%s)",
                self._description(sub), tenant_id, sub_id, status,
            )
            return "created"

    async def _record_created(
        self,
        client: OrionClient,
        sub: SubscriptionDef,
        sub_id: str,
        tenant_id: str,
        errors: list[str],
    ) -> None:
        """Log a successful create and run the legacy-duplicate sweep.

        Shared by the primary 201 path and the ambiguous-failure retry's
        201 outcome — both mean this call is the one that made the
        subscription exist.
        """
        logger.info(
            "Subscription created: %s (tenant=%s, id=%s)",
            self._description(sub), tenant_id, sub_id,
        )
        await self._purge_legacy_duplicates(client, sub, sub_id, tenant_id, errors)

    async def ensure_all(self, tenant_ids: list[str]) -> dict:
        """Ensure subscriptions exist and match the declaration. Idempotent, never raises.

        No listing is done to decide whether to create: each subscription
        POSTs straight away with its deterministic id (`_subscription_id`)
        and the create either succeeds (201, this process made it exist)
        or collides (409, it already existed — the expected outcome when a
        concurrent heal cycle won the race, not an error). A 409 is
        followed by `_converge_existing`, which PATCHes the existing
        subscription onto the declaration and re-arms it if paused;
        `converged` counts those. A non-409
        failure is not trusted at face value — the broker can answer 500
        for the same race loss it would normally answer 409 for — so it is
        reconciled via `_reconcile_ambiguous_create_failure`, which retries
        the create rather than reading (reads are eventually consistent on
        this platform; see that method's docstring). Legacy subscriptions
        from before this scheme are converged away after a 201 (first-try
        or on retry) via `_purge_legacy_duplicates`.
        """
        created, skipped, converged, errors = 0, 0, 0, []
        for tenant_id in tenant_ids:
            client = OrionClient(
                tenant_id, base_url=self.orion_url, context_url=self.context_url
            )
            try:
                for sub in self._subs:
                    sub_id = self._subscription_id(sub)
                    try:
                        await client.create_subscription(self._body(sub, tenant_id))
                    except httpx.HTTPStatusError as e:
                        status = e.response.status_code if e.response is not None else None
                        if status == 409:
                            skipped += 1
                            logger.debug(
                                "Subscription already exists (concurrent create won): "
                                "%s (tenant=%s, id=%s)",
                                self._description(sub), tenant_id, sub_id,
                            )
                            if await self._converge_existing(
                                client, sub, sub_id, tenant_id, errors
                            ):
                                converged += 1
                        else:
                            outcome = await self._reconcile_ambiguous_create_failure(
                                client, sub, sub_id, tenant_id, status, e, errors
                            )
                            if outcome == "skipped":
                                skipped += 1
                                if await self._converge_existing(
                                    client, sub, sub_id, tenant_id, errors
                                ):
                                    converged += 1
                            elif outcome == "created":
                                created += 1
                                await self._record_created(
                                    client, sub, sub_id, tenant_id, errors
                                )
                    except Exception as e:
                        outcome = await self._reconcile_ambiguous_create_failure(
                            client, sub, sub_id, tenant_id, None, e, errors
                        )
                        if outcome == "skipped":
                            skipped += 1
                            if await self._converge_existing(
                                client, sub, sub_id, tenant_id, errors
                            ):
                                converged += 1
                        elif outcome == "created":
                            created += 1
                            await self._record_created(
                                client, sub, sub_id, tenant_id, errors
                            )
                    else:
                        created += 1
                        await self._record_created(client, sub, sub_id, tenant_id, errors)
            finally:
                await client.close()
        return {
            "created": created,
            "skipped": skipped,
            "converged": converged,
            "errors": errors,
        }

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
                    "Subscription heal: created=%d skipped=%d converged=%d errors=%d",
                    result["created"], result["skipped"], result["converged"],
                    len(result["errors"]),
                )
            except Exception as e:
                logger.warning("Subscription heal cycle failed: %s", e)

    def _is_own_legacy(self, existing: dict, sub: SubscriptionDef) -> bool:
        """A copy of `sub` under a non-deterministic id, made by this module.

        Exact description (it already names type and module) AND the same
        entity type. Another registrar of the same module with other types,
        or another module watching the same type, never matches.
        """
        if existing.get("description") != self._description(sub):
            return False
        entities = existing.get("entities") or []
        if isinstance(entities, dict):  # Orion unwraps one-element arrays
            entities = [entities]
        types = {_short_type(e.get("type")) for e in entities if isinstance(e, dict)}
        return types == {sub.type}

    async def remove_all(self, tenant_ids: list[str]) -> dict:
        """Delete this registrar's subscriptions in each tenant. Idempotent, never raises.

        Per declared subscription: its deterministic id and legacy copies
        (`_is_own_legacy`). 404 counts as already absent. Nothing else is
        touched. A failed listing only skips the legacy sweep.
        """
        removed, absent, errors = 0, 0, []
        for tenant_id in tenant_ids:
            client = OrionClient(
                tenant_id, base_url=self.orion_url, context_url=self.context_url
            )
            try:
                try:
                    existing = await client.query_all_subscriptions()
                except Exception as e:
                    existing = None
                    errors.append(f"{tenant_id} remove list: {e}")
                for sub in self._subs:
                    targets = [self._subscription_id(sub)]
                    for s in existing or []:
                        sid = s.get("id")
                        if sid and sid not in targets and self._is_own_legacy(s, sub):
                            targets.append(sid)
                    for sid in targets:
                        try:
                            await client.delete_subscription(sid)
                            removed += 1
                            logger.info(
                                "Subscription removed: %s (tenant=%s, id=%s)",
                                self._description(sub), tenant_id, sid,
                            )
                        except httpx.HTTPStatusError as e:
                            if e.response is not None and e.response.status_code == 404:
                                absent += 1
                            else:
                                errors.append(f"{tenant_id}/{sub.type} remove {sid}: {e}")
                        except Exception as e:
                            errors.append(f"{tenant_id}/{sub.type} remove {sid}: {e}")
            finally:
                await client.close()
        return {"removed": removed, "absent": absent, "errors": errors}

    async def reconcile_installations(self, module_id: str, **installed_kwargs) -> dict:
        """Converge on where `module_id` is installed; an outage never deletes.

        Asks entity-manager (`installed_tenants`). When it cannot answer,
        nothing is touched. Otherwise ensure_all(enabled) and
        remove_all(disabled + not_installed). Serialized per registrar, and
        the snapshot is taken inside the lock, so a stale heal cannot undo a
        fresher lifecycle hint.
        """
        async with self._reconcile_lock:
            try:
                inst = await installed_tenants(module_id, **installed_kwargs)
            except InstalledTenantsUnavailable as e:
                logger.warning("Subscription reconcile skipped for %s: %s", module_id, e)
                return {"skipped": "unavailable", "reason": str(e)}
            ensured = await self.ensure_all(list(inst.enabled))
            removed = await self.remove_all(list(inst.disabled + inst.not_installed))
            return {"ensured": ensured, "removed": removed}

    async def periodic_reconcile(
        self, module_id: str, interval_minutes: int = 60, **installed_kwargs
    ) -> None:
        """`reconcile_installations` every `interval_minutes`. Never exits, never raises."""
        while True:
            await asyncio.sleep(interval_minutes * 60)
            try:
                result = await self.reconcile_installations(module_id, **installed_kwargs)
                logger.info("Subscription reconcile for %s: %s", module_id, result)
            except Exception as e:
                logger.warning("Subscription reconcile cycle failed for %s: %s", module_id, e)
