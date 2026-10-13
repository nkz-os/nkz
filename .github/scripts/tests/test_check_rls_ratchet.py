"""The RLS ratchet: new tables declare an owner and a tenant policy; the forced list only grows."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check_rls_ratchet.py"
MIG = "config/timescaledb/migrations"
FORCED = "config/timescaledb/rls_forced_tables.txt"


def _git(repo, *args):
    subprocess.check_call(["git", "-C", str(repo), *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / MIG).mkdir(parents=True)
    (tmp_path / FORCED).write_text("# forced tables\npublic.weather_alerts\n")
    (tmp_path / MIG / "001_base.sql").write_text("CREATE TABLE public.base (id int);\nALTER TABLE public.base OWNER TO nkz_core;\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    return tmp_path


def _run(repo):
    _git(repo, "add", "-A")
    return subprocess.run([sys.executable, str(SCRIPT), "--base", "main"], cwd=repo, capture_output=True, text=True)


def test_compliant_migration_passes(repo):
    (repo / MIG / "002_ok.sql").write_text(textwrap.dedent("""
        CREATE TABLE IF NOT EXISTS public.things (id int, tenant_id text);
        ALTER TABLE public.things OWNER TO nkz_module_cue;
        ALTER TABLE public.things ENABLE ROW LEVEL SECURITY;
        CREATE POLICY things_tenant ON public.things USING (tenant_id = current_setting('app.current_tenant', true));
    """))
    r = _run(repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_table_without_owner_fails(repo):
    (repo / MIG / "002_no_owner.sql").write_text("CREATE TABLE public.things (id int);\n")
    r = _run(repo)
    assert r.returncode == 1
    assert "public.things" in r.stdout and "OWNER TO" in r.stdout


def test_tenant_table_without_policy_fails(repo):
    (repo / MIG / "002_no_policy.sql").write_text(textwrap.dedent("""
        CREATE TABLE public.things (id int, tenant_id text);
        ALTER TABLE public.things OWNER TO nkz_core;
    """))
    r = _run(repo)
    assert r.returncode == 1
    assert "ROW LEVEL SECURITY" in r.stdout


def test_shrinking_forced_list_fails(repo):
    (repo / FORCED).write_text("# forced tables\n")
    r = _run(repo)
    assert r.returncode == 1
    assert "public.weather_alerts" in r.stdout


def test_temp_tables_are_ignored(repo):
    (repo / MIG / "002_tmp.sql").write_text("CREATE TEMP TABLE scratch (id int);\n")
    assert _run(repo).returncode == 0
