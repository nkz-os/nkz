"""The subscription-creation failure metric increments on both failure paths
(non-2xx Orion response and exception). Sync tests, no async plugin needed."""

import os
import sys
from unittest.mock import MagicMock, patch

# ── Path setup (mirrors other telemetry-worker tests) ──────────────────────
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")

for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import telemetry_worker.subscription_manager as sm


def _resp(status, payload=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload if payload is not None else []
    r.raise_for_status.return_value = None
    r.text = ""
    return r


def test_non_2xx_post_increments_failure_metric():
    """A subscription that Orion rejects with a non-2xx status increments the
    counter labelled with the HTTP status reason."""
    with patch.object(sm, "SUBSCRIPTION_CREATION_FAILED") as metric, patch.object(
        sm.requests, "get", return_value=_resp(200, [])
    ), patch.object(sm.requests, "post", return_value=_resp(500)):
        sm._ensure_tenant_subscriptions("tenant1")

    assert metric.labels.called
    reasons = [c.kwargs.get("reason") for c in metric.labels.call_args_list]
    assert any(str(r).startswith("http_500") for r in reasons)
    # .labels(...).inc() actually fired
    assert metric.labels.return_value.inc.called


def test_exception_increments_failure_metric():
    """If talking to Orion raises, the counter increments with reason=exception
    and the error is swallowed (never crashes the worker)."""
    with patch.object(sm, "SUBSCRIPTION_CREATION_FAILED") as metric, patch.object(
        sm.requests, "get", side_effect=ConnectionError("orion down")
    ):
        sm._ensure_tenant_subscriptions("tenant1")  # must not raise

    reasons = [c.kwargs.get("reason") for c in metric.labels.call_args_list]
    assert "exception" in reasons
    assert metric.labels.return_value.inc.called


def test_successful_creation_does_not_increment():
    """A 201 create leaves the failure counter untouched."""
    with patch.object(sm, "SUBSCRIPTION_CREATION_FAILED") as metric, patch.object(
        sm.requests, "get", return_value=_resp(200, [])
    ), patch.object(sm.requests, "post", return_value=_resp(201)):
        sm._ensure_tenant_subscriptions("tenant1")

    assert not metric.labels.called
