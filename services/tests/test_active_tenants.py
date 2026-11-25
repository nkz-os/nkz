"""Background work skips suspended tenants, and there is one way to say so."""
import re
import sqlite3
from pathlib import Path

import pytest

from common.active_tenants import ACTIVE_TENANT_IDS_SQL, TENANT_IS_ACTIVE_SQL

SERVICES = Path(__file__).resolve().parents[1]


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE tenants(tenant_id TEXT, deleted_at TEXT);
        INSERT INTO tenants VALUES ('acme', NULL), ('tenant-a', NULL), ('gone', '2026-01-01'),
                                   (NULL, NULL);
        CREATE TABLE work(tenant_id TEXT);
        INSERT INTO work VALUES ('acme'), ('gone'), ('no-row');
    """)
    return conn


def test_active_tenants(db):
    assert {r[0] for r in db.execute(ACTIVE_TENANT_IDS_SQL)} == {"acme", "tenant-a"}


def test_nobody_is_lost_when_nobody_is_suspended(db):
    db.execute("UPDATE tenants SET deleted_at = NULL")
    assert {r[0] for r in db.execute(ACTIVE_TENANT_IDS_SQL)} == {"acme", "tenant-a", "gone"}


def test_filter_over_another_table(db):
    rows = db.execute(f"SELECT tenant_id FROM work WHERE {TENANT_IS_ACTIVE_SQL}")
    assert [r[0] for r in rows] == ["acme"]


@pytest.mark.parametrize("path, name", [
    ("telemetry-worker/telemetry_worker/subscription_manager.py", "ACTIVE_TENANT_IDS_SQL"),
    ("telemetry-worker/telemetry_worker/recompute.py", "ACTIVE_TENANT_IDS_SQL"),
    ("entity-manager/subscription_manager.py", "ACTIVE_TENANT_IDS_SQL"),
    ("entity-manager/blueprints/notifications.py", "ACTIVE_TENANT_IDS_SQL"),
    ("entity-manager/parcel_reconcile.py", "TENANT_IS_ACTIVE_SQL"),
    ("weather-worker/weather_worker/parcel_engine.py", "TENANT_IS_ACTIVE_SQL"),
    ("weather-worker/weather_worker/storage/timescaledb_writer.py", "ACTIVE_TENANT_IDS_SQL"),
    ("weather-worker/weather_worker/storage/orion_writer.py", "TENANT_IS_ACTIVE_SQL"),
    ("weather-worker/weather_worker/storage/timescaledb_writer.py", "TENANT_IS_ACTIVE_SQL"),
])
def test_loops_use_it(path, name):
    src = (SERVICES / path).read_text()
    assert re.search(rf"from common\.active_tenants import .*\b{name}\b", src)
    assert len(re.findall(rf"\b{name}\b", src)) >= 2, "imported but not used"


def test_the_status_column_alone_never_decides():
    """A suspension does not write tenants.status: deleted_at has to be asked too."""
    offenders = []
    for p in SERVICES.rglob("*.py"):
        if "tests" in p.parts:
            continue
        for m in re.finditer(r"FROM (?:public\.)?tenants WHERE status = 'active'(.*)", p.read_text()):
            if "deleted_at IS NULL" not in m.group(1):
                offenders.append(str(p.relative_to(SERVICES)))
    assert offenders == []


def test_no_loop_lists_every_tenant():
    offenders = [
        str(p.relative_to(SERVICES))
        for p in SERVICES.rglob("*.py")
        if "tests" not in p.parts
        and re.search(r"SELECT DISTINCT tenant_id FROM tenants WHERE tenant_id IS NOT NULL",
                      p.read_text())
    ]
    assert offenders == []
