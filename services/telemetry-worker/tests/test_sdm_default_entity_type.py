"""Cuando no hay perfil, la medida debe actualizar un `Device`, no un `AgriSensor`.

`sdm.py` construye el id de la entidad a actualizar a partir del `sdm_entity_type` del perfil.
Si el perfil no aparece cae a un respaldo hardcodeado, y ese respaldo tiene que apuntar al tipo
que la plataforma aprovisiona de verdad. Con el catálogo migrado a los tipos canónicos de SDM,
un respaldo a `AgriSensor` haría que la lectura actualizase `urn:ngsi-ld:AgriSensor:...` mientras
la entidad real es `urn:ngsi-ld:Device:...`: **la medida se perdería en silencio**, que es el modo
de fallo más caro de esta plataforma.
"""

import os
import re
import sys

_test_dir = os.path.dirname(os.path.abspath(__file__))
_svc_dir = os.path.normpath(os.path.join(_test_dir, ".."))
_services_dir = os.path.normpath(os.path.join(_svc_dir, ".."))
for _p in (_svc_dir, _services_dir, os.path.join(_services_dir, "common")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SDM_SOURCE = os.path.join(_svc_dir, "telemetry_worker", "sdm.py")


def _fallback_types() -> list:
    """TODOS los tipos de respaldo del fichero, leídos del fuente.

    Se lee el fichero en vez de importar el módulo porque `sdm.py` arrastra dependencias de
    runtime (Orion, PostgreSQL, ajustes) que no aportan nada a este contrato.

    Devuelve una lista, no un valor: hay **tres** respaldos —la ruta async, la sync y la de
    MQTT— y un test que solo mirase el primero dejaría pasar los otros dos.
    """
    with open(SDM_SOURCE, encoding="utf-8") as fh:
        source = fh.read()
    found = re.findall(r"'sdm_entity_type':\s*'([A-Za-z]+)'", source)
    assert found, "no se encontró ningún respaldo de sdm_entity_type en sdm.py"
    return found


def test_every_fallback_is_the_canonical_device_type():
    assert set(_fallback_types()) == {"Device"}


def test_all_three_fallback_sites_are_still_covered():
    """Si alguien añade o quita una ruta, este recuento lo delata."""
    assert len(_fallback_types()) == 3


def test_no_retired_platform_type_is_reintroduced():
    """Guarda contra reintroducir cualquiera de los cinco tipos propios retirados."""
    retired = {
        "AgriSensor",
        "AgriculturalTractor",
        "AgriculturalImplement",
        "AgriculturalRobot",
        "AgriOperation",
    }
    assert not (set(_fallback_types()) & retired)
