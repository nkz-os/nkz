"""Contract: a paused subscription must be re-armed, an expired one must not.

Orion-LD deactivates a subscription after three consecutive notification
failures. Every subscription_manager on the platform reconciles by *description*
and treated "a subscription with this description exists" as "nothing to do", so
a paused subscription was never re-armed — the fix to the receiving endpoint
landed, and the broker still never called it.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_SERVICES_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICES_DIR not in sys.path:
    sys.path.insert(0, _SERVICES_DIR)

from common.subscription_health import (  # noqa: E402
    is_firing,
    reactivate_if_paused,
)

ORION = "http://orion-ld:1026"
HEADERS = {"NGSILD-Tenant": "acme", "Link": "<ctx>; rel=..."}


def _sub(**overrides):
    sub = {
        "id": "urn:ngsi-ld:subscription:abc",
        "description": "Telemetry Worker - WeatherObserved",
        "isActive": True,
        "status": "active",
    }
    sub.update(overrides)
    return sub


@pytest.mark.parametrize(
    "sub,expected",
    [
        (_sub(), True),
        (_sub(status=None), True),  # Orion omits status on some reads
        (_sub(isActive=False, status="paused"), False),
        (_sub(isActive=False), False),  # deactivated but status not reported
        (_sub(status="paused"), False),  # paused but isActive still true
        (_sub(status="expired"), False),
    ],
    ids=["active", "no-status", "paused", "inactive-only", "paused-only", "expired"],
)
def test_is_firing_reads_both_signals(sub, expected):
    assert is_firing(sub) is expected


def test_active_subscription_is_left_alone():
    with patch("common.subscription_health.requests.patch") as patched:
        assert reactivate_if_paused(ORION, HEADERS, _sub()) is False
    patched.assert_not_called()


def test_paused_subscription_is_rearmed():
    with patch("common.subscription_health.requests.patch") as patched:
        patched.return_value = MagicMock(status_code=204)
        assert reactivate_if_paused(ORION, HEADERS, _sub(isActive=False, status="paused"))

    patched.assert_called_once()
    _, kwargs = patched.call_args
    assert kwargs["json"] == {"isActive": True}
    # The fragment carries no @context of its own, so it must go as plain JSON
    # with the tenant's Link header preserved.
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert kwargs["headers"]["NGSILD-Tenant"] == "acme"
    assert kwargs["headers"]["Link"] == HEADERS["Link"]


def test_caller_headers_are_not_mutated():
    before = dict(HEADERS)
    with patch("common.subscription_health.requests.patch") as patched:
        patched.return_value = MagicMock(status_code=204)
        reactivate_if_paused(ORION, HEADERS, _sub(status="paused"))
    assert HEADERS == before


def test_expired_subscription_is_reported_but_not_touched():
    """Re-arming an expired subscription needs a new expiresAt — not our call."""
    with patch("common.subscription_health.requests.patch") as patched:
        assert reactivate_if_paused(ORION, HEADERS, _sub(status="expired")) is False
    patched.assert_not_called()


def test_broker_rejection_is_reported_not_swallowed():
    with patch("common.subscription_health.requests.patch") as patched:
        patched.return_value = MagicMock(status_code=404, text="not found")
        assert reactivate_if_paused(ORION, HEADERS, _sub(status="paused")) is False


def test_transport_failure_does_not_escape():
    """The reconciler runs on a timer; one unreachable broker must not kill it."""
    with patch("common.subscription_health.requests.patch") as patched:
        patched.side_effect = OSError("connection refused")
        assert reactivate_if_paused(ORION, HEADERS, _sub(status="paused")) is False


def test_subscription_without_id_is_not_patched():
    sub = _sub(status="paused")
    del sub["id"]
    with patch("common.subscription_health.requests.patch") as patched:
        assert reactivate_if_paused(ORION, HEADERS, sub) is False
    patched.assert_not_called()
