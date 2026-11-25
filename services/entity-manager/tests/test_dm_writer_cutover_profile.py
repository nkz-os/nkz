"""The cutover command finds a device's profile as every other reader."""
import pytest

from tests.telemetry_fakes import TENANT  # noqa: F401,I001  (stubs first)

import dataloggers  # noqa: E402
from tests.test_dm_writer_cutover import (  # noqa: F401,E402  (fixtures)
    BASE, MS, ONE_MS, VERIFY, Http, dm, http, move_enabled, real_tenant_normaliser, run, sleeps, world)

URN = f"urn:ngsi-ld:Device:{TENANT}:S1"


def _prop(value):
    return {"type": "Property", "value": value}


def _ref(code, tenant=TENANT):
    return {"type": "Relationship", "object": f"urn:ngsi-ld:DeviceModel:{tenant}:{code}"}


# Every one is on profile "p"; where there is another code, it is "other".
ON_P = {
    "model-only": {"refDeviceModel": _ref("p")},
    "legacy-only": {"profileCode": _prop("p")},
    "both-equal": {"refDeviceModel": _ref("p"), "profileCode": _prop("p")},
    "both-profile-code-wins": {"refDeviceModel": _ref("other"), "profileCode": _prop("p")},
    "foreign-model": {"refDeviceModel": _ref("other", "othertenant"), "profileCode": _prop("p")},
    "malformed-model": {"refDeviceModel": _ref("other", ""), "profileCode": _prop("p")},
}
each_device = pytest.mark.parametrize("attrs", ON_P.values(), ids=ON_P.keys())


@pytest.fixture
def device(world, monkeypatch):  # noqa: F811
    """One linked device; returns the function that gives it its attributes."""
    world.add("S1")
    world.profiles["other"] = list(ONE_MS)

    def carries(attrs):
        monkeypatch.setattr(dataloggers, "_orion_devices", lambda tenant, urns: {
            u: {"id": u, "type": u.split(":")[2], **attrs} for u in urns})

    return carries


def _attributes(state, code="p"):
    return dataloggers._attributes_for_state(state, TENANT, URN, "S1", code, MS)


@each_device
def test_a_move_to_the_agent_puts_the_mappings_of_the_profile(attrs, device, world):  # noqa: F811
    device(attrs)
    assert run(*BASE, "--to", "iota", "--shape", "sdm", "--apply") == 0
    assert world.agent["S1"] == _attributes(("iota", "sdm"))
    assert world.puts == 2


@each_device
def test_a_rerun_compares_with_the_profile(attrs, device, world, capsys):  # noqa: F811
    device(attrs)
    world.rows[URN].update(dm_writer="iota", dm_shape="sdm")
    world.agent["S1"] = _attributes(("iota", "sdm"))
    assert run(*BASE, "--to", "iota", "--shape", "sdm", "--apply") == 0
    assert world.puts == 0


@each_device
def test_a_move_back_puts_the_mappings_of_the_profile(attrs, device, world):  # noqa: F811
    device(attrs)
    world.rows[URN].update(dm_writer="iota", dm_shape="sdm")
    assert run(*BASE, "--to", "em", "--apply") == 0
    assert world.agent["S1"] == _attributes(("em", None))


@each_device
def test_a_failed_move_restores_the_mappings_of_the_profile(attrs, device, world, caplog):  # noqa: F811
    device(attrs)
    world.update_fail = True
    assert run(*BASE, "--to", "iota", "--shape", "sdm", "--apply") == 1
    assert world.agent["S1"] == _attributes(("em", None))
    assert "plan-time mappings" not in caplog.text


@pytest.mark.parametrize("attrs", [
    {}, {"refDeviceModel": _ref("p", "othertenant")}, {"profileCode": _prop("")},
], ids=["neither", "foreign-model-only", "empty-code"])
def test_a_device_on_no_profile_is_not_moved(attrs, device, world, capsys):  # noqa: F811
    device(attrs)
    assert run(*BASE, "--to", "iota", "--shape", "sdm", "--apply") == 1
    assert "no profile on the Orion entity" in capsys.readouterr().out
    assert world.puts == 0 and world.rows[URN]["dm_writer"] == "em"


def test_a_machine_is_moved_by_its_profile_code_only(world, monkeypatch):  # noqa: F811
    world.add("S1")
    world.profiles["other"] = list(ONE_MS)
    monkeypatch.setattr(dataloggers, "_orion_devices", lambda tenant, urns: {
        u: {"id": u, "type": "ManufacturingMachine", "refDeviceModel": _ref("other"),
            "profileCode": _prop("p")} for u in urns})
    assert run(*BASE, "--to", "iota", "--shape", "sdm", "--apply") == 0
    assert world.agent["S1"] == _attributes(("iota", "sdm"))


@each_device
def test_the_verification_expects_the_mappings_of_the_profile(attrs, device, world, http):  # noqa: F811
    device(attrs)
    world.rows[URN].update(dm_writer="iota", dm_shape="sdm")
    http(Http([dm("S1", name, shape="sdm", mtype="instant")
               for name in ("airTemperature", "relativeHumidity")]))
    assert world.agent["S1"] == _attributes(("iota", "sdm"))
    assert run(*BASE, *VERIFY) == 0
