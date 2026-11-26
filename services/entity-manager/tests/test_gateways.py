"""The gateway device contract: ids, entity, IoT Agent mappings (no network, no database).

A datalogger is an official `Device` (`deviceCategory` network) with a beacon. These tests pin
what the entity-manager writes (`build_entity`), what the IoT Agent writes (`build_iota_attributes`)
and the value rules of the beacon. The agent's JEXL is evaluated by `gateway_jexl.py` in the
agent image; its recorded results (`fixtures/gateway_jexl_golden.json`) are checked here against
those rules, and bound to the generated mappings by a digest. Set NKZ_JEXL_LIVE=1 (docker) to run the
agent again instead of trusting the record.
"""
import copy
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import sys

import pytest

from tests.real_common import seed_real_common  # noqa: I001  (stubs first)

seed_real_common()

import gateways  # noqa: E402
from tests import gateway_jexl  # noqa: E402
from common.device_measurement_shape import SHAPES, measurement_channels  # noqa: E402
from common.iota_dm_mappings import device_attributes  # noqa: E402
from common.unit_codes import to_unit_code  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[3]
TENANT = "acme"
EXT = "gw-0123456789ab"
URN = f"urn:ngsi-ld:Device:{TENANT}:{EXT}"
SENSOR_A = f"urn:ngsi-ld:Device:{TENANT}:sensor-001"
SENSOR_B = f"urn:ngsi-ld:ManufacturingMachine:{TENANT}:tractor-7"
CREATED = datetime.datetime(2026, 11, 25, 10, 30, 15, 123456, tzinfo=datetime.timezone.utc)


def row(**over):
    base = {"gateway_external_id": EXT, "name": "Field box 1", "status": "active", "created_at": CREATED}
    base.update(over)
    return base


# ── identifiers ─────────────────────────────────────────────────────────────


class TestIdentifiers:
    def test_regex_accepts_only_twelve_lowercase_hex(self):
        assert gateways.GATEWAY_ID_RE.fullmatch("gw-0123456789ab")
        for bad in ("gw-0123456789AB", "gw-0123456789a", "gw-0123456789abc", "gw-0123456789ag",
                    "sensor-0123456789ab", "GW-0123456789ab", " gw-0123456789ab", "gw-0123456789ab\n",
                    "gw-", "", "xgw-0123456789ab"):
            assert not gateways.GATEWAY_ID_RE.fullmatch(bad), bad

    def test_is_gateway_external_id(self):
        assert gateways.is_gateway_external_id("gw-0123456789ab")
        for bad in ("gw-0123456789AB", "gw-0123456789a", "gw-0123456789abc", "sensor-001", "", None, 12, b"gw-0123456789ab"):
            assert not gateways.is_gateway_external_id(bad), bad

    def test_new_external_id_matches_and_varies(self):
        ids = {gateways.new_external_id() for _ in range(50)}
        assert len(ids) == 50
        assert all(gateways.GATEWAY_ID_RE.fullmatch(i) for i in ids)

    def test_gateway_urn(self):
        assert gateways.gateway_urn(TENANT, EXT) == URN

    @pytest.mark.parametrize("tenant,ext", [("", EXT), (TENANT, "gw-ABCDEF012345"), (TENANT, "sensor-1"),
                                            ("a:b", EXT), (TENANT, "")])
    def test_gateway_urn_refuses_what_is_not_an_id(self, tenant, ext):
        with pytest.raises(ValueError):
            gateways.gateway_urn(tenant, ext)

    def test_is_gateway_urn(self):
        assert gateways.is_gateway_urn(URN)
        for bad in (SENSOR_A, SENSOR_B, f"urn:ngsi-ld:Device:{TENANT}:gw-0123456789AB",
                    f"urn:ngsi-ld:Device:{TENANT}:gw-0123456789abc", f"urn:ngsi-ld:AgriParcel:{TENANT}:gw-0123456789ab",
                    f"urn:ngsi-ld:ManufacturingMachine:{TENANT}:gw-0123456789ab",
                    f"urn:ngsi-ld:Device:{TENANT}:x:gw-0123456789ab", "gw-0123456789ab", "", None, 5):
            assert not gateways.is_gateway_urn(bad), bad

    def test_beacon_constants(self):
        assert gateways.BEACON_INTERVAL_S == 300

    def test_beacon_topic(self):
        assert gateways.beacon_topic("key123", EXT) == f"/key123/{EXT}/attrs"

    @pytest.mark.parametrize("apikey", ["", "a/b", "a+b", "a#", "a b", None, 5])
    def test_beacon_topic_refuses_an_apikey_that_widens_the_acl(self, apikey):
        with pytest.raises(ValueError):
            gateways.beacon_topic(apikey, EXT)

    def test_beacon_topic_refuses_a_non_gateway_id(self):
        with pytest.raises(ValueError):
            gateways.beacon_topic("key123", "sensor-001")


class TestFlag:
    @pytest.mark.parametrize("raw,tenant,expected", [
        (None, TENANT, False), ("", TENANT, False), ("   ", TENANT, False), (",", TENANT, False),
        (TENANT, TENANT, True), ("other", TENANT, False), ("other,acme", TENANT, True),
        (" other , acme ", TENANT, True), ("*", TENANT, True), (" * ", TENANT, True),
        ("other,*", TENANT, True), ("acme", "acm", False), ("acme", "acme2", False),
        ("acme", "ACME", False), ("*", "", False), ("acme", "", False),
    ])
    def test_enabled(self, monkeypatch, raw, tenant, expected):
        if raw is None:
            monkeypatch.delenv("NKZ_GATEWAY_DEVICES", raising=False)
        else:
            monkeypatch.setenv("NKZ_GATEWAY_DEVICES", raw)
        assert gateways.enabled(tenant) is expected

    @pytest.mark.parametrize("raw,expected", [(None, "legacy"), ("", "legacy"), ("legacy", "legacy"), ("sdm", "sdm")])
    def test_shape_in_force(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("IOTA_DM_SHAPE", raising=False)
        else:
            monkeypatch.setenv("IOTA_DM_SHAPE", raw)
        assert gateways.iota_dm_shape() == expected

    @pytest.mark.parametrize("raw", ["SDM", "new", "true", " sdm"])
    def test_shape_in_force_refuses_anything_else(self, monkeypatch, raw):
        monkeypatch.setenv("IOTA_DM_SHAPE", raw)
        with pytest.raises(ValueError):
            gateways.iota_dm_shape()


# ── the entity the entity-manager writes ────────────────────────────────────


def prop(value):
    return {"type": "Property", "value": value}


class TestBuildEntity:
    def test_new_gateway_without_sensors(self):
        assert gateways.build_entity(TENANT, row(), []) == {
            "id": URN, "type": "Device",
            "name": prop("Field box 1"),
            "deviceCategory": prop(["network"]),
            "supportedProtocol": prop(["mqtt"]),
            "controlledProperty": prop([]),
            "deviceState": prop("active"),
            "dateInstalled": prop("2026-11-25T10:30:15Z"),
        }

    def test_controlled_asset_is_a_relationship_list_of_sorted_unique_urns(self):
        ent = gateways.build_entity(TENANT, row(), [SENSOR_B, SENSOR_A, SENSOR_A])
        assert ent["controlledAsset"] == {"type": "Relationship", "object": [SENSOR_A, SENSOR_B]}

    def test_one_sensor_is_still_a_list(self):
        assert gateways.build_entity(TENANT, row(), [SENSOR_A])["controlledAsset"]["object"] == [SENSOR_A]

    def test_revoked(self):
        assert gateways.build_entity(TENANT, row(status="revoked"), [])["deviceState"] == prop("revoked")

    def test_only_the_entity_manager_attributes(self):
        ent = gateways.build_entity(TENANT, row(), [SENSOR_A])
        assert set(ent) == {"id", "type", "name", "deviceCategory", "supportedProtocol", "controlledProperty",
                            "controlledAsset", "deviceState", "dateInstalled"}

    def test_never_the_attributes_of_the_iot_agent_or_the_series(self):
        """The two writers never share an attribute; the series stays off the Device."""
        ent = gateways.build_entity(TENANT, row(), [SENSOR_A])
        for forbidden in ("dateLastValueReported", "softwareVersion", "rssi", "uptime", "batteryLevel",
                          "location", "refDeviceModel", "deploymentId", "profileCode", "refAgriParcel",
                          "hasAgriParcel"):
            assert forbidden not in ent

    def test_date_installed_is_utc_whatever_the_offset(self):
        madrid = datetime.timezone(datetime.timedelta(hours=1))
        at = datetime.datetime(2026, 11, 25, 11, 30, 15, tzinfo=madrid)
        assert gateways.build_entity(TENANT, row(created_at=at), [])["dateInstalled"] == prop("2026-11-25T10:30:15Z")

    def test_the_input_is_not_modified(self):
        r, sensors = row(), [SENSOR_B, SENSOR_A]
        before = (copy.deepcopy(r), list(sensors))
        gateways.build_entity(TENANT, r, sensors)
        assert (r, sensors) == before

    @pytest.mark.parametrize("bad", [
        row(gateway_external_id=None), row(gateway_external_id="sensor-1"), row(name=""), row(name=None),
        row(status="deleted"), row(status=None), row(created_at=None), row(created_at="2026-11-25T10:30:15Z"),
        row(created_at=datetime.datetime(2026, 11, 25, 10, 30)),
    ])
    def test_refuses_a_row_it_cannot_describe(self, bad):
        with pytest.raises(ValueError):
            gateways.build_entity(TENANT, bad, [])

    @pytest.mark.parametrize("sensors", [[URN], ["not-a-urn"], [""], [None], ["urn:ngsi-ld:AgriParcel:acme:p1"],
                                         [f"urn:ngsi-ld:Device:{TENANT}:gw-0123456789ab"]])
    def test_refuses_sensors_that_are_not_sensor_urns(self, sensors):
        with pytest.raises(ValueError):
            gateways.build_entity(TENANT, row(), sensors)

    def test_tenant_of_the_urn_is_the_one_given(self):
        assert gateways.build_entity("other-co", row(), [])["id"] == f"urn:ngsi-ld:Device:other-co:{EXT}"


# ── the official schema ─────────────────────────────────────────────────────


def _validator():
    spec = importlib.util.spec_from_file_location(
        "check_sdm_conformance", ROOT / ".github" / "scripts" / "check_sdm_conformance.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_sdm_conformance"] = mod
    spec.loader.exec_module(mod)
    cache = pathlib.Path(os.environ.get("SDM_CONFORMANCE_CACHE", pathlib.Path.home() / ".cache" / "sdm-conformance"))
    pins = mod.load_pins(ROOT / "config" / "sdm" / "pins.json")
    try:
        files = mod.fetch(pins, cache)
    except Exception as exc:  # no network and no cache: the CI job `sdm-conformance` still runs it
        pytest.skip(f"official schemas not available: {exc}")
    context = json.loads((ROOT / "config" / "ngsi-ld-context.json").read_text(encoding="utf-8"))
    return mod.Validator(pins, files, context)


@pytest.fixture(scope="module")
def validator():
    return _validator()


def schema_findings(validator, entity):
    """Findings of the official JSON Schema only. The `@context` checks wait for the four terms
    (not yet in the broker's context); an array the broker returns as one value is not a failure."""
    return [f for f in validator.check_entity(entity, single_for_array=True) if f.kind == "schema"]


def agent_attributes(**extra):
    """What the IoT Agent adds to the Device after a beacon, normalized form."""
    out = {"dateLastValueReported": {"type": "Property", "value": "2026-11-25T10:35:00.000Z"}}
    if "softwareVersion" in extra:
        out["softwareVersion"] = prop(extra["softwareVersion"])
    if "rssi" in extra:
        out["rssi"] = {"type": "Property", "value": extra["rssi"], "unitCode": "DBM"}
    return out


class TestOfficialDeviceSchema:
    FORMS = {
        "new": (row(), []),
        "one sensor": (row(), [SENSOR_A]),
        "two sensors": (row(), [SENSOR_A, SENSOR_B]),
        "revoked": (row(status="revoked"), []),
        "revoked with sensors": (row(status="revoked"), [SENSOR_A, SENSOR_B]),
    }

    @pytest.mark.parametrize("form", FORMS)
    def test_entity_form_is_valid(self, validator, form):
        r, sensors = self.FORMS[form]
        assert schema_findings(validator, gateways.build_entity(TENANT, r, sensors)) == []

    @pytest.mark.parametrize("form", FORMS)
    def test_entity_form_with_the_agent_attributes_is_valid(self, validator, form):
        r, sensors = self.FORMS[form]
        ent = {**gateways.build_entity(TENANT, r, sensors), **agent_attributes(softwareVersion="1.2.3", rssi=-71)}
        assert schema_findings(validator, ent) == []

    def test_the_validator_does_see_a_broken_form(self, validator):
        """A mutant check: without `controlledProperty` the official schema must complain."""
        ent = gateways.build_entity(TENANT, row(), [])
        del ent["controlledProperty"]
        assert schema_findings(validator, ent)

    def test_the_validator_rejects_a_wrong_category_type(self, validator):
        ent = gateways.build_entity(TENANT, row(), [])
        ent["deviceCategory"] = prop(5)
        assert schema_findings(validator, ent)

    def test_a_sensor_style_gateway_attribute_is_not_what_we_write(self, validator):
        ent = gateways.build_entity(TENANT, row(), [])
        assert ent["deviceCategory"]["value"] == ["network"]


# ── units ───────────────────────────────────────────────────────────────────


class TestUnits:
    def test_seconds_and_dbm_are_in_the_table(self):
        assert to_unit_code("s") == "SEC"
        assert to_unit_code("dBm") == "DBM"

    def test_channel_units(self):
        channels = {c.name: c for c in gateways.gateway_channels(TENANT, EXT, "legacy")}
        assert {n: c.unit_code for n, c in channels.items()} == {"uptime": "SEC", "batteryLevel": "C62", "rssi": "DBM"}


# ── the IoT Agent mappings ──────────────────────────────────────────────────

# The expressions, exactly as the agent evaluates them. Each is a row of the golden record
# (`fixtures/gateway_jexl_golden.json`): the agent's JEXL ran every value class over them.
UPTIME_OK = ("(((uptime|typeof) == 'number') && uptime >= 0 && uptime < 1000000000000"
             " && (uptime|floor) == uptime)")
BATTERY_OK = ("(((batteryLevel|typeof) == 'number') && ((batteryLevel >= 0 && batteryLevel <= 1)"
              " || batteryLevel == -1))")
RSSI_OK = "(((rssi|typeof) == 'number') && rssi >= -200 && rssi <= 0)"
VERSION_OK = ("(((softwareVersion|typeof) == 'string') && softwareVersion != ''"
              " && (softwareVersion|length) <= 64)")
NOW = "(0|now|toisodate)"


def mappings(shape="legacy", tenant=TENANT, ext=EXT):
    return gateways.build_iota_attributes(tenant, ext, shape)


def of_entity(attrs, entity_name):
    return [a for a in attrs if a.get("entity_name") == entity_name]


GOLD_EXT = gateway_jexl.EXTERNAL_ID  # the id the golden record was produced for
GOLD_URN = f"urn:ngsi-ld:Device:{gateway_jexl.TENANT}:{GOLD_EXT}"


def dm_id(channel, ext=EXT):
    return f"urn:ngsi-ld:DeviceMeasurement:{TENANT}:{ext}:{channel}"


def device_mappings(attrs):
    return [a for a in attrs if "entity_name" not in a]


@pytest.mark.parametrize("shape", SHAPES)
class TestMappingStructure:
    def test_dm_mappings_first_then_device_mappings(self, shape):
        attrs = mappings(shape)
        kinds = ["dm" if "entity_name" in a else "device" for a in attrs]
        assert kinds == sorted(kinds, key=lambda k: k != "dm")
        assert kinds.count("device") == 3
        assert [a["name"] for a in device_mappings(attrs)] == ["dateLastValueReported", "softwareVersion", "rssi"]

    def test_device_mappings_never_carry_uptime_or_battery(self, shape):
        """The series stays off the Device (`uptime`, `batteryLevel`), whatever the key read."""
        for a in device_mappings(mappings(shape)):
            assert a["name"] not in ("uptime", "batteryLevel")
        assert {a["name"] for a in device_mappings(mappings(shape))} == {"dateLastValueReported", "softwareVersion", "rssi"}
        assert "batteryLevel" not in {a["name"] for a in device_mappings(mappings(shape))}
        # the only Device mapping that reads `uptime` writes the instant, never the value
        readers = [a for a in device_mappings(mappings(shape)) if a["object_id"] == "uptime"]
        assert [a["name"] for a in readers] == ["dateLastValueReported"]
        assert not any(a["object_id"] == "batteryLevel" for a in device_mappings(mappings(shape)))

    def test_exact_expression_strings_of_the_device(self, shape):
        by_name = {a["name"]: a for a in device_mappings(mappings(shape))}
        assert by_name["dateLastValueReported"] == {
            "object_id": "uptime", "name": "dateLastValueReported", "type": "DateTime",
            "expression": f"{UPTIME_OK} ? {NOW} : null"}
        assert by_name["softwareVersion"] == {
            "object_id": "softwareVersion", "name": "softwareVersion", "type": "Text",
            "expression": f"{VERSION_OK} ? softwareVersion : null"}
        assert by_name["rssi"] == {
            "object_id": "rssi", "name": "rssi", "type": "Number",
            "expression": f"{RSSI_OK} ? rssi : null",
            "metadata": {"unitCode": {"type": "Text", "value": "DBM"}}}

    def test_dlvr_condition_is_the_uptime_dm_condition(self, shape):
        """`dateLastValueReported` is written exactly when the `uptime` DM is written."""
        attrs = mappings(shape)
        dlvr = next(a for a in device_mappings(attrs) if a["name"] == "dateLastValueReported")
        uptime_dm = of_entity(attrs, dm_id("uptime"))
        assert uptime_dm
        dlvr_condition = dlvr["expression"].split(" ? ")[0]
        for a in uptime_dm:
            assert a["expression"].split(" ? ")[0] == dlvr_condition, a["name"]

    def test_exact_expression_strings_of_the_series(self, shape):
        attrs = mappings(shape)
        for channel, key, ok in (("uptime", "uptime", UPTIME_OK), ("batteryLevel", "batteryLevel", BATTERY_OK),
                                 ("rssi", "rssi", RSSI_OK)):
            dm = {a["name"]: a for a in of_entity(attrs, dm_id(channel))}
            assert dm["numValue"]["expression"] == f"{ok} ? {key} : null"
            assert dm["numValue"]["object_id"] == key
            assert dm["dateObserved"]["expression"] == f"{ok} ? {NOW} : null"
            assert dm["timeSource"]["expression"] == f"{ok} ? 'server' : null"
            assert dm["refDevice"]["expression"] == f"{ok} ? '{URN}' : null"
            assert dm["controlledProperty"]["expression"] == f"{ok} ? '{channel}' : null"

    def test_uptime_rejects_strings(self, shape):
        """A JSON number only. Pinned against the golden rows `uptime: numeric string`, `mixed string`."""
        for a in [*of_entity(mappings(shape), dm_id("uptime")), *device_mappings(mappings(shape))]:
            if a["object_id"] == "uptime":
                assert "'string'" not in a["expression"]
                assert "(uptime|typeof) == 'number'" in a["expression"]

    def test_battery_range_is_zero_to_one_or_minus_one(self, shape):
        """`batteryLevel` 5 is refused. Golden rows `batteryLevel: five`, `just above one`, `unknown`."""
        expr = next(a for a in of_entity(mappings(shape), dm_id("batteryLevel")) if a["name"] == "numValue")["expression"]
        assert expr.startswith(BATTERY_OK)
        assert "<= 1" in expr and ">= 0" in expr and "== -1" in expr

    def test_series_ignores_time_instant(self, shape):
        """`dateObserved` is the arrival time and `timeSource` is `server`; the box's clock is never read."""
        attrs = mappings(shape)
        for a in attrs:
            assert a["object_id"] != "TimeInstant"
            assert a["name"] != "TimeInstant"
            assert "TimeInstant" not in a.get("expression", "")
            assert "dateObserved ?" not in a.get("expression", "")
            assert "timeSource ?" not in a.get("expression", "")
        for dm in (dm_id("uptime"), dm_id("batteryLevel"), dm_id("rssi")):
            by_name = {a["name"]: a for a in of_entity(attrs, dm)}
            assert NOW in by_name["dateObserved"]["expression"]
            assert "'server'" in by_name["timeSource"]["expression"]
            assert "'device'" not in by_name["timeSource"]["expression"]

    def test_dm_ids_and_fixed_attributes(self, shape):
        attrs = mappings(shape)
        for channel in ("uptime", "batteryLevel", "rssi"):
            dm = {a["name"]: a for a in of_entity(attrs, dm_id(channel))}
            assert all(a["entity_type"] == "DeviceMeasurement" for a in dm.values())
            expected = {"numValue", "dateObserved", "timeSource", "refDevice", "controlledProperty", "measurementType"}
            if shape == "sdm":
                expected.add("name")
                assert dm["name"]["expression"].endswith(f"? '{channel}' : null")
                assert dm["measurementType"]["expression"].endswith("? 'instant' : null")
            else:
                assert dm["measurementType"]["expression"].endswith(f"? '{channel}' : null")
            assert set(dm) == expected
            assert "deviceType" not in dm  # a gateway has no profile

    def test_units_on_the_value_only(self, shape):
        attrs = mappings(shape)
        units = {"uptime": "SEC", "batteryLevel": "C62", "rssi": "DBM"}
        for channel, code in units.items():
            dm = {a["name"]: a for a in of_entity(attrs, dm_id(channel))}
            assert dm["numValue"]["metadata"] == {"unitCode": {"type": "Text", "value": code}}
            assert all("metadata" not in a for n, a in dm.items() if n != "numValue")

    def test_deterministic_and_pure(self, shape):
        assert mappings(shape) == mappings(shape)

    def test_other_tenant_and_id_only_change_ids(self, shape):
        other = mappings(shape, tenant="beta", ext="gw-ffffffffffff")
        dump = json.dumps(other)
        assert "acme" not in dump and EXT not in dump
        assert "urn:ngsi-ld:Device:beta:gw-ffffffffffff" in dump

    @pytest.mark.parametrize("tenant,ext", [("", EXT), (TENANT, "sensor-1"), (TENANT, "gw-ABCDEF012345"), ("a:b", EXT)])
    def test_refuses_bad_identifiers(self, shape, tenant, ext):
        with pytest.raises(ValueError):
            gateways.build_iota_attributes(tenant, ext, shape)


def test_unknown_shape_is_refused():
    with pytest.raises(ValueError):
        gateways.build_iota_attributes(TENANT, EXT, "other")


# ── every value class, by the agent's own JEXL (golden record) ────────


def _golden():
    return json.loads(gateway_jexl.GOLDEN.read_text(encoding="utf-8"))


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def expected_series(message):
    """The value rules as plain Python: {channel: value} the series must hold for this message."""
    out = {}
    up = message.get("uptime")
    if _is_number(up) and 0 <= up < 10 ** 12 and float(up).is_integer():
        out["uptime"] = up
    bat = message.get("batteryLevel")
    if _is_number(bat) and (0 <= bat <= 1 or bat == -1):
        out["batteryLevel"] = bat
    rssi = message.get("rssi")
    if _is_number(rssi) and -200 <= rssi <= 0:
        out["rssi"] = rssi
    return out


def expected_device(message):
    out = {}
    if "uptime" in expected_series(message):
        out["dateLastValueReported"] = "<NOW>"
    sw = message.get("softwareVersion")
    if isinstance(sw, str) and 1 <= len(sw) <= 64:
        out["softwareVersion"] = sw
    rssi = message.get("rssi")
    if _is_number(rssi) and -200 <= rssi <= 0:
        out["rssi"] = rssi
    return out


GOLDEN_CASES = [(shape, case["id"]) for shape in SHAPES for case in
                (_golden()["shapes"][shape]["cases"] if gateway_jexl.GOLDEN.exists() else [])]


def test_golden_record_exists_and_covers_every_value_class():
    golden = _golden()
    ids = {c["id"] for c in golden["shapes"]["legacy"]["cases"]}
    for attr, cases in gateway_jexl.VALUES.items():
        labels = {label for label, _ in cases}
        # number, numeric string, fraction (where it can be one), negative, huge, null, bool, list,
        # object and empty string are all in the table of every attribute that takes a number
        assert {"null", "true", "list", "object", "empty string"} <= labels, attr
        for label in labels:
            assert f"{attr}: {label}" in ids
    for needed in ("numeric string", "fraction", "negative", "huge", "zero", "typical"):
        assert needed in {l for l, _ in gateway_jexl.VALUES["uptime"]}, needed
    assert {"whole message", "empty message", "past TimeInstant", "garbage TimeInstant"} <= ids


@pytest.mark.parametrize("shape", SHAPES)
def test_golden_is_bound_to_the_generated_mappings(shape):
    """A mapping changed without a new agent run (`python3 gateway_jexl.py --write`) fails here."""
    golden = _golden()
    assert golden["shapes"][shape]["mappings_sha256"] == gateway_jexl.mappings_digest(
        mappings(shape, tenant=gateway_jexl.TENANT, ext=GOLD_EXT))


@pytest.mark.parametrize("shape,case_id", GOLDEN_CASES)
def test_agent_writes_what_the_value_rules_say(shape, case_id):
    case = next(c for c in _golden()["shapes"][shape]["cases"] if c["id"] == case_id)
    message, written = case["message"], case["written"]
    series = expected_series(message)

    got_series = {}
    for entity, attrs in written.items():
        if entity == "<primary>":
            continue
        channel = entity.rsplit(":", 1)[1]
        assert entity == dm_id(channel, GOLD_EXT), entity
        got_series[channel] = attrs["numValue"]["value"]
        # one measurement writes all of its attributes, with the arrival time, never the box's
        assert attrs["dateObserved"]["value"] == "<NOW>"
        assert attrs["timeSource"]["value"] == "server"
        assert attrs["refDevice"]["value"] == GOLD_URN
        assert attrs["controlledProperty"]["value"] == channel
        assert "TimeInstant" not in attrs
        assert ("name" in attrs) == (shape == "sdm")
        if shape == "sdm":
            assert attrs["name"]["value"] == channel
            assert attrs["measurementType"]["value"] == "instant"
        assert attrs["numValue"]["metadata"]["unitCode"]["value"] == {"uptime": "SEC", "batteryLevel": "C62",
                                                                       "rssi": "DBM"}[channel]
        # nothing from the incoming keys leaks into the entity
        assert set(attrs) <= {"numValue", "dateObserved", "timeSource", "refDevice", "controlledProperty",
                              "measurementType", "name"}
    assert got_series == series, f"{case_id}: series"

    device = {k: v["value"] for k, v in written.get("<primary>", {}).items()}
    assert device == expected_device(message), f"{case_id}: device"
    assert not {"uptime", "batteryLevel"} & set(device)


def test_dlvr_is_written_exactly_when_the_uptime_series_is():
    """On every recorded case of both shapes: Device and series never disagree."""
    for shape in SHAPES:
        for case in _golden()["shapes"][shape]["cases"]:
            dlvr = "dateLastValueReported" in case["written"].get("<primary>", {})
            assert dlvr == (dm_id("uptime", GOLD_EXT) in case["written"]), case["id"]


def test_every_value_class_of_every_attribute_is_recorded_with_its_outcome():
    """The table the value rules draw, by label, so a drift in either side is visible in one place."""
    accepted = {
        "uptime": {"zero", "typical", "max", "integral float"},
        "batteryLevel": {"zero", "half", "one", "unknown"},
        "rssi": {"typical", "fraction", "zero", "floor"},
        "softwareVersion": {"typical", "64 chars", "numeric string", "blank"},
    }
    for attr, cases in gateway_jexl.VALUES.items():
        for label, value in cases:
            message = {attr: value}
            got = (expected_device(message) if attr == "softwareVersion" else expected_series(message))
            assert bool(got) == (label in accepted[attr]), f"{attr}: {label}"


@pytest.mark.skipif(os.environ.get("NKZ_JEXL_LIVE") != "1", reason="NKZ_JEXL_LIVE=1 runs the agent image (docker)")
@pytest.mark.parametrize("shape", SHAPES)
def test_live_agent_agrees_with_the_golden_record(shape):
    msgs = gateway_jexl.messages()
    live = gateway_jexl.evaluate(mappings(shape, ext=GOLD_EXT), [m for _, m in msgs])
    recorded = [c["written"] for c in _golden()["shapes"][shape]["cases"]]
    assert json.loads(json.dumps(live)) == recorded


# ── the official DeviceMeasurement schema over what the agent writes ────────


def dm_entity(entity_id, attrs, shape):
    ent = {"id": entity_id, "type": "DeviceMeasurement"}
    for name, spec in attrs.items():
        value = spec["value"]
        if name == "refDevice":
            ent[name] = {"type": "Relationship", "object": value}
            continue
        if value == "<NOW>":
            value = "2026-11-25T10:35:00.000Z"
        attr = {"type": "Property", "value": value, "observedAt": "2026-11-25T10:35:00.000Z"}
        if "metadata" in spec:
            attr["unitCode"] = spec["metadata"]["unitCode"]["value"]
        ent[name] = attr
    return ent


@pytest.mark.parametrize("shape", SHAPES)
def test_every_series_entity_the_agent_writes_is_official(validator, shape):
    case = next(c for c in _golden()["shapes"][shape]["cases"] if c["id"] == "whole message")
    entities = {e: a for e, a in case["written"].items() if e != "<primary>"}
    assert len(entities) == 3
    for entity_id, attrs in entities.items():
        assert schema_findings(validator, dm_entity(entity_id, attrs, shape)) == [], entity_id


# ── sensors are untouched ───────────────────────────────────────────────────

SENSOR_PROFILES = None


def _sensor_profiles():
    from tests.telemetry_fakes import PROFILE
    return {
        "profile": PROFILE["mapping"]["measurements"],
        "stats": [{"type": "temp", "unit": "°C", "sdmAttribute": "soilTemp", "statistics": ["mean", "min", "max"]}],
        "scaled": [{"type": "t", "incomingKey": "r1", "sdmAttribute": "airTemperature", "unit": "°C",
                    "expression": "r1 * 0.1"}],
        "text": [{"type": "stat", "sdmAttribute": "status", "valueType": "Text"}],
        "runtime": [{"type": "rt", "sdmAttribute": "windSpeed"}],
    }


def sensor_mappings(shape):
    out = {}
    urn = "urn:ngsi-ld:Device:acme:dev-1"
    for name, ms in _sensor_profiles().items():
        channels = measurement_channels(ms, tenant="acme", external_id="dev-1", device_urn=urn,
                                        profile_code="PROF", shape=shape)
        out[name] = device_attributes(ms, channels, device_urn=urn)
    return out


# sha256 of json.dumps of each list, taken on the base commit before the gateway code existed
BASE_SENSOR_DIGESTS = {
    "profile/legacy": "8dd68709e10ce1ddaa209e721b3174829f7f78b2f8b7c15375fc2900473a7762",
    "profile/sdm": "93c34c213d20cbaed63d7180173298bd0099ea6a4b4d560b10c752b214d1eec4",
    "stats/legacy": "35b49f783543688fc8cd44c277b2f2dd32ca05b2d1e1e3ffad4936dade1335c3",
    "stats/sdm": "6e268e1b42c22dab10b18077948feb6735a179ce9730e0463a37a6c9141ec1d3",
    "scaled/legacy": "60180e1488eacd9707163d7b8f3c76021cdd08438dff956567b6e2fe94d29b56",
    "scaled/sdm": "79ecd7dea72c54803d2e348b29674c8a571a239190dc0f90b0926356f70344bd",
    "text/legacy": "cac52512c303a9b0f075c0321c9228f37dbad9d0eb372c052e5dd6caa82d794f",
    "text/sdm": "00b61372cb19f7fd6f5adeaf5cff84bf39d2be259a82982c4812e55ba4a65c25",
    "runtime/legacy": "80bcbb21350d48de955887f664f44360db3836b087122e7135872dcaab6fa04f",
    "runtime/sdm": "6592e49f5cdbd492c55168c86e5d0500daffed4673fb9e5390fd7621072a1857",
}


@pytest.mark.parametrize("shape", SHAPES)
def test_sensor_mappings_are_byte_identical_to_the_base(shape):
    """The generator and the unit table feed sensors too: nothing here may change their mappings."""
    got = {f"{n}/{shape}": hashlib.sha256(json.dumps(a).encode()).hexdigest()
           for n, a in sensor_mappings(shape).items()}
    assert got == {k: v for k, v in BASE_SENSOR_DIGESTS.items() if k.endswith(f"/{shape}")}


@pytest.mark.parametrize("shape", SHAPES)
def test_gateway_mapping_does_not_change_a_sensor_mapping(shape):
    """Building gateway mappings (in either order, any number of times) leaves sensor output as it was."""
    before = sensor_mappings(shape)
    for s in SHAPES:
        gateways.build_iota_attributes(TENANT, EXT, s)
        gateways.gateway_channels(TENANT, EXT, s)
    assert sensor_mappings(shape) == before


def test_gateway_channels_are_not_shared_state():
    first = gateways.gateway_channels(TENANT, EXT, "sdm")
    with pytest.raises(Exception):
        first[0].name = "x"
    assert gateways.gateway_channels(TENANT, EXT, "sdm") == first


# ── the migration file ──────────────────────────────────────────────────────

MIGRATION = ROOT / "config" / "timescaledb" / "migrations" / "123_gateway_devices.sql"


class TestMigrationText:
    def test_the_number_is_free_and_unique(self):
        numbers = [p.name.split("_", 1)[0] for p in MIGRATION.parent.glob("[0-9]*.sql")]
        assert numbers.count("123") == 1

    def test_additive_and_idempotent_text(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert re.search(r"ADD COLUMN IF NOT EXISTS gateway_external_id\s+text\b", sql, re.I)
        assert re.search(r"ADD COLUMN IF NOT EXISTS gateway_provisioned_at\s+timestamptz\b", sql, re.I)
        assert "gw-[0-9a-f]{12}" in sql
        assert not re.search(r"\bDROP\b|\bDELETE\b|\bTRUNCATE\b|NOT NULL", sql.replace("-- ", "--"))
        assert "OWNER TO" not in sql and "POLICY" not in sql  # owner and RLS unchanged
