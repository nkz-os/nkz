"""Static checks of migration 116 (observation columns + trigger)."""
import re
from pathlib import Path

import pytest

from common.observation_context import MEDIA

MIG = (
    Path(__file__).resolve().parents[2]
    / "config" / "timescaledb" / "migrations"
    / "116_telemetry_measurements_observations.sql"
)

COLUMNS = [
    ("observed_property", "TEXT"),
    ("feature_of_interest", "TEXT"),
    ("medium", "TEXT"),
    ("depth_from_cm", "DOUBLE PRECISION"),
    ("depth_to_cm", "DOUBLE PRECISION"),
    ("height_m", "DOUBLE PRECISION"),
    ("organ", "TEXT"),
    ("context_ref", "TEXT"),
    ("deployment_id", "UUID"),
    ("source_kind", "TEXT"),
    ("location", "GEOGRAPHY(Point, 4326)"),
    ("time_source", "TEXT"),
    ("quality_flag", "TEXT"),
]


@pytest.fixture(scope="module")
def sql() -> str:
    return MIG.read_text(encoding="utf-8")


@pytest.mark.parametrize("col,typ", COLUMNS)
def test_columns_nullable_without_default(sql, col, typ):
    lines = [
        ln for ln in sql.splitlines()
        if re.search(rf"ADD COLUMN IF NOT EXISTS {col}\b", ln)
    ]
    assert len(lines) == 1, f"{col}: expected one ADD COLUMN IF NOT EXISTS line"
    line = lines[0]
    assert typ in line
    assert "DEFAULT" not in line.upper()
    assert "CHECK" not in line.upper()
    assert "NOT NULL" not in line.upper()


def test_indexes(sql):
    for name, cols in [
        ("idx_tm_tenant_property_time", "(tenant_id, observed_property, observed_at DESC)"),
        ("idx_tm_tenant_foi_time", "(tenant_id, feature_of_interest, observed_at DESC)"),
    ]:
        assert re.search(
            rf"CREATE INDEX IF NOT EXISTS {name}\s+ON telemetry_measurements\s*{re.escape(cols)}",
            sql,
        ), name
    assert re.search(
        r"CREATE INDEX IF NOT EXISTS idx_tm_location\s+ON telemetry_measurements\s+USING GIST \(location\)",
        sql,
    )


def test_trigger_replaced_same_name(sql):
    assert "CREATE OR REPLACE FUNCTION sync_telemetry_event_measurements()" in sql
    assert "CREATE TRIGGER" not in sql.upper().replace("CREATE OR REPLACE", "")


def test_no_segmentby_change(sql):
    assert "compress_segmentby" not in sql


def test_no_constraint_on_hypertable(sql):
    assert "ADD CONSTRAINT" not in sql.upper()


def test_media_vocabulary_equals_python(sql):
    m = re.search(r"v_media\s+CONSTANT TEXT\[\]\s*:=\s*ARRAY\[(.*?)\]", sql, re.S)
    assert m, "media vocabulary array not found"
    sql_media = set(re.findall(r"'([^']+)'", m.group(1)))
    assert sql_media == set(MEDIA)
