"""Source planning and fetch SQL shape of the `best` service (fake cursor)."""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from observations.request import BestRequest, RequestError  # noqa: E402
from observations.sources import (  # noqa: E402
    fetch_plan,
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
        if tag in ("series", "latest") and tag not in self.answers:
            self._rows = [_as_aggregated(r, tag) for r in self.answers.get("values", [])]
        else:
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
           "source_kind": "device", "feature_of_interest": FEATURE, "depth_from_cm": None, "depth_to_cm": None,
           "height_m": 2.0, "organ": None, "context_ref": None, "calibrated": False,
           "r_in_area": False, "r_dist_m": None}
    row.update(kw)
    return row


def _as_aggregated(row, tag):
    """The SQL-aggregated row a single stored raw row gives (series: n=1 fold; latest: newest)."""
    out = {k: v for k, v in row.items() if k not in ("observed_at", "value", "attribute_name")}
    out.update(oor_n=0, oor_first=None, oor_last=None)
    if tag == "series":  # one series key, one grain
        out.update(n=[1], value_sum=[row["value"]], t_first=[row["observed_at"]],
                   calibrated=[row["calibrated"]], oor_n=[0], oor_first=[None], oor_last=[None])
    else:
        out.update(observed_at=row["observed_at"], value=row["value"],
                   attribute_name=row["attribute_name"])
    return out


BOTH_PATHS = pytest.mark.parametrize("sql_path", [False, True], ids=["raw", "sql"])


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
    out = observed_tiers(cur, T, req(), prop(), "ambientAir", AREA,
                   aggregate_in_sql=False)
    assert [r.source_id for r in out[1]] == ["urn:ngsi-ld:Device:tenantA:dev1"]
    _, sql, params = cur.sql("deployments")[0]
    assert "d.tenant_id = %s" in sql and "d.valid && tstzrange" in sql
    assert T in params
    # alias attribute canonicalised before use
    attrs = cur.sql("values")[0][2][6]
    assert attrs == ["airTemperature"]
    # tier 1 reading: sub-context built from columns, None omitted, no distance
    r = out[1][0]
    assert r.sub_context == {"heightAboveGround": 2.0} and r.distance_m is None
    assert r.kind == "device" and r.deployment_id == "d1"


@BOTH_PATHS
def test_tier1_matches_channel_override_feature_and_medium(sql_path):
    d = dep(feature=None, medium="soil",
            overrides={"temperature": {"observesFeature": FEATURE,
                                       "observationContext": {"medium": "ambientAir"}}})
    cur = FakeCursor(answers(deployments=[d]))
    out = observed_tiers(cur, T, req(), prop(), "ambientAir", AREA,
                         aggregate_in_sql=sql_path)
    assert len(out[1]) == 1


@BOTH_PATHS
def test_medium_mismatch_excludes_deployment(sql_path):
    cur = FakeCursor(answers(deployments=[dep(medium="ambientAirUnderStructure")]))
    out = observed_tiers(cur, T, req(), prop(), "ambientAir", AREA,
                         aggregate_in_sql=sql_path)
    assert out == {1: [], 2: [], 3: []}
    assert not cur.sql("values")


def test_fetch_filters_segmentby_keys():
    cur = FakeCursor(answers())
    observed_tiers(cur, T, req(), prop(), "ambientAir", AREA,
                   aggregate_in_sql=False)
    _, sql, params = cur.sql("values")[0]
    for needle in ("m.tenant_id = %s", "m.attribute_name = ANY(", "m.device_id = ANY(",
                   "m.observed_property = %s", "m.medium = %s", "m.deployment_id IS NOT NULL",
                   "m.quality_flag = 'valid'"):
        assert needle in sql, needle
    assert T in params and "dev1" in params[7]
    for needle in ("e.tenant_id = %s", "e.device_id = ANY(", "e.observed_at >= %s"):
        assert needle in sql, needle  # event join is bounded


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


@BOTH_PATHS
def test_tier2_by_location_and_by_covered_feature_excluding_tier1(sql_path):
    other = "urn:ngsi-ld:AgriParcelZone:tenantA:z2"
    deps = [dep("a", "urn:x:Device:t:a", feature=FEATURE),
            dep("b", "urn:x:Device:t:b", feature=None, in_area=True),
            dep("c", "urn:x:Device:t:c", feature=other)]
    rows = [value_row("a", "a"),
            value_row("b", "b", feature_of_interest=None, r_in_area=True),
            value_row("c", "c", feature_of_interest=other)]
    cur = FakeCursor(answers(deployments=deps, values=rows, covered=[{"entity_urn": other}]))
    out = observed_tiers(cur, T, req(), prop(), "ambientAir", AREA,
                         aggregate_in_sql=sql_path)
    assert [r.deployment_id for r in out[1]] == ["a"]
    assert sorted(r.deployment_id for r in out[2]) == ["b", "c"]
    assert out[3] == []
    assert FEATURE not in cur.sql("covered")[0][2][1]  # tier-1 feature not probed


@BOTH_PATHS
def test_two_channels_one_device_split_per_row(sql_path):
    """A datalogger with channel temperature -> tree1 and temperature2 -> tree2."""
    tree1, tree2 = "urn:t:tree1", "urn:t:tree2"
    d = dep(feature=None, overrides={
        "temperature": {"observesFeature": tree1},
        "temperature2": {"observesFeature": tree2}})
    rows = [value_row(v=10.0, feature_of_interest=tree1),
            value_row(v=20.0, feature_of_interest=tree2)]
    at = [{"attr": "temperature"}, {"attr": "temperature2"}]
    cur = FakeCursor(answers(deployments=[d], values=rows, attrs=at))
    out = observed_tiers(cur, T, req(feature=tree1), prop(), "ambientAir", AREA,
                         aggregate_in_sql=sql_path)
    assert [r.value for r in out[1]] == [10.0]
    assert out[2] == [] and out[3] == []
    cur = FakeCursor(answers(deployments=[d], values=rows, covered=[{"entity_urn": tree2}], attrs=at))
    out = observed_tiers(cur, T, req(feature=None, geometry=AREA), prop(), "ambientAir", AREA,
                         aggregate_in_sql=sql_path)
    assert [r.value for r in out[2]] == [20.0] and out[1] == []


@BOTH_PATHS
def test_equipment_medium_without_feature_observes_own_device(sql_path):
    urn = "urn:ngsi-ld:Device:tenantA:eq"
    d = dep("e", urn, feature=None, medium="equipment")
    cur = FakeCursor(answers(deployments=[d], values=[value_row("e", "eq", feature_of_interest=urn)]))
    out = observed_tiers(cur, T, req(feature=urn), prop(allowed_media=["equipment"]),
                         "equipment", AREA,
                         aggregate_in_sql=sql_path)
    assert len(out[1]) == 1


@BOTH_PATHS
def test_tier3_carries_distance_and_mobile_uses_reading_location(sql_path):
    deps = [dep("n", "urn:x:Device:t:n", feature=None, dist=500.0),
            dep("m", "urn:x:Device:t:m", feature=None, mobility="mobile")]
    nf = {"feature_of_interest": None}
    rows = [value_row("n", "n", r_dist_m=500.0, **nf),
            value_row("m", "m", r_in_area=True, **nf),
            value_row("m", "m", r_dist_m=300.0, **nf),
            value_row("m", "m", r_dist_m=5000.0, **nf)]
    cur = FakeCursor(answers(deployments=deps, values=rows))
    out = observed_tiers(cur, T, req(), prop(), "ambientAir", AREA,
                         aggregate_in_sql=sql_path)
    # same instant for all rows: the SQL path orders ties by source; the raw path keeps row order
    assert sorted((r.deployment_id, r.distance_m) for r in out[3]) == [("m", 300.0), ("n", 500.0)]
    assert [r.deployment_id for r in out[2]] == ["m"]
    assert all(r.distance_m is None for r in out[2])


@BOTH_PATHS
def test_tier3_excluded_when_radius_zero(sql_path):
    for radius in (0, None):
        deps = [dep("n", "urn:x:Device:t:n", feature=None, dist=10.0, in_area=False)]
        rows = [value_row("n", "n", r_dist_m=10.0, feature_of_interest=None)]
        cur = FakeCursor(answers(deployments=deps, values=rows))
        out = observed_tiers(cur, T, req(), prop(nearby_radius_m=radius), "ambientAir", AREA,
                         aggregate_in_sql=sql_path)
        assert out == {1: [], 2: [], 3: []}


@BOTH_PATHS
def test_no_area_only_tier1(sql_path):
    deps = [dep("a", feature=FEATURE), dep("b", "urn:x:Device:t:b", feature=None, in_area=True)]
    cur = FakeCursor(answers(deployments=deps, values=[value_row("a"), value_row(
        "b", "b", feature_of_interest=None, r_in_area=True)]))
    out = observed_tiers(cur, T, req(), prop(), "ambientAir", None,
                         aggregate_in_sql=sql_path)
    assert len(out[1]) == 1 and out[2] == [] and out[3] == []


@BOTH_PATHS
def test_request_sub_context_filter(sql_path):
    rows = [value_row(height_m=2.0), value_row(height_m=5.0)]
    cur = FakeCursor(answers(values=rows))
    out = observed_tiers(cur, T, req(sub_context={"height": 2.0}), prop(), "ambientAir", AREA,
                         aggregate_in_sql=sql_path)
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


def test_regional_tier5_query_and_readings():
    rows = [{"observed_at": datetime(2026, 11, 1, 2, tzinfo=UTC), "value": 9.0, "distance_m": 4200.0}]
    cur = FakeCursor({"regional": rows, "model": []})
    out = model_tiers(cur, T, req(), prop(), None, (-1.6, 42.8))  # no parcel: tier 5 still runs
    r = out[5][0]
    assert (r.source_id, r.kind, r.distance_m, r.value) == ("regional_proxy", "model", 4200.0, 9.0)
    _, sql, params = cur.sql("regional")[0]
    assert "NOT LIKE '%%:parcel-%%'" in sql and "NOT LIKE '%%-daily'" in sql and "ST_DWithin" in sql
    assert "->>'airTemperature'" in sql and "x.tenant_id = %s" in sql and "e.tenant_id = %s" in sql
    assert 10000.0 in params  # nearby_radius_m 1000 * 10
    assert not cur.sql("model")


def test_regional_radius_capped_and_skipped_without_point_or_radius():
    cur = FakeCursor({"regional": []})
    model_tiers(cur, T, req(), prop(nearby_radius_m=20000), None, (0.0, 0.0))
    assert 50000.0 in cur.sql("regional")[0][2]
    cur = FakeCursor({"regional": []})
    model_tiers(cur, T, req(), prop(), None, None)
    model_tiers(cur, T, req(), prop(nearby_radius_m=0), None, (0.0, 0.0))
    assert not cur.calls


def test_preselection_drops_far_deployment_with_uncovered_feature():
    far = "urn:ngsi-ld:AgriParcelZone:tenantA:far"
    near = "urn:ngsi-ld:AgriParcelZone:tenantA:near"
    deps = [dep("f", "urn:x:Device:t:far", feature=far),
            dep("n", "urn:x:Device:t:near", feature=near)]
    cur = FakeCursor(answers(deployments=deps, values=[], covered=[{"entity_urn": near}]))
    observed_tiers(cur, T, req(feature=None, geometry=AREA), prop(), "ambientAir", AREA,
                   aggregate_in_sql=False)
    assert cur.sql("values")[0][2][7] == ["near"]
    assert cur.sql("covered")[0][2][1] == [far, near]


# ------------------------------------------------- SQL-aggregated fetch (8.10)

def test_fetch_plan_dispatch():
    assert fetch_plan(req(), prop()) == "series"
    assert fetch_plan(req(resolution="P1D"), prop(temporal_semantics="rate")) == "series"
    assert fetch_plan(req(resolution="PT15M"), prop(temporal_semantics=None)) == "series"
    assert fetch_plan(req(resolution="raw"), prop()) == "raw"
    assert fetch_plan(req(), prop(temporal_semantics="cumulative")) == "raw"
    assert fetch_plan(req(mode="latest", resolution="raw"), prop()) == "latest"
    assert fetch_plan(req(mode="latest", resolution="raw"),
                      prop(temporal_semantics="cumulative")) == "raw"


@pytest.mark.parametrize("resolution,grain", [
    ("PT1H", "time_bucket(interval '1 hour', m.observed_at)"),
    ("PT15M", "time_bucket(interval '15 minutes', m.observed_at)"),
    ("P1D", "width_bucket(m.observed_at, b.s)"),
])
def test_series_sql_filters_grain_and_params(resolution, grain):
    cur = FakeCursor(answers())
    observed_tiers(cur, T, req(resolution=resolution, tz="Europe/Madrid"),
                   prop(valid_min=-40.0, valid_max=None), "ambientAir", AREA)
    assert not cur.sql("values") and not cur.sql("prior")
    (_, sql, params), = cur.sql("series")
    assert grain in sql
    for needle in ("m.tenant_id = %(tenant)s", "m.attribute_name = ANY(%(attrs)s)",
                   "m.device_id = ANY(%(devices)s)", "m.observed_property = %(prop)s",
                   "m.medium = %(medium)s", "m.quality_flag = 'valid'",
                   "e.device_id = ANY(%(devices)s)", "count(*) FILTER (WHERE NOT ok) AS oor_n",
                   "sum(value) FILTER (WHERE ok) AS value_sum",
                   "bool_and(calibrated) FILTER (WHERE ok)", "min(observed_at) FILTER (WHERE ok)",
                   "m.entity_id LIKE ANY(%(entity_like)s)",
                   "array_agg(value_sum ORDER BY grain) AS value_sum"):
        assert needle in sql, needle
    assert params["tenant"] == T and params["devices"] == ["dev1"]
    assert params["attrs"] == ["airTemperature"] and params["deps"] == ["d1"]
    assert params["lo"] == -40.0 and params["hi"] == float("inf")
    assert params["entity_like"] == ["%:dev1:airTemperature"]
    if resolution == "P1D":  # local midnights of Madrid
        assert params["starts"][0] == datetime(2026, 10, 31, 23, tzinfo=UTC)
    else:
        assert params["starts"] == []


def test_series_rows_become_folded_readings_ordered_by_first_instant():
    t = datetime(2026, 11, 1, 3, tzinfo=UTC)
    base = {k: v for k, v in value_row().items()
            if k not in ("observed_at", "value", "attribute_name")}
    rows = [dict(base, n=[3, 0], value_sum=[30.0, None], t_first=[t.replace(minute=20), None],
                 calibrated=[True, None], oor_n=[1, 2],
                 oor_first=[t.replace(minute=5), t.replace(hour=4)],
                 oor_last=[t.replace(minute=5), t.replace(hour=4, minute=9)]),
            dict(base, n=[1], value_sum=[7.0], t_first=[t.replace(minute=1)], calibrated=[False],
                 oor_n=[0], oor_first=[None], oor_last=[None])]
    cur = FakeCursor(answers(series=rows))
    out = observed_tiers(cur, T, req(), prop(), "ambientAir", AREA)
    r1, r2, r3 = out[1]
    assert (r1.t, r1.n, r1.value_sum, r1.value) == (t.replace(minute=1), 1, 7.0, 7.0)
    assert (r2.n, r2.value, r2.calibrated, r2.oor_n) == (3, 10.0, True, 1)
    assert r2.oor_first == r2.oor_last == t.replace(minute=5)
    assert (r3.n, r3.value_sum, r3.t, r3.oor_n, r3.calibrated) == (0, None, t.replace(hour=4), 2, False)


def test_latest_sql_lateral_per_series_key_and_readings():
    cur = FakeCursor(answers())
    q = req(mode="latest", resolution="raw")
    observed_tiers(cur, T, q, prop(valid_min=-40.0, valid_max=60.0), "ambientAir", AREA)
    (_, sql, params), = cur.sql("latest")
    assert not cur.sql("values") and not cur.sql("series")
    assert "LEFT JOIN LATERAL" in sql and "LIMIT 1" in sql
    assert "ORDER BY m.observed_at DESC, m.value DESC" in sql
    assert "m.value >= %(lo)s AND m.value <= %(hi)s" in sql
    for col in ("feature_of_interest", "depth_from_cm", "depth_to_cm", "height_m", "organ",
                "context_ref", "source_kind"):
        assert f"m.{col} IS NOT DISTINCT FROM k.{col}" in sql, col
    assert "m.attribute_name = k.attribute_name" in sql
    assert "m.location::bytea IS NOT DISTINCT FROM k.loc" in sql
    assert (params["w0"], params["w1"]) == (q.start, q.end)
    assert (params["lo"], params["hi"]) == (-40.0, 60.0)

    t = datetime(2026, 11, 1, 3, tzinfo=UTC)
    base = {k: v for k, v in value_row().items() if k not in ("observed_at", "value")}
    rows = [dict(base, observed_at=t, value=5.0, calibrated=True, oor_n=2,
                 oor_first=t.replace(minute=7), oor_last=t.replace(minute=9)),
            dict(base, height_m=5.0, observed_at=None, value=None, calibrated=None, oor_n=1,
                 oor_first=t.replace(minute=8), oor_last=t.replace(minute=8))]
    cur = FakeCursor(answers(latest=rows))
    out = observed_tiers(cur, T, q, prop(), "ambientAir", AREA)
    a, b = out[1]
    assert (a.t, a.value, a.n, a.calibrated, a.oor_n) == (t, 5.0, 1, True, 2)
    assert (b.n, b.oor_n, b.t, b.sub_context) == (0, 1, t.replace(minute=8), {"heightAboveGround": 5.0})
