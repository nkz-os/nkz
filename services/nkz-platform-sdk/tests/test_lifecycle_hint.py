"""lifecycle_hint_router — a verified hint only schedules a reconcile."""

import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nkz_platform_sdk.lifecycle import _HintCoalescer, lifecycle_hint_router
from nkz_platform_sdk.notify_auth import notification_token

PATH = "/api/x/internal/lifecycle"


def _post(calls, headers, secret="s3", wait=True):
    async def reconcile():
        calls.append(1)

    app = FastAPI()
    app.include_router(lifecycle_hint_router(PATH, reconcile, secret=secret, followup_seconds=0))
    with TestClient(app) as client:
        r = client.post(PATH, headers=headers)
        deadline = time.monotonic() + 2
        while wait and r.status_code == 202 and not calls and time.monotonic() < deadline:
            time.sleep(0.01)
    return r


def test_valid_token_schedules_reconcile():
    calls = []
    token = notification_token("s3", "Tenant-A", "testserver")
    r = _post(calls, {"X-Notification-Token": token, "NGSILD-Tenant": "Tenant-A"})
    assert r.status_code == 202
    assert calls == [1]


def test_bad_token_is_401_and_does_nothing():
    calls = []
    r = _post(calls, {"X-Notification-Token": "0" * 64, "NGSILD-Tenant": "tenant-a"})
    assert r.status_code == 401
    assert calls == []


def test_master_accepted_until_disabled(monkeypatch):
    calls = []
    assert _post(calls, {"X-Internal-Service-Secret": "s3"}).status_code == 202
    monkeypatch.setenv("NKZ_NOTIFY_ACCEPT_MASTER", "false")
    assert _post(calls, {"X-Internal-Service-Secret": "s3"}).status_code == 401
    assert calls == [1]


def test_empty_secret_fails_closed(monkeypatch):
    monkeypatch.delenv("INTERNAL_SERVICE_SECRET", raising=False)
    calls = []
    token = notification_token("s3", "tenant-a", "testserver")
    r = _post(calls, {"X-Notification-Token": token, "NGSILD-Tenant": "tenant-a"}, secret=None)
    assert r.status_code == 401
    assert calls == []


def test_reconcile_failure_does_not_break_response():
    async def boom():
        raise RuntimeError("x")

    app = FastAPI()
    app.include_router(lifecycle_hint_router(PATH, boom, secret="s3", followup_seconds=0))
    with TestClient(app) as client:
        r = client.post(PATH, headers={"X-Internal-Service-Secret": "s3"})
    assert r.status_code == 202


@pytest.mark.asyncio
async def test_burst_of_hints_coalesces():
    """A burst while one reconcile runs costs one more pass, not one per hint."""
    calls = []

    async def reconcile():
        calls.append(1)
        await asyncio.sleep(0.02)

    hints = _HintCoalescer(reconcile, followup_seconds=0)
    for _ in range(5):
        hints.poke()
        await asyncio.sleep(0)
    await hints.wait_idle()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_followup_reconcile_runs_once_after_delay():
    """One delayed pass covers another replica's stale heal and Orion's read cache."""
    calls = []

    async def reconcile():
        calls.append(1)

    hints = _HintCoalescer(reconcile, followup_seconds=0.01)
    hints.poke()
    await hints.wait_idle()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_hint_during_followup_is_not_lost():
    calls = []

    async def reconcile():
        calls.append(1)

    hints = _HintCoalescer(reconcile, followup_seconds=0.05)
    hints.poke()
    await asyncio.sleep(0.01)  # first pass done, waiting for the follow-up
    hints.poke()
    await hints.wait_idle()
    assert len(calls) >= 3


def test_package_exports():
    import nkz_platform_sdk as sdk

    for name in ("installed_tenants", "TenantInstallations",
                 "InstalledTenantsUnavailable", "lifecycle_hint_router"):
        assert hasattr(sdk, name) and name in sdk.__all__
