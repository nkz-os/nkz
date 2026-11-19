"""POST /api/observations (raw): cursor paging, CSV streaming, filters, error mapping."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

if "auth_middleware" not in sys.modules:
    _auth_stub = MagicMock()
    _auth_stub.require_auth = lambda f: f
    _auth_stub.inject_fiware_headers = lambda headers, tenant=None: dict(headers or {})
    sys.modules["auth_middleware"] = _auth_stub

import psycopg2.errors  # noqa: E402
import pytest  # noqa: E402

import app  # noqa: E402
from observations import raw as raw_mod  # noqa: E402
from observations.request import RequestError  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 11, 1, tzinfo=UTC)
HEADERS = {"X-Tenant-ID": "testtenant"}
PARCEL = "urn:ngsi-ld:AgriParcel:testtenant:p1"
OTHER = "urn:ngsi-ld:AgriParcel:testtenant:other"
BODY = {"feature": PARCEL, "property": "airTemperature",
        "from": "2026-11-01T00:00:00Z", "to": "2026-11-02T00:00:00Z"}
DEPS = {"dep-1": "urn:ngsi-ld:Device:testtenant:S1", "dep-2": "urn:ngsi-ld:Device:testtenant:S2"}


class FakeCursor:
    def close(self):
        pass

    def execute(self, *a, **k):
        pass


class FakeConn:
    def __init__(self):
        self.closed = False

    def cursor(self, *a, **k):
        return FakeCursor()

    def close(self):
        self.closed = True

    def commit(self):
        pass

    def rollback(self):
        pass


def row(i, feature=PARCEL, dep="dep-1", attr="airTemperature", eid=None, flag="valid", **kw):
    r = {"observed_at": T0 + timedelta(minutes=i), "device_id": "S1", "attribute_name": attr,
         "value": float(i), "deployment_id": dep, "source_kind": "device",
         "feature_of_interest": feature, "depth_from_cm": None, "depth_to_cm": None,
         "height_m": None, "organ": None, "context_ref": None, "calibrated": False,
         "r_in_area": False, "r_dist_m": None, "source_event_id": eid if eid is not None else i,
         "medium": "ambientAir", "time_source": "device", "quality_flag": flag,
         "location_json": None}
    r.update(kw)
    return r


def prop():
    return {"name": "airTemperature", "nearby_radius_m": 0.0, "allowed_media": ["ambientAir"],
            "default_medium": "ambientAir", "sub_context_tolerance": {}}


class Env:
    """In-memory store behind `_fetch_rows` (honours keyset, quality flags, LIMIT)."""

    def __init__(self, rows, area=None):
        self.rows = sorted(rows, key=lambda r: (r["observed_at"], r["source_event_id"],
                                                r["attribute_name"]))
        self.area = area
        self.conn = FakeConn()
        self.calls = []

    def fetch(self, cur, tenant, plan, flags, after, n):
        self.calls.append((flags, after, n))
        out = []
        for r in self.rows:
            pos = (r["observed_at"], r["source_event_id"], r["attribute_name"])
            if after and pos <= after:
                continue
            if flags and r["quality_flag"] not in flags:
                continue
            out.append(r)
        return out[:n]

    def plan(self, conn, tenant, req, fetch=None):
        return raw_mod.RawPlan(req=req, prop=prop(), medium="ambientAir", area=self.area,
                               attrs=["airTemperature"], urns=dict(DEPS),
                               device_ids=["S1", "S2"], radius=0.0, checked={PARCEL})

    def __enter__(self):
        self.ps = [patch.object(raw_mod, "_fetch_rows", self.fetch),
                   patch.object(raw_mod, "make_plan", self.plan)]
        for p in self.ps:
            p.start()
        return self

    def __exit__(self, *a):
        for p in self.ps:
            p.stop()

    def post(self, body=None, headers=None):
        with patch("app.get_db_connection", return_value=self.conn):
            with app.app.test_client() as c:
                return c.post("/api/observations", json=body or BODY,
                              headers={**HEADERS, **(headers or {})})


# interleaved: every third row belongs to another feature (tier None)
def interleaved(n=30):
    return [row(i, feature=OTHER if i % 3 == 0 else PARCEL) for i in range(n)]


def follow(env, limit, extra=None):
    got, cur, pages = [], None, 0
    while True:
        body = {**BODY, "limit": limit, **(extra or {})}
        if cur:
            body["cursor"] = cur
        r = env.post(body)
        assert r.status_code == 200, r.get_json()
        j = r.get_json()
        got += j["items"]
        pages += 1
        cur = j["nextCursor"]
        if not cur:
            return got, pages


def test_keyset_pagination_no_duplicates_across_pages():
    env = Env(interleaved(30))
    with env:
        got, pages = follow(env, 4)
    expected = [float(i) for i in range(30) if i % 3 != 0]
    assert [g["value"] for g in got] == expected
    assert len({g["t"] for g in got}) == len(got) and pages == 5  # 20 rows / 4 = 5 full pages


def test_pagination_exact_multiple_and_cursor_after_last_returned():
    env = Env([row(i) for i in range(6)])
    with env:
        r = env.post({**BODY, "limit": 3}).get_json()
        assert len(r["items"]) == 3 and r["nextCursor"]
        pos = raw_mod.decode_cursor(r["nextCursor"])
        assert pos == (T0 + timedelta(minutes=2), 2, "airTemperature")  # last RETURNED row
        r2 = env.post({**BODY, "limit": 3, "cursor": r["nextCursor"]}).get_json()
        assert len(r2["items"]) == 3 and r2["nextCursor"] is None  # no phantom empty page


def test_pagination_with_small_batches_and_long_non_qualifying_runs(monkeypatch):
    monkeypatch.setattr(raw_mod, "MIN_BATCH", 2)
    rows = [row(i, feature=PARCEL if i in (7, 20) else OTHER) for i in range(25)]
    env = Env(rows)
    with env:
        got, _ = follow(env, 1)
    assert [g["value"] for g in got] == [7.0, 20.0]


def test_examination_cap_returns_cursor_without_skipping(monkeypatch):
    monkeypatch.setattr(raw_mod, "MIN_BATCH", 2)
    monkeypatch.setattr(raw_mod, "MAX_EXAMINED", 4)
    rows = [row(i, feature=PARCEL if i in (9, 15) else OTHER) for i in range(20)]
    env = Env(rows)
    with env:
        got, pages = follow(env, 5)
    assert [g["value"] for g in got] == [9.0, 15.0] and pages > 1


def test_same_instant_ties_ordered_by_event_then_attribute():
    rows = [row(0, eid=5, attr="airTemperature"), row(0, eid=3, attr="airTemperature", dep="dep-2"),
            row(0, eid=5, attr="temperature")]
    env = Env(rows)
    with env:
        got, _ = follow(env, 1)
    assert [g["deviceId"] for g in got] == [DEPS["dep-2"], DEPS["dep-1"], DEPS["dep-1"]]


def test_unfused_returns_every_source_with_contract_fields():
    rows = [row(0, dep="dep-1", value=10.0), row(0, dep="dep-2", eid=2, value=11.0,
            location_json=json.dumps({"type": "Point", "coordinates": [-1.6, 42.8]}),
            calibrated=True, depth_from_cm=5, depth_to_cm=10, height_m=2.0, organ="leaf")]
    with Env(rows) as env:
        j = env.post().get_json()
    assert len(j["items"]) == 2 and j["nextCursor"] is None
    a, b = j["items"]
    assert {a["deviceId"], b["deviceId"]} == set(DEPS.values())
    assert set(a) == {"t", "value", "observedProperty", "featureOfInterest", "medium", "depthFromCm",
                      "depthToCm", "heightM", "organ", "contextRef", "deploymentId", "sourceKind",
                      "location", "timeSource", "qualityFlag", "calibrated", "deviceId", "tier",
                      "distanceM"}
    assert a["t"] == "2026-11-01T00:00:00Z" and a["tier"] == 1 and a["location"] is None
    assert a["observedProperty"] == "airTemperature" and a["qualityFlag"] == "valid"
    assert b["calibrated"] is True and b["location"]["coordinates"] == [-1.6, 42.8]
    assert (b["depthFromCm"], b["depthToCm"], b["heightM"], b["organ"]) == (5.0, 10.0, 2.0, "leaf")


def test_sub_context_filter_applies():
    rows = [row(0, organ="leaf"), row(1, organ="fruit")]
    with Env(rows) as env:
        j = env.post({**BODY, "subContext": {"organ": "fruit"}}).get_json()
    assert [i["organ"] for i in j["items"]] == ["fruit"]


def test_quality_flags_default_all_and_filter():
    rows = [row(0, flag="valid"), row(1, flag="out_of_range"), row(2, flag="suspect")]
    with Env(rows) as env:
        allf = env.post().get_json()["items"]
        some = env.post({**BODY, "qualityFlags": ["valid", "suspect"]}).get_json()["items"]
        bad = env.post({**BODY, "qualityFlags": ["bogus"]})
        empty = env.post({**BODY, "qualityFlags": []})
    assert [i["qualityFlag"] for i in allf] == ["valid", "out_of_range", "suspect"]
    assert [i["qualityFlag"] for i in some] == ["valid", "suspect"]
    assert bad.status_code == 400 and empty.status_code == 400
    assert env.calls[0][0] is None and env.calls[1][0] == ("suspect", "valid")


@pytest.mark.parametrize("cursor", ["", "!!!", "e30=", "bm90LWpzb24=",
                                    raw_mod.base64.urlsafe_b64encode(b'{"t":"x","e":1,"a":"a"}').decode(),
                                    raw_mod.base64.urlsafe_b64encode(b'{"t":"2026-11-01T00:00:00Z","e":"1","a":"a"}').decode(),
                                    raw_mod.base64.urlsafe_b64encode(b'{"t":"2026-11-01T00:00:00","e":1,"a":"a"}').decode()])
def test_invalid_cursor_400(cursor):
    with Env([row(0)]) as env:
        r = env.post({**BODY, "cursor": cursor})
    if cursor == "":  # empty cursor = first page
        assert r.status_code == 200
    else:
        assert r.status_code == 400 and r.get_json() == {"error": "invalid cursor"}
        assert not env.calls  # rejected before any query


def test_limit_capped_10000_and_validation():
    with Env([row(0)]) as env:
        assert env.post({**BODY, "limit": 50_000}).status_code == 200
        assert env.post({**BODY, "limit": 0}).status_code == 400
        assert env.post({**BODY, "limit": "x"}).status_code == 400
        assert env.post({**BODY, "limit": True}).status_code == 400
        env.post(BODY)
    sizes = [c[2] for c in env.calls]
    assert sizes[0] == 10_001 and sizes[-1] == 1_001  # batch = limit + 1 (default 1000)
    assert raw_mod.parse_raw({**BODY, "limit": 50_000}).limit == 10_000
    assert raw_mod.parse_raw(BODY).limit == 1_000


def test_cursor_roundtrip_keeps_microseconds():
    pos = (T0 + timedelta(microseconds=123456), 42, "airTemperature")
    assert raw_mod.decode_cursor(raw_mod.encode_cursor(pos)) == pos


def test_connection_closed_json_and_errors():
    with Env([row(0)]) as env:
        env.post()
    assert env.conn.closed
    with Env([row(0)]) as env:
        with patch.object(raw_mod, "raw_page", side_effect=psycopg2.errors.QueryCanceled()):
            r = env.post()
    assert r.status_code == 503 and env.conn.closed
    with Env([row(0)]) as env:
        with patch.object(raw_mod, "make_plan", side_effect=RequestError(422, "medium")):
            r = env.post()
    assert r.status_code == 422 and env.conn.closed


# --------------------------------------------------------------------- CSV

def csv_post(env, body=None):
    return env.post(body, headers={"Accept": "text/csv"})


def test_csv_header_and_rows():
    rows = [row(0, location_json=json.dumps({"type": "Point", "coordinates": [-1.6, 42.8]}),
                calibrated=True), row(1), row(2, feature=OTHER)]
    with Env(rows) as env:
        r = csv_post(env)
        text = r.get_data(as_text=True)
    assert r.status_code == 200 and r.mimetype == "text/csv"
    assert r.headers["Content-Disposition"] == 'attachment; filename="observations.csv"'
    lines = text.strip().splitlines()
    assert lines[0] == ",".join(raw_mod.CSV_COLUMNS) and len(lines) == 3  # header once, 2 rows
    h = lines[0].split(",")
    first = dict(zip(h, lines[1].split(",")))
    assert first["t"] == "2026-11-01T00:00:00Z" and first["lon"] == "-1.6" and first["lat"] == "42.8"
    assert first["calibrated"] == "true" and first["tier"] == "1" and first["deviceId"] == DEPS["dep-1"]
    assert dict(zip(h, lines[2].split(",")))["lon"] == ""
    assert env.conn.closed


def test_csv_follows_cursor_over_pages_header_once(monkeypatch):
    monkeypatch.setattr(raw_mod, "CSV_PAGE", 4)
    with Env(interleaved(30)) as env:
        text = csv_post(env).get_data(as_text=True)
    lines = text.strip().splitlines()
    assert len(lines) == 1 + 20 and lines.count(",".join(raw_mod.CSV_COLUMNS)) == 1
    assert len(env.calls) > 1


def test_csv_truncation_marker(monkeypatch):
    monkeypatch.setattr(raw_mod, "CSV_PAGE", 4)
    monkeypatch.setattr(raw_mod, "MAX_CSV_ROWS", 6)
    with Env([row(i) for i in range(20)]) as env:
        text = csv_post(env).get_data(as_text=True)
    lines = text.strip().splitlines()
    assert len(lines) == 1 + 6 + 1 and lines[-1] == "# truncated: narrow the period"
    assert env.conn.closed


def test_csv_exact_max_rows_not_truncated(monkeypatch):
    monkeypatch.setattr(raw_mod, "MAX_CSV_ROWS", 6)
    with Env([row(i) for i in range(6)]) as env:
        text = csv_post(env).get_data(as_text=True)
    assert "truncated" not in text and len(text.strip().splitlines()) == 7


def test_csv_streaming_closes_connection_on_abort_and_unstarted():
    conn = FakeConn()
    env = Env([row(i) for i in range(10)])
    plan = env.plan(conn, "t", raw_mod.parse_raw(BODY).base)
    with env:
        s = raw_mod.CsvStream(conn, "testtenant", plan)
        assert next(s).startswith("t,value") and not conn.closed  # lazy: open while streaming
        s.close()
        assert conn.closed
        conn2 = FakeConn()
        s2 = raw_mod.CsvStream(conn2, "testtenant", plan)
        s2.close()  # client gone before the first chunk
        assert conn2.closed


def test_csv_error_before_stream_maps_status_and_closes():
    with Env([row(0)]) as env:
        with patch.object(raw_mod, "make_plan", side_effect=RequestError(404, "feature not found")):
            r = csv_post(env)
    assert r.status_code == 404 and env.conn.closed


def test_csv_midstream_error_is_marked_and_closes(monkeypatch):
    with Env([row(0)]) as env:
        with patch.object(raw_mod, "raw_page", side_effect=RuntimeError("boom")):
            text = csv_post(env).get_data(as_text=True)
    assert text.splitlines()[-1].startswith("# error:") and env.conn.closed


def test_statement_timeout_per_batch_uses_tenant_cursor_each_time(monkeypatch):
    monkeypatch.setattr(raw_mod, "MIN_BATCH", 2)
    opened = []
    real = raw_mod.tenant_cursor

    def spy(conn, tenant):
        opened.append(tenant)
        return real(conn, tenant)

    with Env(interleaved(30)) as env, patch.object(raw_mod, "tenant_cursor", spy):
        follow(env, 4)
    assert len(opened) == len(env.calls) and len(opened) > 5
