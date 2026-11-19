"""Observation fields on sensor registration, the deployment routes and the @context terms."""
import json
import pathlib
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.telemetry_fakes import (AUTH, PARCEL, PROFILE, TENANT, FakeOrion, FakeState, _resp,
                                   client, seed_datalogger, wired)

DL = "11111111-1111-1111-1111-111111111111"
URN = f"urn:ngsi-ld:Device:{TENANT}:S1"
FEATURE = "urn:ngsi-ld:AgriParcel:p2"
OTHER = "urn:ngsi-ld:AgriParcel:elsewhere"
T0 = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
CTX = {"medium": "ambientAir", "heightAboveGround": 2}
OVERRIDES = {"airTemperature": {"observationContext": {"medium": "soil", "depthFrom": 0, "depthTo": 10}}}

BASE = {"external_id": "S1", "name": "Sensor 1", "profile": "p",
        "location": {"lat": 42.0, "lon": -2.0}, "controlledAsset": [PARCEL], "datalogger_id": DL}


class ObsOrion(FakeOrion):
    """FakeOrion plus entity GET by id and attribute append/delete."""

    def __init__(self, state):
        super().__init__(state)
        self.gets = []
        self.attr_posts = []
        self.attr_deletes = []
        self.attr_status = 204

    def get(self, url, **kw):
        m = re.search(r"/entities/([^?]+)$", url)
        if m:
            self.gets.append(m.group(1))
            ent = self.entities.get(m.group(1))
            return _resp(200, ent) if ent else _resp(404, {})
        return super().get(url, **kw)

    def post(self, url, **kw):
        if url.endswith("/attrs"):
            eid = url.rsplit("/entities/", 1)[-1][:-len("/attrs")]
            self.state.log.append(("orion", "attrs", eid))
            self.attr_posts.append((eid, kw.get("json"), kw.get("headers")))
            return _resp(self.attr_status)
        return super().post(url, **kw)

    def delete(self, url, **kw):
        if "/attrs/" in url:
            eid, attr = url.rsplit("/entities/", 1)[-1].split("/attrs/")
            self.attr_deletes.append((eid, attr))
            return _resp(404)
        return super().delete(url, **kw)


def _dep(dep_id="dep-1", valid_from=T0, valid_to=None, feature=None, ctx=None, overrides=None,
         mobility="fixed", loc=(42.0, -2.0)):
    return {"id": dep_id, "device_urn": URN, "valid_from": valid_from, "valid_to": valid_to,
            "observesFeature": feature, "observationContext": ctx or {},
            "channelOverrides": overrides or {}, "mobility": mobility,
            "location": {"lat": loc[0], "lon": loc[1]} if loc else None}


@contextmanager
def env(current=None):
    """wired() + an Orion that answers entity reads + deployment repository fakes."""
    import blueprints.sensors as sensors
    import dataloggers
    st = FakeState()
    seed_datalogger(st, dl_id=DL)
    st.profiles["p"] = dict(PROFILE)
    with wired(st) as w:
        orion = ObsOrion(st)
        orion.entities[FEATURE] = {"id": FEATURE}
        orion.entities[URN] = {"id": URN, "profileCode": {"type": "Property", "value": "p"}}
        opened = []

        def open_dep(tenant, urn, spec, valid_from, actor, reason):
            st.log.append(("dep", "open"))
            opened.append(SimpleNamespace(tenant=tenant, urn=urn, spec=spec,
                                          valid_from=valid_from, actor=actor, reason=reason))
            return _dep("dep-new", valid_from, None, spec.observes_feature,
                        spec.observation_context, spec.channel_overrides, spec.mobility,
                        (spec.location[1], spec.location[0]) if spec.location else None)

        def delete_deps(tenant, urn):
            st.log.append(("dep", "delete", urn))

        d = SimpleNamespace(
            open=MagicMock(side_effect=open_dep), delete=MagicMock(side_effect=delete_deps),
            revert=MagicMock(), correct=MagicMock(), opened=opened,
            current=MagicMock(return_value=current), get=MagicMock(),
            listing=MagicMock(return_value=[]))
        with patch.object(sensors, "requests", orion), patch.object(dataloggers, "requests", orion), \
                patch.object(sensors.deployments, "open_deployment", d.open), \
                patch.object(sensors.deployments, "delete_deployments", d.delete), \
                patch.object(sensors.deployments, "revert_open", d.revert), \
                patch.object(sensors.deployments, "correct_deployment", d.correct), \
                patch.object(sensors.deployments, "current_deployment", d.current), \
                patch.object(sensors.deployments, "get_deployment", d.get), \
                patch.object(sensors.deployments, "list_deployments", d.listing):
            yield SimpleNamespace(state=st, orion=orion, dep=d)


def _register(c, **over):
    body = {k: v for k, v in dict(BASE, **over).items() if v is not None}
    return c.post("/api/sensors/register", content_type="application/json",
                  headers=AUTH, data=json.dumps(body))


def _send(c, method, path, body=None):
    kw = {"headers": AUTH}
    if body is not None:
        kw.update(content_type="application/json", data=json.dumps(body))
    return getattr(c, method)(path, **kw)


# ---- registration ------------------------------------------------------------

def test_register_with_observation_fields_writes_device_attrs_and_first_deployment():
    with env() as e:
        r = _register(client(), observesFeature=FEATURE, observationContext=CTX,
                      channelOverrides=OVERRIDES, mobility="mobile",
                      validFrom="2026-03-01T10:00:00+02:00")
    assert r.status_code == 201, r.get_json()
    ent = e.orion.entities[URN]
    assert ent["observesFeature"] == {"type": "Relationship", "object": FEATURE}
    assert ent["observationContext"] == {"type": "Property", "value": CTX}
    assert ent["channelOverrides"] == {"type": "Property", "value": OVERRIDES}
    assert ent["mobility"] == {"type": "Property", "value": "mobile"}
    assert ent["deploymentId"] == {"type": "Property", "value": "dep-new"}
    assert ent["installedAt"]["value"] == "2026-03-01T08:00:00Z"
    call = e.dep.opened[0]
    assert call.valid_from == T0 and call.urn == URN
    assert call.spec.location == (-2.0, 42.0)
    assert call.spec.observes_feature == FEATURE and call.spec.mobility == "mobile"
    assert FEATURE in e.orion.gets


def test_register_default_valid_from_is_the_installed_at_instant():
    with env() as e:
        r = _register(client())
    assert r.status_code == 201
    installed = e.orion.entities[URN]["installedAt"]["value"]
    assert installed.endswith("Z")
    assert datetime.fromisoformat(installed.replace("Z", "+00:00")) == e.dep.opened[0].valid_from


def test_register_rejects_future_valid_from():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with env() as e:
        r = _register(client(), validFrom=future)
    assert r.status_code == 400
    assert ("orion", "post") not in e.state.log and not e.dep.opened


@pytest.mark.parametrize("bad", ["2026-03-01T08:00:00", "yesterday", 5])
def test_register_rejects_valid_from_without_offset(bad):
    with env() as e:
        r = _register(client(), validFrom=bad)
    assert r.status_code == 400 and not e.dep.opened


def test_register_rejects_observes_feature_of_other_tenant():
    # The tenant-scoped read finds nothing for another tenant's entity: 404.
    with env() as e:
        r = _register(client(), observesFeature=OTHER)
    assert r.status_code == 400
    assert OTHER in r.get_json()["error"]
    assert OTHER in e.orion.gets
    assert not e.dep.opened and ("orion", "post") not in e.state.log


def test_register_rejects_missing_observes_feature_entity():
    with env() as e:
        r = _register(client(), observesFeature="urn:ngsi-ld:AgriParcel:nope")
    assert r.status_code == 400
    assert "urn:ngsi-ld:AgriParcel:nope" in r.get_json()["error"]
    assert not e.dep.opened


def test_register_checks_structure_enclosure_and_override_urns():
    ctx = {"medium": "enclosedAir", "enclosure": "urn:ngsi-ld:Greenhouse:gone"}
    with env() as e:
        r = _register(client(), observationContext=ctx)
        assert r.status_code == 400 and "urn:ngsi-ld:Greenhouse:gone" in r.get_json()["error"]
        ov = {"airTemperature": {"observesFeature": "urn:ngsi-ld:Plant:gone"}}
        r = _register(client(), channelOverrides=ov)
        assert r.status_code == 400 and "urn:ngsi-ld:Plant:gone" in r.get_json()["error"]
    assert not e.dep.opened


def test_register_orion_read_error_is_502():
    with env() as e:
        e.orion.get = MagicMock(return_value=_resp(500, {}))
        r = _register(client(), observesFeature=FEATURE)
    assert r.status_code == 502 and not e.dep.opened


def test_register_rejects_override_for_attribute_not_in_profile():
    with env() as e:
        r = _register(client(), channelOverrides={"soilMoisture": {
            "observationContext": {"medium": "water"}}})
    assert r.status_code == 400 and "soilMoisture" in r.get_json()["error"]
    assert not e.dep.opened


def test_register_rejects_invalid_context():
    from common.observation_context import validate_observation_context
    with pytest.raises(ValueError) as exc:
        validate_observation_context({"medium": "moon"})
    with env() as e:
        r = _register(client(), observationContext={"medium": "moon"})
    assert r.status_code == 400
    assert r.get_json()["error"] == str(exc.value)
    assert not e.dep.opened


@pytest.mark.parametrize("over", [{"mobility": "flying"}, {"observationContext": []}])
def test_register_rejects_bad_mobility_or_context_shape(over):
    with env():
        assert _register(client(), **over).status_code == 400


def test_registration_failure_after_deployment_removes_it():
    with env() as e:
        e.state.fail["set_role_topics"] = True
        r = _register(client())
    assert r.status_code == 502
    log = e.state.log
    assert ("dep", "delete", URN) in log
    # Undo runs in reverse: the entity goes before the deployment row.
    assert log.index(("orion", "delete", URN)) < log.index(("dep", "delete", URN))
    assert log.index(("dep", "open")) < log.index(("orion", "post"))
    assert ("orion", "delete", URN) in log


def test_orion_create_failure_removes_the_deployment():
    with env() as e:
        e.state.fail["orion_post"] = True
        r = _register(client())
    assert r.status_code == 502
    assert ("dep", "delete", URN) in e.state.log


def test_register_does_not_open_deployment_over_an_existing_device():
    with env() as e:
        e.dep.listing.return_value = [_dep()]
        r = _register(client())
    assert r.status_code == 409
    assert not e.dep.opened and ("dep", "delete", URN) not in e.state.log


def test_register_without_new_fields_still_works():
    with env() as e:
        r = _register(client())
    assert r.status_code == 201, r.get_json()
    spec = e.dep.opened[0].spec
    assert (spec.observes_feature, spec.observation_context, spec.channel_overrides,
            spec.mobility, spec.location) == (None, {}, {}, "fixed", (-2.0, 42.0))
    ent = e.orion.entities[URN]
    assert "observesFeature" not in ent and "observationContext" not in ent
    assert "channelOverrides" not in ent
    assert ent["mobility"]["value"] == "fixed" and ent["deploymentId"]["value"] == "dep-new"


def test_equipment_medium_without_feature_writes_no_self_reference():
    with env() as e:
        r = _register(client(), observationContext={"medium": "equipment"})
    assert r.status_code == 201
    assert "observesFeature" not in e.orion.entities[URN]


# ---- deployment routes -------------------------------------------------------

def test_list_deployments_route():
    with env() as e:
        e.dep.listing.return_value = [_dep(), _dep("dep-0", T0 - timedelta(days=9), T0)]
        r = _send(client(), "get", f"/api/sensors/devices/{URN}/deployments")
    assert r.status_code == 200
    body = r.get_json()["deployments"]
    assert [d["id"] for d in body] == ["dep-1", "dep-0"]
    assert body[0]["valid_from"] == "2026-03-01T08:00:00+00:00" and body[0]["valid_to"] is None


def test_move_opens_new_deployment_and_mirrors_current_to_orion():
    cur = _dep(feature=None, ctx=CTX)
    with env(current=cur) as e:
        r = _send(client(), "post", f"/api/sensors/devices/{URN}/deployments",
                  {"validFrom": "2026-04-01T00:00:00Z", "observesFeature": FEATURE,
                   "location": {"lat": 43.0, "lon": -1.0}, "reason": "moved to the field"})
    assert r.status_code == 201, r.get_json()
    call = e.dep.opened[0]
    assert call.spec.observes_feature == FEATURE
    assert call.spec.observation_context == CTX            # copied from the current deployment
    assert call.spec.location == (-1.0, 43.0)
    assert call.reason == "moved to the field"
    (eid, attrs, headers), = e.orion.attr_posts
    assert eid == URN
    assert attrs["observesFeature"] == {"type": "Relationship", "object": FEATURE}
    assert attrs["deploymentId"] == {"type": "Property", "value": "dep-new"}
    assert attrs["mobility"] == {"type": "Property", "value": "fixed"}
    assert "@context" not in attrs
    assert all(a.get("value", a.get("object")) is not None for a in attrs.values())
    assert headers["Content-Type"] == "application/json"
    assert ("dep", "open") in e.state.log
    assert r.get_json()["id"] == "dep-new"


def test_move_without_feature_deletes_the_relationship_attribute():
    with env(current=_dep(feature=FEATURE)) as e:
        r = _send(client(), "post", f"/api/sensors/devices/{URN}/deployments",
                  {"observesFeature": None, "reason": "no longer observing"})
    assert r.status_code == 201, r.get_json()
    assert (URN, "observesFeature") in e.orion.attr_deletes
    assert "observesFeature" not in e.orion.attr_posts[0][1]


def test_move_rolls_back_when_the_orion_mirror_fails():
    with env(current=_dep()) as e:
        e.orion.attr_status = 500
        r = _send(client(), "post", f"/api/sensors/devices/{URN}/deployments",
                  {"reason": "moved", "location": {"lat": 1, "lon": 1}})
    assert r.status_code == 502
    e.dep.revert.assert_called_once()
    assert e.dep.revert.call_args[0][:2] == (TENANT, "dep-new")


@pytest.mark.parametrize("body", [
    {"observesFeature": FEATURE},                                  # no reason
    {"reason": "  "},
    {"reason": "x", "mobility": "flying"},
    {"reason": "x", "observationContext": {"medium": "moon"}},
    {"reason": "x", "channelOverrides": {"soilMoisture": {"observationContext": {"medium": "water"}}}},
    {"reason": "x", "observesFeature": "urn:ngsi-ld:AgriParcel:nope"},
    {"reason": "x", "validFrom": "2999-01-01T00:00:00Z"},
    {"reason": "x", "mobility": "fixed", "location": None},
])
def test_move_validation_errors_are_400_and_write_nothing(body):
    with env(current=_dep()) as e:
        r = _send(client(), "post", f"/api/sensors/devices/{URN}/deployments", body)
    assert r.status_code == 400, r.get_json()
    assert not e.dep.opened and e.orion.attr_posts == []


def test_move_of_unknown_device_is_404():
    with env() as e:
        del e.orion.entities[URN]
        r = _send(client(), "post", f"/api/sensors/devices/{URN}/deployments", {"reason": "x"})
    assert r.status_code == 404 and not e.dep.opened


def test_correct_current_deployment_mirrors_to_orion():
    before = _dep(feature=None)
    after = _dep(feature=FEATURE)
    with env(current=after) as e:
        e.dep.get.return_value = before
        e.dep.correct.return_value = after
        r = _send(client(), "put", "/api/sensors/deployments/dep-1",
                  {"observesFeature": FEATURE, "reason": "wrong feature"})
    assert r.status_code == 200, r.get_json()
    args = e.dep.correct.call_args[0]
    assert args[:3] == (TENANT, "dep-1", {"observesFeature": FEATURE})
    assert args[4] == "wrong feature"
    assert e.orion.attr_posts[0][1]["observesFeature"]["object"] == FEATURE


def test_correct_past_deployment_does_not_patch_orion():
    past = _dep("dep-0", T0 - timedelta(days=9), T0)
    with env(current=_dep()) as e:
        e.dep.get.return_value = past
        e.dep.correct.return_value = dict(past, observationContext=CTX)
        r = _send(client(), "put", "/api/sensors/deployments/dep-0",
                  {"observationContext": CTX, "reason": "typo"})
    assert r.status_code == 200, r.get_json()
    assert e.orion.attr_posts == [] and e.orion.attr_deletes == []
    assert e.dep.correct.call_args[0][2] == {"observation_context": CTX}


def test_correct_maps_api_names_to_repository_names():
    with env() as e:
        past = _dep("dep-0", T0 - timedelta(days=9), T0)
        e.dep.get.return_value = past
        e.dep.correct.return_value = past
        r = _send(client(), "put", "/api/sensors/deployments/dep-0",
                  {"validFrom": "2026-02-01T00:00:00Z", "validTo": "2026-03-01T08:00:00Z",
                   "channelOverrides": OVERRIDES, "location": {"lat": 1, "lon": 2}, "reason": "fix"})
    assert r.status_code == 200, r.get_json()
    changes = e.dep.correct.call_args[0][2]
    assert changes["valid_from"] == datetime(2026, 2, 1, tzinfo=timezone.utc)
    assert changes["valid_to"] == T0
    assert changes["channel_overrides"] == OVERRIDES
    assert changes["location"] == (2.0, 1.0)


def test_correct_rolls_the_row_back_when_the_orion_mirror_fails():
    before, after = _dep(feature=None), _dep(feature=FEATURE)
    with env(current=after) as e:
        e.orion.attr_status = 500
        e.dep.get.return_value = before
        e.dep.correct.return_value = after
        r = _send(client(), "put", "/api/sensors/deployments/dep-1",
                  {"observesFeature": FEATURE, "reason": "r"})
    assert r.status_code == 502
    assert e.dep.correct.call_count == 2
    assert e.dep.correct.call_args[0][2]["observesFeature"] is None


@pytest.mark.parametrize("body", [
    {"observesFeature": FEATURE},                                   # no reason
    {"reason": "x"},                                                # no changes
    {"reason": "x", "mobility": "mobile"},                          # not correctable
    {"reason": "x", "bogus": 1},
    {"reason": "x", "location": None},                              # would leave fixed without location
    {"reason": "x", "observationContext": {"medium": "soil"}},
    {"reason": "x", "channelOverrides": {"soilMoisture": {"observationContext": {"medium": "water"}}}},
    {"reason": "x", "observesFeature": "urn:ngsi-ld:AgriParcel:nope"},
])
def test_correct_validation_errors_are_400(body):
    with env(current=_dep()) as e:
        e.dep.get.return_value = _dep()
        r = _send(client(), "put", "/api/sensors/deployments/dep-1", body)
    assert r.status_code == 400, r.get_json()
    e.dep.correct.assert_not_called()


def test_correct_unknown_deployment_is_404():
    with env() as e:
        e.dep.get.return_value = None
        r = _send(client(), "put", "/api/sensors/deployments/nope", {"reason": "x", "observesFeature": None})
    assert r.status_code == 404


# ---- @context ----------------------------------------------------------------

def test_context_declares_new_terms():
    root = pathlib.Path(__file__).resolve().parents[3]
    ctx = json.loads((root / "config" / "ngsi-ld-context.json").read_text())["@context"][1]
    assert ctx["observesFeature"] == "http://www.w3.org/ns/sosa/hasFeatureOfInterest"
    assert not isinstance(ctx["observesFeature"], dict) or "@type" not in ctx["observesFeature"]
    for term in ("observationContext", "channelOverrides", "mobility", "deploymentId"):
        assert ctx[term] == f"nkz:{term}"
