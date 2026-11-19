"""Source planning and fetch SQL shape of the `best` service (fake cursor)."""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from observations.request import BestRequest, RequestError  # noqa: E402
from observations.sources import (  # noqa: E402
    model_semantics,
    model_tiers,
    observed_tiers,
    parcel_urn_of,
    resolve_medium,
)

UTC = timezone.utc
T = "tenantA"
FEATURE = "urn:ngsi-ld:AgriParcelZone:tenantA:z1"
AREA = {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]}


def prop(**kw):
    base = {"name": "airTemperature", "default_medium": "ambientAir",
            "allowed_media": ["ambientAir", "enclosedAir"], "temporal_semantics": "instant",
            "nearby_radius_m": 1000, "has_model": True, "sub_context_tolerance": {}}
    base.update(kw)
    return base


def req(**kw):
    base = dict(feature=FEATURE, geometry=None, property="airTemperature",
                start=datetime(2026, 11, 1, tzinfo=UTC), end=datetime(2026, 11, 2, tzinfo=UTC),
                mode="series", resolution="PT1H", tz="UTC", medium=None)
    base.update(kw)
    return BestRequest(**base)


class FakeCursor:
    """Records executes; answers by the `-- obs:<tag>` marker of the statement."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []
        self._rows = []

    def execute(self, sql, params=None):
        tag = sql.split("\n", 1)[0].replace("-- obs:", "").strip()
        self.calls.append((tag, sql, params))
        self._rows = list(self.answers.get(tag, []))

    def fetchall(self):
        return self._rows

    def sql(self, tag):
        return [c for c in self.calls if c[0] == tag]


def dep(id_="d1", urn="urn:ngsi-ld:Device:tenantA:dev1", feature=FEATURE, medium="ambientAir",
        mobility="fixed", in_area=False, dist=None, overrides=None):
    return {"id": id_, "device_urn": urn, "observes_feature": feature,
            "observation_context": {"medium": medium}, "channel_overrides": overrides or {},
            "mobility": mobility, "in_area": in_area, "dist_m": dist}


def value_row(dep_id="d1", dev="dev1", t=None, v=1.0, **kw):
    row = {"observed_at": t or datetime(2026, 11, 1, 3, tzinfo=UTC), "device_id": dev,
           "attribute_name": "airTemperature", "value": v, "deployment_id": dep_id,
           "source_kind": "device", "depth_from_cm": None, "depth_to_cm": None,
           "height_m": 2.0, "organ": None, "context_ref": None, "calibrated": False,
           "r_in_area": False, "r_dist_m": None}
    row.update(kw)
    return row


# ---------------------------------------------------------------- medium

def test_medium_default_from_catalogue():
    assert resolve_medium(req(), prop(), None) == "ambientAir"


def test_medium_from_greenhouse_feature():
    ent = {"type": "AgriGreenhouse"}
    assert resolve_medium(req(), prop(), ent) == "enclosedAir"


def test_medium_explicit_wins_over_greenhouse():
    assert resolve_medium(req(medium="ambientAir"), prop(), {"type": "Greenhouse"}) == "ambientAir"


def test_medium_not_allowed_422():
    with pytest.raises(RequestError) as e:
        resolve_medium(req(medium="soil"), prop(), None)
    assert e.value.status == 422
    with pytest.raises(RequestError):
        resolve_medium(req(), prop(allowed_media=["enclosedAir"]), None)


# ------------------------------------------------------------- tiers 1-3

def answers(**kw):
    base = {"attrs": [{"attr": "temperature"}], "deployments": [dep()],
            "values": [value_row()]}
    base.update(kw)
    return base


def test_tier1_sql_filters_feature_medium_tenant():
    cur = FakeCursor(answers())
    out = observed_tiers(cur, T, req(), prop(), "ambientAir", AREA)
    assert [r.source_id for r in out[1]] == ["urn:ngsi-ld:Device:tenantA:dev1"]
    _, sql, params = cur.sql("deployments")[0]
    assert "d.tenant_id = %s" in sql and "d.valid && tstzrange" in sql
    assert T in params
    # alias attribute canonicalised before use
    attrs = cur.sql("values")[0][2][2]
    assert attrs == ["airTemperature"]
    # tier 1 reading: sub-context built from columns, None omitted, no distance
    r = out[1][0]
    assert r.sub_context == {"heightAboveGround": 2.0} and r.distance_m is None
    assert r.kind == "device" and r.deployment_id == "d1"


def test_tier1_matches_channel_override_feature_and_medium():
    d = dep(feature=None, medium="soil",
            overrides={"temperature": {"observesFeature": FEATURE,
                                       "observationContext": {"medium": "ambientAir"}}})
    cur = FakeCursor(answers(deployments=[d]))
    out = observed_tiers(cur, T, req(), prop(), "ambientAir", AREA)
    assert len(out[1]) == 1


def test_medium_mismatch_excludes_deployment():
    cur = FakeCursor(answers(deployments=[dep(medium="ambientAirUnderStructure")]))
    out = observed_tiers(cur, T, req(), prop(), "ambientAir", AREA)
    assert out == {1: [], 2: [], 3: []}
    assert not cur.sql("values")


def test_fetch_filters_segmentby_keys():
    cur = FakeCursor(answers())
    observed_tiers(cur, T, req(), prop(), "ambientAir", AREA)
    _, sql, params = cur.sql("values")[0]
    for needle in ("m.tenant_id = %s", "m.attribute_name = ANY(", "m.device_id = ANY(",
                   "m.observed_property = %s", "m.medium = %s", "m.deployment_id IS NOT NULL",
                   "m.quality_flag = 'valid'"):
        assert needle in sql, needle
    assert T in params and "dev1" in params[3]


def test_window_and_cumulative_prior_query():
    cur = FakeCursor(answers(attrs=[{"attr": "rainfall"}],
                             values=[value_row(attribute_name="rainfall", v=5.0)],
                             prior=[value_row(attribute_name="rainfall", v=4.0,
                                              t=datetime(2026, 10, 31, 23, tzinfo=UTC))],
                             counter_max=[{"device_id": "dev1", "profile_tenant": None,
                                           "attr": "rainfall", "counter_max": "100"}]))
    p = prop(name="precipitation", temporal_semantics="cumulative")
    out = observed_tiers(cur, T, req(property="precipitation"), p, "ambientAir", AREA)
    vals = cur.sql("values")[0][2]
    assert vals[-2:] == (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 2, tzinfo=UTC))
    prior = cur.sql("prior")[0]
    assert "DISTINCT ON (m.device_id, m.attribute_name)" in prior[1]
    assert prior[2][-1] == datetime(2026, 11, 1, tzinfo=UTC)
    assert len(cur.sql("counter_max")) == 1
    assert [r.value for r in out[1]] == [4.0, 5.0]
    assert all(r.counter_max == 100.0 for r in out[1])


def test_instant_property_has_no_prior_or_counter_queries():
    cur = FakeCursor(answers())
    observed_tiers(cur, T, req(), prop(), "ambientAir", AREA)
    assert not cur.sql("prior") and not cur.sql("counter_max")


def test_tier2_by_location_and_by_covered_feature_excluding_tier1():
    other = "urn:ngsi-ld:AgriParcelZone:tenantA:z2"
    deps = [dep("a", "urn:x:Device:t:a", feature=FEATURE),
            dep("b", "urn:x:Device:t:b", feature=None, in_area=True),
            dep("c", "urn:x:Device:t:c", feature=other)]
    rows = [value_row("a", "a"), value_row("b", "b"), value_row("c", "c")]
    cur = FakeCursor(answers(deployments=deps, values=rows, covered=[{"entity_urn": other}]))
    out = observed_tiers(cur, T, req(), prop(), "ambientAir", AREA)
    assert [r.deployment_id for r in out[1]] == ["a"]
    assert sorted(r.deployment_id for r in out[2]) == ["b", "c"]
    assert out[3] == []
    assert FEATURE not in cur.sql("covered")[0][2][1]  # tier-1 feature not probed


def test_tier3_carries_distance_and_mobile_uses_reading_location():
    deps = [dep("n", "urn:x:Device:t:n", feature=None, dist=500.0),
            dep("m", "urn:x:Device:t:m", feature=None, mobility="mobile")]
    rows = [value_row("n", "n"),
            value_row("m", "m", r_in_area=True),
            value_row("m", "m", r_dist_m=300.0),
            value_row("m", "m", r_dist_m=5000.0)]
    cur = FakeCursor(answers(deployments=deps, values=rows))
    out = observed_tiers(cur, T, req(), prop(), "ambientAir", AREA)
    assert [(r.deployment_id, r.distance_m) for r in out[3]] == [("n", 500.0), ("m", 300.0)]
    assert [r.deployment_id for r in out[2]] == ["m"]


def test_tier3_excluded_when_radius_zero():
    for radius in (0, None):
        deps = [dep("n", "urn:x:Device:t:n", feature=None, dist=10.0)]
        cur = FakeCursor(answers(deployments=deps))
        out = observed_tiers(cur, T, req(), prop(nearby_radius_m=radius), "ambientAir", AREA)
        assert out == {1: [], 2: [], 3: []}


def test_no_area_only_tier1():
    deps = [dep("a", feature=FEATURE), dep("b", "urn:x:Device:t:b", feature=None, in_area=True)]
    cur = FakeCursor(answers(deployments=deps, values=[value_row("a")]))
    out = observed_tiers(cur, T, req(), prop(), "ambientAir", None)
    assert len(out[1]) == 1 and out[2] == [] and out[3] == []


def test_request_sub_context_filter():
    rows = [value_row(height_m=2.0), value_row(height_m=5.0)]
    cur = FakeCursor(answers(values=rows))
    out = observed_tiers(cur, T, req(sub_context={"height": 2.0}), prop(), "ambientAir", AREA)
    assert [r.sub_context["heightAboveGround"] for r in out[1]] == [2.0]


# ------------------------------------------------------------- tiers 4-5

def test_model_tiers_only_when_has_model():
    cur = FakeCursor({"model": [{"observed_at": datetime(2026, 11, 1, 1, tzinfo=UTC), "value": 12.5}]})
    parcel = "urn:ngsi-ld:AgriParcel:tenantA:p1"
    assert model_tiers(cur, T, req(), prop(has_model=False), parcel, None) == {4: [], 5: []}
    assert model_tiers(cur, T, req(tiers="observed"), prop(), parcel, None) == {4: [], 5: []}
    assert model_tiers(cur, T, req(), prop(), None, None) == {4: [], 5: []}
    assert not cur.calls
    out = model_tiers(cur, T, req(), prop(), parcel, None)
    r = out[4][0]
    assert (r.source_id, r.kind, r.value, r.deployment_id) == ("parcel_weather", "model", 12.5, None)
    assert out[5] == []
    _, sql, params = cur.sql("model")[0]
    assert "e.tenant_id = %s" in sql and "payload->'measurements'->>'airTemperature'" in sql
    assert params[2] == "%:parcel-p1"


def test_model_keys_daily_twin_and_humidity_key():
    cur = FakeCursor({"model": []})
    parcel = "urn:ngsi-ld:AgriParcel:tenantA:p_1"
    model_tiers(cur, T, req(resolution="P1D", property="relativeHumidity"),
                prop(name="relativeHumidity"), parcel, None)
    _, sql, params = cur.sql("model")[0]
    assert "->>'humidity'" in sql
    assert params[2] == "%:parcel-p\\_1-daily"


def test_model_property_without_key_skips():
    cur = FakeCursor({"model": []})
    out = model_tiers(cur, T, req(property="soilMoisture"), prop(name="soilMoisture"),
                      "urn:x:AgriParcel:t:p", None)
    assert out == {4: [], 5: []} and not cur.calls


def test_precipitation_semantics_marker_and_parcel_urn():
    assert model_semantics(prop(name="precipitation")) == "amount"
    assert model_semantics(prop()) is None
    assert parcel_urn_of("urn:p", {"type": "AgriParcel"}) == "urn:p"
    assert parcel_urn_of("urn:z", {"type": "Zone", "refAgriParcel":
                                   {"type": "Relationship", "object": "urn:p"}}) == "urn:p"
    assert parcel_urn_of("urn:z", {"type": "Zone"}) is None
