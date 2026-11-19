"""Static checks of migrations 116 (observation geometry cache) and 117 (recompute runs)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIG_DIR = ROOT / "config" / "timescaledb" / "migrations"
CACHE_SQL = MIG_DIR / "116_observation_geometry_cache.sql"
RUNS_SQL = MIG_DIR / "117_observation_recompute_runs.sql"
RATCHET = ROOT / "config" / "timescaledb" / "rls_forced_tables.txt"


def _cache():
    return CACHE_SQL.read_text(encoding="utf-8")


def _runs():
    return RUNS_SQL.read_text(encoding="utf-8")


def _norm(text):
    return re.sub(r"\s+", " ", text)


def test_cache_pk_and_gist():
    text = _cache()
    assert re.search(r"PRIMARY KEY \(tenant_id, entity_urn\)", text)
    assert re.search(
        r"CREATE INDEX IF NOT EXISTS \S+\s+ON public\.observation_geometry_cache\s+USING GIST \(geom\)",
        text,
    )


def test_cache_columns():
    text = _norm(_cache())
    for col in (
        "tenant_id TEXT NOT NULL",
        "entity_urn TEXT NOT NULL",
        "entity_type TEXT NOT NULL",
        "geom GEOGRAPHY NOT NULL",
        "updated_at TIMESTAMPTZ NOT NULL",
    ):
        assert col in text, col
    # Any geometry type: no subtype modifier on the column.
    assert not re.search(r"geom GEOGRAPHY\(", text)


def test_runs_status_check():
    text = _norm(_runs())
    assert re.search(
        r"status TEXT NOT NULL DEFAULT 'pending' CHECK \(status IN \('pending', 'running', 'done', 'failed'\)\)",
        text,
    )


def test_runs_columns():
    text = _norm(_runs())
    for col in (
        "id UUID PRIMARY KEY DEFAULT gen_random_uuid()",
        "tenant_id TEXT NOT NULL CHECK (tenant_id <> '')",
        "device_urn TEXT NOT NULL",
        "deployment_id UUID NOT NULL",
        "range_from TIMESTAMPTZ NOT NULL",
        "range_to TIMESTAMPTZ,",
        "requested_by TEXT NOT NULL",
        "reason TEXT NOT NULL CHECK (reason <> '')",
        "rows_changed BIGINT",
        "error TEXT",
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        "started_at TIMESTAMPTZ",
        "finished_at TIMESTAMPTZ",
    ):
        assert col in text, col
    # range_to is NULL = open: it must not be NOT NULL.
    assert "range_to TIMESTAMPTZ NOT NULL" not in text


def test_runs_status_created_index():
    assert re.search(
        r"CREATE INDEX IF NOT EXISTS \S+\s+ON public\.observation_recompute_runs \(status, created_at\)",
        _runs(),
    )


def test_both_force_rls():
    for text, table in ((_cache(), "observation_geometry_cache"),
                        (_runs(), "observation_recompute_runs")):
        assert f"ALTER TABLE public.{table} OWNER TO nkz_core" in text
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY" in text
        assert f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY" in text
        assert re.search(
            rf"tablename = '{table}' AND policyname = '{table}_tenant_isolation'", text
        ), table
        assert f"CREATE POLICY {table}_tenant_isolation ON public.{table}" in text
        assert text.count("current_setting('app.current_tenant', true)") >= 2


def test_ratchet_lists_both():
    lines = RATCHET.read_text().split()
    assert "public.observation_geometry_cache" in lines
    assert "public.observation_recompute_runs" in lines
