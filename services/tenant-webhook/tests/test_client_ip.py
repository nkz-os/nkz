"""The rate limiter keys on request.remote_addr: it must be the client behind
Traefik, not the proxy pod every public request shares."""

from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.test import EnvironBuilder


def _remote_addr(webhook_module, headers):
    middleware = webhook_module.app.wsgi_app
    seen = {}
    inner = middleware.app
    middleware.app = lambda environ, start_response: seen.update(environ) or [b""]
    try:
        environ = EnvironBuilder(
            path="/webhook/register",
            method="POST",
            headers=headers,
            environ_base={"REMOTE_ADDR": "10.42.0.9"},
        ).get_environ()
        middleware(environ, lambda *_args: None)
    finally:
        middleware.app = inner
    return seen["REMOTE_ADDR"]


def test_app_trusts_one_proxy_hop(webhook_module):
    middleware = webhook_module.app.wsgi_app
    assert isinstance(middleware, ProxyFix)
    assert middleware.x_for == 1


def test_remote_addr_is_the_forwarded_client(webhook_module):
    assert _remote_addr(webhook_module, {"X-Forwarded-For": "203.0.113.7"}) == "203.0.113.7"


def test_spoofed_hops_before_the_proxy_are_ignored(webhook_module):
    headers = {"X-Forwarded-For": "198.51.100.1, 203.0.113.7"}
    assert _remote_addr(webhook_module, headers) == "203.0.113.7"


def test_in_cluster_caller_without_header_keeps_peer(webhook_module):
    assert _remote_addr(webhook_module, {}) == "10.42.0.9"
