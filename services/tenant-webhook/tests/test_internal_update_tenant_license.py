"""Regression tests for the helpers extracted from
`internal_update_tenant_license` during the C901 refactor (PR3c-3).

This endpoint is the single integration seam between the billing
module and the `admin_platform.tenants` licensing fields. The
refactor split a 22-complexity function into:

  _parse_license_expires_at      - ISO8601 / null / type guard
  _parse_license_plan_tier       - tier validation against SSOT
  _validate_license_payload      - full body validation
  _build_license_update_sql      - parameterized UPDATE builder
  _normalize_license_row         - dict-cursor / tuple-cursor bridge
  _serialize_license_row         - JSON-friendly response body
  _persist_license_update        - DB execute + audit + serialize

These tests pin the parsing & SQL contract exactly. The endpoint
itself stays thin enough that the existing integration paths
(billing module HTTP call) are exercised in the full E2E flow that
the user is verifying manually in the UI.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask


@pytest.fixture
def helpers(webhook_module):
    return webhook_module


@pytest.fixture
def app_ctx():
    """Real Flask app context so jsonify works inside helpers."""
    app = Flask(__name__)
    with app.app_context():
        yield app


class TestParseLicenseExpiresAt:
    def test_none_passes_through(self, helpers, app_ctx):
        parsed, err = helpers._parse_license_expires_at(None)
        assert parsed is None
        assert err is None

    def test_iso_with_z_suffix_normalized(self, helpers, app_ctx):
        parsed, err = helpers._parse_license_expires_at("2026-12-31T23:59:59Z")
        assert err is None
        assert isinstance(parsed, datetime)
        assert parsed.tzinfo is not None

    def test_iso_with_offset(self, helpers, app_ctx):
        parsed, err = helpers._parse_license_expires_at("2026-12-31T23:59:59+02:00")
        assert err is None
        assert isinstance(parsed, datetime)

    def test_non_string_returns_400(self, helpers, app_ctx):
        parsed, err = helpers._parse_license_expires_at(12345)
        assert parsed is None
        assert err is not None
        assert err[1] == 400

    def test_invalid_iso_returns_400(self, helpers, app_ctx):
        parsed, err = helpers._parse_license_expires_at("not-a-date")
        assert parsed is None
        assert err is not None
        assert err[1] == 400


class TestParseLicensePlanTier:
    def test_none_clears_plan(self, helpers, app_ctx):
        plan_type, plan_level, err = helpers._parse_license_plan_tier(None)
        assert plan_type is None
        assert plan_level is None
        assert err is None

    def test_empty_string_treated_as_clear(self, helpers, app_ctx):
        plan_type, plan_level, err = helpers._parse_license_plan_tier("")
        assert plan_type is None
        assert plan_level is None
        assert err is None

    def test_valid_pro_tier(self, helpers, app_ctx):
        plan_type, plan_level, err = helpers._parse_license_plan_tier("pro")
        assert err is None
        assert plan_type == "pro"
        assert plan_level == helpers._BILLING_PLAN_LEVELS["pro"]

    def test_invalid_tier_returns_400(self, helpers, app_ctx):
        plan_type, plan_level, err = helpers._parse_license_plan_tier("super-mega")
        assert plan_type is None
        assert plan_level is None
        assert err is not None
        assert err[1] == 400

    def test_non_string_returns_400(self, helpers, app_ctx):
        plan_type, plan_level, err = helpers._parse_license_plan_tier(99)
        assert plan_type is None
        assert plan_level is None
        assert err is not None
        assert err[1] == 400

    def test_uppercase_normalized_to_lower(self, helpers, app_ctx):
        plan_type, plan_level, err = helpers._parse_license_plan_tier("PRO")
        assert err is None
        assert plan_type == "pro"


class TestValidateLicensePayload:
    def test_active_status_resolves_to_active(self, helpers, app_ctx):
        validated, err = helpers._validate_license_payload(
            "tenant-1",
            {"subscription_status": "active", "expires_at": None, "plan_tier": None},
        )
        assert err is None
        assert validated["desired_status"] == "active"
        assert validated["raw_status"] == "active"

    def test_canceled_status_resolves_to_cancelled(self, helpers, app_ctx):
        validated, err = helpers._validate_license_payload(
            "tenant-1",
            {"subscription_status": "canceled", "expires_at": None, "plan_tier": None},
        )
        assert err is None
        assert validated["desired_status"] == "cancelled"

    def test_unknown_status_returns_400(self, helpers, app_ctx):
        validated, err = helpers._validate_license_payload(
            "tenant-1", {"subscription_status": "weird"}
        )
        assert validated is None
        assert err is not None and err[1] == 400

    def test_full_valid_payload_returns_all_fields(self, helpers, app_ctx):
        validated, err = helpers._validate_license_payload(
            "tenant-1",
            {
                "subscription_status": "trialing",
                "expires_at": "2026-12-31T00:00:00Z",
                "plan_tier": "pro",
            },
        )
        assert err is None
        assert validated["raw_status"] == "trialing"
        assert validated["desired_status"] == "active"
        assert validated["plan_type"] == "pro"
        assert validated["plan_level"] == helpers._BILLING_PLAN_LEVELS["pro"]
        assert isinstance(validated["expires_at"], datetime)

    def test_expires_at_error_propagates(self, helpers, app_ctx):
        validated, err = helpers._validate_license_payload(
            "tenant-1",
            {"subscription_status": "active", "expires_at": "not-a-date"},
        )
        assert validated is None
        assert err is not None and err[1] == 400


class TestBuildLicenseUpdateSql:
    def test_minimal_update_only_sets_three_columns(self, helpers, app_ctx):
        validated = {
            "expires_at": None,
            "desired_status": "active",
            "plan_type": None,
            "plan_level": None,
            "raw_status": "active",
        }
        sql, params = helpers._build_license_update_sql(validated, "tenant-1")
        # Only the SET clause must omit plan_type/plan_level when they are
        # None (the RETURNING clause always lists those columns).
        set_clause = sql.split("SET", 1)[1].split("WHERE", 1)[0]
        assert "expires_at = %s" in set_clause
        assert "status = %s" in set_clause
        assert "updated_at = NOW()" in set_clause
        assert "plan_type" not in set_clause
        assert "plan_level" not in set_clause
        assert params == [None, "active", "tenant-1"]

    def test_with_plan_tier_appends_columns(self, helpers, app_ctx):
        ts = datetime(2026, 12, 31, tzinfo=UTC)
        validated = {
            "expires_at": ts,
            "desired_status": "active",
            "plan_type": "pro",
            "plan_level": 1,
            "raw_status": "active",
        }
        sql, params = helpers._build_license_update_sql(validated, "tenant-1")
        assert "plan_type = %s" in sql
        assert "plan_level = %s" in sql
        assert params == [ts, "active", "pro", 1, "tenant-1"]

    def test_returning_clause_present(self, helpers, app_ctx):
        validated = {
            "expires_at": None,
            "desired_status": "active",
            "plan_type": None,
            "plan_level": None,
            "raw_status": "active",
        }
        sql, _ = helpers._build_license_update_sql(validated, "tenant-1")
        assert "RETURNING tenant_id, expires_at, status, plan_type, plan_level" in sql


class TestNormalizeLicenseRow:
    def test_dict_cursor_passes_through(self, helpers, app_ctx):
        d = {
            "tenant_id": "t1",
            "expires_at": None,
            "status": "active",
            "plan_type": "pro",
            "plan_level": 1,
        }
        assert helpers._normalize_license_row(d) is d

    def test_tuple_cursor_mapped_to_dict(self, helpers, app_ctx):
        ts = datetime(2026, 12, 31, tzinfo=UTC)
        out = helpers._normalize_license_row(("t1", ts, "active", "pro", 1))
        assert out == {
            "tenant_id": "t1",
            "expires_at": ts,
            "status": "active",
            "plan_type": "pro",
            "plan_level": 1,
        }

    def test_none_returns_empty(self, helpers, app_ctx):
        assert helpers._normalize_license_row(None) == {}


class TestSerializeLicenseRow:
    def test_iso_formats_datetime(self, helpers, app_ctx):
        ts = datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)
        out = helpers._serialize_license_row(
            {
                "tenant_id": "t1",
                "expires_at": ts,
                "status": "active",
                "plan_type": "pro",
                "plan_level": 1,
            }
        )
        assert out["expires_at"] == ts.isoformat()
        assert out["tenant_id"] == "t1"
        assert out["plan_level"] == 1

    def test_none_expires_passes_through(self, helpers, app_ctx):
        out = helpers._serialize_license_row(
            {
                "tenant_id": "t1",
                "expires_at": None,
                "status": "cancelled",
                "plan_type": None,
                "plan_level": None,
            }
        )
        assert out["expires_at"] is None
        assert out["status"] == "cancelled"

    def test_string_expires_passes_through(self, helpers, app_ctx):
        out = helpers._serialize_license_row({"tenant_id": "t1", "expires_at": "x"})
        assert out["expires_at"] == "x"

    def test_empty_dict_yields_all_none(self, helpers, app_ctx):
        out = helpers._serialize_license_row({})
        assert out == {
            "tenant_id": None,
            "expires_at": None,
            "status": None,
            "plan_type": None,
            "plan_level": None,
        }


class TestPersistLicenseUpdate:
    def test_returns_404_when_tenant_missing(self, helpers, app_ctx):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        conn = MagicMock()
        conn.cursor.return_value = cursor

        validated = {
            "raw_status": "active",
            "desired_status": "active",
            "expires_at": None,
            "plan_type": None,
            "plan_level": None,
        }
        # Mock the RLS context setter — its commit is unrelated to
        # the licensing UPDATE we want to assert against.
        with patch.object(helpers.webhook_service, "_apply_admin_context"):
            resp, status = helpers._persist_license_update(
                conn, "tenant-x", "UPDATE ...", [None, "active", "tenant-x"], validated
            )
        assert status == 404
        # 404 path must NOT commit the UPDATE.
        conn.commit.assert_not_called()
        cursor.close.assert_called()

    def test_happy_path_commits_and_returns_200(self, helpers):
        cursor = MagicMock()
        ts = datetime(2026, 12, 31, tzinfo=UTC)
        cursor.fetchone.side_effect = [
            ("tenant-x",),
            ("tenant-x", ts, "active", "pro", 1),
        ]
        conn = MagicMock()
        conn.cursor.return_value = cursor

        validated = {
            "raw_status": "active",
            "desired_status": "active",
            "expires_at": ts,
            "plan_type": "pro",
            "plan_level": 1,
        }
        # Need a real request context for audit_log (reads request
        # headers), and the RLS apply mocked out to keep commit-counts
        # focused on the UPDATE itself.
        with (
            helpers.app.test_request_context("/internal/billing/x"),
            patch.object(helpers.webhook_service, "_apply_admin_context"),
        ):
            resp, status = helpers._persist_license_update(
                conn,
                "tenant-x",
                "UPDATE tenants ...",
                [ts, "active", "pro", 1, "tenant-x"],
                validated,
            )
        assert status == 200
        conn.commit.assert_called_once()


class TestEndpointDecoratorBinding:
    """Catch the recurring footgun: making sure the @app.route stayed
    on the public endpoint and didn't migrate to a helper. Same kind
    of guard added in PR3c-2 for keycloak_webhook + update_user_roles.
    """

    def test_route_bound_to_endpoint(self, helpers):
        rules = [
            r
            for r in helpers.app.url_map.iter_rules()
            if r.rule == "/internal/billing/tenants/<tenant_id>/license"
        ]
        assert len(rules) == 1
        assert rules[0].endpoint == "internal_update_tenant_license"
