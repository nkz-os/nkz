"""The list carries each datalogger's gateway status, the config its beacon."""
import json

import pytest

from tests.telemetry_fakes import (AUTH, PROFILE, TENANT, FakeState, client, seed_datalogger,
                                   seed_device, wired)

import dataloggers  # noqa: E402
import gateways  # noqa: E402

DL = "11111111-1111-1111-1111-111111111111"
DL2 = "22222222-2222-2222-2222-222222222222"
DL3 = "33333333-3333-3333-3333-333333333333"
GW, GW2 = "gw-0123456789ab", "gw-ba9876543210"
URN, URN2 = (f"urn:ngsi-ld:Device:{TENANT}:{g}" for g in (GW, GW2))
LIST, CONFIG = "/api/sensors/dataloggers", f"/api/sensors/dataloggers/{DL}/config"


@pytest.fixture(autouse=True)
def flag(monkeypatch):
    monkeypatch.setenv(gateways.FLAG, TENANT)


def state(*, marker=True, second=True):
    st = FakeState()
    seed_datalogger(st, dl_id=DL, username="dl_aaaaaaaaaaaaaaaa")
    seed_device(st, DL, "S1")
    st.profiles["p"] = dict(PROFILE)
    st.dataloggers[DL].update(gateway_external_id=GW, gateway_provisioned_at="now" if marker else None)
    if second:
        seed_datalogger(st, dl_id=DL2, username="dl_bbbbbbbbbbbbbbbb", name="Second")
        st.dataloggers[DL2].update(gateway_external_id=GW2, gateway_provisioned_at="now")
    seed_datalogger(st, dl_id=DL3, username="dl_cccccccccccccccc", name="No id yet")  # id not assigned
    return st


def device(urn, **attrs):
    ent = {"id": urn, "type": "Device"}
    ent.update({k: {"type": "Property", "value": v} for k, v in attrs.items()})
    return ent


def get(path, st, setup=None):
    with wired(st) as w:
        for s in ("S1",):
            u = f"urn:ngsi-ld:Device:{TENANT}:{s}"
            w.orion.entities[u] = device(u, name=s, profileCode="p")
        w.orion.gets = []
        real = w.orion.get

        def spy(url, **kw):
            w.orion.gets.append(url)
            return real(url, **kw)
        w.orion.get = spy
        if setup:
            setup(w)
        resp = client().get(path, headers=AUTH)
    return w, resp


def by_id(resp):
    return {x["id"]: x for x in resp.get_json()}


# ── list ────────────────────────────────────────────────────────────────────

def test_a_seen_gateway_has_last_seen_rssi_version_and_urn():
    def setup(w):
        w.orion.entities[URN] = device(URN, dateLastValueReported="2026-11-26T10:00:00Z", rssi=-71,
                                       softwareVersion="1.2.3")
    w, resp = get(LIST, state(), setup)
    assert resp.status_code == 200
    assert by_id(resp)[DL]["gateway"] == {"urn": URN, "lastSeen": "2026-11-26T10:00:00Z",
                                          "rssi": -71, "softwareVersion": "1.2.3"}


def test_a_gateway_never_seen_has_nulls_and_one_read_serves_all_gateways():
    def setup(w):
        w.orion.entities[URN2] = device(URN2, dateLastValueReported="2026-11-26T10:00:00Z")
    w, resp = get(LIST, state(), setup)
    items = by_id(resp)
    assert items[DL]["gateway"] == {"urn": URN, "lastSeen": None, "rssi": None, "softwareVersion": None}
    assert items[DL2]["gateway"]["lastSeen"] == "2026-11-26T10:00:00Z"
    reads = [u for u in w.orion.gets if "dateLastValueReported" in u]
    assert len(reads) == 1 and URN in reads[0] and URN2 in reads[0]
    assert "attrs=dateLastValueReported,rssi,softwareVersion" in reads[0]
    ids = reads[0].split("id=")[1].split("&")[0].split(",")
    assert sorted(ids) == sorted([URN, URN2])


def test_more_than_50_gateways_take_two_reads_and_are_merged():
    st = FakeState()
    urns = {}
    for i in range(51):
        dl = f"00000000-0000-0000-0000-{i:012d}"
        gw = f"gw-{i:012x}"
        seed_datalogger(st, dl_id=dl, username=f"dl_{i:016x}", name=f"L{i}")
        st.dataloggers[dl].update(gateway_external_id=gw, gateway_provisioned_at="now")
        urns[dl] = f"urn:ngsi-ld:Device:{TENANT}:{gw}"

    def setup(w):
        for i, u in enumerate(urns.values()):
            w.orion.entities[u] = device(u, rssi=-i)
    w, resp = get(LIST, st, setup)
    reads = [u for u in w.orion.gets if "dateLastValueReported" in u]
    assert len(reads) == 2
    sizes = sorted(len(r.split("id=")[1].split("&")[0].split(",")) for r in reads)
    assert sizes == [1, 50]
    got = {i["id"]: i["gateway"] for i in resp.get_json()}
    assert all(got[dl]["urn"] == u for dl, u in urns.items())
    assert got["00000000-0000-0000-0000-000000000050"]["rssi"] == -50   # in the second chunk
    assert got["00000000-0000-0000-0000-000000000000"]["rssi"] == 0


def test_a_datalogger_without_gateway_id_has_null_and_is_not_asked_for():
    w, resp = get(LIST, state())
    assert by_id(resp)[DL3]["gateway"] is None
    assert not any("gw-" in u and DL3 in u for u in w.orion.gets)


def test_orion_datetime_form_is_read():
    def setup(w):
        w.orion.entities[URN] = {"id": URN, "type": "Device", "dateLastValueReported": {
            "type": "Property", "value": {"@type": "DateTime", "@value": "2026-11-26T10:00:00.250Z"}}}
    _w, resp = get(LIST, state(), setup)
    assert by_id(resp)[DL]["gateway"]["lastSeen"] == "2026-11-26T10:00:00.250Z"


@pytest.mark.parametrize("bad", [{"rssi": "-70"}, {"rssi": True}, {"softwareVersion": 3},
                                 {"dateLastValueReported": 5}, {"softwareVersion": ""}])
def test_values_of_the_wrong_type_read_as_absent(bad):
    def setup(w):
        w.orion.entities[URN] = device(URN, **bad)
    _w, resp = get(LIST, state(), setup)
    assert by_id(resp)[DL]["gateway"] == {"urn": URN, "lastSeen": None, "rssi": None, "softwareVersion": None}


@pytest.mark.parametrize("how", ["500", "raises"])
def test_orion_failing_nulls_every_gateway_and_the_list_still_answers(how):
    def setup(w):
        def boom(url, **kw):
            if how == "raises":
                raise w.orion.exceptions.ConnectionError("down")
            from tests.telemetry_fakes import _resp
            return _resp(500)
        w.orion.get = boom
    _w, resp = get(LIST, state(), setup)
    assert resp.status_code == 200
    items = by_id(resp)
    assert all(i["gateway"] is None for i in items.values())
    assert items[DL]["devices"] == [{"externalId": "S1", "deviceUrn": f"urn:ngsi-ld:Device:{TENANT}:S1"}]


def test_a_schema_without_the_columns_lists_without_gateways():
    st = state()
    st.no_gateway_columns = True
    w, resp = get(LIST, st)
    assert resp.status_code == 200 and all(i["gateway"] is None for i in resp.get_json())
    assert not any("gw-" in u for u in w.orion.gets)


def test_a_failed_gateway_read_does_not_fail_the_list():
    st = state()
    import tests.telemetry_fakes as fakes
    real = fakes._Cursor.execute

    def execute(self, sql, params=()):
        if "SELECT id, gateway_external_id" in " ".join(sql.split()):
            raise RuntimeError("boom")
        return real(self, sql, params)
    fakes._Cursor.execute = execute
    try:
        _w, resp = get(LIST, st)
    finally:
        fakes._Cursor.execute = real
    assert resp.status_code == 200 and all(i["gateway"] is None for i in resp.get_json())


def test_no_gateway_at_all_makes_no_orion_read_for_status():
    st = FakeState()
    seed_datalogger(st, dl_id=DL)
    st.dataloggers[DL].update(gateway_external_id=None)
    w, resp = get(LIST, st)
    assert resp.get_json()[0]["gateway"] is None
    assert not any("dateLastValueReported" in u for u in w.orion.gets)


# ── config ──────────────────────────────────────────────────────────────────

def test_a_provisioned_gateway_adds_the_beacon_topic_and_interval():
    _w, resp = get(CONFIG, state())
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["beacon"] == {"topic": f"/KEY123/{GW}/attrs", "intervalSeconds": 300}
    assert body["mqtt"]["apikey"] == "KEY123"


def test_no_marker_no_beacon():
    _w, resp = get(CONFIG, state(marker=False))
    assert "beacon" not in resp.get_json()


def test_no_gateway_id_no_beacon():
    st = state()
    st.dataloggers[DL]["gateway_external_id"] = None
    _w, resp = get(CONFIG, st)
    assert "beacon" not in resp.get_json()


def test_a_schema_without_the_columns_gives_the_config_without_beacon():
    st = state()
    st.no_gateway_columns = True
    _w, resp = get(CONFIG, st)
    assert resp.status_code == 200 and "beacon" not in resp.get_json()


def test_the_beacon_changes_nothing_else_in_the_config():
    _w, on = get(CONFIG, state())
    st = state(marker=False)
    _w, off = get(CONFIG, st)
    body = on.get_json()
    del body["beacon"]
    assert body == off.get_json()


# ── flag off: nothing new is read ───────────────────────────────────────────

def test_with_the_flag_off_no_gateway_sql_and_no_extra_orion_read(monkeypatch):
    monkeypatch.delenv(gateways.FLAG)
    st = state()
    w, resp = get(LIST, st)
    assert "gateway" not in resp.get_json()[0]
    assert ("db", "gateway_read") not in st.log and ("db", "gateway_schema") not in st.log
    assert w.orion.gets == []
    w, resp = get(CONFIG, st)
    assert "beacon" not in resp.get_json()
    assert ("db", "gateway_read") not in st.log
