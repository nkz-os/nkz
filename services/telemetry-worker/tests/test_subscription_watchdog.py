"""The watchdog re-arms paused core subscriptions and fails only when one stays not firing."""

import pytest
import requests

from telemetry_worker import subscription_watchdog as wd

EM = "urn:ngsi-ld:Subscription:entity-manager-measurements:Device"
TW = "urn:ngsi-ld:Subscription:telemetry-worker:devicemeasurement-readings"
MOD = "urn:ngsi-ld:Subscription:crop-health:something"


def sub(id_, status="active", is_active=True, **extra):
    return {"id": id_, "description": id_, "status": status, "isActive": is_active, **extra}


def test_only_core_prefixes_are_watched():
    assert wd.is_watched(sub(EM)) and wd.is_watched(sub(TW))
    assert not wd.is_watched(sub(MOD))


def test_paused_watched_subscription_is_rearmed():
    calls = []
    r = wd.check_tenant("acme", fetch=lambda t: [sub(EM, "paused", False)],
                        rearm=lambda s: calls.append(s["id"]) or True)
    assert calls == [EM] and r.rearmed == (EM,) and r.not_firing == () and r.error is None


def test_active_subscriptions_are_not_touched():
    calls = []
    r = wd.check_tenant("acme", fetch=lambda t: [sub(EM), sub(TW)],
                        rearm=lambda s: calls.append(s) or True)
    assert calls == [] and r == wd.TenantResult("acme", watched=2)


def test_module_subscription_is_ignored_even_if_paused():
    calls = []
    r = wd.check_tenant("acme", fetch=lambda t: [sub(MOD, "paused", False)],
                        rearm=lambda s: calls.append(s) or True)
    assert calls == [] and r == wd.TenantResult("acme")


def test_expired_or_failed_rearm_is_reported_not_firing():
    r = wd.check_tenant("acme", fetch=lambda t: [sub(EM, "expired"), sub(TW, "paused", False)],
                        rearm=lambda s: False)
    assert set(r.not_firing) == {EM, TW} and r.rearmed == ()


def _http_error(status, body=b""):
    resp = requests.Response()
    resp.status_code = status
    resp._content = body
    return requests.HTTPError(response=resp)


def test_unknown_tenant_listing_is_nothing_to_watch():
    """Orion answers 404 NonExistingTenant for a tenant it has never seen."""
    body = b'{"type": "https://uri.etsi.org/ngsi-ld/errors/NonExistingTenant", "title": "No such tenant"}'

    def fetch(t):
        raise _http_error(404, body)
    assert wd.check_tenant("acme", fetch=fetch, rearm=lambda s: True) == wd.TenantResult("acme")


def test_any_other_404_is_an_error():
    """A wrong path or a proxy answering 404 must not read as 'nothing to watch'."""
    def fetch(t):
        raise _http_error(404, b"<html>Not Found</html>")
    assert wd.check_tenant("acme", fetch=fetch, rearm=lambda s: True).error


def test_empty_listing_is_nothing_to_watch():
    assert wd.check_tenant("acme", fetch=lambda t: [], rearm=lambda s: True) == wd.TenantResult("acme")


def test_unreadable_tenant_is_an_error():
    def fetch(t):
        raise requests.ConnectionError("refused")
    r = wd.check_tenant("acme", fetch=fetch, rearm=lambda s: True)
    assert r.error and r.rearmed == () and r.not_firing == ()


def test_server_error_listing_is_an_error():
    def fetch(t):
        resp = requests.Response()
        resp.status_code = 500
        raise requests.HTTPError(response=resp)
    assert wd.check_tenant("acme", fetch=fetch, rearm=lambda s: True).error


def test_read_timeouts_are_not_retried():
    calls = []

    def fn():
        calls.append(1)
        raise requests.ReadTimeout("slow")
    with pytest.raises(requests.ReadTimeout):
        wd.retry_connection(fn, sleep=lambda s: None)
    assert calls == [1]


def test_connection_errors_are_retried_then_succeed():
    attempts = iter([requests.ConnectionError("cni"), requests.ConnectionError("cni"), ["ok"]])

    def fn():
        x = next(attempts)
        if isinstance(x, Exception):
            raise x
        return x
    assert wd.retry_connection(fn, sleep=lambda s: None) == ["ok"]


def test_connection_errors_give_up_after_the_last_attempt():
    calls = []

    def fn():
        calls.append(1)
        raise requests.ConnectionError("down")
    with pytest.raises(requests.ConnectionError):
        wd.retry_connection(fn, attempts=3, sleep=lambda s: None)
    assert len(calls) == 3


def test_http_errors_are_not_retried():
    calls = []

    def fn():
        calls.append(1)
        raise requests.HTTPError("500")
    with pytest.raises(requests.HTTPError):
        wd.retry_connection(fn, sleep=lambda s: None)
    assert calls == [1]


def test_exit_code():
    assert wd.exit_code([wd.TenantResult("a"), wd.TenantResult("b", watched=3)]) == 0
    assert wd.exit_code([wd.TenantResult("a", not_firing=(EM,))]) == 1
    assert wd.exit_code([wd.TenantResult("a", error="x")]) == 1


def test_a_rearm_fails_the_run_so_a_recurring_pause_alerts():
    """Re-armed is good news once; every minute it means readings are being lost."""
    assert wd.exit_code([wd.TenantResult("a", rearmed=(EM,))]) == 1


def test_tenants_are_retried_while_the_database_answers_nothing():
    answers = iter([[], [], ["acme", "tenant-a"]])
    assert wd.load_tenants(get=lambda: next(answers), sleep=lambda s: None) == ["acme", "tenant-a"]


def test_no_tenants_is_an_error_not_a_fallback():
    with pytest.raises(RuntimeError):
        wd.load_tenants(get=lambda: [], attempts=3, sleep=lambda s: None)


def test_the_run_stops_at_its_time_budget_and_reports_the_rest():
    clock = iter([0.0, 10.0, 20.0, 45.0])  # start, before a, before b, before c
    seen = []

    def check(t):
        seen.append(t)
        return wd.TenantResult(t, watched=1)
    results = wd.run_tenants(["a", "b", "c"], check, budget_s=40, clock=lambda: next(clock))
    assert seen == ["a", "b"]
    assert [r.tenant_id for r in results] == ["a", "b", "c"]
    assert results[2].error == "budget exhausted"


def test_main_fails_when_the_tenant_list_cannot_be_read(monkeypatch):
    from telemetry_worker import subscription_manager as sm
    monkeypatch.setattr(sm, "_get_active_tenants", lambda: [])
    monkeypatch.setattr(wd, "TENANT_RETRY_DELAY_S", 0)
    assert wd.main() == 1


def test_main_checks_every_tenant_from_the_database(monkeypatch):
    from telemetry_worker import subscription_manager as sm
    monkeypatch.setattr(sm, "_get_active_tenants", lambda: ["acme", "tenant-a"])
    seen = []

    def fetch_all(headers, timeout=None):
        seen.append(headers.get("NGSILD-Tenant"))
        return [sub(EM)]
    monkeypatch.setattr(sm, "_fetch_all_subscriptions", fetch_all)
    assert wd.main() == 0
    assert seen == ["acme", "tenant-a"]
