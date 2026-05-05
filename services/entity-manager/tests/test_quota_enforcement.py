"""Test quota enforcement logic."""
import sys
from unittest.mock import MagicMock, patch


# Mock modules that entity_management_api depends on before importing
_common_mock = MagicMock()

# require_auth can be used as @require_auth or @require_auth(require_hmac=False)
def _require_auth(f=None, **kwargs):
    if f is not None:
        return f
    return lambda g: g

_common_mock.require_auth = _require_auth
_common_mock.inject_fiware_headers = lambda h, t=None, **kw: h
sys.modules['common'] = _common_mock
sys.modules['common.auth_middleware'] = _common_mock

# Mock other optional dependencies
sys.modules['parcel_sync'] = MagicMock()
sys.modules['module_metrics'] = MagicMock()

import pytest
from entity_management_api import _check_entity_total_limit, _check_parcel_count_limit


def test_basic_tenant_blocked_at_total_entities():
    """Basic tier: max_entities_total=2. Having 2 entities → 3rd should be denied."""
    assert not _check_entity_total_limit(current_count=2, max_total=2)
    assert _check_entity_total_limit(current_count=0, max_total=2)


def test_enterprise_unlimited_entities():
    """Enterprise: max_entities_total=None → no limit."""
    assert _check_entity_total_limit(current_count=50, max_total=None)


def test_unknown_count_allowed():
    """If Orion count returns None (failure), creation should be allowed (fail open)."""
    assert _check_entity_total_limit(current_count=None, max_total=2)


def test_negative_max_total_disables_check():
    """A negative max_entities_total should not gate (treated as disabled)."""
    assert _check_entity_total_limit(current_count=100, max_total=-1)


def test_parcel_count_limit():
    """Pro tier: max_parcels=5. 5 parcels → 6th denied."""
    assert not _check_parcel_count_limit(current_count=5, max_parcels=5)
    assert _check_parcel_count_limit(current_count=4, max_parcels=5)


def test_unlimited_parcels():
    """max_parcels=None → no limit."""
    assert _check_parcel_count_limit(current_count=100, max_parcels=None)
