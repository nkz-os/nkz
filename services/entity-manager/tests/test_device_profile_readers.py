"""Every reader of a device's profile: `refDeviceModel` alone, `profileCode` alone, and both.

With both, the model wins. Machines only carry `profileCode`.
"""
import json
import logging
from unittest.mock import patch

import pytest

from tests.telemetry_fakes import (AUTH, PROFILE, TENANT, FakeState, client, post_json, seed_datalogger,
                                   wired)

import dataloggers  # noqa: E402
import deployments  # noqa: E402
import device_profile  # noqa: E402

DL = "11111111-1111-1111-1111-111111111111"
URN = f"urn:ngsi-ld:Device:{TENANT}:S1"
MACHINE = f"urn:ngsi-ld:ManufacturingMachine:{TENANT}:S1"
AIR = PROFILE["mapping"]
SOIL = {"measurements": [{"type": "x", "sdmAttribute": "soilMoisture", "unit": "%"}], "windowSeconds": 600}
WIND = {"measurements": [{"type": "w", "sdmAttribute": "windSpeed", "unit": "m/s"}]}


def _prop(value):
    return {"type": "Property", "value": value}


def _ref(code, tenant=TENANT):
    return {"type": "Relationship", "object": f"urn:ngsi-ld:DeviceModel:{tenant}:{code}"}


def _entity(urn=URN, **attrs):
    return {"id": urn, "type": urn.split(":")[2], "name": _prop("Probe"), **attrs}


# The profile of each is "soil"; where there is another code, it is "air".
ON_SOIL = {
    "model-only": _entity(refDeviceModel=_ref("soil")),
    "legacy-only": _entity(profileCode=_prop("soil")),
    "both-model-wins": _entity(refDeviceModel=_ref("soil"), profileCode=_prop("air")),
    "foreign-model": _entity(refDeviceModel=_ref("air", "othertenant"), profileCode=_prop("soil")),
    "machine": _entity(MACHINE, profileCode=_prop("soil")),
    "machine-ignores-a-model": _entity(MACHINE, refDeviceModel=_ref("air"), profileCode=_prop("soil")),
}
each_reader = pytest.mark.parametrize("entity", ON_SOIL.values(), ids=ON_SOIL.keys())


@pytest.fixture(autouse=True)
def _nothing_logged_yet(monkeypatch):
    monkeypatch.setattr(device_profile, "_logged", type(device_profile._logged)())


def _state(entity):
    st = FakeState()
    seed_datalogger(st, dl_id=DL)
    for code, mapping in (("air", AIR), ("soil", SOIL), ("wind", WIND)):
        st.profiles[code] = {"tenant_id": TENANT, "name": code, "mapping": mapping}
    st.devices.append(dict(datalogger_id=DL, tenant_id=TENANT, device_urn=entity["id"], external_id="S1",
                           dm_writer="em", dm_shape=None))
    return st


@each_reader
def test_the_gateway_config_has_the_measurements_of_the_profile(entity):
    st = _state(entity)
    with wired(st) as w:
        w.orion.entities[entity["id"]] = entity
        device, = dataloggers.get_datalogger_config(TENANT, DL)["devices"]
    assert [m["sdmAttribute"] for m in device["measurements"]] == ["soilMoisture"]
    assert device["windowSeconds"] == 600 and device["name"] == "Probe"


@each_reader
def test_a_device_is_on_one_profile(entity):
    assert dataloggers.is_on_profile(TENANT, entity, "soil") is True
    assert dataloggers.is_on_profile(TENANT, entity, "air") is False


def test_no_entity_is_on_no_profile():
    assert dataloggers.is_on_profile(TENANT, None, "soil") is False


@each_reader
def test_the_listed_devices_of_a_profile(entity):
    st = _state(entity)
    with wired(st) as w:
        w.orion.entities[entity["id"]] = entity
        assert [d["device_urn"] for d in dataloggers.links_on_profile(TENANT, "soil")] == [entity["id"]]
        assert dataloggers.links_on_profile(TENANT, "air") == []


@each_reader
def test_a_failed_profile_change_restores_the_attributes_of_the_old_profile(entity):
    st = _state(entity)
    st.fail.update(orion_attrs=500, orion_patch=True)
    sent = []
    with wired(st) as w:
        w.orion.entities[entity["id"]] = entity
        update = w.iota.update_device_attributes

        def _update(url, headers, device_id, attributes):
            sent.append([a["name"] for a in attributes])
            return update(url, headers, device_id, attributes)

        w.iota.update_device_attributes = _update
        with pytest.raises(dataloggers.ApiError) as err:
            dataloggers.change_device_profile(TENANT, entity["id"], "wind")
    assert err.value.step == "orion_profile"
    assert sent == [["windSpeed"], ["soilMoisture"]]


@each_reader
def test_channel_overrides_are_checked_against_the_profile(entity):
    st = _state(entity)
    opened = []

    def _open(tenant, urn, spec, valid_from, actor, reason):
        opened.append(spec.channel_overrides)
        return {"id": "dep-2", "device_urn": urn, "valid_from": valid_from, "valid_to": None,
                "observesFeature": None, "observationContext": {},
                "channelOverrides": spec.channel_overrides, "mobility": "mobile", "location": None}

    def _move(attribute):
        return post_json(client(), f"/api/sensors/devices/{entity['id']}/deployments",
                         {"reason": "moved", "mobility": "mobile",
                          "channelOverrides": {attribute: {"observationContext": {"medium": "water"}}}})

    with wired(st) as w, patch.object(deployments, "current_deployment", lambda tenant, urn: None), \
         patch.object(deployments, "open_deployment", _open):
        w.orion.entities[entity["id"]] = entity
        wrong, right = _move("airTemperature"), _move("soilMoisture")
    assert wrong.status_code == 400 and "airTemperature" in wrong.get_json()["error"]
    assert right.status_code == 201, right.get_json()
    assert [list(o) for o in opened] == [["soilMoisture"]]


def test_channel_overrides_of_a_device_on_no_profile_of_the_tenant_are_400():
    entity = _entity(refDeviceModel=_ref("soil", "othertenant"))
    st = _state(entity)
    with wired(st) as w:
        w.orion.entities[URN] = entity
        r = post_json(client(), f"/api/sensors/devices/{URN}/deployments",
                      {"reason": "moved", "mobility": "mobile",
                       "channelOverrides": {"soilMoisture": {"observationContext": {"medium": "water"}}}})
    assert r.status_code == 400 and "no known profile" in r.get_json()["error"]


# ---- profile edit: the listing and the fresh read use one rule ----------------

def _edit_soil(mapping=AIR):
    body = {"name": "Soil", "mapping": {"measurements": [
        {k: m[k] for k in ("type", "sdmAttribute")} | {"unit": "%"} for m in mapping["measurements"]]}}
    r = client().put("/api/sensors/profiles/soil", headers=AUTH, content_type="application/json",
                     data=json.dumps(body))
    assert r.status_code == 200, r.get_json()
    return r.get_json()


DEVICES_ON_SOIL = {k: v for k, v in ON_SOIL.items() if not k.startswith("machine")}


@pytest.mark.parametrize("entity", DEVICES_ON_SOIL.values(), ids=DEVICES_ON_SOIL.keys())
def test_a_profile_edit_reaches_the_devices_on_it(entity):
    st = _state(entity)
    with wired(st) as w:
        w.orion.entities[URN] = dict(entity)
        out = _edit_soil()
    assert out["reprovisioned"] == [URN] and out["refreshed"] == [URN]
    assert w.orion.entities[URN]["controlledProperty"] == _prop(["humidity", "temperature"])


LEFT_FOR = {
    "model-moved": {"refDeviceModel": _ref("wind"), "profileCode": _prop("soil")},
    "model-only-moved": {"refDeviceModel": _ref("wind")},
    "legacy-moved": {"profileCode": _prop("wind")},
}


@pytest.mark.parametrize("attrs", LEFT_FOR.values(), ids=LEFT_FOR.keys())
def test_a_device_that_left_by_its_model_is_not_written(attrs, caplog):
    entity = _entity(refDeviceModel=_ref("soil"))
    moved = _entity(**attrs)
    st = _state(entity)
    with wired(st) as w, caplog.at_level(logging.WARNING):
        w.orion.entities[URN] = entity
        listed = dataloggers.links_on_profile

        def _listing(tenant, code):
            found = listed(tenant, code)
            w.orion.entities[URN] = dict(moved)
            return found

        with patch.object(dataloggers, "links_on_profile", _listing), \
             patch.object(dataloggers, "reprovision_profile_devices",
                          lambda *a: {"reprovisioned": [], "failed": []}):
            out = _edit_soil()
    assert out["refreshed"] == out["skipped"] == out["refresh_failed"] == []
    assert w.orion.entities[URN] == moved and w.orion.attr_posts == []
    assert any(all(part in r.getMessage() for part in (TENANT, URN, "expected='soil'", "found='wind'"))
               for r in caplog.records)


# ---- the fallback log goes through every reader --------------------------------

def test_the_readers_share_one_log_per_device(caplog):
    entity = ON_SOIL["legacy-only"]
    st = _state(entity)
    with wired(st) as w, caplog.at_level(logging.INFO):
        w.orion.entities[URN] = entity
        dataloggers.get_datalogger_config(TENANT, DL)
        dataloggers.links_on_profile(TENANT, "soil")
        dataloggers.change_device_profile(TENANT, URN, "wind")
    assert [r.args for r in caplog.records
            if r.getMessage().startswith("device_profile_code_fallback")] == [(TENANT, URN, "soil")]
