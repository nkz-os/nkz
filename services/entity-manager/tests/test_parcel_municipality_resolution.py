"""Parcel→municipality resolves from the Orion entity, not the cadastral_parcels mirror.

Plan 2 / Task 1: the resolver must read the parcel's `municipality` straight from the
Orion AgriParcel entity and look up `catalog_municipalities.ine_code` by name. It must
never touch the `cadastral_parcels` read-model (retired).
"""
import os
import sys
from functools import wraps
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Module-level stubs — must come BEFORE importing blueprints.entities
# ---------------------------------------------------------------------------
os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")
os.environ.setdefault("ASSETS_BUCKET", "test-bucket")
os.environ.setdefault("MINIO_ENDPOINT", "localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "test")
os.environ.setdefault("MINIO_SECRET_KEY", "test")
os.environ.setdefault("MQTT_HOST", "localhost")
os.environ.setdefault("MQTT_PORT", "1883")
os.environ.setdefault("INTERNAL_SERVICE_SECRET", "test-secret")

_services_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _services_dir not in sys.path:
    sys.path.insert(0, _services_dir)

_common_mock = MagicMock()


def _require_auth(f=None, **kwargs):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kw):
            return func(*args, **kw)
        return wrapper
    if f is not None:
        return decorator(f)
    return decorator


_common_mock.require_auth = _require_auth
_common_mock.inject_fiware_headers = lambda h, t=None, **kw: h
sys.modules["common"] = _common_mock
sys.modules["common.auth_middleware"] = _common_mock
sys.modules["common.ngsi_headers"] = _common_mock
sys.modules["common.config_manager"] = MagicMock()
sys.modules["common.tier_quotas"] = MagicMock()
# db_helper provides get_db_connection_with_tenant — stubbed here, patched per test
sys.modules["db_helper"] = MagicMock()
sys.modules["orion_writer"] = MagicMock()
sys.modules["module_upload_service"] = MagicMock()
sys.modules["parcel_sync"] = MagicMock()
sys.modules["module_metrics"] = MagicMock()
sys.modules["geo_utils"] = MagicMock()


def test_municipality_from_orion_entity():
    import blueprints.entities as e
    # Orion AgriParcel entity carries municipality as a Property
    entity = {"id": "urn:ngsi-ld:AgriParcel:11111111-1111-1111-1111-111111111111",
              "type": "AgriParcel", "municipality": {"type": "Property", "value": "Allo"}}
    fake_cur = MagicMock()
    fake_cur.fetchone.return_value = {"ine_code": "31013"}
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cur
    with patch.object(e, "get_db_connection_with_tenant") as gconn:
        gconn.return_value.__enter__.return_value = fake_conn
        result = e._parcel_urn_to_municipality_code("montiko", entity["id"], entity)
    assert result == ("31013", "municipality")
    # the SQL must be the catalog lookup by municipality name — never cadastral_parcels
    sql = " ".join(c.args[0] for c in fake_cur.execute.call_args_list)
    assert "cadastral_parcels" not in sql
    assert "catalog_municipalities" in sql


def test_municipality_property_takes_precedence_over_address():
    """A `municipality` Property wins over `address.addressLocality`."""
    import blueprints.entities as e
    entity = {"id": "urn:ngsi-ld:AgriParcel:22222222-2222-2222-2222-222222222222",
              "type": "AgriParcel",
              "municipality": {"type": "Property", "value": "Allo"},
              "address": {"type": "Property", "value": {"addressLocality": "Other"}}}
    captured = {}

    fake_cur = MagicMock()
    fake_cur.fetchone.return_value = {"ine_code": "31013"}
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cur

    def _record(sql, params=None):
        captured["params"] = params
    fake_cur.execute.side_effect = _record

    with patch.object(e, "get_db_connection_with_tenant") as gconn:
        gconn.return_value.__enter__.return_value = fake_conn
        result = e._parcel_urn_to_municipality_code("montiko", entity["id"], entity)
    assert result == ("31013", "municipality")
    # looked up by the Property value "Allo", not the address "Other"
    assert captured["params"] == ("Allo",)
