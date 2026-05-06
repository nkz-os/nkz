"""Test harness for tenant-webhook unit tests.

Importing `enhanced-tenant-webhook.py` directly is expensive: it instantiates
a Flask app, a Redis-backed Limiter, a WebhookService that opens DB
connections, and pulls in keycloak_auth/audit_logger/grafana/kubernetes.
For unit tests of the small security helpers (`TENANT_ID_RE`,
`_is_valid_tenant_id`, `_verify_shared_secret`, `_internal_error`,
`_delete_keycloak_user_best_effort`) we don't want any of that real
behavior, but we do want to exercise the actual production code paths so
the tests catch regressions rather than re-implementing the contracts.

This conftest pre-mocks all heavy/optional dependencies via `sys.modules`
BEFORE the SUT is imported, then exposes the helpers as fixtures.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

_TEST_DIR = os.path.dirname(__file__)
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))


def _install_module_mocks() -> None:
    """Pre-mock optional/heavy dependencies before SUT import.

    Mirrors the pattern in services/entity-manager/tests/test_quota_enforcement.py
    so both services share the same shape of test harness.
    """
    if _SVC_DIR not in sys.path:
        sys.path.insert(0, _SVC_DIR)
    if _SERVICES_DIR not in sys.path:
        sys.path.insert(0, _SERVICES_DIR)

    # Do NOT mock the `common` package. Tests need the real
    # `common.tier_quotas`, and `common.config_manager` is loaded via
    # try/except in the SUT so a real-but-imperfect import is fine.
    # `services/` must be on sys.path for `common.*` to resolve as a
    # namespace package (no __init__.py exists in services/common/).

    # keycloak_auth lives at services/common/keycloak_auth.py — let the
    # real one import. audit_logger and grafana_manager have safe fallbacks
    # in the SUT's try/except blocks; leaving them un-mocked exercises the
    # real import path the production container takes.

    # pymongo is an external dep not always installed in CI/local sandbox.
    sys.modules.setdefault("pymongo", MagicMock())

    # flask_limiter is only available in the container; the @limiter.limit
    # decorators must not break module import AND must produce a passthrough
    # so Flask can still register the underlying view function (real Flask
    # needs `__name__` on the view to derive the endpoint).
    class _FakeLimiter:
        def limit(self, *_args, **_kwargs):
            def deco(f):
                return f

            return deco

        def exempt(self, f):
            return f

    flask_limiter_mock = MagicMock()
    flask_limiter_mock.Limiter = lambda *args, **kwargs: _FakeLimiter()
    sys.modules.setdefault("flask_limiter", flask_limiter_mock)
    flask_limiter_util = MagicMock()
    flask_limiter_util.get_remote_address = MagicMock(return_value="127.0.0.1")
    sys.modules.setdefault("flask_limiter.util", flask_limiter_util)

    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("POSTGRES_URL", "")
    os.environ.setdefault("KEYCLOAK_URL", "http://test")
    os.environ.setdefault("KEYCLOAK_REALM", "test")
    os.environ.setdefault("WEBHOOK_SECRET", "")
    os.environ.setdefault("WOOCOMMERCE_WEBHOOK_SECRET", "")
    os.environ.setdefault("INTERNAL_BILLING_SECRET", "")
    os.environ.setdefault("CORS_ORIGINS", "")


_install_module_mocks()


@pytest.fixture(scope="session")
def webhook_module():
    """Import the SUT exactly once per test session, after mocks are in place.

    The module file is `enhanced-tenant-webhook.py` (with a hyphen). Python
    cannot natively import that name, so we load it via `importlib.util`.
    """
    import importlib.util

    spec_path = os.path.join(_SVC_DIR, "enhanced-tenant-webhook.py")
    spec = importlib.util.spec_from_file_location("enhanced_tenant_webhook", spec_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not build import spec for {spec_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["enhanced_tenant_webhook"] = module
    spec.loader.exec_module(module)
    return module
