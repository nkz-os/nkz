"""Re-arm paused core subscriptions every minute; fail loudly when one stays paused.

Orion-LD pauses a subscription after three consecutive delivery failures and never
retries the notifications it could not deliver. The reconcilers that create the core
subscriptions only look at them on start-up (entity-manager) or once an hour
(telemetry-worker), so a pause used to cost hours of readings. This checker runs as a
short-lived job every minute: for every tenant it lists the subscriptions, keeps the
watched core ones and re-arms any that is not firing, through the same
``reactivate_if_paused`` every reconciler already uses.

Exit code 0 only when every watched subscription of every tenant was read and found
firing. 1 when one had to be re-armed, stays not firing (expired, or the re-arm failed), a
tenant cannot be read, the tenant list cannot be read, or the run hits its time budget. A
single pause therefore fails a run or two; a pause that keeps coming back fails every run,
and the alert fires on repeated failures.

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

# The CronJob kills the pod at 50 s; the run stops itself before that so it can still log.
RUN_BUDGET_S = 40.0
# Per Orion call: a hung broker must not eat the whole budget on one tenant.
ORION_TIMEOUT_S = 8.0
TENANT_RETRY_ATTEMPTS = 3
TENANT_RETRY_DELAY_S = 3.0

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
    watched: int = 0


def is_watched(subscription: dict) -> bool:
    return str(subscription.get("id") or "").startswith(WATCHED_ID_PREFIXES)


def retry_connection(fn: Callable[[], T], attempts: int = 3, delay_s: float = 5,
                     sleep: Callable[[float], None] = time.sleep) -> T:
    """Call ``fn``, retrying only connection failures.

    A short-lived pod can start before its network policy is programmed, which shows as a
    refused or timed-out connect (``ConnectTimeout`` is a ``ConnectionError``). A read
    timeout or an HTTP error is an answer from a slow or failing Orion: returned at once.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except requests.ConnectionError:
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
        if status == 404 and _is_non_existing_tenant(exc.response):
            # Orion answers 404 NonExistingTenant for a tenant it has never stored
            # anything for: there is nothing to watch there yet. Any other 404 (wrong
            # path, a proxy) is an error, or every tenant would read as healthy.
            return TenantResult(tenant_id)
        return TenantResult(tenant_id, error=f"HTTPError {status}: {exc}")
    except Exception as exc:  # unreadable tenant: reported, never silenced
        return TenantResult(tenant_id, error=f"{type(exc).__name__}: {exc}")

    rearmed: list[str] = []
    not_firing: list[str] = []
    watched = 0
    for subscription in subscriptions:
        if not is_watched(subscription):
            continue
        watched += 1
        if is_firing(subscription):
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
    return TenantResult(tenant_id, tuple(rearmed), tuple(not_firing), watched=watched)


def _is_non_existing_tenant(response) -> bool:
    try:
        body = response.json()
    except Exception:
        return False
    return isinstance(body, dict) and str(body.get("type", "")).endswith("NonExistingTenant")


def exit_code(results: list) -> int:
    return 1 if any(r.rearmed or r.not_firing or r.error for r in results) else 0


def load_tenants(get: Callable[[], list], attempts: int = TENANT_RETRY_ATTEMPTS,
                 delay_s: Optional[float] = None,
                 sleep: Callable[[float], None] = time.sleep) -> list:
    """The active tenants, or RuntimeError. Never a fallback tenant.

    An empty answer is what the shared query returns when Postgres is unreachable (it
    logs and returns []), and the database is this pod's first network call, so it is
    retried like the start-up race; still empty means the run cannot check anything.
    """
    delay = TENANT_RETRY_DELAY_S if delay_s is None else delay_s
    for attempt in range(1, attempts + 1):
        tenants = get()
        if tenants:
            return list(tenants)
        if attempt < attempts:
            sleep(delay)
    raise RuntimeError("no active tenants could be read from the database")


def run_tenants(tenants: list, check: Callable[[str], TenantResult],
                budget_s: float = RUN_BUDGET_S,
                clock: Callable[[], float] = time.monotonic) -> list:
    """Check tenants one by one, logging each result at once, within a time budget."""
    start = clock()
    results: list = []
    for tenant_id in tenants:
        if clock() - start > budget_s:
            result = TenantResult(tenant_id, error="budget exhausted")
        else:
            result = check(tenant_id)
        _log_result(result)
        results.append(result)
    return results


def _log_result(r: TenantResult) -> None:
    if r.error:
        logger.error("tenant_unreadable tenant=%s error=%s", r.tenant_id, r.error)
    for sub_id in r.rearmed:
        logger.warning("subscription_rearmed tenant=%s id=%s", r.tenant_id, sub_id)
    for sub_id in r.not_firing:
        logger.error("subscription_still_not_firing tenant=%s id=%s", r.tenant_id, sub_id)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from telemetry_worker import subscription_manager as sm

    try:
        tenants = load_tenants(sm._get_active_tenants, delay_s=TENANT_RETRY_DELAY_S)
    except RuntimeError as exc:
        logger.error("watchdog_run tenants=0 error=%s", exc)
        return 1

    def fetch(tenant_id: str) -> list:
        headers = sm._make_headers(tenant_id)
        return retry_connection(
            lambda: sm._fetch_all_subscriptions(headers, timeout=ORION_TIMEOUT_S))

    def check(tenant_id: str) -> TenantResult:
        headers = sm._make_headers(tenant_id)
        return check_tenant(
            tenant_id, fetch=fetch,
            rearm=lambda subscription: reactivate_if_paused(
                sm.ORION_URL, headers, subscription, logger, timeout=ORION_TIMEOUT_S))

    results = run_tenants(tenants, check)
    logger.info(
        "watchdog_run tenants=%d watched=%d rearmed=%d not_firing=%d errors=%d",
        len(results), sum(r.watched for r in results), sum(len(r.rearmed) for r in results),
        sum(len(r.not_firing) for r in results), sum(1 for r in results if r.error),
    )
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
