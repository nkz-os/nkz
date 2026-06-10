from decimal import Decimal

import pytest
from tier_quotas import (
    PLAN_LEVELS,
    TIER_QUOTAS,
    plan_level_for,
    quotas_for_tier,
    quotas_for_level,
)


def test_plan_levels_are_canonical():
    assert PLAN_LEVELS == {"basic": 0, "pro": 1, "premium": 2, "enterprise": 3}


def test_basic_tier_quotas():
    q = quotas_for_tier("basic")
    assert q["max_users"] == 1
    assert q["max_sensors"] == 0
    assert q["max_robots"] == 0
    assert q["max_parcels"] == 0
    assert q["max_area_hectares"] == Decimal("0.20")
    assert q["max_entities_total"] == 2


def test_pro_tier_quotas():
    q = quotas_for_tier("pro")
    assert q["max_users"] == 5
    assert q["max_sensors"] == 10
    assert q["max_robots"] == 2
    assert q["max_parcels"] == 5
    assert q["max_area_hectares"] == Decimal("50.00")
    assert q["max_entities_total"] is None


def test_premium_tier_quotas():
    q = quotas_for_tier("premium")
    assert q["max_users"] == 10
    assert q["max_sensors"] == 50
    assert q["max_robots"] == 5
    assert q["max_parcels"] == 20
    assert q["max_area_hectares"] == Decimal("500.00")
    assert q["max_entities_total"] is None


def test_enterprise_is_unlimited():
    q = quotas_for_tier("enterprise")
    for k in (
        "max_users",
        "max_sensors",
        "max_robots",
        "max_parcels",
        "max_area_hectares",
        "max_entities_total",
    ):
        assert q[k] is None, f"{k} should be None (unlimited)"


def test_plan_level_for_case_insensitive():
    assert plan_level_for("PRO") == 1
    assert plan_level_for("Pro") == 1
    assert plan_level_for("BASIC") == 0


def test_plan_level_for_unknown_raises():
    with pytest.raises(KeyError):
        plan_level_for("vip")


def test_quotas_for_tier_unknown_raises():
    with pytest.raises(KeyError):
        quotas_for_tier("vip")


def test_quotas_for_level():
    assert quotas_for_level(1)["max_users"] == 5
    assert quotas_for_level(3)["max_users"] is None
    with pytest.raises(KeyError):
        quotas_for_level(99)


def test_quotas_returns_copy_not_reference():
    q = quotas_for_tier("pro")
    q["max_users"] = 999
    # Original should be unaffected
    assert TIER_QUOTAS["pro"]["max_users"] == 5


def test_plan_level_for_empty_or_none():
    with pytest.raises(KeyError):
        plan_level_for("")
    with pytest.raises(KeyError):
        plan_level_for(None)
