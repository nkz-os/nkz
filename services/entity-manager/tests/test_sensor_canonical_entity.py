"""Un sensor aprovisionado se crea como `Device` y su parcela va en `controlledAsset`.

`Device` y `controlledAsset` son Smart Data Models oficiales; `AgriSensor` y `parcelId` eran
extensiones propias. `controlledAsset` es además la relación canónica dispositivo→activo, así
que sustituye a la cadena de tres deletreos que había que probar en orden.

⚠️ El campo `parcelId` de las respuestas JSON de las APIs de módulo es otra cosa y NO se toca:
aquí solo cambia el atributo de la entidad NGSI-LD que se escribe en el broker.
"""

import json
import os
import sys
from functools import wraps
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")
os.environ.setdefault("MQTT_HOST", "localhost")
os.environ.setdefault("MQTT_PORT", "1883")
os.environ.setdefault("INTERNAL_SERVICE_SECRET", "test-secret")

_test_dir = os.path.dirname(os.path.abspath(__file__))
_svc_dir = os.path.normpath(os.path.join(_test_dir, ".."))
_services_dir = os.path.normpath(os.path.join(_svc_dir, ".."))
# `services/common/` también: sensors.py importa db_helper como módulo de primer nivel.
for _p in (_svc_dir, _services_dir, os.path.join(_services_dir, "common")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _require_auth(f=None, **kwargs):
    """Passthrough: la autenticación no es lo que se prueba aquí."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kw):
            from flask import g

            g.current_user = {"tenant_id": "montiko", "user_id": "u1"}
            g.tenant = "montiko"  # el handler lo lee de aquí
            return func(*args, **kw)

        return wrapper

    return decorator(f) if f else decorator


# La suite de entity-manager stubea `common` antes de importar los blueprints.
_common_mock = MagicMock()
_common_mock.require_auth = _require_auth
_common_mock.inject_fiware_headers = lambda h, **kw: dict(h)
_common_mock.internal_error = lambda *a, **kw: ({"error": "internal"}, 500)
for _mod in ("common", "common.auth_middleware", "common.api_errors", "common.ngsi_headers"):
    sys.modules.setdefault(_mod, _common_mock)

from blueprints.sensors import sensors_bp  # noqa: E402

PROFILE_NO_TYPE = {"id": 1, "sdm_entity_type": None, "mapping": {"measurements": []}}


def _db(profile_row):
    cur = MagicMock()
    cur.fetchone.return_value = profile_row
    cur.fetchall.return_value = []
    cur.rowcount = 1
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    conn.closed = False
    return conn


def _client():
    """App mínima con el contexto de request que el handler espera.

    El stub de `common` es estado global compartido: si otro fichero de la suite lo instaló
    primero, el `require_auth` que decora la vista NO es el nuestro. Por eso el tenant se
    inyecta con un `before_request` (que corre antes que el decorador) y se manda cabecera de
    autorización, para pasar cualquier stub que exija token.
    """
    from flask import Flask, g

    app = Flask(__name__)

    @app.before_request
    def _seed_identity():
        g.tenant = "montiko"
        g.current_user = {"tenant_id": "montiko", "user_id": "u1"}

    app.register_blueprint(sensors_bp)
    return app.test_client()


def _orion_response(status=201):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = []
    r.text = ""
    r.headers = {}
    return r


def _register(payload, profile_row=PROFILE_NO_TYPE):
    """Lanza el registro y devuelve la entidad NGSI-LD que se envió a Orion."""
    posted = []

    def _capture_post(url, **kwargs):
        if "/entities" in url:
            posted.append(kwargs.get("json"))
        return _orion_response(201)

    with patch("blueprints.sensors.get_db_connection_with_tenant", return_value=_db(profile_row)), \
         patch("blueprints.sensors.get_db_connection_simple", return_value=_db(profile_row)), \
         patch("blueprints.sensors.requests.get", return_value=_orion_response(200)), \
         patch("blueprints.sensors.requests.post", side_effect=_capture_post), \
         patch("blueprints.sensors.mqtt", MagicMock()):
        _client().post("/api/sensors/register", content_type="application/json",
                       headers={"Authorization": "Bearer test-token"},
                       data=json.dumps(payload))
    assert posted, "no se envió ninguna entidad a Orion"
    return posted[0]


BASE = {"external_id": "S1", "name": "Sensor 1", "profile": "temperature",
        "location": {"lat": 42.0, "lon": -2.0}}


def test_a_profile_without_a_type_falls_back_to_device():
    """El respaldo era `AgriSensor`, una extensión propia. Ahora es el tipo SDM oficial."""
    entity = _register(dict(BASE))
    assert entity["type"] == "Device"
    assert entity["id"].startswith("urn:ngsi-ld:Device:")


def test_the_parcel_travels_in_controlled_asset_as_a_relationship():
    entity = _register(dict(BASE, parcel_id="Parcela-4"))
    assert entity["controlledAsset"] == {
        "type": "Relationship",
        "object": "urn:ngsi-ld:AgriParcel:Parcela-4",
    }


def test_a_parcel_given_as_a_urn_is_not_prefixed_twice():
    entity = _register(dict(BASE, parcel_id="urn:ngsi-ld:AgriParcel:Parcela-4"))
    assert entity["controlledAsset"]["object"] == "urn:ngsi-ld:AgriParcel:Parcela-4"


def test_no_parcel_means_no_controlled_asset():
    entity = _register(dict(BASE))
    assert "controlledAsset" not in entity


@pytest.mark.parametrize("payload", [dict(BASE), dict(BASE, parcel_id="Parcela-4")])
def test_the_platform_specific_parcel_id_attribute_is_gone(payload):
    """`parcelId` era una Property inventada; la relación canónica es `controlledAsset`."""
    assert "parcelId" not in _register(payload)
