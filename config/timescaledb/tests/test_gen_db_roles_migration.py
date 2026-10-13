"""The generator turns the owner map into explicit, idempotent SQL."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from gen_db_roles_migration import generate  # noqa: E402

SAMPLE = {
    "roles": {
        "nkz_core": {"owns": ["public.tenants", "public.tenants_id_seq", "public.weather_observations"], "reads": [], "writes": []},
        "nkz_module_cue": {"owns": ["public.cue_estado_envio"], "reads": ["public.module_tenant_plan", "public.telemetry_events"], "writes": ["public.device_audit_log"]},
    },
    "unclassified": [],
    "postgis_untouched": ["public.spatial_ref_sys"],
    "other_databases": {"soil": "soil", "keycloak": "keycloak", "orion_*": "orion"},
    "hypertables": ["public.weather_observations"],
    "caggs": ["public.telemetry_1h"],
    "catalog": {"public.tenants": "r", "public.tenants_id_seq": "S", "public.weather_observations": "r", "public.cue_estado_envio": "r", "public.telemetry_1h": "m", "public.spatial_ref_sys": "r"},
}


def test_roles_are_created_idempotently():
    sql = generate(SAMPLE)
    assert "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nkz_core')" in sql
    assert "CREATE ROLE nkz_core LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT" in sql
    assert "CREATE ROLE nkz_provisioner LOGIN NOSUPERUSER NOBYPASSRLS CREATEDB CREATEROLE NOINHERIT" in sql
    assert "CREATE ROLE keycloak LOGIN" in sql


def test_ownership_uses_the_right_statement_per_kind():
    sql = generate(SAMPLE)
    assert "ALTER TABLE IF EXISTS public.tenants OWNER TO nkz_core;" in sql
    assert "ALTER SEQUENCE IF EXISTS public.tenants_id_seq OWNER TO nkz_core;" in sql
    assert "ALTER TABLE IF EXISTS public.cue_estado_envio OWNER TO nkz_module_cue;" in sql
    assert "spatial_ref_sys" not in sql.split("-- ownership")[1]


def test_transition_grants_and_views():
    sql = generate(SAMPLE)
    assert "CREATE OR REPLACE VIEW public.module_tenant_plan AS" in sql
    assert "GRANT SELECT ON public.module_tenant_plan TO nkz_module_cue;" in sql
    assert "GRANT SELECT ON public.telemetry_events TO nkz_module_cue;" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON public.device_audit_log TO nkz_module_cue;" in sql


def test_connect_is_revoked_from_public_and_granted_explicitly():
    sql = generate(SAMPLE)
    assert "REVOKE CONNECT ON DATABASE nekazari FROM PUBLIC;" in sql
    assert "GRANT CONNECT ON DATABASE nekazari TO nkz_core, nkz_module_cue;" in sql
    assert "REVOKE CONNECT ON DATABASE soil FROM PUBLIC" in sql and "GRANT CONNECT ON DATABASE soil TO soil" in sql
    assert "datname = 'soil'" in sql  # guarded: that database need not exist where the migration is tested
    assert "orion_*" not in sql  # wildcard databases are handled in-pod, not in the migration


def test_module_role_never_gets_platform_tables():
    sql = generate(SAMPLE)
    assert "ON public.tenants TO nkz_module_cue" not in sql
    assert "ON public.api_keys" not in sql
