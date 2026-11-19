"""Request parsing and tenant cursor of the /api/observations foundation."""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

import pytest  # noqa: E402

from observations.db import tenant_cursor  # noqa: E402
from observations.request import RequestError, iso_z, parse_best  # noqa: E402

POLY = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}


def body(**over):
    b = {
        "feature": "urn:ngsi-ld:AgriParcel:t:p1",
        "property": "airTemperature",
        "from": "2026-11-01T00:00:00Z",
        "to": "2026-11-08T00:00:00Z",
    }
    b.update(over)
    return b


def err(b):
    with pytest.raises(RequestError) as e:
        parse_best(b)
    return e.value


def test_defaults_and_fields():
    r = parse_best(body())
    assert r.feature and r.geometry is None
    assert (r.mode, r.resolution, r.tz, r.tiers) == ("series", "PT1H", "UTC", "all")
    assert r.medium is None and r.sub_context == {} and r.allow_medium_fallback is False
    assert r.start == datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_geometry_only_ok():
    assert parse_best(body(feature=None, geometry=POLY)).geometry == POLY


@pytest.mark.parametrize("over", [{"geometry": POLY}, {"feature": None}])
def test_exactly_one_of_feature_or_geometry(over):
    e = err(body(**over))
    assert (e.status, e.message) == (400, "exactly one of feature or geometry is required")


def test_from_must_precede_to():
    e = err(body(**{"from": "2026-11-08T00:00:00Z", "to": "2026-11-01T00:00:00Z"}))
    assert (e.status, e.message) == (400, "from must be before to")


def test_bad_dates():
    assert err(body(**{"from": "nope"})).status == 400
    assert err(body(**{"to": None})).status == 400
    assert err(body(**{"from": "2026-11-01T00:00:00"})).status == 400  # naive


def test_unknown_resolution():
    e = err(body(resolution="PT5M"))
    assert (e.status, e.message) == (400, "unknown resolution")


@pytest.mark.parametrize("res", ["raw", "PT15M", "PT1H", "P1D"])
def test_known_resolutions(res):
    assert parse_best(body(resolution=res)).resolution == res


@pytest.mark.parametrize("geo", [
    {"type": "Point", "coordinates": [0, 0]}, {"type": "Polygon"}, "x",
    {"type": "Polygon", "coordinates": []},
])
def test_geometry_must_be_polygon(geo):
    assert err(body(feature=None, geometry=geo)).status == 400


def test_unknown_tz():
    assert err(body(tz="Mars/Base")).status == 400
    assert parse_best(body(tz="Europe/Madrid")).tz == "Europe/Madrid"


def test_other_validation():
    assert err(body(mode="x")).status == 400
    assert err(body(tiers="x")).status == 400
    assert err(body(property="")).status == 400
    assert err(body(subContext={"bogus": 1})).status == 400
    assert err(body(allowMediumFallback="yes")).status == 400
    assert parse_best(body(subContext={"depthFrom": 20, "organ": "leaf"})).sub_context == {
        "depthFrom": 20, "organ": "leaf"}


def test_iso_z_format():
    assert iso_z(datetime(2026, 11, 1, tzinfo=timezone.utc)) == "2026-11-01T00:00:00Z"
    from datetime import timedelta
    assert iso_z(datetime(2026, 11, 1, 2, tzinfo=timezone(timedelta(hours=2)))) == "2026-11-01T00:00:00Z"
    with pytest.raises(ValueError):
        iso_z(datetime(2026, 11, 1))


class _Cur:
    def __init__(self):
        self.executed, self.closed = [], False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def close(self):
        self.closed = True


class _Conn:
    def __init__(self):
        self.cur, self.kwargs = _Cur(), None

    def cursor(self, **kw):
        self.kwargs = kw
        return self.cur


def test_tenant_cursor_sets_tenant_then_timeout():
    conn = _Conn()
    with tenant_cursor(conn, "tn") as cur:
        assert cur is conn.cur and not cur.closed
    sqls = [s for s, _ in conn.cur.executed]
    assert "set_config('app.current_tenant'" in sqls[0] and conn.cur.executed[0][1] == ("tn",)
    assert sqls[1] == "SET LOCAL statement_timeout = '10s'"
    assert conn.cur.closed
    from psycopg2.extras import RealDictCursor
    assert conn.kwargs == {"cursor_factory": RealDictCursor}


def test_tenant_cursor_closes_on_error_and_rejects_empty_tenant():
    conn = _Conn()
    with pytest.raises(RuntimeError):
        with tenant_cursor(conn, "tn"):
            raise RuntimeError("x")
    assert conn.cur.closed
    with pytest.raises(ValueError):
        with tenant_cursor(_Conn(), ""):
            pass
