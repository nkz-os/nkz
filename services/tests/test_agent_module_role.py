"""La migración 103 crea el rol de BD de menor privilegio del módulo agent.

El módulo conectaba como `postgres` (rolsuper + rolbypassrls): el RLS de la
migración 102 era inerte porque un superusuario se lo salta incondicionalmente,
FORCE incluido. Este rol es lo que hace que aquella política proteja algo.

La contraseña NO va en la migración (repo público): se asigna fuera de banda y
vive solo en el SealedSecret del módulo. La verificación funcional con un rol
real sin superusuario vive en el repo del módulo (test_agent_turn_audit_schema),
que ya creaba su propio rol de prueba desde la Task 1.
"""

import pathlib
import re

import pytest

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "config" / "timescaledb" / "migrations" / "103_agent_module_role.sql"
)

ROLE = "agent_module"
# Tablas de identidad/dedup (101): el módulo gestiona su ciclo de vida completo.
FULL_CRUD_TABLES = ["agent_channel_links", "agent_link_tokens", "agent_processed_updates"]
# Tabla de auditoría (102): append + lectura, jamás UPDATE/DELETE.
APPEND_ONLY_TABLE = "agent_turn_audit"
SEQUENCES = ["agent_channel_links_id_seq", "agent_turn_audit_id_seq"]


def test_sequence_grant_covers_every_agent_sequence(sql):
    """Cada tabla agent con clave sustituta lleva BIGSERIAL: sin USAGE,SELECT en
    SU secuencia el INSERT falla por permisos (y NO por RLS) — distinguirlo por
    mensaje al testear, o el falso positivo esconde la garantía real. El set de
    secuencias se declara explícito: una nueva tabla agent con serial debe
    añadirse aquí a propósito, no colarse. (Encontrado en verificación real: la
    primera versión de esta migración solo cubría la de auditoría y el INSERT en
    agent_channel_links fallaba.)"""
    grants = _grant_statements(sql)
    seq = [g for g in grants if "ON SEQUENCE" in g.upper()]
    assert len(seq) == 1, "un solo GRANT debe portar todas las secuencias"
    g = seq[0].upper()
    assert g.startswith("GRANT USAGE, SELECT ON SEQUENCE")
    for s in SEQUENCES:
        assert re.search(rf"\b{s}\b", seq[0]), f"falta la secuencia {s}"


@pytest.fixture(scope="module")
def sql():
    assert MIGRATION.is_file(), f"falta la migración: {MIGRATION}"
    return MIGRATION.read_text(encoding="utf-8")


def _code_only(sql: str) -> str:
    """Strip SQL comment lines: this file's header explains WHY there is no
    password in it, which would otherwise trip the password check on its own
    wording."""
    return "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )


def _grant_statements(sql: str) -> list[str]:
    return [
        s.strip()
        for s in _code_only(sql).split(";")
        if s.strip().upper().startswith("GRANT")
    ]


def test_role_creation_is_idempotent(sql):
    """CREATE ROLE directo falla si el rol ya existe en el segundo apply; tiene
    que ir dentro de DO + IF NOT EXISTS sobre pg_roles."""
    code = _code_only(sql)
    assert "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent_module')" in code
    assert "CREATE ROLE agent_module LOGIN" in code


def test_no_password_in_public_repo(sql):
    code = _code_only(sql)
    assert not re.search(r"\bPASSWORD\b", code, re.IGNORECASE), (
        "la contraseña va fuera de banda (ALTER ROLE en el despliegue) y en el "
        "SealedSecret — jamás en un repo público"
    )


def test_connect_grant_uses_current_database(sql):
    """Endurecer CONNECT contra un nombre de BD hardcodeado rompería cualquier
    despliegue cuyo nombre difiera. current_database() en un DO lo mantiene
    portable."""
    assert "GRANT CONNECT ON DATABASE %I TO agent_module" in _code_only(sql)
    assert "current_database()" in _code_only(sql)


def test_schema_usage_granted(sql):
    grants = _grant_statements(sql)
    assert any(
        g.upper().startswith("GRANT USAGE ON SCHEMA PUBLIC TO AGENT_MODULE") for g in grants
    ), "sin USAGE en el schema, ningún otro grant sirve"


@pytest.mark.parametrize("table", FULL_CRUD_TABLES)
def test_identity_tables_get_exactly_crud(sql, table):
    grants = _grant_statements(sql)
    crud = [g for g in grants if "UPDATE" in g.upper()]
    assert crud, "falta el GRANT con UPDATE (CRUD de las tablas de identidad)"
    assert len(crud) == 1, "un solo GRANT debe portar el CRUD completo"
    g = crud[0].upper()
    for verb in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert verb in g, f"falta {verb} en el CRUD"
    for t in FULL_CRUD_TABLES:
        assert re.search(rf"\b{re.escape(t)}\b", crud[0]), f"falta la tabla {t} en el CRUD"
    # El CRUD no puede alcanzar la tabla de auditoría por descuido.
    assert not re.search(rf"\b{APPEND_ONLY_TABLE}\b", crud[0]), (
        f"{APPEND_ONLY_TABLE} no debe estar en el GRANT de CRUD: es append-only"
    )


def test_audit_table_is_append_only(sql):
    grants = _grant_statements(sql)
    audit = [g for g in grants if re.search(rf"\b{APPEND_ONLY_TABLE}\b", g)]
    assert len(audit) == 1, "exactamente un GRANT para la tabla de auditoría"
    g = audit[0].upper()
    assert "INSERT" in g and "SELECT" in g, "append + lectura para cuota"
    assert "UPDATE" not in g and "DELETE" not in g, (
        "una auditoría que se puede reescribir no es una auditoría"
    )
    for t in FULL_CRUD_TABLES:
        assert not re.search(rf"\b{t}\b", audit[0]), f"{t} no pertenece a este GRANT"


def test_no_drop_or_truncate(sql):
    """Expand & Contract: esta migración no destruye nada."""
    lowered = _code_only(sql).lower()
    for forbidden in ("drop table", "truncate", "delete from", "revoke"):
        assert forbidden not in lowered, f"prohibido en esta fase: {forbidden}"
