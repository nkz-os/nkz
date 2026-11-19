"""Per-module activation roles: metadata.activation_roles gates parcel activation."""

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.test_module_publish import app  # noqa: F401  (loads the shared stubs)
import blueprints.modules as m
import parcel_activation as pa

ROOT = Path(__file__).resolve().parents[3]
SQL = ROOT / "config/timescaledb/migrations/115_vegetation_activation_roles.sql"
DEFAULT = frozenset({"TenantAdmin", "PlatformAdmin"})
PARCEL = "urn:ngsi-ld:AgriParcel:acme:p1"
URL = f"/api/entities/parcels/{PARCEL}/modules/demo/{{}}"
HEADERS = {"Authorization": "Bearer x"}


# --- pure function ----------------------------------------------------------

def test_default_without_key():
    assert pa.activation_roles({}) == DEFAULT
    assert pa.activation_roles(None) == DEFAULT


def test_declared_roles_plus_platform_admin():
    roles = pa.activation_roles({"activation_roles": ["Farmer", "TechnicalConsultant"]})
    assert roles == frozenset({"Farmer", "TechnicalConsultant", "PlatformAdmin"})


@pytest.mark.parametrize("junk", [
    "Farmer", [], ["nope"], [1, None], {"a": 1}, 5, None, ["Farmer", "Hacker"],
])
def test_junk_falls_back_to_default(junk):
    assert pa.activation_roles({"activation_roles": junk}) == DEFAULT


# --- endpoint ---------------------------------------------------------------

@pytest.fixture
def client():
    with app.test_client() as c:
        yield c


def _post(client, action, roles, metadata):
    with patch.object(m, "_get_user_roles", return_value=roles), \
         patch.object(m, "get_module_metadata", return_value=metadata), \
         patch.object(m, "is_module_installed", return_value=True), \
         patch.object(m, "_parcel_in_tenant", return_value=(True, "P1")), \
         patch.object(m, "check_parcel_limit", return_value=(True, "")), \
         patch.object(m, "persist_activation"), \
         patch.object(m, "dispatch_to_module", return_value=(200, {})):
        return client.post(URL.format(action), headers=HEADERS, json={})


def test_no_key_farmer_403_admin_201(client):
    assert _post(client, "activate", ["Farmer"], {}).status_code == 403
    assert _post(client, "activate", ["TenantAdmin"], {}).status_code == 201


def test_declared_farmer_201(client):
    meta = {"activation_roles": ["Farmer", "TenantAdmin"]}
    assert _post(client, "activate", ["Farmer"], meta).status_code == 201


def test_junk_list_like_no_key(client):
    meta = {"activation_roles": ["bogus"]}
    assert _post(client, "activate", ["Farmer"], meta).status_code == 403
    assert _post(client, "activate", ["TenantAdmin"], meta).status_code == 201


def test_platform_admin_always_allowed(client):
    meta = {"activation_roles": ["Farmer"]}
    assert _post(client, "activate", ["PlatformAdmin"], meta).status_code == 201


def test_role_check_precedes_install_check(client):
    with patch.object(m, "_get_user_roles", return_value=["Farmer"]), \
         patch.object(m, "get_module_metadata", return_value={}), \
         patch.object(m, "is_module_installed") as installed:
        r = client.post(URL.format("activate"), headers=HEADERS, json={})
    assert r.status_code == 403
    installed.assert_not_called()


def test_deactivate_farmer_403_even_if_declared(client):
    meta = {"activation_roles": ["Farmer"]}
    assert _post(client, "deactivate", ["Farmer"], meta).status_code == 403
    assert _post(client, "deactivate", ["TenantAdmin"], meta).status_code == 200


def test_get_module_metadata_fail_closed_on_db_error():
    with patch.object(pa, "_get_db", side_effect=RuntimeError("down")):
        assert pa.get_module_metadata("demo") == {}


# --- migration --------------------------------------------------------------

def test_migration_idempotent_merge_for_vegetation():
    text = SQL.read_text(encoding="utf-8")
    assert re.search(r"COALESCE\(metadata, '\{\}'::jsonb\)\s*\|\|", text)
    assert "WHERE id = 'vegetation-prime'" in text
    for role in ("Farmer", "TechnicalConsultant", "TenantAdmin", "PlatformAdmin"):
        assert f'"{role}"' in text
    assert not re.search(r"\b(DROP|DELETE)\b", text, re.I)
