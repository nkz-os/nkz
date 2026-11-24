"""The profile code of a measurement: the Device's `refDeviceModel`, else its `profileCode`.

The bridge and `build_measurements` read it with the same rule, so the code that selects the
profile is the one written as `deviceType`.
"""
import copy
import json
import logging
import socket
from unittest.mock import MagicMock, patch

import pytest

from tests.telemetry_fakes import TENANT  # noqa: F401,I001  (stubs first)

import blueprints.notifications as notif  # noqa: E402
from blueprints.measurements import SDM_SHAPE_FLAG, build_measurements  # noqa: E402

OBSERVED = "2026-08-29T10:00:00Z"
ENTITY_TIME = "2026-08-29T09:00:00Z"
DEVICE = "urn:ngsi-ld:Device:acmefarm:sensor-001"
MACHINE = "urn:ngsi-ld:ManufacturingMachine:acmefarm:tractor-1"
PROFILE_ROW = {"id": 1, "sdm_entity_type": "Device", "mapping": {"measurements": [
    {"type": "t", "incomingKey": "r1", "sdmAttribute": "temperature", "unit": "°C",
     "statistics": ["mean", "max"]},
    {"type": "soil", "sdmAttribute": "soilMoisture", "unit": "%",
     "observedProperty": "volumetricWaterContent"},
    {"type": "state", "sdmAttribute": "status", "valueType": "Text"},
]}}


def _prop(value, **extra):
    return {"type": "Property", "value": value, **extra}


def _ref(code, tenant="acmefarm"):
    return {"type": "Relationship", "object": f"urn:ngsi-ld:DeviceModel:{tenant}:{code}"}


def _entity(urn=DEVICE, **attrs):
    """Numbers, a window statistic, a text reading and a reading with the entity's time."""
    return {"id": urn, "type": urn.split(":")[2], "name": _prop("Probe"),
            "observedAt": _prop(ENTITY_TIME), **attrs,
            "airTemperature": _prop(21.5, observedAt=OBSERVED),
            "airTemperatureMax": _prop(28.4, observedAt=OBSERVED),
            "soilMoisture": _prop(31), "status": _prop("ok", observedAt=OBSERVED)}


# What each entity carries -> the code of its profile.
CODES = {
    "model-only": (_entity(refDeviceModel=_ref("soil")), "soil"),
    "model-iri-only": (_entity(**{"https://smartdatamodels.org/dataModel.Device/refDeviceModel": _ref("soil")}),
                       "soil"),
    "type-iri-model-only": ({**_entity(refDeviceModel=_ref("soil")),
                             "type": "https://smartdatamodels.org/dataModel.Device/Device"}, "soil"),
    "legacy-only": (_entity(profileCode=_prop("bench")), "bench"),
    "both-model-wins": (_entity(refDeviceModel=_ref("soil"), profileCode=_prop("bench")), "soil"),
    "both-equal": (_entity(refDeviceModel=_ref("bench"), profileCode=_prop("bench")), "bench"),
    "foreign-model": (_entity(refDeviceModel=_ref("soil", "othertenant"), profileCode=_prop("bench")),
                      "bench"),
    "malformed-model": (_entity(refDeviceModel={"type": "Relationship", "object": "soil"},
                                profileCode=_prop("bench")), "bench"),
    "foreign-model-only": (_entity(refDeviceModel=_ref("soil", "othertenant")), None),
    "malformed-model-only": (_entity(refDeviceModel={"type": "Relationship", "object": "soil"}), None),
    "neither": (_entity(), None),
    "empty-code": (_entity(profileCode=_prop("")), None),
    "machine": (_entity(MACHINE, profileCode=_prop("bench")), "bench"),
    "machine-ignores-a-model": (_entity(MACHINE, refDeviceModel=_ref("soil"), profileCode=_prop("bench")),
                                "bench"),
    "machine-with-a-model-only": (_entity(MACHINE, refDeviceModel=_ref("soil")), None),
}
each_entity = pytest.mark.parametrize("entity,code", CODES.values(), ids=CODES.keys())
each_shape = pytest.mark.parametrize("flag", ["false", "true"], ids=["legacy", "sdm"])


# ---- build_measurements ------------------------------------------------------

@each_shape
@each_entity
def test_every_measurement_carries_the_code_of_the_profile(entity, code, flag, monkeypatch):
    monkeypatch.setenv(SDM_SHAPE_FLAG, flag)
    built = build_measurements(entity, PROFILE_ROW)
    assert len(built) == 4
    assert {json.dumps(m.get("deviceType")) for m in built} == {
        json.dumps(_prop(code) if code else None)}


def test_build_measurements_changes_neither_argument_and_opens_nothing(monkeypatch):
    def _refuse(*a, **kw):
        raise AssertionError("not pure")

    monkeypatch.setattr(socket, "socket", _refuse)
    monkeypatch.setattr("builtins.open", _refuse)
    entity, profile = copy.deepcopy(CODES["both-model-wins"][0]), copy.deepcopy(PROFILE_ROW)
    first = build_measurements(entity, profile)
    assert build_measurements(entity, profile) == first
    assert entity == CODES["both-model-wins"][0] and profile == PROFILE_ROW


# The output of `build_measurements` when the code was read from `profileCode`: channel, value
# attribute, instant, then (controlledProperty, measurementType) in each shape.
RECORDED = [
    ("airTemperature", "numValue", _prop(21.5, unitCode="CEL"), OBSERVED,
     ("airTemperature", "t"), ("airTemperature", "mean")),
    ("airTemperatureMax", "numValue", _prop(28.4, unitCode="CEL"), OBSERVED,
     ("airTemperatureMax", "t"), ("airTemperature", "max")),
    ("soilMoisture", "numValue", _prop(31, unitCode="P1"), ENTITY_TIME,
     ("soilMoisture", "soil"), ("volumetricWaterContent", "instant")),
    ("status", "textValue", _prop("ok"), OBSERVED, ("status", "state"), ("status", "instant")),
]


def _recorded(urn, sdm):
    out = []
    for channel, key, value, instant, legacy_attrs, sdm_attrs in RECORDED:
        controlled, kind = sdm_attrs if sdm else legacy_attrs
        out.append({
            "id": f"urn:ngsi-ld:DeviceMeasurement:{urn.split(':', 3)[3]}:{channel}",
            "type": "DeviceMeasurement", "refDevice": {"type": "Relationship", "object": urn},
            key: value, "dateObserved": _prop(instant), "timeSource": _prop("device"),
            "controlledProperty": _prop(controlled), "measurementType": _prop(kind),
            **({"name": _prop(channel)} if sdm else {}), "deviceType": _prop("bench")})
    return json.dumps(out)


UNCHANGED = {
    "both": _entity(refDeviceModel=_ref("bench"), profileCode=_prop("bench")),
    "legacy-only": _entity(profileCode=_prop("bench")),
    "machine": _entity(MACHINE, profileCode=_prop("bench")),
    "machine-with-another-model": _entity(MACHINE, refDeviceModel=_ref("soil"), profileCode=_prop("bench")),
}


@each_shape
@pytest.mark.parametrize("entity", UNCHANGED.values(), ids=UNCHANGED.keys())
def test_dm_device_type_unchanged(entity, flag, monkeypatch):
    """Byte for byte."""
    monkeypatch.setenv(SDM_SHAPE_FLAG, flag)
    assert json.dumps(build_measurements(entity, PROFILE_ROW)) == _recorded(entity["id"], flag == "true")


@each_shape
def test_a_device_with_only_a_model_gets_the_same_measurements(flag, monkeypatch):
    monkeypatch.setenv(SDM_SHAPE_FLAG, flag)
    built = build_measurements(_entity(refDeviceModel=_ref("bench")), PROFILE_ROW)
    assert json.dumps(built) == _recorded(DEVICE, flag == "true")


# ---- the bridge --------------------------------------------------------------

class _Cursor:
    """The writer row and the profile row of whatever code is asked for; records the reads."""

    def __init__(self, writer, reads):
        self.writer, self.reads, self.row = writer, reads, None

    def execute(self, sql, params=None):
        if "iot_datalogger_devices" in sql:
            self.reads.append(("writer", params))
            self.row = {"dm_writer": self.writer}
        elif "sensor_profiles" in sql:
            self.reads.append(("profile", params))
            self.row = PROFILE_ROW

    def fetchone(self):
        return self.row

    def close(self):
        pass


def _notify(entity, writer="em", tenant="acmefarm"):
    """(handled, database reads, upserted batches) of one notification."""
    reads, client = [], MagicMock()
    client.upsert_entities_batch.return_value = {"upserted": 4, "errors": [], "entity_ids": []}
    conn = MagicMock()
    conn.cursor.return_value = _Cursor(writer, reads)
    with patch.object(notif.psycopg2, "connect", return_value=conn), \
         patch.object(notif, "POSTGRES_URL", "postgresql://x/y"), \
         patch.object(notif, "SyncOrionClient", return_value=client):
        handled = notif._handle_device_measurement_notification(tenant, entity)
    return handled, reads, [c.args[0] for c in client.upsert_entities_batch.call_args_list]


@each_entity
def test_the_bridge_reads_the_profile_of_the_code_and_writes_it(entity, code, caplog):
    handled, reads, batches = _notify(entity)
    assert handled is True
    assert not any("No sensor_profiles row" in r.getMessage() for r in caplog.records)
    if code is None:
        assert reads == [] and batches == []
        return
    assert reads == [("writer", ("acmefarm", entity["id"])), ("profile", (code, "acmefarm"))]
    (batch,) = batches
    assert len(batch) == 4 and {m["deviceType"]["value"] for m in batch} == {code}


@each_shape
@pytest.mark.parametrize("entity", UNCHANGED.values(), ids=UNCHANGED.keys())
def test_the_bridge_writes_what_it_always_wrote(entity, flag, monkeypatch):
    monkeypatch.setenv(SDM_SHAPE_FLAG, flag)
    _, _, (batch,) = _notify(entity)
    assert json.dumps(batch) == _recorded(entity["id"], flag == "true")


@pytest.mark.parametrize("name", ["model-only", "legacy-only", "both-model-wins"])
def test_a_device_written_by_the_iot_agent_is_still_skipped(name, caplog):
    entity, _ = CODES[name]
    assert _notify(entity, writer="iota") == (True, [("writer", ("acmefarm", DEVICE))], [])
    assert not any("No sensor_profiles row" in r.getMessage() for r in caplog.records)


def test_a_notification_of_another_tenant_never_follows_the_model():
    """The model names a profile of its own tenant: elsewhere it is ignored, as any foreign one."""
    handled, reads, batches = _notify(_entity(refDeviceModel=_ref("soil")), tenant="othertenant")
    assert (handled, reads, batches) == (True, [], [])
    _, reads, _ = _notify(CODES["both-equal"][0], tenant="othertenant")
    assert reads[-1] == ("profile", ("bench", "othertenant"))


def test_one_device_is_logged_once_by_the_bridge_and_the_transformer(caplog, monkeypatch):
    import device_profile
    monkeypatch.setattr(device_profile, "_logged", type(device_profile._logged)())
    with caplog.at_level(logging.INFO):
        _notify(CODES["legacy-only"][0])
        _notify(CODES["legacy-only"][0])
    assert [r.args for r in caplog.records
            if r.getMessage().startswith("device_profile_code_fallback")] == [("acmefarm", DEVICE, "bench")]
