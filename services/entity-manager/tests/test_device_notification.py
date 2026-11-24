"""What a Device notification writes to the sensors table, with the old attributes, the official
ones or both; a Device-typed twin that nobody registered is ignored."""
import logging
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")

_services_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _services_dir not in sys.path:
    sys.path.insert(0, _services_dir)
for _mod in ("common", "common.auth_middleware", "common.api_errors", "common.ngsi_headers"):
    sys.modules.setdefault(_mod, MagicMock())

from tests.real_common import seed_real_common  # noqa: E402

seed_real_common()

import device_profile  # noqa: E402
import notification_handler as nh  # noqa: E402

TENANT = "acmefarm"
URN = f"urn:ngsi-ld:Device:{TENANT}:S1"
IRI = "https://smartdatamodels.org/dataModel.Device/"
PROFILE_IDS = {"model_code": "id-of-model-code", "legacy_code": "id-of-legacy-code"}
COLUMNS = ("tenant_id", "external_id", "profile_id", "name", "lon", "lat", "altitude_meters",
           "is_under_canopy", "parcel_id", "metadata", "status", "installed_at")


def _prop(value):
    return {"type": "Property", "value": value}


def _ref(code, tenant=TENANT):
    return {"type": "Relationship", "object": f"urn:ngsi-ld:DeviceModel:{tenant}:{code}"}


def _device(urn=URN, type_="Device", **attrs):
    return {"id": urn, "type": type_, "name": _prop("Sensor 1"), **attrs}


MODEL = {"refDeviceModel": _ref("model_code")}
LEGACY = {"profileCode": _prop("legacy_code"), "externalId": _prop("S1")}
BOTH = {**LEGACY, **MODEL}


@pytest.fixture(autouse=True)
def _nothing_logged_yet(monkeypatch):
    monkeypatch.setattr(device_profile, "_logged", type(device_profile._logged)())


class _Cursor:
    def __init__(self, rows):
        self.rows, self.found = rows, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        if sql.startswith("SELECT id FROM sensor_profiles"):
            code, tenant = params
            assert tenant == TENANT
            self.found = (PROFILE_IDS[code],) if code in PROFILE_IDS else None
        elif sql.startswith("INSERT INTO sensors"):
            self.rows.append(dict(zip(COLUMNS, params, strict=True)))
        else:
            assert "set_config" in sql, sql

    def fetchone(self):
        return self.found


def _notify(*entities, tenant=TENANT):
    """The rows written for these entities, through the notification endpoint."""
    from flask import Flask

    rows = []
    conn = MagicMock()
    conn.cursor = lambda: _Cursor(rows)
    app = Flask(__name__)
    app.register_blueprint(nh.notify_bp)
    with patch.object(nh, "_get_conn", lambda: conn), patch.object(nh, "_notify_unauthorized", lambda: False):
        r = app.test_client().post("/notify", json={"data": list(entities)},
                                   headers={"NGSILD-Tenant": tenant})
    assert r.status_code == 200 and r.get_json()["persisted"] == len(rows)
    assert not conn.rollback.called
    return rows


# ---- registered devices --------------------------------------------------------

@pytest.mark.parametrize("attrs,profile", [
    (MODEL, "id-of-model-code"),
    (LEGACY, "id-of-legacy-code"),
    (BOTH, "id-of-legacy-code"),
    ({IRI + "refDeviceModel": _ref("model_code")}, "id-of-model-code"),
    ({"refDeviceModel": _ref("model_code", "othertenant"), **LEGACY}, "id-of-legacy-code"),
    ({"refDeviceModel": _ref("unknown_code")}, None),
], ids=["model-only", "legacy-only", "both-profile-code-wins", "iri", "foreign-model-and-legacy", "unknown-profile"])
def test_a_registered_device_is_stored_with_its_profile(attrs, profile):
    row, = _notify(_device(**attrs))
    assert (row["tenant_id"], row["external_id"], row["name"]) == (TENANT, "S1", "Sensor 1")
    assert row["profile_id"] == profile


def test_the_external_id_of_a_device_is_the_last_part_of_its_id():
    row, = _notify(_device(**MODEL, externalId=_prop("something-else")))
    assert row["external_id"] == "S1"


OLD = {"installedAt": _prop("2026-01-01T00:00:00Z"), "status": _prop("old-state")}
NEW = {"dateInstalled": _prop("2026-03-01T08:00:00Z"), "deviceState": _prop("new-state")}
NEW_IRI = {IRI + k: v for k, v in NEW.items()}
OLD_ROW = ("2026-01-01T00:00:00Z", "old-state")
NEW_ROW = ("2026-03-01T08:00:00Z", "new-state")


@pytest.mark.parametrize("attrs,expected", [
    (NEW, NEW_ROW), (NEW_IRI, NEW_ROW), (OLD, OLD_ROW),
    ({**OLD, **NEW}, NEW_ROW), ({**OLD, **NEW_IRI}, NEW_ROW), ({}, (None, "active")),
    ({**OLD, "dateInstalled": _prop("")}, OLD_ROW), ({"dateInstalled": _prop("")}, (None, "active")),
], ids=["new", "new-iri", "old", "both", "both-iri", "none", "empty-date", "only-an-empty-date"])
@pytest.mark.parametrize("profile", [MODEL, LEGACY], ids=["model", "legacy"])
def test_the_official_attributes_are_read_first(profile, attrs, expected):
    row, = _notify(_device(**profile, **attrs))
    assert (row["installed_at"], row["status"]) == expected


@pytest.mark.parametrize("state", [_prop("active"), _prop(None), _prop("")])
@pytest.mark.parametrize("key", ["deviceState", IRI + "deviceState"])
def test_a_status_reading_is_never_the_state_of_a_device_with_device_state(key, state):
    """A profile may declare `status` as a channel: an inverter reports its code there."""
    row, = _notify(_device(**MODEL, **{key: state}, status=_prop(512)))
    assert row["status"] == "active"


def test_the_shape_a_subscription_with_an_older_context_delivers():
    """Recorded from Orion-LD: the context of the subscription does not know the newer terms."""
    row, = _notify({
        "id": URN, "type": "Device", "name": _prop("S1"),
        IRI + "refDeviceModel": _ref("model_code"),
        IRI + "dateInstalled": _prop("2026-03-01T08:00:00Z"),
        "deviceState": _prop("active"),
        "controlledAsset": {"type": "Relationship", "object": [
            "urn:ngsi-ld:AgriParcel:11111111-1111-1111-1111-111111111111"]},
        "location": {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [-2, 42]}}})
    assert row == {
        "tenant_id": TENANT, "external_id": "S1", "profile_id": "id-of-model-code", "name": "S1",
        "lon": -2, "lat": 42, "altitude_meters": None, "is_under_canopy": False,
        "parcel_id": "11111111-1111-1111-1111-111111111111", "metadata": "{}",
        "status": "active", "installed_at": "2026-03-01T08:00:00Z"}


# ---- everything else is ignored -------------------------------------------------

FOREIGN = {
    "no-profile": _device(externalId=_prop("S1")),
    "model-of-another-tenant": _device(refDeviceModel=_ref("model_code", "othertenant"), externalId=_prop("S1")),
    "malformed-model": _device(refDeviceModel={"type": "Relationship", "object": "urn:x"}),
    "other-id-shape": _device("urn:ngsi-ld:Device:twin-1", **BOTH),
    "uuid-id": _device("urn:ngsi-ld:Device:11111111-1111-1111-1111-111111111111", **BOTH),
    "extra-segment": _device(f"{URN}:x", **BOTH),
    "other-tenant": _device("urn:ngsi-ld:Device:othertenant:S1", **BOTH),
    "empty-external-id": _device(f"urn:ngsi-ld:Device:{TENANT}:", **BOTH),
    "bad-external-id": _device(f"urn:ngsi-ld:Device:{TENANT}:S 1", **BOTH),
    "long-external-id": _device(f"urn:ngsi-ld:Device:{TENANT}:{'x' * 65}", **BOTH),
    "machine-id": _device(f"urn:ngsi-ld:ManufacturingMachine:{TENANT}:S1", **BOTH),
    "no-id": {k: v for k, v in _device(**BOTH).items() if k != "id"},
    "id-not-text": _device(["urn"], **BOTH),
}


@pytest.mark.parametrize("entity", FOREIGN.values(), ids=FOREIGN.keys())
def test_foreign_twin_is_still_ignored(entity, caplog):
    registered = _device(f"urn:ngsi-ld:Device:{TENANT}:S2", **MODEL)
    with caplog.at_level(logging.DEBUG, logger="notification_handler"):
        rows = _notify(entity, registered)
    assert [r["external_id"] for r in rows] == ["S2"]  # the rest of the batch is stored
    ignored, = [r for r in caplog.records if r.getMessage().startswith("device notification ignored")]
    assert ignored.levelno == logging.DEBUG
    assert TENANT in ignored.getMessage() and str(entity.get("id")) in ignored.getMessage()


@pytest.mark.parametrize("name", [None, _prop(""), _prop(None)])
def test_a_device_without_a_name_is_ignored(name):
    entity = _device(**BOTH)
    entity.pop("name")
    if name is not None:
        entity["name"] = name
    assert _notify(entity) == []


def test_a_device_of_another_tenant_than_the_notification_is_ignored():
    assert _notify(_device(**BOTH), tenant="othertenant") == []


# ---- the legacy type ------------------------------------------------------------

def test_an_agrisensor_is_read_as_always():
    row, = _notify(_device("urn:ngsi-ld:AgriSensor:anything", "AgriSensor", **BOTH, **OLD, **NEW))
    assert row["external_id"] == "S1" and row["profile_id"] == "id-of-legacy-code"
    assert (row["installed_at"], row["status"]) == OLD_ROW


def test_an_agrisensor_needs_an_external_id_and_no_profile():
    assert _notify(_device(f"urn:ngsi-ld:AgriSensor:{TENANT}:S1", "AgriSensor", **MODEL)) == []
    row, = _notify(_device("urn:ngsi-ld:AgriSensor:x", "AgriSensor", externalId=_prop("S1")))
    assert row["profile_id"] is None and row["status"] == "active"


# ---- the fallback log -------------------------------------------------------------

def test_a_device_read_by_its_profile_code_is_logged_once(caplog):
    sensor = _device("urn:ngsi-ld:AgriSensor:x", "AgriSensor", **LEGACY)
    with caplog.at_level(logging.INFO):
        for _ in range(2):
            _notify(_device(**LEGACY), _device(f"urn:ngsi-ld:Device:{TENANT}:S2", **BOTH), sensor)
    assert [r.args for r in caplog.records
            if r.getMessage().startswith("device_profile_code_fallback")] == [(TENANT, URN, "legacy_code")]
