"""SSOT regression tests for tenant-webhook tier/quota usage (PR2a).

PR2a removed three hardcoded `plan_limits = {...}` dicts from
`enhanced-tenant-webhook.py` (woocommerce_webhook, create_tenant_directly,
register_tenant) and replaced them with calls to `common.tier_quotas`,
the canonical Single Source of Truth for plan-level/quota mappings.

Three failure modes these tests guard against:
  1. The SSOT itself drifts (basic tier suddenly grants robots).
  2. The webhook re-introduces a hardcoded dict (silent quota mismatch
     between webhook-created tenants and entity-manager enforcement).
  3. The PLAN_LEVELS contract changes (a 5th tier added without updating
     `_BILLING_PLAN_LEVELS` consumers).

Tests use AST parsing (not regex) for the source-level guard to avoid
false positives on string literals or comments.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SUT_PATH = Path(__file__).resolve().parent.parent / "enhanced-tenant-webhook.py"


@pytest.fixture(scope="module")
def tier_quotas():
    """The real SSOT — must come from `common.tier_quotas`, NOT a mock."""
    import common.tier_quotas as tq

    return tq


class TestSsotShape:
    """Lock the canonical 4-tier contract. Any change here forces a
    deliberate review of every downstream consumer."""

    def test_exactly_four_tiers(self, tier_quotas):
        assert set(tier_quotas.PLAN_LEVELS.keys()) == {
            "basic",
            "pro",
            "premium",
            "enterprise",
        }

    def test_plan_levels_are_zero_indexed_ordered(self, tier_quotas):
        assert tier_quotas.PLAN_LEVELS == {
            "basic": 0,
            "pro": 1,
            "premium": 2,
            "enterprise": 3,
        }

    def test_level_to_tier_is_inverse_of_plan_levels(self, tier_quotas):
        for tier, level in tier_quotas.PLAN_LEVELS.items():
            assert tier_quotas.LEVEL_TO_TIER[level] == tier

    def test_basic_tier_has_no_robots(self, tier_quotas):
        """Basic is invite-only and intentionally cannot register robotics
        devices. Grandfathered into the spec — see CLAUDE.md."""
        assert tier_quotas.TIER_QUOTAS["basic"]["max_robots"] == 0

    def test_enterprise_tier_has_unlimited_entities(self, tier_quotas):
        assert tier_quotas.TIER_QUOTAS["enterprise"]["max_entities_total"] is None


class TestSutImportsSsot:
    """Verify the SUT actually consumes the SSOT, not a private copy.

    These imports are the integration seam — if they're missing or
    renamed, the audit-period regression of hardcoded plan_limits dicts
    likely returned somewhere in the file."""

    def test_billing_plan_levels_alias_resolves_to_ssot(self, webhook_module):
        """`_BILLING_PLAN_LEVELS` is an alias on `PLAN_LEVELS` from the
        SSOT (line ~2471). It is referenced by
        `internal_update_tenant_license` to validate plan_type and
        compute plan_level."""
        import common.tier_quotas as tq

        assert webhook_module._BILLING_PLAN_LEVELS is tq.PLAN_LEVELS

    def test_quotas_for_tier_basic_matches_ssot(self):
        """woocommerce_webhook + create_tenant_directly + register_tenant
        all import quotas_for_tier from the SSOT. A regression would
        mean a private dict was reintroduced — caught by the AST guard
        below — but this test additionally confirms the SSOT itself
        returns sane values for the basic tier."""
        from common.tier_quotas import quotas_for_tier

        q = quotas_for_tier("basic")
        assert q["max_robots"] == 0
        assert q["max_users"] is not None


class TestNoHardcodedPlanLimits:
    """AST-level guard: the file must not re-introduce the hardcoded
    `plan_limits = {"basic": {...}, "pro": {...}}` dict pattern that
    PR2a removed.

    We accept two forms of legitimate plan-keyed dicts that PR2a
    intentionally kept:
      - `duration_days_by_tier` (license window length, NOT a quota)
      - small lookup tables in tests / migrations / docstrings (out of
        scope for this file)

    Any other Dict literal whose keys are exactly the 4 tier names is
    treated as a hardcoded quota table and FAILS this test, forcing the
    author to either alias the SSOT or add an exemption with reasoning.
    """

    ALLOWED_VAR_NAMES = {"duration_days_by_tier"}
    TIER_KEYS = {"basic", "pro", "premium", "enterprise"}

    def _iter_assignments(self, tree):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                yield node
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                yield node

    def _collect_target_names(self, node):
        names = []
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for t in targets:
            if isinstance(t, ast.Name):
                names.append(t.id)
        return names

    def _is_tier_keyed_dict(self, value):
        if not isinstance(value, ast.Dict):
            return False
        keys = []
        for k in value.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                keys.append(k.value)
            else:
                return False
        return set(keys) == self.TIER_KEYS

    def test_no_hardcoded_tier_quota_dicts(self):
        source = _SUT_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        violations = []
        for node in self._iter_assignments(tree):
            if not self._is_tier_keyed_dict(node.value):
                continue
            names = self._collect_target_names(node)
            for name in names:
                if name in self.ALLOWED_VAR_NAMES:
                    continue
                violations.append((name, node.lineno))
        assert not violations, (
            "Hardcoded tier-keyed dicts re-introduced. Use "
            "`common.tier_quotas.quotas_for_tier()` instead. "
            f"Offenders: {violations}"
        )
