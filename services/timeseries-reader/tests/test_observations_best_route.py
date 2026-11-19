"""`best` endpoint: contract 2.1 shape, rulings (transaction order, model gating, fallback)."""
import hashlib
import hmac
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")

_here = os.path.dirname(__file__)
sys.path.insert(0, os.path.normpath(os.path.join(_here, "..")))

if "auth_middleware" not in sys.modules:
    _auth_stub = MagicMock()
    _auth_stub.require_auth = lambda f: f
    _auth_stub.inject_fiware_headers = lambda headers, tenant=None: dict(headers or {})
    sys.modules["auth_middleware"] = _auth_stub

import psycopg2.errors  # noqa: E402
import pytest  # noqa: E402
from flask import Flask  # noqa: E402

import app  # noqa: E402
from observations import best as best_mod  # noqa: E402
from observations import routes  # noqa: E402
from observations.engine import Reading  # noqa: E402
from observations.request import RequestError, parse_best  # noqa: E402

UTC = timezone.utc


class FakeCursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def close(self):
        pass


class FakeConn:
    def __init__(self, cursor):
        self.cur = cursor
        self.closed = False

    def cursor(self, *a, **k):
        return self.cur

    def close(self):
        self.closed = True

    def commit(self):
        pass

    def rollback(self):
        pass


def prop(name, tenant=None, unit="°C", **kw):
    row = {
        "id": "id", "tenant_id": tenant, "name": name, "domain": "environment",
        "unit": unit, "default_medium": "ambientAir", "allowed_media": ["ambientAir"],
        "temporal_semantics": "instant", "valid_min": -40.0, "valid_max": 60.0,
        "max_rate_per_hour": None, "fusion": "median", "nearby_radius_m": 2000.0,
        "max_age_s": 10800, "min_coverage": 0.83, "sub_context_tolerance": {},
        "has_model": True, "deprecated_at": None,
        "created_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
    }
    row.update(kw)
    return row


T0 = datetime(2026, 11, 1, tzinfo=UTC)
HEADERS = {"X-Tenant-ID": "testtenant"}
PARCEL = "urn:ngsi-ld:AgriParcel:testtenant:p1"
NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)

BODY = {"feature": PARCEL, "property": "airTemperature",
        "from": "2026-11-01T00:00:00Z", "to": "2026-11-01T03:00:00Z", "resolution": "PT1H"}


def rd(h, v, src="urn:ngsi-ld:Device:testtenant:S1", kind="device", cal=False, sub=None, dist=None,
       minutes=0):
    return Reading(source_id=src, deployment_id="dep-1", t=T0 + timedelta(hours=h, minutes=minutes),
                   value=v, sub_context=sub or {}, distance_m=dist, kind=kind, calibrated=cal)


def model_rd(h, v, src="parcel_weather"):
    return Reading(source_id=src, deployment_id=None, t=T0 + timedelta(hours=h), value=v,
                   sub_context={}, distance_m=None, kind="model", calibrated=False)


class Env:
    """Patches everything around `best`; records call order."""

    def __init__(self, observed=None, model=None, p=None, orion=("ok", None), geo=None):
        self.events = []
        self.observed = observed or {1: [], 2: [], 3: []}
        self.model = model or {4: [], 5: []}
        self.prop = p or prop("airTemperature")
        self.orion = orion
        self.geo = geo
        self.cur = FakeCursor()
        self.conn = FakeConn(self.cur)
        self.conn.commit = lambda: self.events.append("commit")
        self.conn.rollback = lambda: self.events.append("rollback")
        self.observed_calls = []
        self.model_calls = []

    def __enter__(self):
        def observed(cur, tenant, req, prp, medium, area):
            self.events.append("observed_tiers")
            self.observed_calls.append(medium)
            if callable(self.observed):
                return self.observed(medium)
            return self.observed

        def model(cur, tenant, req, prp, parcel, point):
            self.events.append("model_tiers")
            self.model_calls.append((parcel, point))
            return self.model

        def geometry(cur, tenant, urn, fetch=None):
            self.events.append("geometry_of")
            return self.geo

        def get_prop(cur, tenant, name):
            self.events.append("get_property")
            if self.prop is None:
                raise RequestError(422, "unknown or deprecated property: x")
            return self.prop

        def orion(tenant, urn):
            self.events.append("orion")
            return self.orion

        self.patches = [
            patch.object(best_mod, "observed_tiers", observed),
            patch.object(best_mod, "model_tiers", model),
            patch.object(best_mod, "geometry_of", geometry),
            patch.object(best_mod, "get_property", get_prop),
            patch.object(best_mod, "fetch_orion_entity_status", orion),
        ]
        for p_ in self.patches:
            p_.start()
        return self

    def __exit__(self, *a):
        for p_ in self.patches:
            p_.stop()

    def run(self, body=None, **kw):
        return best_mod.best(self.conn, "testtenant", parse_best(dict(body or BODY)), now=NOW, **kw)


def entity(t="AgriParcel"):
    return ("ok", {"id": PARCEL, "type": t})


def http(env, method="POST", body=None, query=None):
    with env, patch("app.get_db_connection", return_value=env.conn):
        with app.app.test_client() as c:
            if method == "POST":
                return c.post("/api/observations/best", json=body or BODY, headers=HEADERS)
            return c.get("/api/observations/best", query_string=query, headers=HEADERS)


# ------------------------------------------------------------------ shape

def test_series_response_shape_matches_contract():
    env = Env(observed={1: [rd(0, 10.0, cal=True), rd(1, 11.0)], 2: [], 3: []},
              model={4: [model_rd(2, 12.0)], 5: []}, orion=entity())
    with env:
        out = env.run()
    assert set(out) == {"feature", "property", "unitCode", "unitSymbol", "medium", "mode",
                        "resolution", "tz", "from", "to", "groups", "tiersUsed", "warnings"}
    assert out["feature"] == PARCEL and out["unitCode"] == "CEL" and out["unitSymbol"] == "°C"
    assert out["medium"] == "ambientAir" and out["mode"] == "series" and out["tz"] == "UTC"
    assert out["from"] == "2026-11-01T00:00:00Z" and out["to"] == "2026-11-01T03:00:00Z"
    assert out["tiersUsed"] == [1, 4] and out["warnings"] == []
    (g,) = out["groups"]
    assert g["subContext"] == {}
    pts = g["points"]
    assert [p["t"] for p in pts] == ["2026-11-01T00:00:00Z", "2026-11-01T01:00:00Z",
                                     "2026-11-01T02:00:00Z"]
    assert all(p["t"].endswith("Z") for p in pts)
    p0 = pts[0]
    assert set(p0) == {"t", "value", "tier", "fidelity", "confidence", "calibrated",
                       "mediumFallback", "coverage", "sources"}
    assert p0["value"] == 10.0 and p0["tier"] == 1 and p0["fidelity"] == "iot_sensor"
    assert p0["confidence"] == "high" and p0["calibrated"] is True and p0["mediumFallback"] is False
    assert p0["coverage"] == 1.0
    assert p0["sources"] == [{"kind": "device", "id": "urn:ngsi-ld:Device:testtenant:S1",
                              "deploymentId": "dep-1", "distanceM": None, "n": 1}]
    assert pts[1]["calibrated"] is False
    assert pts[2]["tier"] == 4 and pts[2]["fidelity"] == "parcel_weather"
    assert pts[2]["confidence"] == "medium" and pts[2]["mediumFallback"] is False
    assert pts[2]["sources"] == [{"kind": "model", "id": "parcel_weather"}]


def test_unavailable_bucket_shape():
    with Env(observed={1: [rd(0, 10.0)], 2: [], 3: []}, orion=entity()) as env:
        out = env.run()
    p = out["groups"][0]["points"][1]
    assert p == {"t": "2026-11-01T01:00:00Z", "value": None, "tier": None,
                 "fidelity": "unavailable", "confidence": "low", "calibrated": False,
                 "mediumFallback": False, "coverage": 0.0, "sources": []}
    assert out["tiersUsed"] == [1]


def test_nothing_at_all_gives_one_empty_group_of_unavailable_points():
    with Env(orion=entity()) as env:
        out = env.run()
    (g,) = out["groups"]
    assert g["subContext"] == {} and len(g["points"]) == 3
    assert all(p["fidelity"] == "unavailable" for p in g["points"]) and out["tiersUsed"] == []


def test_unknown_unit_gives_null_code_and_warning():
    with Env(p=prop("x", unit="weird"), orion=entity()) as env:
        out = env.run()
    assert out["unitCode"] is None and out["unitSymbol"] == "weird"
    assert "unknown_unit:weird" in out["warnings"]


def test_subcontext_groups_never_fused_and_model_merged_into_each():
    obs = {1: [rd(0, 10.0, sub={"depthFrom": 0.0, "depthTo": 10.0}),
               rd(0, 20.0, src="S2", sub={"depthFrom": 20.0, "depthTo": 40.0})], 2: [], 3: []}
    with Env(observed=obs, model={4: [model_rd(1, 5.0)], 5: []}, orion=entity()) as env:
        out = env.run()
    assert [g["subContext"] for g in out["groups"]] == [
        {"depthFrom": 0.0, "depthTo": 10.0}, {"depthFrom": 20.0, "depthTo": 40.0}]
    for g in out["groups"]:
        assert g["points"][1]["tier"] == 4 and g["points"][1]["value"] == 5.0
    assert out["groups"][0]["points"][0]["value"] == 10.0
    assert out["groups"][1]["points"][0]["value"] == 20.0


def test_height_is_reported_as_heightM():
    with Env(observed={1: [rd(0, 1.0, sub={"heightAboveGround": 2.0})], 2: [], 3: []},
             orion=entity()) as env:
        out = env.run()
    assert out["groups"][0]["subContext"] == {"heightM": 2.0}


def test_model_only_group_has_empty_subcontext():
    with Env(model={4: [model_rd(0, 5.0)], 5: []}, orion=entity()) as env:
        out = env.run()
    assert [g["subContext"] for g in out["groups"]] == [{}]
    assert out["tiersUsed"] == [4]


# --------------------------------------------------------------- fidelity

def test_fidelity_manual_import_mixed_and_regional():
    kinds = {"manual": "manual_observation", "import": "manual_observation"}
    for kind, want in kinds.items():
        with Env(observed={1: [rd(0, 1.0, kind=kind)], 2: [], 3: []}, orion=entity()) as env:
            p = env.run()["groups"][0]["points"][0]
        assert p["fidelity"] == want and p["confidence"] == "high"
    mixed = {1: [rd(0, 1.0, kind="manual"), rd(0, 3.0, src="S2", kind="device")], 2: [], 3: []}
    with Env(observed=mixed, orion=entity()) as env:
        p = env.run()["groups"][0]["points"][0]
    assert p["fidelity"] == "iot_sensor" and len(p["sources"]) == 2
    with Env(model={4: [], 5: [model_rd(0, 1.0, "regional_proxy")]}, orion=entity()) as env:
        p = env.run()["groups"][0]["points"][0]
    assert (p["tier"], p["fidelity"], p["confidence"]) == (5, "regional_proxy", "low")
    assert p["sources"] == [{"kind": "model", "id": "regional_proxy"}]


def test_precipitation_model_tiers_use_amount_semantics():
    p = prop("precipitation", unit="mm", temporal_semantics="cumulative", valid_max=500.0)
    body = dict(BODY, property="precipitation", resolution="P1D", to="2026-11-02T00:00:00Z")
    model = {4: [model_rd(h, 1.0) for h in range(24)], 5: []}
    with Env(p=p, model=model, orion=entity()) as env:
        out = env.run(body)
    pt = out["groups"][0]["points"][0]
    assert pt["tier"] == 4 and pt["value"] == 24.0  # amount: summed, no counter rule


# ----------------------------------------------------------------- rulings

def test_transaction_order_geometry_commit_then_reads():
    with Env(observed={1: [rd(0, 1.0)], 2: [], 3: []}, orion=entity()) as env:
        env.run()
    ev = env.events
    assert ev.index("geometry_of") < ev.index("commit") < ev.index("observed_tiers")
    assert ev.index("commit") < ev.index("model_tiers")
    assert ev[-1] == "rollback"
    # one tenant_cursor per transaction: set_config + statement_timeout each
    sets = [s for s, _ in env.cur.executed if "set_config" in s]
    assert len(sets) == 2


def test_404_unknown_feature_and_503_orion_error_are_distinct():
    with Env(orion=("not_found", None)) as env:
        with pytest.raises(RequestError) as e:
            env.run()
    assert (e.value.status, e.value.message) == (404, "feature not found")
    assert "observed_tiers" not in env.events and "commit" in env.events
    with Env(orion=("error", None)) as env:
        with pytest.raises(RequestError) as e:
            env.run()
    assert e.value.status == 503 and "observed_tiers" not in env.events
    r = http(Env(orion=("not_found", None)))
    assert r.status_code == 404 and r.get_json() == {"error": "feature not found"}
    assert http(Env(orion=("error", None))).status_code == 503


def test_422_unknown_property():
    env = Env(orion=entity())
    env.prop = None
    r = http(env)
    assert r.status_code == 422 and "error" in r.get_json()
    assert "orion" not in env.events
    assert env.conn.closed


def test_422_medium_not_allowed():
    r = http(Env(orion=entity()), body=dict(BODY, medium="soil"))
    assert r.status_code == 422


def test_503_on_query_canceled():
    env = Env(orion=entity())
    env.observed = lambda medium: (_ for _ in ()).throw(psycopg2.errors.QueryCanceled("t"))
    r = http(env)
    assert r.status_code == 503 and r.get_json() == {"error": "observation query timed out"}
    assert env.conn.closed and env.events[-1] == "rollback"


def test_400_request_error():
    with app.app.test_client() as c:
        r = c.post("/api/observations/best", json={"property": "x"}, headers=HEADERS)
        assert r.status_code == 400 and "error" in r.get_json()
        r = c.post("/api/observations/best", data="[]", content_type="application/json",
                   headers=HEADERS)
        assert r.status_code == 400


def test_tiers_observed_never_calls_model_tiers():
    env = Env(model={4: [model_rd(0, 1.0)], 5: []}, orion=entity())
    with env:
        out = env.run(dict(BODY, tiers="observed"))
    assert env.model_calls == [] and out["tiersUsed"] == []


def test_model_tiers_gated_by_medium():
    p = prop("soilMoisture", allowed_media=["soil", "ambientAir"], default_medium="soil")
    model = {4: [model_rd(0, 5.0)], 5: []}
    with Env(p=p, model=model, orion=entity()) as env:
        out = env.run()
    assert env.model_calls == [] and out["medium"] == "soil" and out["tiersUsed"] == []
    # allowMediumFallback opens them, flagged as fallback
    with Env(p=p, model=model, orion=entity()) as env:
        out = env.run(dict(BODY, allowMediumFallback=True))
    assert len(env.model_calls) == 1
    pt = out["groups"][0]["points"][0]
    assert pt["tier"] == 4 and pt["mediumFallback"] is True
    # ambientAir: included, not flagged
    with Env(model=model, orion=entity()) as env:
        pt = env.run()["groups"][0]["points"][0]
    assert pt["tier"] == 4 and pt["mediumFallback"] is False
    # property without a model
    with Env(p=prop("airTemperature", has_model=False), model=model, orion=entity()) as env:
        env.run()
    assert env.model_calls == []


def test_model_tiers_receive_parcel_and_lonlat_point():
    geo = {"type": "Polygon", "coordinates": [[[-2.0, 42.0], [-1.0, 42.0], [-1.0, 43.0],
                                                [-2.0, 43.0], [-2.0, 42.0]]]}
    with Env(orion=entity(), geo=geo) as env:
        env.run()
    assert env.model_calls == [(PARCEL, (-1.5, 42.5))]


def test_medium_fallback_flagged():
    p = prop("soilTemperature", allowed_media=["soil", "ambientAir"], default_medium="soil")

    def observed(medium):
        if medium == "ambientAir":
            return {1: [rd(1, 7.0)], 2: [], 3: []}
        return {1: [rd(0, 3.0)], 2: [], 3: []}

    with Env(p=p, observed=observed, orion=entity()) as env:
        out = env.run(dict(BODY, allowMediumFallback=True))
    pts = out["groups"][0]["points"]
    assert env.observed_calls == ["soil", "ambientAir"]
    assert pts[0]["value"] == 3.0 and pts[0]["mediumFallback"] is False
    assert pts[1]["value"] == 7.0 and pts[1]["mediumFallback"] is True
    assert pts[2]["fidelity"] == "unavailable"
    # without the flag: no second query, no fallback
    with Env(p=p, observed=observed, orion=entity()) as env:
        out = env.run()
    assert env.observed_calls == ["soil"]
    assert out["groups"][0]["points"][1]["fidelity"] == "unavailable"


def test_geometry_request_skips_orion():
    body = {"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]},
            "property": "airTemperature", "from": BODY["from"], "to": BODY["to"]}
    with Env() as env:
        out = env.run(body)
    assert "orion" not in env.events and out["feature"] is None


# ------------------------------------------------------------------ latest

def test_latest_respects_max_age():
    p = prop("airTemperature", max_age_s=3600)
    seen = {}

    def observed(medium):
        return {1: [Reading("S1", "d", NOW - timedelta(minutes=50), 4.0, {}, None, "device", False),
                    Reading("S1", "d", NOW - timedelta(minutes=10), 5.0, {}, None, "device", False)],
                2: [], 3: []}

    env = Env(p=p, observed=observed, orion=entity())
    with env:
        orig = best_mod.observed_tiers

        def spy(cur, tenant, req, prp, medium, area):
            seen["start"], seen["end"], seen["res"] = req.start, req.end, req.resolution
            return orig(cur, tenant, req, prp, medium, area)

        with patch.object(best_mod, "observed_tiers", spy):
            out = env.run(dict(BODY, mode="latest"))
    assert seen == {"start": NOW - timedelta(hours=1), "end": NOW, "res": "raw"}
    (g,) = out["groups"]
    assert len(g["points"]) == 1
    pt = g["points"][0]
    assert pt["value"] == 5.0 and pt["t"] == "2026-11-10T11:50:00Z" and pt["tier"] == 1
    assert out["mode"] == "latest" and out["tiersUsed"] == [1]


def test_latest_without_data_has_no_points_and_prefers_lower_tier():
    with Env(orion=entity()) as env:
        out = env.run(dict(BODY, mode="latest"))
    assert out["groups"][0]["points"] == []
    obs = {1: [Reading("S1", "d", NOW - timedelta(minutes=50), 4.0, {}, None, "device", False)],
           2: [], 3: []}
    model = {4: [Reading("parcel_weather", None, NOW - timedelta(minutes=5), 9.0, {}, None,
                         "model", False)], 5: []}
    with Env(observed=obs, model=model, orion=entity()) as env:
        pts = env.run(dict(BODY, mode="latest"))["groups"][0]["points"]
    assert len(pts) == 1 and pts[0]["tier"] == 1 and pts[0]["value"] == 4.0


# --------------------------------------------------------------------- 413

def test_413_over_10000_points():
    body = dict(BODY, to="2026-12-05T00:00:00Z", resolution="PT15M")  # 34 days x 96 = 3264
    with Env(orion=entity()) as env:
        env.run(body)  # under the limit
    body = dict(BODY, to="2027-03-01T00:00:00Z", resolution="PT15M")  # > 10 000 buckets
    env = Env(orion=entity())
    r = http(env, body=body)
    assert r.status_code == 413 and "error" in r.get_json()
    assert "observed_tiers" not in env.events


def test_413_raw_points_over_limit():
    many = [Reading("S1", "d", T0 + timedelta(seconds=i), 1.0, {}, None, "device", False)
            for i in range(10_001)]
    body = dict(BODY, resolution="raw")
    env = Env(observed={1: many, 2: [], 3: []}, orion=entity())
    r = http(env, body=body)
    assert r.status_code == 413


# --------------------------------------------------------------------- GET

def test_get_coerces_numeric_and_bool_params():
    q = {"feature": PARCEL, "property": "soilMoisture", "from": BODY["from"], "to": BODY["to"],
         "depthFrom": "20", "depthTo": "40.5", "height": "2", "organ": "root",
         "allowMediumFallback": "true", "tiers": "observed", "tz": "Europe/Madrid"}
    body = routes.query_to_body(q)
    req = parse_best(body)
    assert req.sub_context == {"depthFrom": 20.0, "depthTo": 40.5, "height": 2.0, "organ": "root"}
    assert req.allow_medium_fallback is True and req.tiers == "observed" and req.tz == "Europe/Madrid"
    assert parse_best(routes.query_to_body(dict(q, allowMediumFallback="false"))).allow_medium_fallback is False
    for bad in ({"depthFrom": "abc"}, {"allowMediumFallback": "maybe"}):
        with pytest.raises(RequestError) as e:
            routes.query_to_body(dict(q, **bad))
        assert e.value.status == 400


def test_get_route_end_to_end():
    env = Env(observed={1: [rd(0, 10.0)], 2: [], 3: []}, orion=entity())
    q = {"feature": PARCEL, "property": "airTemperature", "from": BODY["from"], "to": BODY["to"]}
    r = http(env, "GET", query=q)
    assert r.status_code == 200 and r.get_json()["groups"][0]["points"][0]["value"] == 10.0
    assert env.conn.closed
    r = http(Env(orion=entity()), "GET", query=dict(q, depthFrom="x"))
    assert r.status_code == 400


# -------------------------------------------------------------------- HMAC

_common = os.path.normpath(os.path.join(_here, "..", "..", "common"))
if _common not in sys.path:
    sys.path.insert(0, _common)
_spec = importlib.util.spec_from_file_location("real_auth_middleware_best",
                                               os.path.join(_common, "auth_middleware.py"))
am = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(am)


def test_module_hmac_accepted(monkeypatch):
    from module_auth import accept_module_signature
    monkeypatch.setattr(am, "HMAC_SECRET", "s3cret")
    monkeypatch.setitem(am.verify_hmac_signature.__globals__, "HMAC_SECRET", "s3cret")
    monkeypatch.setattr(am, "REQUIRE_HMAC_SIGNATURE", True)
    flask_app = Flask(__name__)
    routes.register(flask_app, require_auth=accept_module_signature(am.require_auth, am),
                    resolve_tenant=lambda: "acme", get_connection=lambda: FakeConn(FakeCursor()),
                    internal_error=lambda e, ctx: ("boom", 500))
    ts = str(int(time.time()))
    sig = hmac.new(b"s3cret", f"|acme|{ts}".encode(), hashlib.sha256).hexdigest()
    headers = {"X-Tenant-ID": "acme", "X-User-ID": "agent", "X-Auth-Signature": f"{sig}:{ts}"}
    with patch.object(routes, "best", return_value={"ok": True}):
        c = flask_app.test_client()
        r = c.post("/api/observations/best", json=BODY, headers=headers)
        assert r.status_code == 200 and r.get_json() == {"ok": True}
        assert c.post("/api/observations/best", json=BODY, headers={"X-Tenant-ID": "acme"}).status_code in (401, 403)
