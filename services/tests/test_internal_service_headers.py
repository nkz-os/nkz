"""Callers of /internal/* routes must send X-Internal-Service-Secret.

The gateway's guard fails closed: it 401s when the header is absent, and it
also 401s when the *server* has no INTERNAL_SERVICE_SECRET (`expected` is
then empty). Both halves were broken at once — the gateway's deployment did
not declare the variable, and none of the three callers sent the header —
so module route registries and tenant suspension state never left the
gateway's cache. These tests pin both the helper and the call sites.
"""
import ast
import logging
import os
import sys

import pytest

_services_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_services_dir, os.path.join(_services_dir, "common")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from common.internal_auth import HEADER_NAME, internal_service_headers


def test_headers_carry_the_secret_when_configured(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "correct-horse-battery-staple")
    assert internal_service_headers("test") == {
        HEADER_NAME: "correct-horse-battery-staple"
    }


def test_missing_secret_yields_no_header_and_logs_an_error(monkeypatch, caplog):
    monkeypatch.delenv("INTERNAL_SERVICE_SECRET", raising=False)
    with caplog.at_level(logging.ERROR):
        assert internal_service_headers("some call site") == {}
    assert "INTERNAL_SERVICE_SECRET is not set" in caplog.text
    assert "some call site" in caplog.text


def test_empty_secret_is_treated_as_unset(monkeypatch):
    """An empty value must not produce an empty header that looks configured."""
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "")
    assert internal_service_headers("test") == {}


# --- call sites -------------------------------------------------------------
# Parsed from source rather than exercised through mocks: a MagicMock accepts
# any keyword, so a mock-based test passes even when `headers=` was dropped.

CALL_SITES = [
    "services/entity-manager/blueprints/modules.py",
    "services/tenant-webhook/enhanced-tenant-webhook.py",
]


def _invalidate_calls(path):
    """Every requests.post(...) whose URL f-string mentions cache/invalidate."""
    tree = ast.parse(open(os.path.join(_services_dir, "..", path)).read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        url = node.args[0]
        if not isinstance(url, ast.JoinedStr):
            continue
        literal = "".join(
            v.value for v in url.values if isinstance(v, ast.Constant)
        )
        if "cache/invalidate" in literal:
            yield node


@pytest.mark.parametrize("path", CALL_SITES)
def test_every_cache_invalidate_call_sends_the_secret(path):
    calls = list(_invalidate_calls(path))
    assert calls, f"no cache/invalidate call found in {path}"
    for call in calls:
        kwargs = {k.arg: k.value for k in call.keywords}
        assert "headers" in kwargs, f"{path}: cache/invalidate call sends no headers"
        headers = kwargs["headers"]
        assert (
            isinstance(headers, ast.Call)
            and getattr(headers.func, "id", "") == "internal_service_headers"
        ), f"{path}: headers must come from internal_service_headers()"
