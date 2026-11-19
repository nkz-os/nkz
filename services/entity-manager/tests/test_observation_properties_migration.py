"""Static checks of migration 113 (observable-property catalogue, seed v1)."""
import re
from pathlib import Path

from common.unit_codes import _UNIT_CODES

SQL = (Path(__file__).resolve().parents[3]
       / "config/timescaledb/migrations/113_observation_properties.sql")

V1_NAMES = [
    "airTemperature", "relativeHumidity", "dewPointTemperature", "precipitation",
    "solarRadiation", "reflectedSolarRadiation", "photosyntheticallyActiveRadiation",
    "soilMoisture", "soilTemperature", "stemDiameterVariation", "equipmentTemperature",
]


def _sql():
    return SQL.read_text(encoding="utf-8")


def _seed_rows():
    # Each seed row starts with ('<name>', '<domain>', '<unit>', '<default_medium>', ...
    return re.findall(r"^\s*\('(\w+)',\s*'(\w+)',\s*'([^']+)',\s*'(\w+)',\s*'\{([^}]*)\}',\s*'(\w+)'",
                      _sql(), re.M)


def test_seed_contains_v1_properties():
    text = _sql()
    assert re.search(r"INSERT INTO public\.observation_properties", text)
    assert re.search(r"ON CONFLICT \(\(COALESCE\(tenant_id,\s*'__global__'\)\),\s*name\)\s+DO NOTHING", text)
    rows = {r[0]: r for r in _seed_rows()}
    assert sorted(rows) == sorted(V1_NAMES)
    assert rows["precipitation"][5] == "cumulative"
    assert rows["equipmentTemperature"][3] == "equipment"
    for name, _domain, unit, *_ in rows.values():
        assert unit in _UNIT_CODES, f"{name}: unit {unit!r} not in _UNIT_CODES"


def test_scope_name_unique_index():
    assert re.search(
        r"CREATE UNIQUE INDEX IF NOT EXISTS observation_properties_scope_name_uq\s+"
        r"ON public\.observation_properties \(\(COALESCE\(tenant_id,\s*'__global__'\)\),\s*name\)", _sql())


def test_policies_follow_sensor_profiles():
    text = _sql()
    assert "observation_properties_select" in text
    assert "observation_properties_write" in text
    assert "FORCE ROW LEVEL SECURITY" in text
    assert "OWNER TO nkz_core" in text
    assert "tenant_id IS NULL" in text


def test_seed_runs_before_force():
    text = _sql()
    assert text.index("INSERT INTO public.observation_properties") < text.index("FORCE ROW LEVEL SECURITY")


def test_cumulative_range_comment():
    text = _sql()
    assert re.search(r"COMMENT ON COLUMN public\.observation_properties\.valid_min", text)
    assert re.search(r"COMMENT ON COLUMN public\.observation_properties\.valid_max", text)
    assert "increment per period" in text


def test_table_in_forced_list():
    forced = (SQL.parents[1] / "rls_forced_tables.txt").read_text()
    assert "public.observation_properties" in forced.split()
