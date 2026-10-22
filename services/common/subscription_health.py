"""Re-arm NGSI-LD subscriptions that Orion-LD has paused.

Orion-LD deactivates a subscription after **three consecutive notification
failures**. The subscription is not deleted: it stays in the broker with
``isActive: false`` / ``status: "paused"`` and simply stops firing.

Every ``subscription_manager`` on the platform reconciles by *description* and
treats "a subscription with this description exists" as "nothing to do", so a
paused subscription is never noticed and never re-armed. That is what kept the
weather timeseries frozen after the receiving endpoints were fixed: the code was
correct again, but the broker had already stopped calling it.

Verified against the live broker: ``PATCH {"isActive": true}`` answers 204 and
moves the subscription from ``paused`` back to ``active``.
"""

import logging
from typing import Optional

import requests

DEFAULT_TIMEOUT = 30

# Orion reports a subscription's state in two places and they can disagree
# depending on how it was deactivated, so both are treated as "not firing".
PAUSED_STATUS = "paused"
EXPIRED_STATUS = "expired"


def is_firing(subscription: dict) -> bool:
    """True when Orion will actually deliver notifications for this subscription."""
    if subscription.get("isActive") is False:
        return False
    status = subscription.get("status")
    return status is None or status == "active"


def reactivate_if_paused(
    orion_url: str,
    headers: dict,
    subscription: dict,
    logger: Optional[logging.Logger] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> bool:
    """Re-arm `subscription` if Orion has paused it. True when it was re-armed.

    `headers` must already carry the tenant and the platform ``Link`` context, as
    produced by ``inject_fiware_headers(..., has_context_in_body=False)`` — the
    body here is an attribute fragment with no ``@context`` of its own.

    An **expired** subscription is reported but not touched: re-arming it needs a
    new ``expiresAt``, which is the owner's decision, not this reconciler's.
    """
    log = logger or logging.getLogger(__name__)

    if is_firing(subscription):
        return False

    sub_id = subscription.get("id")
    description = subscription.get("description") or sub_id
    if not sub_id:
        log.error("Cannot re-arm a subscription with no id: %s", description)
        return False

    if subscription.get("status") == EXPIRED_STATUS:
        log.error(
            "Subscription '%s' (%s) has EXPIRED and will not fire; it needs a new "
            "expiresAt, which this reconciler will not invent.",
            description,
            sub_id,
        )
        return False

    # This is an incident, not routine reconciliation: Orion only pauses a
    # subscription after three consecutive delivery failures.
    log.warning(
        "Subscription '%s' (%s) is PAUSED and has stopped firing — re-arming it. "
        "Orion pauses after 3 consecutive notification failures, so check the "
        "receiving endpoint: it must answer 204 with no body.",
        description,
        sub_id,
    )

    patch_headers = dict(headers)
    patch_headers["Content-Type"] = "application/json"
    try:
        response = requests.patch(
            f"{orion_url}/ngsi-ld/v1/subscriptions/{sub_id}",
            json={"isActive": True},
            headers=patch_headers,
            timeout=timeout,
        )
    except Exception as exc:
        log.error("Failed to re-arm subscription '%s': %s", description, exc)
        return False

    if response.status_code in (200, 204):
        log.info("Re-armed subscription '%s' (%s)", description, sub_id)
        return True

    log.error(
        "Failed to re-arm subscription '%s': %s %s",
        description,
        response.status_code,
        response.text[:200],
    )
    return False
