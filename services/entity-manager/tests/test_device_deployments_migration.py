"""Static checks of migration 114 (versioned device deployments)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SQL = ROOT / "config/timescaledb/migrations/114_device_deployments.sql"
RATCHET = ROOT / "config/timescaledb/rls_forced_tables.txt"


def _sql():
    return SQL.read_text(encoding="utf-8")


def test_btree_gist_extension():
    assert re.search(r"CREATE EXTENSION IF NOT EXISTS btree_gist", _sql())


def test_exclusion_constraint_on_valid_overlap():
    assert re.search(
        r"EXCLUDE USING gist\s*\(\s*tenant_id WITH =,\s*device_urn WITH =,\s*valid WITH &&\s*\)",
        _sql())


def test_valid_lower_bound_required():
    assert re.search(r"CHECK \(lower\(valid\) IS NOT NULL AND NOT lower_inf\(valid\)\)", _sql())


def test_columns():
    text = _sql()
    for col in ("observes_feature TEXT", "observation_context JSONB NOT NULL DEFAULT",
                "channel_overrides JSONB NOT NULL DEFAULT", "location GEOGRAPHY(Point, 4326)",
                "created_by TEXT NOT NULL"):
        assert re.sub(r"\s+", " ", col) in re.sub(r"\s+", " ", text), col
    assert re.search(r"mobility\s+TEXT NOT NULL DEFAULT 'fixed' CHECK \(mobility IN \('fixed', 'mobile'\)\)", text)


def test_changes_table_reason_not_empty():
    text = _sql()
    assert "public.device_deployment_changes" in text
    assert re.search(r"reason\s+TEXT NOT NULL CHECK \(reason <> ''\)", text)


def test_force_rls_owner_and_policies_on_both_tables():
    text = _sql()
    for table in ("device_deployments", "device_deployment_changes"):
        assert f"ALTER TABLE public.{table} OWNER TO nkz_core" in text
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY" in text
        assert f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY" in text
        assert re.search(rf"tablename = '{table}' AND policyname = '{table}_tenant_isolation'", text)
    assert text.count("current_setting('app.current_tenant', true)") >= 4


def test_tenant_device_index():
    assert re.search(r"ON public\.device_deployments \(tenant_id, device_urn\)", _sql())


def test_tables_in_ratchet_list():
    lines = RATCHET.read_text().split()
    assert "public.device_deployments" in lines
    assert "public.device_deployment_changes" in lines
