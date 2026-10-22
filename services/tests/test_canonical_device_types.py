"""La migración 099 debe llevar los 6 tipos propios a los 2 canónicos de SDM.

`sensor_profiles.sdm_entity_type` es lo que leen los tres servicios que crean o actualizan la
entidad de un dispositivo, así que este mapeo ES el contrato de aprovisionamiento.
"""

import pathlib

import pytest

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "config" / "timescaledb" / "migrations" / "099_canonical_device_types.sql"
)

TO_DEVICE = ["AgriSensor", "WeatherObserved"]
TO_MACHINE = ["AgriculturalImplement", "AgriculturalRobot", "AgriculturalTractor", "AgriOperation"]


@pytest.fixture(scope="module")
def sql():
    assert MIGRATION.is_file(), f"falta la migración: {MIGRATION}"
    return MIGRATION.read_text(encoding="utf-8")


@pytest.mark.parametrize("legacy", TO_DEVICE + TO_MACHINE)
def test_every_legacy_type_is_migrated(sql, legacy):
    assert legacy in sql, f"la migración no menciona {legacy}"


def test_targets_are_the_two_canonical_types(sql):
    assert "'Device'" in sql
    assert "'ManufacturingMachine'" in sql


def test_migration_is_idempotent(sql):
    """Reaplicarla no debe romper ni revertir: solo toca filas que aún estén en el valor viejo."""
    lowered = sql.lower()
    assert "where" in lowered, "un UPDATE sin WHERE no es idempotente frente a datos ya migrados"
    for legacy in TO_DEVICE + TO_MACHINE:
        assert legacy.lower() in lowered


def test_no_drop_or_truncate(sql):
    """Expand & Contract: esta migración no destruye nada."""
    lowered = sql.lower()
    for forbidden in ("drop table", "truncate", "delete from"):
        assert forbidden not in lowered, f"prohibido en esta fase: {forbidden}"
