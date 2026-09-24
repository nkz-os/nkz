"""La migración 102 crea la tabla de auditoría de turnos del agente conversacional.

Una fila por turno modelo<->usuario, escrita tanto si el turno tuvo éxito como si
falló, para que Task 7 tenga qué leer. No hay ruta de update en el código: el valor
de un log de auditoría es que es append-only, y la migración tampoco debe invitar a
un upsert (sin ON CONFLICT, sin UPDATE, sin unique key sobre contenido).
"""

import pathlib
import re

import pytest

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "config" / "timescaledb" / "migrations" / "102_agent_turn_audit.sql"
)

TABLE = "agent_turn_audit"
# The three columns that identify who a turn belongs to: which tenant, which
# platform user, and which channel account. Without NOT NULL on all three, a
# row could be written that nothing can attribute to anyone.
IDENTITY_COLUMNS = ["tenant_id", "user_id", "channel_user_id"]
INDEXES = ["agent_turn_audit_tenant_idx", "agent_turn_audit_trace_idx"]


@pytest.fixture(scope="module")
def sql():
    assert MIGRATION.is_file(), f"falta la migración: {MIGRATION}"
    return MIGRATION.read_text(encoding="utf-8")


def _column_line(lowered_sql: str, column: str) -> str:
    """Return the DDL line that DEFINES `column`, anchored so that e.g. `user_id`
    cannot match inside `channel_user_id` — the column name must start the
    (trimmed) line, not just appear somewhere in it."""
    match = re.search(rf"^[ \t]*{re.escape(column)}[ \t]+\w", lowered_sql, re.MULTILINE)
    assert match, f"falta la columna {column}"
    line_end = lowered_sql.find("\n", match.start())
    return lowered_sql[match.start() : line_end if line_end != -1 else None]


def _names_exact(sql: str, keyword: str, name: str) -> bool:
    """`f"{keyword} {name}" in sql` also matches a longer name that has `name`
    as a prefix (e.g. a table renamed to agent_turn_audit_v2). \\b anchors on
    the exact identifier."""
    return re.search(rf"{keyword} {re.escape(name)}\b", sql) is not None


def test_table_is_created(sql):
    assert _names_exact(sql, "CREATE TABLE IF NOT EXISTS", TABLE), f"falta la tabla {TABLE}"


@pytest.mark.parametrize("column", IDENTITY_COLUMNS)
def test_identity_column_is_not_null(sql, column):
    """Sin NOT NULL en estas columnas, una fila de auditoría podría quedar sin
    dueño: ni tenant, ni usuario, ni cuenta de canal a quien atribuirla."""
    line = _column_line(sql.lower(), column)
    assert "not null" in line, f"{column} debe ser NOT NULL"


def test_outcome_check_pinned_to_known_values(sql):
    assert (
        "CHECK (outcome IN ('ok', 'refused', 'budget_exhausted', 'unconfigured', 'error'))"
        in sql
    ), "el CHECK de outcome debe limitarse exactamente a los valores conocidos"


@pytest.mark.parametrize("index", INDEXES)
def test_index_exists(sql, index):
    assert _names_exact(sql, "CREATE INDEX IF NOT EXISTS", index), f"falta el índice {index}"


def test_migration_is_idempotent(sql):
    assert _names_exact(sql, "CREATE TABLE IF NOT EXISTS", TABLE)
    for index in INDEXES:
        assert _names_exact(sql, "CREATE INDEX IF NOT EXISTS", index)


def test_no_drop_or_truncate(sql):
    """Expand & Contract: esta migración no destruye nada."""
    lowered = sql.lower()
    for forbidden in ("drop table", "truncate", "delete from"):
        assert forbidden not in lowered, f"prohibido en esta fase: {forbidden}"


def test_no_timeseries_writes(sql):
    """Admin/metadata only — this schema must never look time-series shaped
    (no hypertable creation, no direct telemetry insert)."""
    assert "create_hypertable" not in sql.lower()


def test_schema_does_not_invite_an_upsert(sql):
    """The audit trail's append-only guarantee has no enforcement besides the
    absence of an update path. If the migration ever grows an ON CONFLICT
    target or an UPDATE statement, that guarantee is gone even though nothing
    else about the table changed. SQL comment lines are stripped first: the
    schema's own header comment talks *about* the absence of an update path,
    which would otherwise trip this check on its own wording."""
    code_only = "\n".join(
        line for line in sql.lower().splitlines() if not line.strip().startswith("--")
    )
    for forbidden in ("on conflict", "update "):
        assert forbidden not in code_only, f"el esquema no debe incluir: {forbidden!r}"


POLICY = "agent_turn_audit_tenant_isolation"


def test_row_security_is_enabled_and_forced(sql):
    """FORCE is the half that is easy to omit.

    Without it the table owner is exempt from the policy, and the owner is the
    role that runs migrations — so the protection would be absent for exactly
    the role a service is most likely to reuse.
    """
    assert _names_exact(sql, "ALTER TABLE", TABLE)
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql


def test_tenant_policy_is_defined_and_scoped_by_the_session_tenant(sql):
    assert _names_exact(sql, "CREATE POLICY", POLICY)
    # Both halves are needed: USING filters reads, WITH CHECK stops a write
    # that would land in another tenant. One without the other is a half-open
    # door, and the two are easy to confuse.
    assert "USING (tenant_id = current_setting('app.current_tenant', true))" in sql
    assert "WITH CHECK (tenant_id = current_setting('app.current_tenant', true))" in sql


def test_policy_creation_is_idempotent(sql):
    """CREATE POLICY has no IF NOT EXISTS, so re-running the migration would
    fail on the second apply without an explicit drop first."""
    assert _names_exact(sql, "DROP POLICY IF EXISTS", POLICY)
