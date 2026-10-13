"""lifecycle_hint_router — a verified hint only schedules a reconcile."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from nkz_platform_sdk.lifecycle import lifecycle_hint_router
from nkz_platform_sdk.notify_auth import notification_token

PATH = "/api/x/internal/lifecycle"


def _app(calls, secret="s3"):
    async def reconcile():
        calls.append(1)

    app = FastAPI()
    app.include_router(lifecycle_hint_router(PATH, reconcile, secret=secret))
    return TestClient(app)


def test_valid_token_schedules_reconcile():
    calls = []
    token = notification_token("s3", "Montiko", "testserver")
    r = _app(calls).post(PATH, headers={"X-Notification-Token": token, "NGSILD-Tenant": "Montiko"})
    assert r.status_code == 202
    assert calls == [1]


def test_bad_token_is_401_and_does_nothing():
    calls = []
    r = _app(calls).post(PATH, headers={"X-Notification-Token": "0" * 64, "NGSILD-Tenant": "montiko"})
    assert r.status_code == 401
    assert calls == []


def test_master_accepted_until_disabled(monkeypatch):
    calls = []
    assert _app(calls).post(PATH, headers={"X-Internal-Service-Secret": "s3"}).status_code == 202
    monkeypatch.setenv("NKZ_NOTIFY_ACCEPT_MASTER", "false")
    assert _app(calls).post(PATH, headers={"X-Internal-Service-Secret": "s3"}).status_code == 401
    assert calls == [1]


def test_empty_secret_fails_closed(monkeypatch):
    monkeypatch.delenv("INTERNAL_SERVICE_SECRET", raising=False)
    calls = []
    token = notification_token("s3", "montiko", "testserver")
    r = _app(calls, secret=None).post(
        PATH, headers={"X-Notification-Token": token, "NGSILD-Tenant": "montiko"}
    )
    assert r.status_code == 401
    assert calls == []


def test_reconcile_failure_does_not_break_response():
    async def boom():
        raise RuntimeError("x")

    app = FastAPI()
    app.include_router(lifecycle_hint_router(PATH, boom, secret="s3"))
    r = TestClient(app).post(PATH, headers={"X-Internal-Service-Secret": "s3"})
    assert r.status_code == 202


def test_package_exports():
    import nkz_platform_sdk as sdk

    for name in ("installed_tenants", "TenantInstallations",
                 "InstalledTenantsUnavailable", "lifecycle_hint_router"):
        assert hasattr(sdk, name) and name in sdk.__all__
