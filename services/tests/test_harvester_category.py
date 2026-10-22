"""La migración 100 debe asignar sdm_device_category='harvester' a los 5 perfiles de cosecha.

Estos perfiles reportan telemetría a nivel de operación (rendimiento, consumo, pérdidas)
y corresponden a cosechadoras automotrices, una categoría distinta de tractor/implemento/robot.
"""

import pathlib

import pytest

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "config" / "timescaledb" / "migrations" / "100_harvester_category.sql"
)

HARVESTER_PROFILES = [
    "operation_area_worked",
    "operation_fuel_consumption",
    "operation_grain_losses",
    "operation_work_quality",
    "operation_yield",
]


@pytest.fixture(scope="module")
def sql():
    assert MIGRATION.is_file(), f"falta la migración: {MIGRATION}"
    return MIGRATION.read_text(encoding="utf-8")


@pytest.mark.parametrize("code", HARVESTER_PROFILES)
def test_every_harvester_profile_is_migrated(sql, code):
    assert code in sql, f"la migración no menciona el perfil {code}"


def test_target_is_harvester_category(sql):
    assert "'harvester'" in sql, "la migración no asigna sdm_device_category='harvester'"


def test_migration_is_idempotent(sql):
    """Reaplicarla no debe romper ni revertir: solo toca filas aún en NULL."""
    lowered = sql.lower()
    assert "where" in lowered, "un UPDATE sin WHERE no es idempotente frente a datos ya migrados"
    assert "is null" in lowered, "debe chequear que sdm_device_category siga siendo NULL"
    for code in HARVESTER_PROFILES:
        assert code.lower() in lowered


def test_no_drop_or_truncate(sql):
    """Expand & Contract: esta migración no destruye nada."""
    lowered = sql.lower()
    for forbidden in ("drop table", "truncate", "delete from"):
        assert forbidden not in lowered, f"prohibido en esta fase: {forbidden}"
