"""Daily freshness check of stored closed days (fails the Job -> alert)."""

import os
import sys
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))

import weather_worker.closed_day_check as cc
from weather_worker import closed_day as cd

TODAY = date(2026, 11, 17)
EID = "urn:ngsi-ld:WeatherObserved:tenant-a:parcel-p1-daily"


def _rows(day):
    return [("tenant-a", EID, day)]


def _inspect(value):
    """Fake inspect(tenant, entity_id): None, (orion_day, parcel_alive) or an exception."""
    calls = []

    def inspect(tenant, entity_id):
        calls.append((tenant, entity_id))
        if isinstance(value, BaseException):
            raise value
        return value

    inspect.calls = calls
    return inspect


def test_parcel_id_from_entity():
    assert cc.parcel_id_from_entity(EID) == "p1"
    assert cc.parcel_id_from_entity(
        "urn:ngsi-ld:WeatherObserved:tenant-a:parcel-da36ccd2-85d2-4c76-b552-c5c835a987c1-daily"
    ) == "da36ccd2-85d2-4c76-b552-c5c835a987c1"


def test_fresh_parcels_no_finding_and_no_orion_read():
    insp = _inspect(("2026-11-16", True))
    for stored in (date(2026, 11, 16), date(2026, 11, 15)):  # lag 1 and 2
        assert cc.find_stale(_rows(stored), TODAY, 2, insp) == []
    assert insp.calls == []


def test_daily_entity_gone_is_ignored():
    assert cc.find_stale(_rows(date(2026, 11, 10)), TODAY, 2, _inspect(None)) == []


def test_orphan_of_deleted_parcel_is_ignored():
    """The daily entity outlived its parcel (worker prune misses it): not an outage."""
    assert cc.find_stale(_rows(date(2026, 11, 5)), TODAY, 2, _inspect(("2026-11-05", False))) == []


def test_stale_parcel_is_reported():
    insp = _inspect(("2026-11-14", True))
    out = cc.find_stale(_rows(date(2026, 11, 14)), TODAY, 2, insp)
    assert out == [{"tenant": "tenant-a", "parcel": "p1", "stored_day": "2026-11-14",
                    "orion_day": "2026-11-14", "reason": "stale"}]
    assert insp.calls == [("tenant-a", EID)]


def test_orion_newer_than_store_is_notification_lost():
    out = cc.find_stale(_rows(date(2026, 11, 10)), TODAY, 2, _inspect(("2026-11-16", True)))
    assert out[0]["reason"] == "notification_lost" and out[0]["orion_day"] == "2026-11-16"


def test_orion_unreadable_is_reported():
    out = cc.find_stale(_rows(date(2026, 11, 10)), TODAY, 2, _inspect(cd.LastPublishedUnreadable("x")))
    assert out[0]["reason"] == "orion_unreadable" and out[0]["orion_day"] is None


# --- inspect_daily against a fake broker ---------------------------------------

PARCEL_URN = "urn:ngsi-ld:AgriParcel:tenant-a:p1"


def _daily_body(rel="refParcel", day="2026-11-14"):
    body = {"id": EID, "dateObserved": {"type": "Property",
                                        "value": {"@type": "DateTime", "@value": f"{day}T00:00:00Z"}}}
    if rel:
        body[rel] = {"type": "Relationship", "object": PARCEL_URN}
    return body


def _broker(daily_status=200, daily_body=None, parcel_status=200):
    def get(url, headers=None, timeout=None):
        if url.endswith(EID):
            return MagicMock(status_code=daily_status, json=MagicMock(return_value=daily_body or {}))
        if url.endswith(PARCEL_URN):
            return MagicMock(status_code=parcel_status, json=MagicMock(return_value={"id": PARCEL_URN}))
        raise AssertionError(url)
    http = MagicMock()
    http.get.side_effect = get
    return http


def test_inspect_daily_gone_is_none():
    http = _broker_by_url({EID: 404, "urn:ngsi-ld:AgriParcel:p1": 404,
                           "urn:ngsi-ld:AgriParcel:tenant-a:p1": 404})
    assert cc.inspect_daily("tenant-a", EID, orion_url="http://o", http=http) is None


def test_inspect_daily_reads_ref_parcel_alias():
    """Orion compacts locatedAt as refParcel (same IRI in the platform context)."""
    http = _broker(200, _daily_body("refParcel"), 200)
    assert cc.inspect_daily("tenant-a", EID, orion_url="http://o", http=http) == ("2026-11-14", True)


def test_inspect_daily_reads_located_at():
    http = _broker(200, _daily_body("locatedAt"), 404)
    assert cc.inspect_daily("tenant-a", EID, orion_url="http://o", http=http) == ("2026-11-14", False)


def test_inspect_daily_without_relationship_counts_as_alive():
    http = _broker(200, _daily_body(None))
    assert cc.inspect_daily("tenant-a", EID, orion_url="http://o", http=http) == ("2026-11-14", True)


def test_inspect_daily_unreadable_raises():
    import pytest
    for http in (_broker(503), _broker(200, _daily_body(), 503), _broker(200, {"id": EID}),
                 _broker_by_url({EID: 404, "urn:ngsi-ld:AgriParcel:p1": 503})):
        with pytest.raises(cd.LastPublishedUnreadable):
            cc.inspect_daily("tenant-a", EID, orion_url="http://o", http=http)


class _Conn:
    def __init__(self, rows):
        self.rows, self.executed, self.closed = rows, [], False

    def cursor(self):
        cur = MagicMock()
        cur.execute.side_effect = lambda q, p=None: self.executed.append((q, p))
        cur.fetchall.return_value = self.rows
        return cur

    def close(self):
        self.closed = True


def _ts(d):
    return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc)


def test_latest_stored_days_query_is_read_only_and_daily_scoped():
    conn = _Conn([("tenant-a", EID, _ts(date(2026, 11, 16)))])
    rows = cc.latest_stored_days(conn, 14)
    assert rows == [("tenant-a", EID, date(2026, 11, 16))]
    q, params = conn.executed[0]
    assert q.lstrip().upper().startswith("SELECT")
    assert "INSERT" not in q.upper() and "UPDATE" not in q.upper() and "DELETE" not in q.upper()
    assert "%:parcel-%-daily" in params and 14 in params


def test_run_exit_codes(monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://x")
    monkeypatch.setenv("CONTEXT_URL", "http://ctx")
    fresh = _Conn([("tenant-a", EID, _ts(date(2026, 11, 16)))])
    assert cc.run(TODAY, connect=lambda url: fresh, inspect=_inspect(None), sleep=lambda s: None) == 0
    assert fresh.closed
    stale = _Conn([("tenant-a", EID, _ts(date(2026, 11, 12)))])
    assert cc.run(TODAY, connect=lambda url: stale, inspect=_inspect(("2026-11-12", True)), sleep=lambda s: None) == 1


def test_run_without_database_url_fails(monkeypatch):
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert cc.run(TODAY, connect=MagicMock(), inspect=_inspect(None), sleep=lambda s: None) == 1


def test_run_accepts_database_url(monkeypatch):
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://y")
    monkeypatch.setenv("CONTEXT_URL", "http://ctx")
    seen = []
    conn = _Conn([])
    assert cc.run(TODAY, connect=lambda url: seen.append(url) or conn, inspect=_inspect(None),
                  sleep=lambda s: None) == 0
    assert seen == ["postgresql://y"]


def test_run_retries_db_connect(monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://x")
    monkeypatch.setenv("CONTEXT_URL", "http://ctx")
    attempts, sleeps = [], []
    conn = _Conn([])

    def connect(url):
        attempts.append(url)
        if len(attempts) < 3:
            raise OSError("connection refused")
        return conn

    assert cc.run(TODAY, connect=connect, inspect=_inspect(None), sleep=sleeps.append) == 0
    assert len(attempts) == 3 and len(sleeps) == 2


def test_run_db_always_down_fails(monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://x")

    def connect(url):
        raise OSError("connection refused")

    assert cc.run(TODAY, connect=connect, inspect=_inspect(None), sleep=lambda s: None) == 1


# --- review fixes ----------------------------------------------------------------

def test_run_without_context_url_fails(monkeypatch):
    """Without the platform context Orion returns full IRIs: every parcel would read as unreadable."""
    monkeypatch.setenv("POSTGRES_URL", "postgresql://x")
    monkeypatch.delenv("CONTEXT_URL", raising=False)
    assert cc.run(TODAY, connect=lambda url: _Conn([]), inspect=_inspect(None), sleep=lambda s: None) == 1


def test_query_matches_parcel_daily_entities_only():
    conn = _Conn([])
    cc.latest_stored_days(conn, 60)
    assert "%:parcel-%-daily" in conn.executed[0][1]


def test_default_lookback_is_60_days():
    assert cc.DEFAULT_LOOKBACK_DAYS == 60


def test_daily_entity_missing_but_parcel_alive_is_reported():
    out = cc.find_stale(_rows(date(2026, 11, 10)), TODAY, 2, _inspect((None, True)))
    assert out[0]["reason"] == "daily_missing" and out[0]["orion_day"] is None


def _broker_by_url(statuses):
    def get(url, headers=None, timeout=None):
        for suffix, status in statuses.items():
            if url.endswith(suffix):
                return MagicMock(status_code=status, json=MagicMock(return_value={}))
        raise AssertionError(url)
    http = MagicMock()
    http.get.side_effect = get
    return http


def test_inspect_daily_gone_parcel_alive_is_daily_missing():
    http = _broker_by_url({EID: 404, "urn:ngsi-ld:AgriParcel:p1": 200})
    assert cc.inspect_daily("tenant-a", EID, orion_url="http://o", http=http) == (None, True)


def test_inspect_daily_gone_parcel_with_tenant_segment_alive():
    http = _broker_by_url({EID: 404, "urn:ngsi-ld:AgriParcel:p1": 404,
                           "urn:ngsi-ld:AgriParcel:tenant-a:p1": 200})
    assert cc.inspect_daily("tenant-a", EID, orion_url="http://o", http=http) == (None, True)


def test_inspect_daily_and_parcel_gone_is_none():
    http = _broker_by_url({EID: 404, "urn:ngsi-ld:AgriParcel:p1": 404,
                           "urn:ngsi-ld:AgriParcel:tenant-a:p1": 404})
    assert cc.inspect_daily("tenant-a", EID, orion_url="http://o", http=http) is None
