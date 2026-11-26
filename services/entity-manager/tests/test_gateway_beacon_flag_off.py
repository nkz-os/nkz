"""Task 5: with the flag off (or the tenant not listed), the list and config answers and their side
effects are the ones recorded on base 7aaee92 (RECORD_BASE=1 on a clean export of that commit)."""
import json
import os
import re

import pytest

from tests.telemetry_fakes import (AUTH, PROFILE, TENANT, FakeState, client, seed_datalogger,
                                   seed_device, wired)

import gateways  # noqa: E402

DL = "11111111-1111-1111-1111-111111111111"
DL2 = "22222222-2222-2222-2222-222222222222"
GW = "gw-0123456789ab"
RECORDING = os.path.join(os.path.dirname(__file__), "recordings", "gateway_beacon_flag_off_base.json")
_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _state(gateway_columns):
    st = FakeState()
    seed_datalogger(st, dl_id=DL, username="dl_aaaaaaaaaaaaaaaa")
    seed_datalogger(st, dl_id=DL2, username="dl_bbbbbbbbbbbbbbbb", name="Second")
    seed_device(st, DL, "S1")
    seed_device(st, DL, "S2")
    st.profiles["p"] = dict(PROFILE)
    if gateway_columns:  # the migration is applied and a gateway exists: still invisible with the flag off
        st.dataloggers[DL].update(gateway_external_id=GW, gateway_provisioned_at="now")
    return st


def _mask(value):
    if isinstance(value, dict):
        return {k: _mask(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v) for v in value]
    return _ID.sub("<id>", value) if isinstance(value, str) else value


def _cases():
    return [(k, g) for k in ("list", "config", "list_empty") for g in (False, True)]


@pytest.mark.parametrize("flag", ["off", "other-tenant"])
@pytest.mark.parametrize("kind,gateway_columns", _cases())
def test_list_and_config_are_the_ones_recorded_on_base(kind, gateway_columns, flag, monkeypatch):
    monkeypatch.delenv(gateways.FLAG, raising=False)
    if flag == "other-tenant":
        monkeypatch.setenv(gateways.FLAG, "someone-else")
    st = FakeState() if kind == "list_empty" else _state(gateway_columns)
    with wired(st) as w:
        for urn in ("S1", "S2"):
            full = f"urn:ngsi-ld:Device:{TENANT}:{urn}"
            w.orion.entities[full] = {"id": full, "type": "Device", "name": {"type": "Property", "value": urn},
                                      "profileCode": {"type": "Property", "value": "p"}}
        c = client()
        path = "/api/sensors/dataloggers" if kind != "config" else f"/api/sensors/dataloggers/{DL}/config"
        resp = c.get(path, headers=AUTH)
        got = {"status": resp.status_code, "type": resp.content_type,
               "body": resp.get_data(as_text=True), "log": _mask(list(st.log)),
               "orion_calls": len(w.orion.posted_urls)}
    key = f"{kind}:{'cols' if gateway_columns else 'nocols'}"
    got["body"] = _ID.sub("<id>", got["body"])
    if os.environ.get("RECORD_BASE"):
        data = json.load(open(RECORDING)) if os.path.exists(RECORDING) else {}
        data[key] = got
        json.dump(data, open(RECORDING, "w"), indent=1, sort_keys=True)
        return
    assert got == json.load(open(RECORDING))[key]
