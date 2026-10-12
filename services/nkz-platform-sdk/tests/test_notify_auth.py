"""Derived notification tokens: one formula, recomputed by the receiver, never stored.

The token binds a tenant and a receiver host to the master secret so a
subscription leaked from the broker is worth one receiver of one tenant,
not every ``/internal/*`` route of the platform.
"""

import hashlib
import hmac

import pytest

from nkz_platform_sdk import notify_auth as na

SECRET = "master-secret"
TENANT = "tenant-a"
HOST = "module-api-service"


def _expected(secret=SECRET, tenant=TENANT, host=HOST) -> str:
    msg = f"nkz-notify-v1|{tenant}|{host}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def _headers(token=None, master=None, tenant=TENANT, host=f"{HOST}:8000", **extra):
    headers = {"Host": host, "NGSILD-Tenant": tenant}
    if token is not None:
        headers[na.TOKEN_HEADER] = token
    if master is not None:
        headers[na.MASTER_HEADER] = master
    headers.update(extra)
    return headers


# --- formula -----------------------------------------------------------------


def test_token_is_the_documented_hmac_over_domain_tenant_host():
    assert na.notification_token(SECRET, TENANT, HOST) == _expected()
    assert len(na.notification_token(SECRET, TENANT, HOST)) == 64


def test_token_differs_per_tenant_and_per_host():
    base = na.notification_token(SECRET, TENANT, HOST)
    assert na.notification_token(SECRET, "tenant-b", HOST) != base
    assert na.notification_token(SECRET, TENANT, "other-service") != base
    assert na.notification_token("other-secret", TENANT, HOST) != base


def test_token_normalises_tenant_and_host():
    assert na.notification_token(SECRET, " Tenant-A ", "Module-API-Service") == _expected()


@pytest.mark.parametrize(
    "secret,tenant,host",
    [("", TENANT, HOST), (SECRET, "", HOST), (SECRET, TENANT, ""), (SECRET, "  ", HOST)],
)
def test_token_refuses_empty_inputs(secret, tenant, host):
    with pytest.raises(ValueError):
        na.notification_token(secret, tenant, host)


# --- receiver host -----------------------------------------------------------


@pytest.mark.parametrize(
    "uri,host",
    [
        ("http://module-api-service:8000/api/webhooks/orion", "module-api-service"),
        ("http://Module-API-Service/notify", "module-api-service"),
        ("https://svc.ns.svc.cluster.local:443/notify", "svc.ns.svc.cluster.local"),
        ("http://[::1]:8080/notify", "::1"),
    ],
)
def test_receiver_host_from_url(uri, host):
    assert na.receiver_host_from_url(uri) == host


@pytest.mark.parametrize("uri", ["", "/notify", "not a url", "http:///notify"])
def test_receiver_host_from_url_refuses_hostless(uri):
    with pytest.raises(ValueError):
        na.receiver_host_from_url(uri)


@pytest.mark.parametrize(
    "host_header,host",
    [
        ("module-api-service:8000", "module-api-service"),
        ("Module-API-Service", "module-api-service"),
        ("[::1]:8080", "::1"),
    ],
)
def test_receiver_host_from_headers_strips_port_and_lowercases(host_header, host):
    assert na.receiver_host_from_headers({"host": host_header}) == host


def test_receiver_host_from_headers_is_none_without_host():
    assert na.receiver_host_from_headers({}) is None
    assert na.receiver_host_from_headers({"Host": ""}) is None


def test_creator_and_receiver_agree_on_the_host():
    uri = "http://module-api-service:8000/notify"
    assert na.receiver_host_from_url(uri) == na.receiver_host_from_headers(
        {"Host": "module-api-service:8000"}
    )


# --- verification ------------------------------------------------------------


def test_verify_accepts_the_derived_token():
    assert na.verify_notification(_headers(token=_expected()), secret=SECRET) is True


def test_verify_accepts_derived_token_even_when_master_is_refused():
    assert (
        na.verify_notification(_headers(token=_expected()), secret=SECRET, accept_master=False)
        is True
    )


def test_verify_accepts_fiware_service_as_tenant():
    headers = {"Host": HOST, "Fiware-Service": TENANT, na.TOKEN_HEADER: _expected()}
    assert na.verify_notification(headers, secret=SECRET) is True


def test_verify_normalises_tenant_header():
    assert na.verify_notification(_headers(token=_expected(), tenant=" Tenant-A"), secret=SECRET)


@pytest.mark.parametrize(
    "headers",
    [
        _headers(token=_expected(tenant="tenant-b")),  # token of another tenant
        _headers(token=_expected(host="other-service")),  # token of another receiver
        _headers(token=_expected(secret="other")),  # token under another secret
        _headers(token="0" * 64),
        _headers(token=""),
        _headers(token=_expected(), tenant=""),  # tenant header missing: nothing to bind
        _headers(token=_expected(), host=""),  # host header missing
        _headers(),  # no auth header at all
    ],
)
def test_verify_refuses_wrong_or_unbound_tokens(headers):
    assert na.verify_notification(headers, secret=SECRET) is False


def test_verify_accepts_master_by_default_and_refuses_it_when_contracted():
    headers = _headers(master=SECRET)
    assert na.verify_notification(headers, secret=SECRET) is True
    assert na.verify_notification(headers, secret=SECRET, accept_master=False) is False


def test_verify_refuses_wrong_master():
    assert na.verify_notification(_headers(master="nope"), secret=SECRET) is False


def test_wrong_token_plus_right_master_follows_the_master_flag():
    headers = _headers(token="0" * 64, master=SECRET)
    assert na.verify_notification(headers, secret=SECRET) is True
    assert na.verify_notification(headers, secret=SECRET, accept_master=False) is False


def test_verify_fails_closed_without_a_secret():
    assert na.verify_notification(_headers(token=_expected()), secret="") is False
    assert na.verify_notification(_headers(master=""), secret="") is False


def test_verify_never_raises_on_non_ascii_header_values():
    assert na.verify_notification(_headers(token="ñ" * 64), secret=SECRET) is False
    assert na.verify_notification(_headers(master="ñ"), secret=SECRET) is False


def test_verify_reads_headers_case_insensitively():
    headers = {
        "host": f"{HOST}:8000",
        "ngsild-tenant": TENANT,
        na.TOKEN_HEADER.lower(): _expected(),
    }
    assert na.verify_notification(headers, secret=SECRET) is True


# --- env flags ---------------------------------------------------------------


def test_env_flags_defaults(monkeypatch):
    monkeypatch.delenv(na.DERIVED_TOKENS_ENV, raising=False)
    monkeypatch.delenv(na.ACCEPT_MASTER_ENV, raising=False)
    assert na.derived_tokens_from_env() is False
    assert na.accept_master_from_env() is True


@pytest.mark.parametrize("value,expected", [("true", True), ("1", True), ("False", False), ("off", False), ("", False)])
def test_derived_tokens_from_env(monkeypatch, value, expected):
    monkeypatch.setenv(na.DERIVED_TOKENS_ENV, value)
    assert na.derived_tokens_from_env() is expected


@pytest.mark.parametrize("value,expected", [("false", False), ("0", False), ("no", False), ("true", True), ("", True)])
def test_accept_master_from_env(monkeypatch, value, expected):
    monkeypatch.setenv(na.ACCEPT_MASTER_ENV, value)
    assert na.accept_master_from_env() is expected
