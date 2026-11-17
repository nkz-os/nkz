"""Daily freshness check of the stored closed days of every parcel.

The worker writes one closed day per parcel and day (``...:parcel-<id>-daily``)
and it reaches ``telemetry_events`` through the Orion subscription. Many things
can stop that silently: an unreadable daily entity (the worker then skips the
parcel), the provider, the worker itself, or a lost notification. This check
looks at the outcome, in the store ``/daily`` serves, and fails the Job (exit 1)
when a parcel is behind, so the CronJob's alert fires.

A parcel is behind when ``today (UTC) - latest stored day > MAX_LAG_DAYS``
(normal lag is 1). For each such parcel the daily entity and its parcel are
read in Orion: a daily entity or a parcel that no longer exists is not an outage
(daily entities can outlive their parcel) and it is ignored; a newer day in Orion
than in the store means the notification was lost.

Run: ``python3 -m weather_worker.closed_day_check``. Read-only.
"""

import logging
import os
import sys
import time
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import requests

sys.path.insert(0, "/app")
from common.ngsi_headers import inject_fiware_headers  # noqa: E402

from weather_worker import closed_day as cd  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_MAX_LAG_DAYS = 2
# Long enough that a parcel stuck for weeks keeps alerting instead of dropping out.
DEFAULT_LOOKBACK_DAYS = 60
DB_CONNECT_ATTEMPTS = 5
DB_BACKOFF_S = 10

_PARCEL_MARK = ":parcel-"
_DAILY_SUFFIX = "-daily"

_LATEST_SQL = """
SELECT tenant_id, entity_id, max(observed_at) AS latest
FROM telemetry_events
WHERE entity_type = %s
  AND entity_id LIKE %s
  AND observed_at >= now() - make_interval(days => %s)
GROUP BY tenant_id, entity_id
"""

Row = Tuple[str, str, date]

# The daily entity names its parcel with locatedAt; the platform context maps
# refParcel to the same IRI, so Orion may compact it under either name.
_PARCEL_RELATIONSHIPS = ("locatedAt", "refParcel")


def parcel_id_from_entity(entity_id: str) -> str:
    """``urn:...:parcel-<bare>-daily`` -> ``<bare>``."""
    bare = entity_id.split(_PARCEL_MARK, 1)[1]
    return bare[: -len(_DAILY_SUFFIX)] if bare.endswith(_DAILY_SUFFIX) else bare


def latest_stored_days(conn: Any, lookback_days: int) -> List[Row]:
    """(tenant, daily entity id, latest stored day) for entities with recent rows."""
    cur = conn.cursor()
    cur.execute(_LATEST_SQL, ("WeatherObserved", f"%{_PARCEL_MARK}%{_DAILY_SUFFIX}", lookback_days))
    out: List[Row] = []
    for tenant, entity_id, latest in cur.fetchall():
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        # observed_at = <day>T23:59:59Z: its UTC date is the day label.
        out.append((tenant, entity_id, latest.astimezone(timezone.utc).date()))
    return out


def inspect_daily(
    tenant: str,
    entity_id: str,
    *,
    orion_url: str = "",
    http: Any = requests,
) -> Optional[Tuple[str, bool]]:
    """(day in Orion, parcel still exists) for a daily entity; None when both are gone.

    A daily entity that is gone while its parcel lives gives ``(None, True)``
    (rows in the store prove it existed). Raises
    ``closed_day.LastPublishedUnreadable`` when a read fails. A daily entity that
    names no parcel counts as alive: better a false alert than a hidden outage.
    """
    orion_url = orion_url or os.getenv("ORION_URL", "http://orion-ld-service:1026")
    base = f"{orion_url}/ngsi-ld/v1/entities/"
    headers = inject_fiware_headers({}, tenant=tenant, body=None)

    def get(eid: str) -> Any:
        try:
            return http.get(base + eid, headers=headers, timeout=10)
        except Exception as e:  # noqa: BLE001 - unknown state, reported as unreadable
            raise cd.LastPublishedUnreadable(eid) from e

    resp = get(entity_id)
    if resp.status_code == 404:
        # The daily entity names its parcel; without it, try both URN forms.
        bare = parcel_id_from_entity(entity_id)
        for urn in (f"urn:ngsi-ld:AgriParcel:{bare}", f"urn:ngsi-ld:AgriParcel:{tenant}:{bare}"):
            presp = get(urn)
            if presp.status_code == 200:
                return None, True
            if presp.status_code != 404:
                raise cd.LastPublishedUnreadable(urn)
        return None
    if resp.status_code != 200:
        raise cd.LastPublishedUnreadable(entity_id)
    try:
        entity = resp.json()
    except Exception as e:  # noqa: BLE001
        raise cd.LastPublishedUnreadable(entity_id) from e
    day = cd.parse_date_observed(entity)
    if day is None:
        raise cd.LastPublishedUnreadable(entity_id)

    parcel_urn = None
    for name in _PARCEL_RELATIONSHIPS:
        node = entity.get(name)
        if isinstance(node, dict) and node.get("object"):
            parcel_urn = node["object"]
            break
    if parcel_urn is None:
        return day, True
    presp = get(parcel_urn)
    if presp.status_code == 404:
        return day, False
    if presp.status_code != 200:
        raise cd.LastPublishedUnreadable(parcel_urn)
    return day, True


def find_stale(
    rows: Iterable[Row],
    today: date,
    max_lag: int,
    inspect: Callable[[str, str], Optional[Tuple[str, bool]]],
) -> List[Dict[str, Any]]:
    """Parcels whose latest stored day lags more than ``max_lag`` days."""
    findings: List[Dict[str, Any]] = []
    for tenant, entity_id, stored in rows:
        if (today - stored).days <= max_lag:
            continue
        parcel = parcel_id_from_entity(entity_id)
        try:
            seen = inspect(tenant, entity_id)
        except cd.LastPublishedUnreadable:
            findings.append({"tenant": tenant, "parcel": parcel, "stored_day": stored.isoformat(),
                             "orion_day": None, "reason": "orion_unreadable"})
            continue
        if seen is None or not seen[1]:
            logger.info("closed days behind but parcel gone: tenant=%s parcel=%s", tenant, parcel)
            continue
        orion_day = seen[0]
        if orion_day is None:
            findings.append({"tenant": tenant, "parcel": parcel, "stored_day": stored.isoformat(),
                             "orion_day": None, "reason": "daily_missing"})
            continue
        reason = "notification_lost" if orion_day > stored.isoformat() else "stale"
        findings.append({"tenant": tenant, "parcel": parcel, "stored_day": stored.isoformat(),
                         "orion_day": orion_day, "reason": reason})
    return findings


def _connect_with_retry(url: str, connect: Callable[[str], Any], sleep: Callable[[float], None]) -> Any:
    # A short Job can start before its network policy is programmed: retry.
    last: Optional[Exception] = None
    for attempt in range(1, DB_CONNECT_ATTEMPTS + 1):
        try:
            return connect(url)
        except Exception as e:  # noqa: BLE001 - retried, then reported
            last = e
            logger.warning("DB connect attempt %d/%d failed: %s", attempt, DB_CONNECT_ATTEMPTS, e)
            if attempt < DB_CONNECT_ATTEMPTS:
                sleep(DB_BACKOFF_S * attempt)
    raise ConnectionError(f"database unreachable after {DB_CONNECT_ATTEMPTS} attempts: {last}")


def _default_connect(url: str) -> Any:
    import psycopg2

    conn = psycopg2.connect(url)
    conn.set_session(readonly=True)
    return conn


def run(
    today: Optional[date] = None,
    *,
    connect: Callable[[str], Any] = _default_connect,
    inspect: Callable[[str, str], Optional[Tuple[str, bool]]] = inspect_daily,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Exit code: 0 all parcels fresh, 1 any parcel behind or the check could not run."""
    today = today or datetime.now(timezone.utc).date()
    url = (os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        logger.error("Closed-day check cannot run: POSTGRES_URL / DATABASE_URL not set")
        return 1
    if not os.getenv("CONTEXT_URL", "").strip():
        # Without the platform context Orion answers with full IRIs and every
        # parcel would read as unreadable: refuse to run instead.
        logger.error("Closed-day check cannot run: CONTEXT_URL not set")
        return 1
    max_lag = int(os.getenv("MAX_LAG_DAYS", DEFAULT_MAX_LAG_DAYS))
    lookback = int(os.getenv("LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS))

    try:
        conn = _connect_with_retry(url, connect, sleep)
    except ConnectionError as e:
        logger.error("Closed-day check failed: %s", e)
        return 1
    try:
        rows = latest_stored_days(conn, lookback)
    except Exception as e:  # noqa: BLE001 - a failed query must fail the Job
        logger.error("Closed-day check query failed: %s", e)
        return 1
    finally:
        conn.close()

    findings = find_stale(rows, today, max_lag, inspect)
    for f in findings:
        logger.error(
            "closed days behind: reason=%s tenant=%s parcel=%s stored_day=%s orion_day=%s",
            f["reason"], f["tenant"], f["parcel"], f["stored_day"], f["orion_day"],
        )
    logger.info(
        "Closed-day check %s: parcels=%d behind=%d (max lag %d d)",
        today.isoformat(), len(rows), len(findings), max_lag,
    )
    return 1 if findings else 0


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return run()


if __name__ == "__main__":
    sys.exit(main())
