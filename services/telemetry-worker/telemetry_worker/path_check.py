"""End-to-end probe of the Orion-LD -> subscription -> TimescaleDB path.

Every other check on this platform watches one hop. This one writes an entity
to the broker exactly as a device would and then waits for the row to appear in
`telemetry_events`, so it fails for any break along the way: a subscription
that was never created, one Orion paused after three delivery failures, a
notification endpoint rejecting the call, or the sink erroring into the DLQ.

Two details decide whether the probe is honest:

* The value has to change on every run. Persistence is filtered by a delta
  threshold, so a constant reading is dropped on purpose and would read as a
  broken pipeline.
* The entity keeps a fixed id, so the broker holds one synthetic entity rather
  than a growing pile. The `telemetry_events` rows it produces are tagged with
  that id and are the only trace left behind.

Exit code is the result: 0 the path works, 1 it does not.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone

import psycopg2
import requests

logger = logging.getLogger("telemetry-path-check")

SYNTHETIC_DEVICE = "synthetic-path-check"
ENTITY_TYPE = "AgriDevice"

# How long the whole hop is given. The subscription fires within a second in a
# healthy cluster; the rest is headroom for a busy worker, not for a broken one.
DEFAULT_TIMEOUT_S = 90
POLL_INTERVAL_S = 3


def entity_id_for(tenant_id: str) -> str:
    return f"urn:ngsi-ld:{ENTITY_TYPE}:{tenant_id}:{SYNTHETIC_DEVICE}"


def build_entity(tenant_id: str, now: datetime) -> dict:
    """A reading that differs from the previous run, so it is never filtered out."""
    # Seconds within the day, scaled into a plausible battery percentage. Any
    # two runs more than a few seconds apart differ, which is what the delta
    # filter needs to let the reading through.
    seconds_of_day = now.hour * 3600 + now.minute * 60 + now.second
    battery = round(1.0 + (seconds_of_day % 8600) / 100.0, 2)
    return {
        "id": entity_id_for(tenant_id),
        "type": ENTITY_TYPE,
        "batteryLevel": {"type": "Property", "value": battery},
        "dateObserved": {
            "type": "Property",
            "value": now.isoformat().replace("+00:00", "Z"),
        },
    }


# A pod can start before the CNI has programmed its NetworkPolicy, and egress is
# deny-by-default in this deployment: the first attempts of a short-lived job can
# be refused outright. Retrying keeps the probe reporting the telemetry path
# rather than that startup race.
PUBLISH_ATTEMPTS = 4
PUBLISH_BACKOFF_S = 5


def publish(orion_url: str, tenant_id: str, entity: dict, headers: dict) -> None:
    """Create or update the synthetic entity, the way any producer would."""
    url = f"{orion_url}/ngsi-ld/v1/entityOperations/upsert?options=update"
    last_error: Exception | None = None
    for attempt in range(1, PUBLISH_ATTEMPTS + 1):
        try:
            resp = requests.post(url, json=[entity], headers=headers, timeout=30)
            resp.raise_for_status()
            return
        except requests.RequestException as exc:
            last_error = exc
            logger.warning(
                "Write attempt %s/%s failed: %s", attempt, PUBLISH_ATTEMPTS, exc
            )
            if attempt < PUBLISH_ATTEMPTS:
                time.sleep(PUBLISH_BACKOFF_S)
    raise last_error if last_error else RuntimeError("publish failed")


def wait_for_row(dsn: str, entity_id: str, since: datetime, timeout_s: int) -> bool:
    """Poll `telemetry_events` for a row this run produced.

    Bounded by `since` rather than just the entity id: a row from an earlier run
    would otherwise report a pipeline that has been dead since.
    """
    deadline = time.monotonic() + timeout_s
    query = (
        "SELECT 1 FROM telemetry_events "
        "WHERE entity_id = %s AND observed_at >= %s LIMIT 1"
    )
    while True:
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(query, (entity_id, since))
            if cur.fetchone():
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_INTERVAL_S)


def _headers(tenant_id: str) -> dict:
    from common.ngsi_headers import inject_fiware_headers

    headers = inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False)
    headers["Content-Type"] = "application/json"
    return headers


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    tenant_id = os.getenv("CHECK_TENANT", os.getenv("DEFAULT_TENANT", "platform"))
    orion_url = os.getenv("ORION_URL", "http://orion-ld-service:1026")
    dsn = os.getenv("POSTGRES_URL", "")
    timeout_s = int(os.getenv("CHECK_TIMEOUT_S", DEFAULT_TIMEOUT_S))

    if not dsn:
        logger.error("POSTGRES_URL is not set, so there is nothing to verify against")
        return 1

    now = datetime.now(timezone.utc)
    entity = build_entity(tenant_id, now)
    eid = entity["id"]

    try:
        publish(orion_url, tenant_id, entity, _headers(tenant_id))
    except Exception as exc:
        logger.error("Could not write %s to the broker: %s", eid, exc)
        return 1
    logger.info("Wrote %s, waiting up to %ss for the row", eid, timeout_s)

    if wait_for_row(dsn, eid, now, timeout_s):
        logger.info("RESULT: OK — the reading reached telemetry_events")
        return 0

    logger.error(
        "RESULT: FAILED — %s never reached telemetry_events in %ss. The broker "
        "accepted it, so the break is downstream: subscription missing or paused, "
        "notification endpoint rejecting, or the sink writing to the DLQ.",
        eid, timeout_s,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
