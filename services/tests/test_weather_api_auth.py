"""Tests for weather-api auth dependencies (gateway-header contract)."""

import sys
import os

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "weather-api"))

from app.auth import (  # noqa: E402
    _decode_jwt_tenant,
    get_request_token,
    require_auth,
    require_auth_optional,
)


class FakeRequest:
    def __init__(self, headers=None, cookies=None):
        self.headers = headers or {}
        self.cookies = cookies or {}


class TestRequireAuth:
    def test_x_tenant_id_header_wins(self, mock_jwt_token):
        token = mock_jwt_token(claims={"tenant_id": "jwt-tenant"})
        assert (
            require_auth(authorization=f"Bearer {token}", x_tenant_id="header-tenant")
            == "header-tenant"
        )

    def test_jwt_fallback_tenant_id_claim(self, mock_jwt_token):
        token = mock_jwt_token(claims={"tenant_id": "farm-a"})
        assert require_auth(authorization=f"Bearer {token}", x_tenant_id=None) == "farm-a"

    def test_jwt_fallback_legacy_tenant_claim(self, mock_jwt_token):
        token = mock_jwt_token(claims={"tenant": "farm-legacy"})
        assert (
            require_auth(authorization=f"Bearer {token}", x_tenant_id=None)
            == "farm-legacy"
        )

    def test_401_without_credentials(self):
        with pytest.raises(HTTPException) as exc:
            require_auth(authorization=None, x_tenant_id=None)
        assert exc.value.status_code == 401

    def test_401_with_blank_header_and_no_token(self):
        with pytest.raises(HTTPException) as exc:
            require_auth(authorization=None, x_tenant_id="   ")
        assert exc.value.status_code == 401

    def test_401_when_token_has_no_tenant(self, mock_jwt_token):
        # conftest default tokens carry 'tenant-id' (hyphen) — weather-api
        # only reads tenant_id/tenant, so this must 401.
        token = mock_jwt_token()
        with pytest.raises(HTTPException):
            require_auth(authorization=f"Bearer {token}", x_tenant_id=None)


class TestRequireAuthOptional:
    def test_returns_none_without_credentials(self):
        assert require_auth_optional(authorization=None, x_tenant_id=None) is None

    def test_returns_tenant_from_header(self):
        assert require_auth_optional(authorization=None, x_tenant_id="t1") == "t1"


class TestDecodeJwtTenant:
    def test_extracts_tenant_with_base64_padding(self, mock_jwt_token):
        token = mock_jwt_token(claims={"tenant_id": "pad-tenant"})
        assert _decode_jwt_tenant(token) == "pad-tenant"

    @pytest.mark.parametrize("bad", ["", "not-a-jwt", "a.!!!invalid-b64!!!.c", "x.y"])
    def test_malformed_tokens_return_none(self, bad):
        assert _decode_jwt_tenant(bad) is None


class TestGetRequestToken:
    def test_bearer_header(self):
        req = FakeRequest(headers={"Authorization": "Bearer abc123"})
        assert get_request_token(req) == "abc123"

    def test_cookie_fallback(self):
        req = FakeRequest(cookies={"nkz_token": "cookie-token"})
        assert get_request_token(req) == "cookie-token"

    def test_none_when_absent(self):
        assert get_request_token(FakeRequest()) is None
