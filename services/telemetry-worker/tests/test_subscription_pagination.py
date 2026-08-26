"""Subscription dedup must see every existing subscription, not just Orion's
first page. Orion-LD returns 20 subscriptions when no limit is given, so a
dedup that reads one unpaginated page stops recognising its own subscriptions
once a tenant holds more than a page of them, and recreates them on every
run. Sync tests, no async plugin needed."""

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

ORION_PAGE_SIZE = 20


def _resp(payload):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    r.text = ""
    return r


def _paged_orion(all_subs):
    """Fake Orion that honours limit/offset and caps an absent limit at 20."""

    def _get(url, headers=None, params=None, timeout=None):
        params = params or {}
        limit = int(params.get("limit", ORION_PAGE_SIZE))
        offset = int(params.get("offset", 0))
        return _resp(all_subs[offset : offset + limit])

    return _get


def test_dedup_sees_subscriptions_past_the_first_page():
    """A tenant already holding every managed subscription, buried behind a
    page of unrelated ones, must not have any of them recreated."""
    unrelated = [{"description": f"Other sub {i}"} for i in range(ORION_PAGE_SIZE)]
    managed = [{"description": s["description"]} for s in sm.SUBSCRIPTIONS]
    existing = unrelated + managed

    with patch.object(sm.requests, "get", side_effect=_paged_orion(existing)), patch.object(
        sm.requests, "post"
    ) as post:
        sm._ensure_tenant_subscriptions("tenant1")

    assert not post.called, (
        f"recreated {post.call_count} subscription(s) that already exist — "
        "dedup only saw Orion's first page"
    )


def test_subscription_listing_is_paginated():
    """Every subscription listing must ask for an explicit page size."""
    existing = [{"description": f"Other sub {i}"} for i in range(ORION_PAGE_SIZE)]

    with patch.object(sm.requests, "get", side_effect=_paged_orion(existing)) as get, patch.object(
        sm.requests, "post", return_value=_resp([])
    ):
        sm._ensure_tenant_subscriptions("tenant1")

    assert get.called
    for call in get.call_args_list:
        params = call.kwargs.get("params") or {}
        assert "limit" in params, f"listing without an explicit limit: {call}"
