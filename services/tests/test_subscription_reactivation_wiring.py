"""Every subscription reconciler must re-arm its own paused subscriptions.

The helper in ``common.subscription_health`` is only useful if the reconcilers
actually call it. They previously matched an existing subscription by description
and did nothing else, so a subscription Orion had paused stayed paused forever —
which is why fixing the notification endpoints did not, on its own, restore the
weather timeseries.

Discovery is from the filesystem, like the other cross-cutting subscription
tests: a service that ships a reconciler without this wiring fails here.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from tests._subscription_managers import KNOWN, MANAGERS, load, service_id

TENANT = "acme"


def _paused(description: str) -> dict:
    return {
        "id": f"urn:ngsi-ld:subscription:{abs(hash(description)) % 10**8}",
        "description": description,
        "isActive": False,
        "status": "paused",
    }


def test_discovery_found_the_known_managers():
    """Guard: an empty glob would make every test below vacuously pass."""
    assert KNOWN <= {service_id(p) for p in MANAGERS}


@pytest.mark.parametrize("manager_path", MANAGERS, ids=service_id)
def test_reconciler_rearms_a_paused_subscription(manager_path, monkeypatch):
    module = load(manager_path)
    subscriptions = getattr(module, "SUBSCRIPTIONS", None)
    assert subscriptions, f"{service_id(manager_path)} declares no SUBSCRIPTIONS"

    # Every declared subscription already exists in the broker, and every one of
    # them is paused: nothing to create, everything to re-arm.
    existing = [_paused(sub["description"]) for sub in subscriptions]
    monkeypatch.setattr(module, "_fetch_all_subscriptions", lambda headers: existing)

    created = MagicMock()
    monkeypatch.setattr(module.requests, "post", created)

    with patch("common.subscription_health.requests.patch") as rearmed:
        rearmed.return_value = MagicMock(status_code=204)
        module._ensure_tenant_subscriptions(TENANT)

    assert rearmed.call_count == len(subscriptions), (
        f"{service_id(manager_path)} re-armed {rearmed.call_count} of "
        f"{len(subscriptions)} paused subscriptions. A reconciler that only checks "
        "whether a subscription exists leaves it paused forever."
    )
    for call in rearmed.call_args_list:
        assert call.kwargs["json"] == {"isActive": True}

    created.assert_not_called()


@pytest.mark.parametrize("manager_path", MANAGERS, ids=service_id)
def test_reconciler_leaves_active_subscriptions_alone(manager_path, monkeypatch):
    """No PATCH storm on the healthy path — this runs on a timer for every tenant."""
    module = load(manager_path)
    subscriptions = getattr(module, "SUBSCRIPTIONS", None)
    assert subscriptions

    active = [
        {
            "id": f"urn:ngsi-ld:subscription:{i}",
            "description": sub["description"],
            "isActive": True,
            "status": "active",
        }
        for i, sub in enumerate(subscriptions)
    ]
    monkeypatch.setattr(module, "_fetch_all_subscriptions", lambda headers: active)
    monkeypatch.setattr(module.requests, "post", MagicMock())

    with patch("common.subscription_health.requests.patch") as rearmed:
        module._ensure_tenant_subscriptions(TENANT)

    rearmed.assert_not_called()
