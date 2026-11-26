"""project_gateway: order, failure, idempotence, suspension, flag and the paths of an existing agent device (in-memory fakes)."""
import datetime
import logging
from unittest.mock import patch

import pytest

from tests.telemetry_fakes import FakeState, TENANT, seed_datalogger, seed_device, wired

import dataloggers  # noqa: E402
import gateways  # noqa: E402

DL = "11111111-1111-1111-1111-111111111111"
USER = "dl_aaaaaaaaaaaaaaaa"
GW = "gw-0123456789ab"
URN = f"urn:ngsi-ld:Device:{TENANT}:{GW}"
BEACON = f"/KEY123/{GW}/attrs"
CREATED = datetime.datetime(2026, 11, 20, 8, 30, tzinfo=datetime.timezone.utc)


@pytest.fixture(autouse=True)
def flag(monkeypatch):
    monkeypatch.setenv(gateways.FLAG, TENANT)
    monkeypatch.delenv(gateways.SHAPE_ENV, raising=False)


def seed(st, *, gw=GW, marker=None, status="active", sensors=("S1",)):
    seed_datalogger(st, dl_id=DL, status=status, username=USER)
    st.dataloggers[DL].update(created_at=CREATED, gateway_external_id=gw, gateway_provisioned_at=marker)
    for s in sensors:
        seed_device(st, DL, s)
    st.iota_strict = True
    return st


def project():
    return gateways.project_gateway(TENANT, DL)


def writes(log):
    return [e for e in log if e[:2] in (("orion", "upsert"), ("orion", "attrs"), ("orion", "delete_attr"),
                                        ("orion", "delete"), ("orion", "post"), ("orion", "patch"),
                                        ("iota", "provision"), ("iota", "delete"), ("iota", "update"),
                                        ("db", "gateway_id"), ("db", "gateway_marker"))]


def indexes(log, *keys):
    return [next(i for i, e in enumerate(log) if e[:len(k)] == k) for k in keys]


def _in_order(log, *keys):
    """Each key appears after the previous one; returns the positions."""
    pos, out = -1, []
    for key in keys:
        pos = next(i for i in range(pos + 1, len(log)) if log[i][:len(key)] == key)
        out.append(pos)
    return out


def test_the_order_of_the_calls_and_no_network_under_a_datalogger_lock():
    st = seed(FakeState(), gw=None)
    with wired(st):
        assert project() == "done"
    ext = st.dataloggers[DL]["gateway_external_id"]
    ingestion = ("sql", "lock", f"ingestion:{TENANT}")
    _in_order(
        st.log,
        ("sql", "lock", f"gateway:{DL}"),                                   # the gateway lock first
        ingestion, ("sql", "FOR UPDATE"), ("db", "gateway_id"), ("sql", "commit"),  # (a) the id
        ingestion, ("sql", "FOR UPDATE"), ("sql", "commit"),                # (a) the rows
        ("orion", "upsert"), ("iota", "get"), ("iota", "provision"),        # (b) no row lock held
        ingestion, ("sql", "FOR UPDATE"), ("db", "gateway_marker"), ("sql", "commit"),   # (c) marker
        ingestion, ("sql", "FOR UPDATE"), ("dynsec", "set_role_topics"), ("sql", "commit"))  # (c) topics
    assert st.locked_network == []  # no Orion or agent call under the ingestion or row lock
    assert ("sql", "lock_timeout", gateways.LOCK_TIMEOUT) in st.log
    assert ext.startswith("gw-") and st.dataloggers[DL]["gateway_provisioned_at"] is not None
    assert st.role_topics[USER] == {"/KEY123/S1/attrs", f"/KEY123/{ext}/attrs"}


def test_a_lock_that_is_not_given_in_time_is_busy_and_nothing_is_called():
    st = seed(FakeState())
    st.fail["lock_timeout"] = True
    with wired(st):
        assert project() == "failed:busy"
    assert not [e for e in st.log if e[0] in ("orion", "iota", "dynsec", "db")]


def test_the_id_is_committed_before_anything_is_written_elsewhere_and_survives_a_failure():
    st = seed(FakeState(), gw=None)
    st.fail["upsert"] = RuntimeError("down")
    with wired(st):
        assert project() == "failed:orion_entity"
    assert st.dataloggers[DL]["gateway_external_id"] is not None  # the committed id stays
    assert st.dataloggers[DL]["gateway_provisioned_at"] is None
    assert not any(e[0] in ("iota", "dynsec") for e in st.log)


def test_the_entity_is_what_the_rows_say():
    st = seed(FakeState(), sensors=("S1", "S2"))
    with wired(st) as w:
        assert project() == "done"
        entity = w.orion.device_models[URN]
    assert entity["deviceState"]["value"] == "active"
    assert entity["controlledAsset"]["object"] == [f"urn:ngsi-ld:Device:{TENANT}:S1",
                                                   f"urn:ngsi-ld:Device:{TENANT}:S2"]
    assert st.iota_devices[GW]["entity_name"] == URN
    assert st.iota_devices[GW]["attributes"] == gateways.build_iota_attributes(TENANT, GW, "legacy")


@pytest.mark.parametrize("failing, outcome, orion_done, agent_done", [
    ({"upsert": RuntimeError("x")}, "failed:orion_entity", False, False),
    ({"iota_get": 500}, "failed:agent_device", True, False),
    ({"provision": True}, "failed:agent_device", True, False),
    ({"set_role_topics": True}, "failed:acl", True, True),
    ({"commit_of_marker": True}, "failed:marker", True, True),
])
def test_a_failing_step_leaves_no_later_step_and_no_permission_without_the_agent_device(
        failing, outcome, orion_done, agent_done):
    st = seed(FakeState())
    st.fail.update(failing)
    with wired(st) as w:
        assert project() == outcome
        assert (URN in w.orion.device_models) is orion_done
    assert (GW in st.iota_devices) is agent_done
    if not agent_done:
        assert BEACON not in st.role_topics.get(USER, set())  # a failed provisioning may withdraw
        assert not [e for e in st.log if e[:2] == ("db", "gateway_marker")]
    # the marker is committed before the broker is asked, never behind it
    assert (st.dataloggers[DL]["gateway_provisioned_at"] is not None) is (outcome == "failed:acl")


def test_no_service_key_is_no_agent_device_and_no_permission():
    st = seed(FakeState())
    with wired(st) as w:
        w.iota.get_or_create_service_group = lambda *a, **k: None
        assert project() == "failed:service_group"
    assert GW not in st.iota_devices and not [e for e in st.log if e[0] == "dynsec"]


def test_a_second_run_on_an_unchanged_gateway_writes_nothing():
    st = seed(FakeState(), sensors=("S1", "S2"))
    with wired(st):
        assert project() == "done"
        before = (dict(st.role_topics), dict(st.iota_devices), dict(st.dataloggers[DL]))
        mark = len(st.log)
        assert project() == "done"
    assert writes(st.log[mark:]) == []
    assert (dict(st.role_topics), dict(st.iota_devices), dict(st.dataloggers[DL])) == before


def test_orion_spellings_of_the_same_values_are_not_a_change():
    st = seed(FakeState(), sensors=("S2", "S1"))
    with wired(st) as w:
        assert project() == "done"
        stored = w.orion.device_models[URN]
        stored["dateInstalled"]["value"] = {"@type": "DateTime", "@value": "2026-11-20T08:30:00.000Z"}
        stored["controlledAsset"]["object"] = list(reversed(stored["controlledAsset"]["object"]))
        mark = len(st.log)
        assert project() == "done"
    assert writes(st.log[mark:]) == []


def test_a_changed_gateway_writes_only_what_differs():
    st = seed(FakeState())
    with wired(st) as w:
        assert project() == "done"
        st.dataloggers[DL]["name"] = "Renamed"
        w.orion.device_models[URN]["iotAgentAttr"] = {"type": "Property", "value": 1}
        mark = len(st.log)
        assert project() == "done"
        assert w.orion.attr_posts[-1] == (URN, {"name": {"type": "Property", "value": "Renamed"}})
        assert "iotAgentAttr" in w.orion.device_models[URN]  # the agent's attributes stay
    assert [e[:2] for e in writes(st.log[mark:])] == [("orion", "attrs")]


def test_controlled_asset_follows_the_sensors_and_goes_when_there_are_none():
    st = seed(FakeState(), sensors=("S1",))
    with wired(st) as w:
        assert project() == "done"
        st.devices.clear()
        assert project() == "done"
        assert "controlledAsset" not in w.orion.device_models[URN]
        assert ("orion", "delete_attr", URN, "controlledAsset") in st.log
        seed_device(st, DL, "S9")
        assert project() == "done"  # POST /attrs creates it again
        assert w.orion.device_models[URN]["controlledAsset"]["object"] == f"urn:ngsi-ld:Device:{TENANT}:S9" \
            or w.orion.device_models[URN]["controlledAsset"]["object"] == [f"urn:ngsi-ld:Device:{TENANT}:S9"]
    assert st.role_topics[USER] == {"/KEY123/S9/attrs", BEACON}


def test_the_projection_repairs_a_bare_entity():
    st = seed(FakeState())
    with wired(st) as w:
        w.orion.device_models[URN] = {"id": URN, "type": "Device"}  # what a beacon to nothing creates
        assert project() == "done"
        assert w.orion.device_models[URN]["controlledProperty"]["value"] == []
        assert w.orion.device_models[URN]["deviceState"]["value"] == "active"


# ── suspension, flag, revoked ───────────────────────────────────────────────


def test_a_suspended_tenant_touches_nothing():
    st = seed(FakeState(), gw=None)
    st.suspended.add(TENANT)
    with wired(st):
        assert project() == "off"
    assert st.dataloggers[DL]["gateway_external_id"] is None
    assert [e for e in st.log if e[0] not in ("sql",)] == []
    assert ("sql", "FOR UPDATE") not in st.log  # the row is not even locked
    assert ("sql", "lock", f"ingestion:{TENANT}") in st.log


@pytest.mark.parametrize("value", ["", "other-tenant", None])
def test_flag_off_makes_no_call_at_all(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(gateways.FLAG)
    else:
        monkeypatch.setenv(gateways.FLAG, value)
    st = seed(FakeState(), gw=None)
    with wired(st) as w:
        assert project() == "off"
        assert w.orion.posted_urls == [] and w.orion.attr_posts == []
    assert st.log == []
    assert st.dataloggers[DL]["gateway_external_id"] is None


def test_a_revoked_gateway_is_marked_revoked_and_nothing_else_moves():
    st = seed(FakeState(), marker="then", status="revoked")
    st.role_topics[USER] = {"/KEY123/S1/attrs", BEACON}
    st.iota_devices[GW] = {"device_id": GW, "entity_name": "x"}
    with wired(st) as w:
        w.orion.device_models[URN] = gateways.build_entity(TENANT, {**st.dataloggers[DL], "status": "active"},
                                                          [f"urn:ngsi-ld:Device:{TENANT}:S1"])
        assert project() == "done"
        assert w.orion.device_models[URN]["deviceState"]["value"] == "revoked"
    assert not [e for e in st.log if e[0] in ("iota", "dynsec")]
    assert st.role_topics[USER] == {"/KEY123/S1/attrs", BEACON}
    assert st.iota_devices[GW] == {"device_id": GW, "entity_name": "x"}


def test_a_revoked_gateway_never_projected_gets_an_entity_and_no_agent_device_or_permission():
    st = seed(FakeState(), status="revoked")
    with wired(st) as w:
        assert project() == "done"
        assert w.orion.device_models[URN]["deviceState"]["value"] == "revoked"
    assert GW not in st.iota_devices and not st.role_topics
    assert st.dataloggers[DL]["gateway_provisioned_at"] is None


def test_an_account_the_broker_lost_is_left_for_rotate():
    st = seed(FakeState())
    st.missing_clients.add(USER)
    with wired(st):
        assert project() == "done"
    assert not [e for e in st.log if e[:2] in (("dynsec", "create_account"), ("dynsec", "delete_account"),
                                               ("dynsec", "set_role_topics"), ("dynsec", "enable"),
                                               ("dynsec", "disable"), ("dynsec", "set_password"))]
    assert GW in st.iota_devices


def test_it_never_raises_and_logs_only_ids_and_type_names(caplog):
    st = seed(FakeState())
    with wired(st) as w, caplog.at_level(logging.DEBUG):
        w.dynsec.dynsec_from_env = lambda: (_ for _ in ()).throw(RuntimeError("secret-password-123"))
        assert project() == "failed:acl"
        assert gateways.project_gateway(TENANT, "not-a-uuid") == "failed:row"
        assert gateways.project_gateway(TENANT, "22222222-2222-2222-2222-222222222222") == "failed:row"
    assert "secret-password-123" not in caplog.text and "KEY123" not in caplog.text
    assert "RuntimeError" in caplog.text or "ApiError" in caplog.text


# ── an existing agent device ──────────────────────────────────────────────────────────────────────


def test_a_409_on_an_equal_device_is_done_without_a_write():
    st = seed(FakeState())
    with wired(st) as w:
        calls = []

        def racing(url, headers, **kw):  # someone provisioned it between our read and our POST
            calls.append(1)
            st.iota_devices[kw["device_id"]] = {
                "device_id": kw["device_id"], "entity_name": kw["entity_name"], "entity_type": "Device",
                "apikey": kw["apikey"], "attributes": kw["attributes"]}
            raise w.iota.IotAgentError(409, "duplicate")

        w.iota.provision_device = racing
        assert project() == "done"
    assert calls == [1]
    assert ("iota", "delete", GW) not in st.log
    assert BEACON in st.role_topics[USER]


def test_a_409_on_a_different_entity_name_is_deleted_and_provisioned():
    st = seed(FakeState())
    # what an unknown beacon autoprovisions: no tenant in the entity name, no attributes
    st.iota_devices[GW] = {"device_id": GW, "entity_name": f"urn:ngsi-ld:Device:{GW}",
                           "entity_type": "Device", "apikey": "KEY123", "attributes": []}
    with wired(st):
        assert project() == "done"
    i = indexes(st.log, ("iota", "delete"), ("iota", "provision"))
    assert i[0] < i[1]
    assert st.iota_devices[GW]["entity_name"] == URN


def test_a_409_that_stays_a_409_fails_without_a_permission():
    st = seed(FakeState())
    st.iota_devices[GW] = {"device_id": GW, "entity_name": "x", "attributes": []}
    with wired(st) as w:
        w.iota.delete_device = lambda *a: True  # cannot get rid of it: the device stays
        assert project() == "failed:agent_device"
    assert BEACON not in st.role_topics.get(USER, set())


def test_an_agent_device_without_its_entity_gets_the_entity_first():
    st = seed(FakeState())
    st.iota_devices[GW] = {"device_id": GW, "entity_name": URN, "entity_type": "Device",
                           "apikey": "KEY123",
                           "attributes": gateways.build_iota_attributes(TENANT, GW, "legacy")}
    with wired(st) as w:
        assert project() == "done"
        assert URN in w.orion.device_models
    upsert, iota = indexes(st.log, ("orion", "upsert"), ("iota", "get"))
    assert upsert < iota
    assert ("iota", "provision", GW) not in st.log  # equal: nothing to provision


def test_an_unreadable_agent_is_not_a_provisioned_one():
    st = seed(FakeState())
    st.iota_devices[GW] = {"device_id": GW, "entity_name": URN, "entity_type": "Device",
                           "apikey": "KEY123",
                           "attributes": gateways.build_iota_attributes(TENANT, GW, "legacy")}
    st.fail["iota_get"] = 503
    with wired(st):
        assert project() == "failed:agent_device"
    assert BEACON not in st.role_topics.get(USER, set())
    assert st.dataloggers[DL]["gateway_provisioned_at"] is None


def test_a_409_whose_document_cannot_be_read_fails():
    st = seed(FakeState())
    with wired(st) as w:
        def conflict(url, headers, **kw):
            st.fail["iota_get"] = 500
            raise w.iota.IotAgentError(409, "duplicate")
        w.iota.provision_device = conflict
        assert project() == "failed:agent_device"
    assert BEACON not in st.role_topics.get(USER, set())


def test_a_changed_mapping_is_put_not_replaced():
    st = seed(FakeState())
    with wired(st):
        assert project() == "done"
        st.iota_devices[GW]["attributes"][0]["expression"] = "uptime"
        mark = len(st.log)
        assert project() == "done"
    assert [e[:2] for e in writes(st.log[mark:])] == [("iota", "update")]  # PUT, no delete


# ── role topics: one place ──────────────────────────────────────────────────


def _old_topics(rows, apikey="KEY123"):
    return {f"/{apikey}/{d['external_id']}/attrs" for d in rows}


@pytest.mark.parametrize("sensors", [(), ("S1",), ("S1", "S2", "S3")])
@pytest.mark.parametrize("flag_value", ["", TENANT, "*"])
def test_without_the_marker_the_topics_are_exactly_todays(monkeypatch, sensors, flag_value):
    monkeypatch.setenv(gateways.FLAG, flag_value)
    st = FakeState()
    seed_datalogger(st, dl_id=DL, username=USER)  # a datalogger as it is today: no gateway columns
    for s in sensors:
        seed_device(st, DL, s)
    with wired(st):
        with dataloggers.get_db_connection_with_tenant(TENANT) as conn:
            cur = conn.cursor()
            row = dataloggers._row(cur, TENANT, DL)
            assert dataloggers.role_topics(cur, TENANT, row, "KEY123") == _old_topics(
                dataloggers._device_rows(cur, TENANT, DL))
        out = dataloggers.reconcile_datalogger_acl(TENANT, DL)
    assert out["topics"] == st.role_topics[USER] == {f"/KEY123/{s}/attrs" for s in sensors}


def test_a_gateway_id_without_the_marker_has_no_beacon():
    st = seed(FakeState(), marker=None)
    with wired(st):
        out = dataloggers.reconcile_datalogger_acl(TENANT, DL)
    assert out["topics"] == {"/KEY123/S1/attrs"}


def test_reconcile_keeps_the_beacon():
    st = seed(FakeState(), marker="then")
    with wired(st):
        out = dataloggers.reconcile_datalogger_acl(TENANT, DL)
    assert out["topics"] == st.role_topics[USER] == {"/KEY123/S1/attrs", BEACON}


def test_reconcile_with_the_flag_off_is_todays_and_reads_no_gateway_column(monkeypatch):
    monkeypatch.setenv(gateways.FLAG, "")
    st = seed(FakeState(), marker="then")
    with wired(st):
        out = dataloggers.reconcile_datalogger_acl(TENANT, DL)
    assert out["topics"] == {"/KEY123/S1/attrs"}
    assert not [e for e in st.log if e[0] == "db" and e[1] in ("gateway_read", "gateway_schema")]


def test_a_broker_failure_after_the_marker_leaves_the_next_reconcile_adding_the_beacon():
    st = seed(FakeState())
    st.fail["set_role_topics"] = True
    with wired(st):
        assert project() == "failed:acl"
        assert st.dataloggers[DL]["gateway_provisioned_at"] is not None  # committed first
        st.fail.pop("set_role_topics")
        out = dataloggers.reconcile_datalogger_acl(TENANT, DL)
    assert out["topics"] == st.role_topics[USER] == {"/KEY123/S1/attrs", BEACON}


def test_a_suspension_between_the_marker_and_the_broker_leaves_the_broker_alone():
    st = seed(FakeState())
    with wired(st) as w:
        # suspend when the second transaction starts: it must stop at the ingestion lock
        real_require = dataloggers.require_active_tenant
        calls = []

        def require(cur, tenant):
            calls.append(1)
            if len(calls) == 3:
                st.suspended.add(tenant)
            return real_require(cur, tenant)

        with patch.object(dataloggers, "require_active_tenant", require):
            assert project() == "off"
    assert st.dataloggers[DL]["gateway_provisioned_at"] is not None
    assert not [e for e in st.log if e[:2] == ("dynsec", "set_role_topics")]


# ── no route depends on migration 123 ───────────────────────────────────────


def test_before_the_migration_the_routes_and_topics_are_todays(monkeypatch):
    for value in ("", TENANT):
        monkeypatch.setenv(gateways.FLAG, value)
        st = FakeState()
        seed_datalogger(st, dl_id=DL, username=USER)
        seed_device(st, DL, "S1")
        st.no_gateway_columns = True
        with wired(st):
            out = dataloggers.reconcile_datalogger_acl(TENANT, DL)
            assert dataloggers.require_usable_datalogger(TENANT, DL)["id"] == DL
        assert out["topics"] == st.role_topics[USER] == {"/KEY123/S1/attrs"}


def test_before_the_migration_the_projection_fails_with_the_schema_and_writes_nothing():
    st = seed(FakeState(), gw=None)
    st.no_gateway_columns = True
    with wired(st):
        assert project() == "failed:schema"
    assert writes(st.log) == []
    assert not [e for e in st.log if e[0] in ("orion", "iota", "dynsec")]
    assert ("sql", "commit") not in st.log


def test_the_row_read_by_every_route_has_no_gateway_column():
    import inspect
    assert "gateway" not in inspect.getsource(dataloggers._row)


def test_rebuilding_a_lost_account_keeps_the_beacon():
    st = seed(FakeState(), marker="then")
    st.missing_clients.add(USER)
    with wired(st):
        dataloggers.rotate_datalogger(TENANT, DL)
    assert st.role_topics[USER] == {"/KEY123/S1/attrs", BEACON}
    assert ("dynsec", "create_account", USER) in st.log


def test_rebuilding_a_lost_revoked_account_keeps_the_beacon_and_stays_disabled():
    st = seed(FakeState(), marker="then", status="revoked")
    st.missing_clients.add(USER)
    with wired(st):
        dataloggers.rotate_datalogger(TENANT, DL)
    assert BEACON in st.role_topics[USER]
    assert ("dynsec", "disable", USER) in st.log


def test_the_projection_gives_the_beacon_through_role_topics():
    st = seed(FakeState())
    with wired(st), patch.object(dataloggers, "role_topics", wraps=dataloggers.role_topics) as spy:
        assert project() == "done"
    assert spy.called
    assert st.role_topics[USER] == {"/KEY123/S1/attrs", BEACON}


# ── the broker: nothing is sent when the role is already right ──────────────


def test_a_second_run_sends_no_acl_command_to_the_broker():
    import importlib.util
    import os
    spec = importlib.util.spec_from_file_location(
        "_real_mqtt_dynsec", os.path.join(os.path.dirname(__file__), "..", "..", "common", "mqtt_dynsec.py"))
    real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real)
    st = seed(FakeState())
    acls, sent = set(), []

    def transport(cmds):
        sent.extend(c["command"] for c in cmds)
        out = []
        for c in cmds:
            if c["command"] == "getRole":
                out.append({"data": {"role": {"acls": [{"acltype": real.ACL_TYPE, "topic": t} for t in acls]}}})
            else:
                if c["command"] == "addRoleACL":
                    acls.add(c["topic"])
                if c["command"] == "removeRoleACL":
                    acls.discard(c["topic"])
                out.append({})
        return out

    client = real.DynsecClient("h", 1, "u", "p", transport=transport)
    with wired(st), patch.object(dataloggers, "mqtt_dynsec", real), \
            patch.object(dataloggers, "_dynsec", lambda: client):
        assert project() == "done"
        assert acls == {"/KEY123/S1/attrs", BEACON}
        sent.clear()
        assert project() == "done"
    assert not {"addRoleACL", "removeRoleACL", "createRole"} & set(sent)
    assert "getClient" in sent


# ── PUT before DELETE, and no permission without a device ──────────────


def detailed():
    return gateways.project_gateway_detailed(TENANT, DL)


def _device(**changes):
    doc = {"device_id": GW, "entity_name": URN, "entity_type": "Device", "apikey": "KEY123",
           "attributes": gateways.build_iota_attributes(TENANT, GW, "legacy")}
    doc.update(changes)
    return doc


def test_outcomes_created_unchanged_updated_replaced():
    st = seed(FakeState())
    with wired(st):
        assert detailed() == ("done", "created")
        assert detailed() == ("done", "unchanged")
        st.iota_devices[GW]["attributes"][0]["expression"] = "uptime"
        assert detailed() == ("done", "updated")
        assert detailed() == ("done", "unchanged")
        st.iota_devices[GW]["entity_name"] = f"urn:ngsi-ld:Device:{GW}"
        assert detailed() == ("done", "replaced")
        assert detailed() == ("done", "unchanged")


@pytest.mark.parametrize("key, value", [("expression", "uptime"), ("object_id", "other"),
                                        ("type", "Text"), ("name", "x"), ("metadata", {})])
def test_one_changed_attribute_key_is_one_put_and_no_delete(key, value):
    st = seed(FakeState())
    st.iota_devices[GW] = _device()
    st.iota_devices[GW]["attributes"][0][key] = value
    with wired(st):
        assert detailed() == ("done", "updated")
    assert [e[:2] for e in writes(st.log) if e[0] == "iota"] == [("iota", "update")]
    assert ("iota", "delete", GW) not in st.log
    assert st.iota_devices[GW]["attributes"] == gateways.build_iota_attributes(TENANT, GW, "legacy")


def test_a_wrong_entity_name_is_deleted_and_provisioned_not_put():
    st = seed(FakeState())
    st.iota_devices[GW] = _device(entity_name=f"urn:ngsi-ld:Device:{GW}", attributes=[])
    with wired(st):
        assert detailed() == ("done", "replaced")
    assert [e[:2] for e in writes(st.log) if e[0] == "iota"] == [("iota", "delete"), ("iota", "provision")]


@pytest.mark.parametrize("device, fail", [
    (_device(entity_name="x", attributes=[]), "provision"),   # replace: DELETE ok, POST fails
    (None, "provision"),                                        # the device was lost, POST fails
    (_device(entity_name="x", attributes=[]), "iota_delete"),   # the wrong device cannot be removed
])
def test_a_failed_replace_withdraws_the_permission_in_the_same_call(device, fail):
    st = seed(FakeState(), marker="then")
    st.role_topics[USER] = {"/KEY123/S1/attrs", BEACON}
    if device:
        st.iota_devices[GW] = device
    st.fail[fail] = True
    with wired(st):
        assert detailed() == ("failed:agent_device", None)
    assert st.dataloggers[DL]["gateway_provisioned_at"] is None
    assert st.role_topics[USER] == {"/KEY123/S1/attrs"}
    marker_clear, topics = indexes(st.log, ("db", "gateway_marker_clear"), ("dynsec", "set_role_topics"))
    assert marker_clear < topics
    assert st.locked_network == []


def test_a_failed_put_keeps_the_marker_and_the_permission():
    st = seed(FakeState(), marker="then")
    st.role_topics[USER] = {"/KEY123/S1/attrs", BEACON}
    st.iota_devices[GW] = _device()
    st.iota_devices[GW]["attributes"][0]["expression"] = "uptime"
    st.fail["iota_update"] = True
    with wired(st):
        assert project() == "failed:agent_device"
    assert st.dataloggers[DL]["gateway_provisioned_at"] == "then"
    assert st.role_topics[USER] == {"/KEY123/S1/attrs", BEACON}


# ── "the gateway columns exist" is cached, "they are missing" is not ────────


def test_missing_columns_are_never_cached_and_present_ones_are():
    st = seed(FakeState())
    st.no_gateway_columns = True
    with wired(st):
        assert project() == "failed:schema"
        st.no_gateway_columns = False  # the migration is applied while the process runs
        assert project() == "done"
        st.log.clear()
        out = dataloggers.reconcile_datalogger_acl(TENANT, DL)
    assert BEACON in out["topics"]
    assert ("db", "gateway_schema") not in st.log  # asked once, then remembered


def test_a_sensor_urn_that_does_not_build_an_entity_is_logged_with_its_own_step(monkeypatch, caplog):
    st = seed(FakeState())
    monkeypatch.setattr(dataloggers, "_device_rows", lambda cur, tenant, dl_id: [{"device_urn": "not-a-urn"}])
    with wired(st), caplog.at_level(logging.ERROR):
        assert project() == "failed:entity"
    assert [e for e in st.log if e[:2] == ("orion", "upsert")] == []
    assert any("step=entity" in x.getMessage() for x in caplog.records)
