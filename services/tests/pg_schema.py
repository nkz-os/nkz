"""Minimal real-Postgres schema for pg_integration tests.

Executes the real migration files from config/timescaledb/migrations/ (no
hand-written copies of table DDL) plus the smallest set of prerequisites those
files assume from the full production history.

Prerequisite DDL added here (not in the listed migrations):
  * extensions: timescaledb, postgis, uuid-ossp, pgcrypto, btree_gist
  * roles: nkz_core (OWNER TO target), nkz_rls_test (RLS-subject test role)
  * function update_updated_at_column() (defined in 001, used by 010 triggers)
  * table tenants (tenant_id TEXT UNIQUE; 010 seed DO block reads it, later
    recompute job uses it)
  * table cadastral_parcels: created by running real migration 007 (FK target of 010)
"""
from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2] / "config" / "timescaledb" / "migrations"
)

# (number, file name). 007 is a prerequisite for 010. 087 has two files with the
# same number; only the telemetry one is relevant (the other is module metadata).
# 115-117 are discovered by glob when present.
_FIXED_MIGRATIONS: list[tuple[int, str]] = [
    (7, "007_cadastral_parcels.sql"),
    (10, "010_sensor_ingestion_schema.sql"),
    (61, "061_telemetry_events_compression.sql"),
    (66, "066_telemetry_measurements_long_format.sql"),
    (87, "087_telemetry_events_quality_flag.sql"),
    (89, "089_telemetry_events_raw_calibration.sql"),
    (113, "113_observation_properties.sql"),
    (114, "114_device_deployments.sql"),
]
_LATE_RANGE = range(115, 118)

RLS_ROLE = "nkz_rls_test"
OWNER_ROLE = "nkz_core"

_EXTENSIONS_SQL = """
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS btree_gist;
"""

# Roles are cluster-wide; creation is idempotent. NOSUPERUSER/NOBYPASSRLS always
# enforced, also if the role pre-exists. NOLOGIN: reached only via SET ROLE.
_ROLES_SQL = """
DO $$
DECLARE r TEXT;
BEGIN
  FOREACH r IN ARRAY ARRAY['nkz_core', 'nkz_rls_test'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE', r);
    ELSE
      EXECUTE format('ALTER ROLE %I NOSUPERUSER NOBYPASSRLS', r);
    END IF;
  END LOOP;
END $$;
"""

_PREREQ_SQL = """
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id   TEXT PRIMARY KEY,
    tenant_name TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_GRANTS_SQL = f"""
GRANT USAGE ON SCHEMA public TO {RLS_ROLE};
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {RLS_ROLE};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {RLS_ROLE};
"""


def _migration_plan(upto: int | None) -> list[Path]:
    plan: list[tuple[int, Path]] = []
    for num, name in _FIXED_MIGRATIONS:
        plan.append((num, MIGRATIONS_DIR / name))
    for num in _LATE_RANGE:
        for p in sorted(MIGRATIONS_DIR.glob(f"{num}_*.sql")):
            plan.append((num, p))
    plan.sort(key=lambda t: (t[0], t[1].name))
    return [p for num, p in plan if upto is None or num <= upto]


def apply_minimal_schema(conn, upto: int | None = None) -> list[str]:
    """Apply prerequisites + migrations up to (and including) number ``upto``.

    ``conn`` is a psycopg2 connection to a fresh database, opened as a
    superuser. Call once per database: real migration 007 is not re-runnable
    (CREATE TRIGGER without guard), so a fresh database is required. Returns the applied migration file names. A migration SQL error
    propagates (never swallowed).
    """
    for _, name in _FIXED_MIGRATIONS:
        if not (MIGRATIONS_DIR / name).is_file():
            raise FileNotFoundError(f"required migration missing: {name}")

    previous = conn.autocommit
    conn.autocommit = True
    applied: list[str] = []
    try:
        with conn.cursor() as cur:
            # timescaledb must be the first thing the session does.
            cur.execute(_EXTENSIONS_SQL)
            cur.execute(_ROLES_SQL)
            cur.execute(_PREREQ_SQL)
            for path in _migration_plan(upto):
                cur.execute(path.read_text(encoding="utf-8"))
                applied.append(path.name)
            cur.execute(_GRANTS_SQL)
    finally:
        conn.autocommit = previous
    return applied
