# tests/test_auth_tenant_regex.py
"""Unit tests for the canonical tenant_id format validator in auth.py.

Canonical pattern (mirrors services/common/tenant_utils.py):
  ^[a-z0-9]+(?:-[a-z0-9]+)*$  (hyphen-separated, no underscores,
  no leading/trailing/double hyphens), length 3-63.
"""

import pytest
from fastapi import HTTPException

from nkz_platform_sdk.auth import _validate_tenant_format


@pytest.mark.parametrize(
    "tenant_id",
    ["montiko", "acme-farm", "a1-b2-c3"],
)
def test_valid_tenant_ids_pass(tenant_id: str) -> None:
    _validate_tenant_format(tenant_id)  # must not raise


@pytest.mark.parametrize(
    "tenant_id",
    ["Acme", "under_score", "double--dash", "-lead", "trail-", "ab"],
)
def test_invalid_tenant_ids_raise(tenant_id: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _validate_tenant_format(tenant_id)
    assert exc_info.value.status_code == 401
    assert tenant_id in exc_info.value.detail
