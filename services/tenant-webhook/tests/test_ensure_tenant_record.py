"""Regression tests for ensure_tenant_record writing tier limits.

Bug context: ensure_tenant_record accepted a `limits` dict but never
inserted it into the `tenants` table, so new tenants silently fell back
to the schema DEFAULT (max_users=1) regardless of their plan. This bit
enterprise tenants created after the one-shot migration 095 backfill
(e.g. intiasa, created 2026-09-20 with max_users=1).

These tests pin the contract: a new tenant's INSERT must include the
limit columns and populate them from `common.tier_quotas`
(limits_columns_for_tier), merged with any explicit caller overrides.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _insert_params(webhook_module, plan, limits, tenant_name="New Tenant"):
    """Run ensure_tenant_record against a fake connection and return the
    (sql, params) of the final INSERT statement."""
    svc = webhook_module.webhook_service
    fake_cursor = MagicMock()
    # fetchone order:
    #   1. existing-tenant search -> None (no existing tenant)
    #   2. uniqueness check -> None (slug is free)
    #   3. INSERT ... RETURNING -> resolved dict
    fake_cursor.fetchone.side_effect = [None, None, {"tenant_id": "new-tenant"}]
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor

    with patch.object(svc, "_apply_admin_context", return_value=None):
        result = svc.ensure_tenant_record(
            conn=fake_conn,
            email="owner@example.test",
            plan=plan,
            limits=limits,
            tenant_name=tenant_name,
            source="admin",
        )

    assert result == "new-tenant"
    # The INSERT is the third execute call (search, uniqueness, insert).
    insert_call = fake_cursor.execute.call_args_list[2]
    return insert_call[0][0], insert_call[0][1]


def test_enterprise_tenant_gets_unlimited_users(webhook_module):
    sql, params = _insert_params(
        webhook_module,
        plan="enterprise",
        limits={"max_users": None, "max_robots": None, "max_sensors": None},
    )

    assert "max_users" in sql
    assert "max_satellite_computations" in sql
    # Param order: tenant_id, tenant_name, plan_type, plan_level, status,
    # metadata, max_users, max_robots, max_sensors, max_area_hectares,
    # max_parcels, max_entities_total, max_satellite_computations, metadata.
    assert params[6] is None  # enterprise => unlimited users
    assert params[7] is None  # enterprise => unlimited robots


def test_pro_tenant_gets_tier_defaults_even_with_partial_limits(webhook_module):
    # create_tenant_directly passes only max_users/max_robots/max_sensors;
    # the remaining columns must still be backfilled from the SSOT.
    sql, params = _insert_params(
        webhook_module,
        plan="pro",
        limits={"max_users": 5, "max_robots": 2, "max_sensors": 10},
    )

    assert params[6] == 5
    assert params[7] == 2
    assert params[8] == 10
    # max_parcels is not in the caller's limits dict, so it must come from
    # the canonical tier quota (pro => 5).
    assert params[10] == 5


def test_explicit_override_wins_over_tier_default(webhook_module):
    sql, params = _insert_params(
        webhook_module,
        plan="pro",
        limits={"max_users": 42},
    )

    assert params[6] == 42
    # max_robots was not overridden => falls back to pro default (2).
    assert params[7] == 2
