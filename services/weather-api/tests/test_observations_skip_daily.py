"""Station listings must not include closed-day series entities ("...-daily")."""
import os
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

import app.routers.observations as obs


@pytest.mark.parametrize("latest_only", [True, False])
def test_telemetry_fallback_excludes_daily_entities(monkeypatch, latest_only):
    executed = []
    cur = MagicMock()
    cur.execute.side_effect = lambda q, p=None: executed.append((q, p))
    cur.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def fake_conn(_tenant):
        yield conn

    monkeypatch.setattr(obs, "get_db_connection", fake_conn)
    obs._fetch_from_telemetry_events("t1", latest_only=latest_only)
    assert executed
    sql, params = executed[0]
    assert "entity_id NOT LIKE '%%-daily'" in sql
    assert params is not None  # %% is only an escape when params are bound


def test_telemetry_query_accepts_both_weather_entity_types(monkeypatch):
    executed = []
    cur = MagicMock()
    cur.execute.side_effect = lambda q, p=None: executed.append((q, p))
    cur.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def fake_conn(_tenant):
        yield conn

    monkeypatch.setattr(obs, "get_db_connection", fake_conn)
    obs._fetch_from_telemetry_events("t1")
    sql, params = executed[0]
    assert "weather_observations" not in sql
    assert "entity_type IN (%s, %s)" in sql
    assert params[:3] == [
        "WeatherObserved",
        "https://saref.etsi.org/saref4agri/WeatherObserved",
        "t1",
    ]


def test_telemetry_query_error_propagates(monkeypatch):
    def boom(_tenant):
        raise RuntimeError("db down")

    monkeypatch.setattr(obs, "get_db_connection", boom)
    with pytest.raises(RuntimeError):
        obs._fetch_from_telemetry_events("t1")
