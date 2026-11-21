"""Re-arm paused core subscriptions every minute; fail loudly when one stays paused.

Orion-LD pauses a subscription after three consecutive delivery failures and never
retries the notifications it could not deliver. The reconcilers that create the core
subscriptions only look at them on start-up (entity-manager) or once an hour
(telemetry-worker), so a pause used to cost hours of readings. This checker runs as a
short-lived job every minute: for every tenant it lists the subscriptions, keeps the
watched core ones and re-arms any that is not firing, through the same
``reactivate_if_paused`` every reconciler already uses.

Exit code 0 when every watched subscription is firing (or was just re-armed); 1 when one
stays not firing (expired, or the re-arm failed) or a tenant cannot be read. The alert is
raised on repeated failed runs.

Every pause of a watched subscription is re-armed. To stop one on purpose, stop the
service that owns it, not the subscription.

Entrypoint: ``python -m telemetry_worker.subscription_watchdog``.
"""

import logging
import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

import requests

from common.subscription_health import is_firing, reactivate_if_paused

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Core subscriptions whose silence loses readings: the entity-manager measurement bridge
# (while it exists) and every telemetry-worker subscription. Module subscriptions belong to
# their modules and are left alone.
WATCHED_ID_PREFIXES: tuple[str, ...] = (
    "urn:ngsi-ld:Subscription:entity-manager-measurements:",
    "urn:ngsi-ld:Subscription:telemetry-worker:",
)


@dataclass(frozen=True)
class TenantResult:
    tenant_id: str
    rearmed: tuple[str, ...] = ()
    not_firing: tuple[str, ...] = ()
    error: Optional[str] = None


def is_watched(subscription: dict) -> bool:
    return str(subscription.get("id") or "").startswith(WATCHED_ID_PREFIXES)


def retry_connection(fn: Callable[[], T], attempts: int = 3, delay_s: float = 5,
                     sleep: Callable[[float], None] = time.sleep) -> T:
    """Call ``fn``, retrying only connection failures and timeouts.

    A short-lived pod can start before its network policy is programmed; an HTTP error
    is an answer from Orion and is returned to the caller at once.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except (requests.ConnectionError, requests.Timeout):
            if attempt == attempts:
                raise
            sleep(delay_s)
    raise AssertionError("unreachable")


def check_tenant(tenant_id: str, fetch: Callable[[str], list],
                 rearm: Callable[[dict], bool]) -> TenantResult:
    """Re-arm the watched subscriptions of one tenant that are not firing."""
    try:
        subscriptions = fetch(tenant_id) or []
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        if status == 404:
            # Orion answers 404 NonExistingTenant for a tenant it has never stored
            # anything for: there is nothing to watch there yet.
            return TenantResult(tenant_id)
        return TenantResult(tenant_id, error=f"HTTPError {status}: {exc}")
    except Exception as exc:  # unreadable tenant: reported, never silenced
        return TenantResult(tenant_id, error=f"{type(exc).__name__}: {exc}")

    rearmed: list[str] = []
    not_firing: list[str] = []
    for subscription in subscriptions:
        if not is_watched(subscription) or is_firing(subscription):
            continue
        notification = subscription.get("notification") or {}
        logger.warning(
            "subscription_not_firing tenant=%s id=%s status=%s isActive=%s timesFailed=%s "
            "lastFailure=%s lastErrorReason=%s",
            tenant_id, subscription.get("id"), subscription.get("status"),
            subscription.get("isActive"), notification.get("timesFailed"),
            notification.get("lastFailure"), notification.get("lastErrorReason"),
        )
        if rearm(subscription):
            rearmed.append(subscription["id"])
        else:
            not_firing.append(subscription.get("id") or "<no id>")
    return TenantResult(tenant_id, tuple(rearmed), tuple(not_firing))


def exit_code(results: list) -> int:
    return 1 if any(r.not_firing or r.error for r in results) else 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from telemetry_worker import subscription_manager as sm

    def fetch(tenant_id: str) -> list:
        headers = sm._make_headers(tenant_id)
        return retry_connection(lambda: sm._fetch_all_subscriptions(headers))

    def rearm_for(tenant_id: str) -> Callable[[dict], bool]:
        headers = sm._make_headers(tenant_id)
        return lambda subscription: reactivate_if_paused(sm.ORION_URL, headers, subscription, logger)

    results = [check_tenant(t, fetch=fetch, rearm=rearm_for(t)) for t in sm.managed_tenants()]
    for r in results:
        if r.error:
            logger.error("tenant_unreadable tenant=%s error=%s", r.tenant_id, r.error)
        for sub_id in r.not_firing:
            logger.error("subscription_still_not_firing tenant=%s id=%s", r.tenant_id, sub_id)
    logger.info(
        "watchdog_run tenants=%d rearmed=%d not_firing=%d errors=%d",
        len(results), sum(len(r.rearmed) for r in results),
        sum(len(r.not_firing) for r in results), sum(1 for r in results if r.error),
    )
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
