"""La migración 101 crea el enlace canal↔tenant del agente conversacional.

`agent_channel_links_active_uq` es el índice único parcial del que dependen las Tasks 4 y 5:
garantiza que una cuenta de canal (channel, channel_user_id) resuelva a lo sumo a un tenant
activo a la vez. Si el predicado, el CHECK de `status` o el NOT NULL de `status` se debilitan,
una fila puede quedar fuera del índice y dos tenants pueden compartir la misma cuenta de canal
sin que nada lo impida.
"""

import pathlib
import re

import pytest

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "config" / "timescaledb" / "migrations" / "101_agent_channel_links.sql"
)

TABLES = ["agent_channel_links", "agent_link_tokens", "agent_processed_updates"]


@pytest.fixture(scope="module")
def sql():
    assert MIGRATION.is_file(), f"falta la migración: {MIGRATION}"
    return MIGRATION.read_text(encoding="utf-8")


@pytest.mark.parametrize("table", TABLES)
def test_every_table_is_created(sql, table):
    assert f"CREATE TABLE IF NOT EXISTS {table}" in sql, f"falta la tabla {table}"


def test_active_link_unique_index_exists(sql):
    assert "CREATE UNIQUE INDEX IF NOT EXISTS agent_channel_links_active_uq" in sql, (
        "falta el índice único que garantiza un enlace activo por cuenta de canal"
    )


def _statement_for(lowered_sql: str, marker: str) -> str:
    """Slice out exactly the one SQL statement containing `marker`, bounded by the
    statement terminators on either side — never a fixed-width window, which can
    silently bleed into the NEXT statement (this schema has more than one index
    with a `where status = 'active'` clause) and make an assertion pass for the
    wrong reason."""
    idx_pos = lowered_sql.find(marker)
    assert idx_pos != -1, f"no aparece {marker!r} en la migración"
    start = lowered_sql.rfind(";", 0, idx_pos) + 1  # 0 if no prior ';'
    end = lowered_sql.find(";", idx_pos)
    assert end != -1, f"la sentencia de {marker!r} no termina en ';'"
    return lowered_sql[start : end + 1]


def test_active_link_unique_index_covers_channel_and_channel_user_id(sql):
    # The CREATE UNIQUE INDEX statement for this index must name both columns
    # of the account identity, in this order, before its WHERE clause.
    statement = _statement_for(sql.lower(), "agent_channel_links_active_uq")
    on_pos = statement.find("on agent_channel_links")
    where_pos = statement.find("where")
    assert on_pos != -1 and where_pos != -1 and on_pos < where_pos
    columns_clause = statement[on_pos:where_pos]
    # Word-boundary match: "channel" is a prefix of "channel_user_id", so a plain
    # substring check would pass even if the bare "channel" column were dropped.
    assert re.search(r"\bchannel\b", columns_clause), "falta la columna channel"
    assert re.search(r"\bchannel_user_id\b", columns_clause), "falta la columna channel_user_id"


def test_active_link_unique_index_is_partial_on_active_status(sql):
    """The index must be partial (WHERE status = 'active'); a plain unique index would
    forbid ever re-linking a revoked account to anyone, which is not the contract."""
    statement = _statement_for(sql.lower(), "agent_channel_links_active_uq")
    assert "where status = 'active'" in statement, (
        "el índice debe ser parcial (WHERE status = 'active'); "
        "sin el predicado, un enlace revocado seguiría bloqueando la cuenta de canal"
    )


def test_status_check_pinned_to_active_or_revoked(sql):
    assert "CHECK (status IN ('active', 'revoked'))" in sql, (
        "el CHECK de status debe limitarse exactamente a 'active'/'revoked'"
    )


def test_status_column_is_not_null(sql):
    """A NULL status would not satisfy `status = 'active'` in the partial index predicate,
    so a NULL-status row falls OUTSIDE the unique index and can silently duplicate across
    tenants — this is the exact leak the index exists to prevent."""
    lowered = sql.lower()
    status_pos = lowered.find("status")
    assert status_pos != -1
    # The column definition line: "status  TEXT NOT NULL DEFAULT 'active',"
    line_end = lowered.find("\n", status_pos)
    column_line = lowered[status_pos:line_end]
    assert "not null" in column_line, "status debe ser NOT NULL"


def test_migration_is_idempotent(sql):
    lowered = sql.lower()
    assert "if not exists" in lowered
    for table in TABLES:
        assert f"create table if not exists {table}" in lowered


def test_no_drop_or_truncate(sql):
    """Expand & Contract: esta migración no destruye nada."""
    lowered = sql.lower()
    for forbidden in ("drop table", "truncate", "delete from"):
        assert forbidden not in lowered, f"prohibido en esta fase: {forbidden}"


def test_no_timeseries_writes(sql):
    """Admin/metadata only — this schema must never look time-series shaped
    (no hypertable creation, no direct telemetry insert)."""
    lowered = sql.lower()
    assert "create_hypertable" not in lowered
