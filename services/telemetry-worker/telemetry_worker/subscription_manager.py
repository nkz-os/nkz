import asyncio
import os
import logging
import re
import time
import requests

from prometheus_client import Counter
from tenacity import retry, stop_after_attempt, wait_fixed
from common.ngsi_headers import inject_fiware_headers
from common.subscription_health import reactivate_if_paused
from nkz_platform_sdk.notify_auth import (
    MASTER_HEADER,
    TOKEN_HEADER,
    derived_tokens_from_env,
    notification_token,
    receiver_host_from_url,
)

logger = logging.getLogger(__name__)

# Incremented whenever creating an Orion-LD subscription fails (non-2xx response
# or an exception). Ops can alert on a non-zero rate — a persistent failure
# means a tenant stops receiving telemetry notifications silently.
SUBSCRIPTION_CREATION_FAILED = Counter(
    "telemetry_subscription_creation_failed_total",
    "Orion-LD subscription creations that failed",
    ["tenant_id", "reason"],
)

ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")
SERVICE_HOST = os.getenv("SERVICE_HOST", "telemetry-worker-service")
SERVICE_PORT = os.getenv("SERVICE_PORT", "80")
NOTIFICATION_URL = f"http://{SERVICE_HOST}:{SERVICE_PORT}/notify"
GEOMETRY_NOTIFICATION_URL = f"http://{SERVICE_HOST}:{SERVICE_PORT}/notify/geometry"
POSTGRES_URL = os.getenv("POSTGRES_URL", "")
DEFAULT_TENANT = os.getenv("DEFAULT_TENANT", "platform")

# Entity types whose `location` feeds observation_geometry_cache (contract 1.4):
# the geometry subscription and the startup backfill (geometry_backfill) share it.
# This tuple is the only place the types are listed: adding one is a one-line change.
# Nothing here depends on the AgriParcelZone name or its id pattern. The per-type plan
# (sheet 12) may migrate AgriParcelZone to AgriManagementZone, which has no @context
# term yet; the entry is then replaced or extended here, with no logic touched.
GEOMETRY_ENTITY_TYPES = ("AgriParcel", "AgriParcelZone", "AgriPlant", "AgriGreenhouse")

# NGSI-LD subscriptions — no watchedAttributes = trigger on ANY attribute change.
# The six "... history" subscriptions carry no "throttling": Orion applies it per
# subscription, covering every entity of the type in the tenant, so any window drops
# changes of the other entities and those changes are stored as history. Their
# descriptions are new so the ids are new (see REPLACED_DESCRIPTIONS).
SUBSCRIPTIONS = [
    {
        # DeviceMeasurement is what entity-manager writes from Device/ManufacturingMachine
        # attribute updates (one entity per device+controlledProperty, overwritten in
        # place — see entity-manager/blueprints/measurements.py). This is the canonical
        # route a sensor reading takes to reach TimescaleDB; notification_handler.py has a
        # dedicated branch to read its inverted shape (device id in refDevice, measurement
        # name in controlledProperty, value in numValue/textValue, instant in dateObserved).
        # No "throttling": Orion applies it per subscription and this one covers every
        # device in the tenant, so any window would drop readings from the others.
        "description": "Telemetry Worker - DeviceMeasurement readings",
        "type": "Subscription",
        "entities": [{"type": "DeviceMeasurement"}],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json",
            },
            "format": "normalized",
        },
        "isActive": True,
    },
    {
        # Lab results (plant tissue samples). Each result carries observedAt = sampling date, so the
        # row lands at the sampling instant, not at import time. No "throttling": a
        # lab import writes hundreds of samples at once and Orion applies the window
        # per subscription, so any value would drop most of them.
        "description": "Telemetry Worker - AgriPlantSample results",
        "type": "Subscription",
        "entities": [{"type": "AgriPlantSample"}],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json",
            },
            "format": "normalized",
        },
        "isActive": True,
    },
    {
        # Lab results (soil samples). Each result carries observedAt = sampling date, so the
        # row lands at the sampling instant, not at import time. No "throttling": a
        # lab import writes hundreds of samples at once and Orion applies the window
        # per subscription, so any value would drop most of them.
        "description": "Telemetry Worker - AgriSoilSample results",
        "type": "Subscription",
        "entities": [{"type": "AgriSoilSample"}],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json",
            },
            "format": "normalized",
        },
        "isActive": True,
    },
    {
        "description": "Telemetry Worker - AgriParcel updates",
        "type": "Subscription",
        "entities": [{"type": "AgriParcel"}],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json",
            },
            "format": "normalized",
        },
        "throttling": 30,
        "isActive": True,
    },
    {
        "description": "Telemetry Worker - VegetationIndex analysis results",
        "type": "Subscription",
        "entities": [{"type": "VegetationIndex"}],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json",
            },
            "format": "normalized",
        },
        "throttling": 5,
        "isActive": True,
    },
    {
        "description": "Telemetry Worker - CropHealthAssessment history",
        "type": "Subscription",
        "entities": [{"type": "CropHealthAssessment"}],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json",
            },
            "format": "normalized",
        },
        "isActive": True,
    },
    {
        "description": "Telemetry Worker - WeatherObserved history",
        "type": "Subscription",
        "entities": [{"type": "WeatherObserved"}],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json",
            },
            "format": "normalized",
        },
        "isActive": True,
    },
    {
        "description": "Telemetry Worker - WeatherForecast history",
        "type": "Subscription",
        "entities": [{"type": "WeatherForecast"}],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json",
            },
            "format": "normalized",
        },
        "isActive": True,
    },
    {
        "description": "Telemetry Worker - WeatherAlert history",
        "type": "Subscription",
        "entities": [{"type": "WeatherAlert"}],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json",
            },
            "format": "normalized",
        },
        "isActive": True,
    },
    {
        "description": "Telemetry Worker - AgriSoil history",
        "type": "Subscription",
        "entities": [{"type": "AgriSoil"}],
        "watchedAttributes": ["currentMoisture", "deficitMm", "fieldCapacity", "awc"],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json",
            },
            "format": "normalized",
            "attributes": [
                "currentMoisture",
                "deficitMm",
                "fieldCapacity",
                "awc",
                "wiltingPoint",
                "forecast7d",
                "lastComputed"
            ],
        },
        "isActive": True,
    },
    {
        "description": "Telemetry Worker - AgriParcelRecord history",
        "type": "Subscription",
        "entities": [{"type": "AgriParcelRecord"}],
        "watchedAttributes": ["eto", "solarRadiation", "soilMoistureVwc"],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json",
            },
            "format": "normalized",
            "attributes": [
                "eto",
                "solarRadiation",
                "soilMoistureVwc",
                "waterBalance",
                "frostRisk",
                "airTemperatureAvg",
                "airTemperatureMin",
                "soilTemperature",
                "relativeHumidity",
            ],
        },
        "isActive": True,
    },
    {
        # No "throttling": Orion applies it per subscription and this one covers every
        # zone of every parcel in the tenant, so any window would drop zones' daily values.
        "description": "Telemetry Worker - Zone downscaled weather (AgriParcelZone)",
        "type": "Subscription",
        "entities": [{"type": "AgriParcelZone"}],
        "watchedAttributes": ["tMin", "tMax", "eto"],
        "notification": {
            "endpoint": {
                "uri": NOTIFICATION_URL,
                "accept": "application/json",
            },
            "format": "normalized",
            "attributes": [
                "tMin",
                "tMax",
                "eto",
                "solarRadiationWm2",
                "frostRisk",
                "zoneId",
                "zoneVersion",
                "zoneType",
            ],
        },
        "isActive": True,
    },
    {
        # Keeps observation_geometry_cache current (contract 1.4). No "throttling": a
        # bulk parcel import must not drop zones. Greenhouse and AgriSolarPark are not
        # terms of the platform @context, so they are left out until they are defined
        # (an undefined type expands to the default vocabulary and never matches).
        "description": "Telemetry Worker - Geometry cache (location)",
        "type": "Subscription",
        "entities": [{"type": t} for t in GEOMETRY_ENTITY_TYPES],
        "watchedAttributes": ["location"],
        "notification": {
            "endpoint": {
                "uri": GEOMETRY_NOTIFICATION_URL,
                "accept": "application/json",
            },
            "format": "normalized",
        },
        "isActive": True,
    },
]

# Subscriptions this worker used to declare. Dropping an entry above only stops it
# being reconciled; the one already registered stays in the broker, per tenant,
# until it is deleted here. Exact descriptions only: never a prefix, never a type.
RETIRED_DESCRIPTIONS = frozenset({
    "Telemetry Worker - AgriSensor updates",
    "Telemetry Worker - AgriDevice updates",
})

# Declared subscriptions that replace an older definition with another id. Orion cannot
# drop "throttling" with a PATCH, and deleting and recreating one id loses notifications
# while the broker's per-replica cache is stale, so the new one is created first and the
# old one (exact description) is deleted only once the new one is proven active.
REPLACED_DESCRIPTIONS = {
    "Telemetry Worker - CropHealthAssessment updates":
        "Telemetry Worker - CropHealthAssessment history",
    "Telemetry Worker - Weather zonal stats (AgriParcelRecord)":
        "Telemetry Worker - AgriParcelRecord history",
    "Telemetry Worker - AgriSoil water budget attributes":
        "Telemetry Worker - AgriSoil history",
    "Telemetry Worker - WeatherObserved virtual stations":
        "Telemetry Worker - WeatherObserved history",
    "Telemetry Worker - WeatherForecast daily aggregates":
        "Telemetry Worker - WeatherForecast history",
    "Telemetry Worker - WeatherAlert updates":
        "Telemetry Worker - WeatherAlert history",
}

# Orion caches subscriptions for about a minute per replica: the old one is deleted
# only this long after this process first saw (or created) its replacement.
RETIRE_MIN_AGE_S = 180
# One early extra reconcile pass, so old and new coexist minutes, not an hour.
FIRST_EXTRA_PASS_DELAY_S = 240
RECONCILE_INTERVAL_S = 3600

_monotonic = time.monotonic
# (tenant, subscription id) -> monotonic time this process first saw or created it.
_FIRST_SEEN: dict = {}
# tenant -> old ids kept at the last pass because their replacement is not yet proven.
_PENDING: dict = {}
MAX_SHORT_PASSES = 10


def has_pending_retirements() -> bool:
    return any(_PENDING.values())


INTERNAL_SERVICE_SECRET = os.getenv("INTERNAL_SERVICE_SECRET", "")
_SUB_ID_PREFIX = "telemetry-worker"


def _slugify(text: str) -> str:
    """'AgriSensor updates' -> 'agrisensor-updates' (deterministic subscription id)."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _subscription_id(sub_def: dict) -> str:
    suffix = sub_def.get("description", "").split(" - ", 1)[-1]
    return f"urn:ngsi-ld:Subscription:{_SUB_ID_PREFIX}:{_slugify(suffix)}"


RECEIVER_HOST = receiver_host_from_url(NOTIFICATION_URL)


def _auth_header(tenant_id):
    """The header Orion must inject into every delivery, or None without a secret.

    Legacy: the master secret itself. With NKZ_NOTIFY_DERIVED_TOKENS on, a token
    bound to this tenant and this receiver (see nkz_platform_sdk.notify_auth), so
    the broker never stores the master.
    """
    if not INTERNAL_SERVICE_SECRET:
        return None
    if derived_tokens_from_env():
        if not tenant_id:
            raise ValueError("derived notification tokens need the tenant to bind to")
        return TOKEN_HEADER, notification_token(INTERNAL_SERVICE_SECRET, tenant_id, RECEIVER_HOST)
    return MASTER_HEADER, INTERNAL_SERVICE_SECRET


def _subscription_body(sub_def: dict, tenant_id=None) -> dict:
    """Subscription body with a deterministic id and the receiverInfo auth header
    (tenant-dependent in derived-token mode)."""
    body = {**sub_def, "id": _subscription_id(sub_def)}
    auth = _auth_header(tenant_id)
    if auth:
        endpoint = {
            **sub_def["notification"]["endpoint"],
            "receiverInfo": [{"key": auth[0], "value": auth[1]}],
        }
        body["notification"] = {**sub_def["notification"], "endpoint": endpoint}
    return body


def _matches_are_stale(matches: list, tenant_id=None) -> bool:
    """Replace existing subscription(s) when they are duplicates (pre-deterministic-id
    creates), lack the auth header the receiver now expects, carry a stale value
    (after a rotation), or still carry the master once tokens are derived."""
    if not matches:
        return False
    if len(matches) > 1:
        return True
    auth = _auth_header(tenant_id)
    if auth is None:
        return False
    key, value = auth
    endpoint = matches[0].get("notification", {}).get("endpoint", {})
    present = {h.get("key"): h.get("value") for h in endpoint.get("receiverInfo") or []}
    if present.get(key) != value:
        return True
    return key != MASTER_HEADER and MASTER_HEADER in present


def _delete_subscription(headers: dict, sub_id: str) -> bool:
    """True when the subscription is gone (200/204/404); False on any failure."""
    if not sub_id:
        return False
    try:
        res = requests.delete(
            f"{ORION_URL}/ngsi-ld/v1/subscriptions/{sub_id}", headers=headers, timeout=30
        )
        if res.status_code not in (200, 204, 404):
            logger.warning("Delete subscription %s: HTTP %s", sub_id, res.status_code)
            return False
        return True
    except requests.RequestException as e:
        logger.warning("Delete subscription %s failed: %s", sub_id, e)
        return False


def _replace_subscription(headers: dict, sub_def: dict, tenant_id: str, sub_id: str) -> None:
    """Update a subscription in place, so it is never briefly absent.

    PATCH carries the body minus the id, which Orion-LD rejects inside an
    update. Falling back to delete-then-create would reopen the gap this closes,
    so a failed patch is reported and the old subscription left alone.
    """
    body = {k: v for k, v in _subscription_body(sub_def, tenant_id).items() if k != "id"}
    try:
        res = requests.patch(
            f"{ORION_URL}/ngsi-ld/v1/subscriptions/{sub_id}",
            json=body,
            headers=headers,
            timeout=30,
        )
        if res.status_code in (200, 204):
            logger.info("Subscription '%s' refreshed for %s", sub_def["description"], tenant_id)
            return
        logger.error(
            "Refresh failed: %s for %s: %s %s",
            sub_def["description"], tenant_id, res.status_code, res.text[:200],
        )
    except requests.RequestException as e:
        logger.error("Refresh failed: %s for %s: %s", sub_def["description"], tenant_id, e)


def _create_subscription(headers: dict, sub_def: dict, tenant_id: str) -> bool:
    """POST the subscription; True when it now exists (created or already there)."""
    res = requests.post(
        f"{ORION_URL}/ngsi-ld/v1/subscriptions",
        json=_subscription_body(sub_def, tenant_id),
        headers=headers,
        timeout=30,
    )
    if res.status_code in (200, 201, 409):  # 409 = concurrent create won
        logger.info("Subscription '%s' ensured for %s", sub_def["description"], tenant_id)
        return True
    else:
        SUBSCRIPTION_CREATION_FAILED.labels(
            tenant_id=tenant_id, reason=f"http_{res.status_code}"
        ).inc()
        logger.error(
            "Failed: %s for %s: %s %s",
            sub_def["description"], tenant_id, res.status_code, res.text[:200],
        )
        return False


def _make_headers(tenant_id: str) -> dict:
    """Build Orion-LD headers — tenant sent AS-IS (canonical is hyphenated)."""
    return inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False)


def _get_active_tenants() -> list:
    """Query PostgreSQL for all active tenant IDs."""
    if not POSTGRES_URL:
        logger.warning("POSTGRES_URL not set, cannot query tenants")
        return []
    try:
        import psycopg2

        conn = psycopg2.connect(POSTGRES_URL)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT DISTINCT tenant_id FROM tenants WHERE tenant_id IS NOT NULL"
            )
            rows = cur.fetchall()
            cur.close()
            return [r[0] for r in rows]
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Error querying active tenants: {e}")
        return []


# Orion-LD serves subscription listings one page at a time and falls back to a
# page of 20 when no limit is given. A dedup built on a single unpaginated page
# stops recognising its own subscriptions once a tenant holds more than a page
# of them, and recreates them on every run.
ORION_PAGE_SIZE = 1000


def _fetch_all_subscriptions(headers: dict, timeout: float = 30) -> list:
    """Return every subscription of a tenant, following Orion's pagination."""
    subs: list = []
    offset = 0
    while True:
        response = requests.get(
            f"{ORION_URL}/ngsi-ld/v1/subscriptions",
            headers=headers,
            params={"limit": ORION_PAGE_SIZE, "offset": offset},
            timeout=timeout,
        )
        response.raise_for_status()
        page = response.json() or []
        subs.extend(page)
        if len(page) < ORION_PAGE_SIZE:
            return subs
        offset += ORION_PAGE_SIZE


def _cleanup_broken_subscriptions(tenant_id: str):
    """Delete subscriptions with wrong port (legacy bug)."""
    headers = _make_headers(tenant_id)
    try:
        for sub in _fetch_all_subscriptions(headers):
            uri = sub.get("notification", {}).get("endpoint", {}).get("uri", "")
            if ":8080" in uri and "telemetry-worker" in uri:
                sub_id = sub.get("id")
                requests.delete(
                    f"{ORION_URL}/ngsi-ld/v1/subscriptions/{sub_id}",
                    headers=headers,
                    timeout=30,
                )
                logger.info(f"Deleted broken subscription {sub_id} (port 8080)")
    except Exception as e:
        logger.warning(f"Error cleaning broken subscriptions for {tenant_id}: {e}")


def _ensure_tenant_subscriptions(tenant_id: str):
    """Reconcile this tenant's subscriptions against their deterministic ids."""
    headers = _make_headers(tenant_id)
    headers["Content-Type"] = "application/json"  # needed for POST below
    existing_subs = None
    by_id: dict = {}
    try:
        existing_subs = _fetch_all_subscriptions(headers) or []
        by_id = {s.get("id"): s for s in existing_subs if s.get("id")}
        by_description: dict = {}
        for existing in existing_subs:
            by_description.setdefault(existing.get("description"), []).append(existing)

        # The clock measures "seen ACTIVE": started only while the listing shows the
        # replacement active, dropped as soon as it does not (absent, paused, expired);
        # one created below starts it at its creation.
        for new_desc in REPLACED_DESCRIPTIONS.values():
            for sub in SUBSCRIPTIONS:
                if sub["description"] != new_desc:
                    continue
                key = (tenant_id, _subscription_id(sub))
                if _is_active(by_id.get(key[1])):
                    _FIRST_SEEN.setdefault(key, _monotonic())
                else:
                    _FIRST_SEEN.pop(key, None)

        # Before the declared ones, so a failure further down cannot skip it.
        # _delete_subscription counts a 404 as done and logs any other failure
        # without raising, so one bad delete never blocks the reconciliation.
        for retired in existing_subs:
            if retired.get("description") in RETIRED_DESCRIPTIONS and retired.get("id"):
                logger.info(
                    "Removing retired subscription %s for %s", retired.get("id"), tenant_id,
                )
                _delete_subscription(headers, retired.get("id"))

        for sub in SUBSCRIPTIONS:
            sub_id = _subscription_id(sub)
            canonical = by_id.get(sub_id)
            # The id is the identity; the description is only a label. Keying on
            # the description made editing one mint a new id and abandon the old
            # subscription, still registered and reconciled by nobody.
            leftovers = [
                s for s in by_description.get(sub["description"], [])
                if s.get("id") and s.get("id") != sub_id
            ]

            if canonical is None:
                if _create_subscription(headers, sub, tenant_id):
                    _FIRST_SEEN[(tenant_id, sub_id)] = _monotonic()
            elif _matches_are_stale([canonical], tenant_id):
                logger.info(
                    "Refreshing subscription '%s' for %s", sub["description"], tenant_id
                )
                _replace_subscription(headers, sub, tenant_id, sub_id)
            else:
                # Existing is not the same as firing: Orion pauses a subscription
                # after 3 consecutive notification failures and never resumes it.
                reactivate_if_paused(ORION_URL, headers, canonical, logger)

            # Only once the canonical one is accounted for: deleting first left a
            # window with no subscription, and notifications raised in it are lost.
            for leftover in leftovers:
                logger.info(
                    "Removing leftover subscription %s for %s (canonical is %s)",
                    leftover.get("id"), tenant_id, sub_id,
                )
                _delete_subscription(headers, leftover.get("id"))
    except Exception as e:
        SUBSCRIPTION_CREATION_FAILED.labels(
            tenant_id=tenant_id, reason="exception"
        ).inc()
        logger.error(f"Error managing subscriptions for {tenant_id}: {e}")

    if existing_subs is not None:
        try:
            _retire_replaced_subscriptions(headers, tenant_id, existing_subs, by_id)
        except Exception as e:
            logger.error(f"Error retiring replaced subscriptions for {tenant_id}: {e}")


def _is_active(sub) -> bool:
    """Stricter than health.is_firing on purpose: there a missing status counts as
    firing (re-arming a healthy one is harmless); here a missing status would delete the
    only working subscription on a guess, so it is not proof."""
    return bool(sub) and sub.get("isActive") is True and sub.get("status") == "active"


def _watch_signature(sub: dict):
    """What a subscription watches: entity types and watchedAttributes (absent = None)."""
    types = frozenset(e.get("type") for e in sub.get("entities") or [])
    watched = sub.get("watchedAttributes")
    return types, (frozenset(watched) if watched is not None else None)


def _replacement_is_proven(tenant_id: str, old: dict, new_id: str, by_id: dict):
    """(proven, reason, level): the start-of-pass listing shows the replacement active,
    watching what the old one watched, and this process has seen it active for
    RETIRE_MIN_AGE_S."""
    new = by_id.get(new_id)
    if new is None:
        return False, "replacement missing", logging.INFO
    if not _is_active(new):
        return False, "replacement not active", logging.INFO
    if _watch_signature(new) != _watch_signature(old):
        return False, (
            f"replacement watches {_watch_signature(new)!r} but the old one {_watch_signature(old)!r}"
        ), logging.ERROR
    first_seen = _FIRST_SEEN.get((tenant_id, new_id))
    if first_seen is None or _monotonic() - first_seen < RETIRE_MIN_AGE_S:
        return False, "replacement too recent", logging.INFO
    return True, "", logging.INFO


def _retire_replaced_subscriptions(headers: dict, tenant_id: str, existing_subs: list, by_id: dict):
    new_ids = {sub["description"]: _subscription_id(sub) for sub in SUBSCRIPTIONS}
    declared_ids = set(new_ids.values())
    pending = set()
    _PENDING[tenant_id] = pending
    for old in existing_subs:
        new_desc = REPLACED_DESCRIPTIONS.get(old.get("description"))
        old_id = old.get("id")
        if new_desc is None or not old_id:
            continue
        try:
            new_id = new_ids[new_desc]
            if old_id in declared_ids:
                logger.error(
                    "Not retiring %s for %s: it is a declared id", old_id, tenant_id,
                )
                continue
            pending.add(old_id)
            proven, reason, level = _replacement_is_proven(tenant_id, old, new_id, by_id)
            if not proven:
                logger.log(
                    level, "Keeping replaced subscription %s for %s: %s (new %s)",
                    old_id, tenant_id, reason, new_id,
                )
                continue
            if _delete_subscription(headers, old_id):
                pending.discard(old_id)
                logger.info(
                    "Retired replaced subscription tenant=%s old_id=%s new_id=%s",
                    tenant_id, old_id, new_id,
                )
        except Exception as e:
            logger.error("Retiring %s for %s failed: %s", old_id, tenant_id, e)


async def run_periodic_reconcile(reconcile, sleep=asyncio.sleep, pending=None):
    """One extra pass shortly after start, then one per interval, for ever.

    While a replaced subscription is still waiting to be retired (``pending()``), the
    short delay is kept, at most MAX_SHORT_PASSES times, then the hourly one.
    A failing pass is logged and the loop goes on. Cancellation propagates (the caller
    cancels and awaits the task; no wait_for, which on 3.11 can swallow it).
    """
    pending = pending or has_pending_retirements
    delay = FIRST_EXTRA_PASS_DELAY_S
    short_passes = 0
    while True:
        await sleep(delay)
        if delay == FIRST_EXTRA_PASS_DELAY_S:
            short_passes += 1
        try:
            await asyncio.get_running_loop().run_in_executor(None, reconcile)
            logger.info("Periodic subscription check completed")
        except Exception as e:
            logger.warning(f"Periodic subscription check failed: {e}")
        if short_passes < MAX_SHORT_PASSES and pending():
            delay = FIRST_EXTRA_PASS_DELAY_S
        else:
            delay = RECONCILE_INTERVAL_S


def managed_tenants() -> list:
    """Tenants this worker reconciles subscriptions for (the default one when the DB has none)."""
    tenants = _get_active_tenants()
    if not tenants:
        tenants = [DEFAULT_TENANT]
        logger.info(f"No tenants from DB, using default: {DEFAULT_TENANT}")
    return tenants


@retry(stop=stop_after_attempt(5), wait=wait_fixed(5))
def ensure_subscriptions_for_all_tenants():
    """Create NGSI-LD subscriptions for all active tenants."""
    tenants = managed_tenants()

    logger.info(f"Ensuring subscriptions for {len(tenants)} tenants: {tenants}")

    for tenant_id in tenants:
        _cleanup_broken_subscriptions(tenant_id)
        _ensure_tenant_subscriptions(tenant_id)


# Backwards compat alias for app.py import
check_or_create_subscription = ensure_subscriptions_for_all_tenants
