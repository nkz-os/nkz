"""Un sensor aprovisionado se crea como `Device` y su parcela va en `controlledAsset`.

`Device` y `controlledAsset` son Smart Data Models oficiales; `AgriSensor` y `parcelId` eran
extensiones propias. `controlledAsset` es además la relación canónica dispositivo→activo, así
que sustituye a la cadena de tres deletreos que había que probar en orden.

⚠️ El campo `parcelId` de las respuestas JSON de las APIs de módulo es otra cosa y NO se toca:
aquí solo cambia el atributo de la entidad NGSI-LD que se escribe en el broker.
"""

import json

import pytest

from tests.telemetry_fakes import (AUTH, PARCEL, FakeState, client, seed_datalogger, wired)

DL = "11111111-1111-1111-1111-111111111111"

PROFILE_NO_TYPE = {"id": 1, "sdm_entity_type": None, "mapping": {"measurements": []}}
BASE = {"external_id": "S1", "name": "Sensor 1", "profile": "temperature",
        "location": {"lat": 42.0, "lon": -2.0}, "controlledAsset": [PARCEL],
        "datalogger_id": DL}


def _register(payload, profile_row=PROFILE_NO_TYPE):
    """Registra y devuelve la entidad NGSI-LD que se envió a Orion."""
    st = FakeState()
    seed_datalogger(st, dl_id=DL)
    st.profiles["temperature"] = profile_row
    with wired(st) as w:
        r = client().post("/api/sensors/register", content_type="application/json",
                          headers=AUTH, data=json.dumps(payload))
    assert r.status_code == 201, r.get_json()
    assert len(w.orion.entities) == 1, "no se envió ninguna entidad a Orion"
    return next(iter(w.orion.entities.values()))


def test_a_profile_without_a_type_falls_back_to_device():
    """El respaldo era `AgriSensor`, una extensión propia. Ahora es el tipo SDM oficial."""
    entity = _register(dict(BASE))
    assert entity["type"] == "Device"
    assert entity["id"].startswith("urn:ngsi-ld:Device:")


def test_the_parcel_travels_in_controlled_asset_as_a_relationship():
    entity = _register(dict(BASE))
    assert entity["controlledAsset"] == {"type": "Relationship", "object": [PARCEL]}


def test_a_single_parcel_given_as_a_string_is_wrapped_in_a_list():
    entity = _register(dict(BASE, controlledAsset=PARCEL))
    assert entity["controlledAsset"]["object"] == [PARCEL]


@pytest.mark.parametrize("payload", [dict(BASE), dict(BASE, parcel_id="Parcela-4")])
def test_the_platform_specific_parcel_id_attribute_is_gone(payload):
    """`parcelId` era una Property inventada; la relación canónica es `controlledAsset`."""
    assert "parcelId" not in _register(payload)


MACHINE_ROBOT = {"id": 2, "sdm_entity_type": "ManufacturingMachine",
                 "sdm_device_category": "robot", "mapping": {"measurements": []}}


def test_a_machine_profile_writes_its_category():
    """Sin `category` un robot es indistinguible de un tractor y la cuota de robots no lo ve."""
    entity = _register(dict(BASE), profile_row=MACHINE_ROBOT)
    assert entity["type"] == "ManufacturingMachine"
    assert entity["category"] == {"type": "Property", "value": "robot"}


def test_a_device_profile_has_no_category():
    assert "category" not in _register(dict(BASE))
