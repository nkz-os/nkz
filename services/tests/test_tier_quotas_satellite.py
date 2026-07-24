"""Regression tests for the satellite-computation quota in the tier model.

Part of the Vegetation-Health BYOK effort (task 3): adds
``max_satellite_computations`` to ``common.tier_quotas`` so a later task
can cap monthly Copernicus/Sentinel-Hub index computations per tenant and
surface the limit in the UI.
"""

from common.tier_quotas import quotas_for_tier, limits_columns_for_tier, limits_camel_for_tier


def test_basic_tier_has_no_satellite_computations():
    assert quotas_for_tier("basic")["max_satellite_computations"] == 0


def test_pro_tier_satellite_computations():
    assert quotas_for_tier("pro")["max_satellite_computations"] == 100


def test_premium_tier_satellite_computations():
    assert quotas_for_tier("premium")["max_satellite_computations"] == 500


def test_enterprise_tier_satellite_computations_unlimited():
    assert quotas_for_tier("enterprise")["max_satellite_computations"] is None


def test_limits_columns_for_tier_includes_satellite_key():
    assert limits_columns_for_tier("pro")["max_satellite_computations"] == 100


def test_limits_camel_for_tier_includes_satellite_key():
    assert limits_camel_for_tier("premium")["maxSatelliteComputations"] == 500
