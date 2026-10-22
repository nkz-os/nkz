"""The sensors endpoints use the tenant connection helper the way it is defined.

`get_db_connection_with_tenant` is a context manager. Mocking it with a plain connection
hid that every endpoint called `.cursor()` on the context manager itself and answered 500.
These tests stub it with a real context manager, as production behaves.
"""

import json
import os
import sys
from contextlib import contextmanager
from functools import wraps
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")
os.environ.setdefault("MQTT_HOST", "localhost")
os.environ.setdefault("MQTT_PORT", "1883")
os.environ.setdefault("INTERNAL_SERVICE_SECRET", "test-secret")

_test_dir = os.path.dirname(os.path.abspath(__file__))
_svc_dir = os.path.normpath(os.path.join(_test_dir, ".."))
_services_dir = os.path.normpath(os.path.join(_svc_dir, ".."))
for _p in (_svc_dir, _services_dir, os.path.join(_services_dir, "common")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _require_auth(f=None, **kwargs):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kw):
            return func(*args, **kw)

        return wrapper

    return decorator(f) if f else decorator


_common_mock = MagicMock()
_common_mock.require_auth = _require_auth
_common_mock.inject_fiware_headers = lambda h, **kw: dict(h)
_common_mock.internal_error = lambda *a, **kw: ({"error": "internal"}, 500)
for _mod in ("common", "common.auth_middleware", "common.api_errors", "common.ngsi_headers"):
    sys.modules.setdefault(_mod, _common_mock)

from blueprints.sensors import sensors_bp  # noqa: E402


def _conn(fetchone=None):
    cur = MagicMock()
    cur.fetchone.return_value = fetchone
    cur.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


def _tenant_helper(conn):
    """Stand-in with the real helper's shape: a context manager yielding the connection."""

    @contextmanager
    def _helper(tenant_id):
        yield conn

    return _helper


def _client():
    from flask import Flask, g

    app = Flask(__name__)

    @app.before_request
    def _seed_identity():
        g.tenant = "t1"
        g.current_user = {"tenant_id": "t1", "user_id": "u1"}

    app.register_blueprint(sensors_bp)
    return app.test_client()


def _orion(status=200, body=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else []
    r.text = ""
    r.headers = {}
    return r


@pytest.mark.parametrize(
    "path, fetchone, expected",
    [
        ("/api/sensors/profiles", None, 200),
        ("/api/sensors/profiles/status", (3,), 200),
        ("/api/sensors", None, 200),
        ("/api/devices/d1/telemetry", None, 200),
        ("/api/devices/d1/telemetry/latest", None, 404),
        ("/api/devices/d1/telemetry/stats",
         {"total_records": 0, "first_record": None, "last_record": None}, 200),
        ("/api/devices/d1/commands", None, 200),
    ],
)
def test_read_endpoints_use_the_connection_from_the_context_manager(path, fetchone, expected):
    conn = _conn(fetchone)
    with patch("blueprints.sensors.get_db_connection_with_tenant", new=_tenant_helper(conn)):
        r = _client().get(path, headers={"Authorization": "Bearer x"})
    assert r.status_code == expected, r.get_json()
    conn.cursor.assert_called()


def test_register_reads_the_profile_and_creates_the_entity():
    profile = {"id": 1, "sdm_entity_type": "Device", "sdm_device_category": None, "mapping": {}}
    conn = _conn(profile)
    with patch("blueprints.sensors.get_db_connection_with_tenant", new=_tenant_helper(conn)), \
         patch("blueprints.sensors.requests.get", return_value=_orion(200, [])), \
         patch("blueprints.sensors.requests.post", return_value=_orion(201)) as post:
        r = _client().post("/api/sensors/register", content_type="application/json",
                           headers={"Authorization": "Bearer x"},
                           data=json.dumps({"external_id": "S1", "name": "S1",
                                            "profile": "air_temperature",
                                            "location": {"lat": 42.0, "lon": -2.0}}))
    assert r.status_code == 201, r.get_json()
    assert any("/ngsi-ld/v1/entities" in c.args[0] for c in post.call_args_list)


def test_register_unknown_profile_is_404():
    conn = _conn(None)
    with patch("blueprints.sensors.get_db_connection_with_tenant", new=_tenant_helper(conn)):
        r = _client().post("/api/sensors/register", content_type="application/json",
                           headers={"Authorization": "Bearer x"},
                           data=json.dumps({"external_id": "S1", "name": "S1", "profile": "nope",
                                            "location": {"lat": 42.0, "lon": -2.0}}))
    assert r.status_code == 404


def test_command_to_unknown_device_is_404():
    conn = _conn(None)
    with patch("blueprints.sensors.get_db_connection_with_tenant", new=_tenant_helper(conn)):
        r = _client().post("/api/devices/d1/commands", content_type="application/json",
                           headers={"Authorization": "Bearer x"},
                           data=json.dumps({"command_type": "ping", "payload": {}}))
    assert r.status_code == 404


def test_heartbeat_returns_the_pooled_connection_instead_of_closing_it():
    conn = _conn({"first_seen": None, "last_seen": None, "event_count": 0})
    with patch("blueprints.sensors.get_db_connection_simple", return_value=conn), \
         patch("blueprints.sensors.return_db_connection", create=True) as returned:
        r = _client().get("/api/heartbeat/check?entity_id=urn:ngsi-ld:Device:t1:d1",
                          headers={"Authorization": "Bearer x"})
    assert r.status_code == 200
    conn.close.assert_not_called()
    returned.assert_called_once_with(conn)


def test_heartbeat_matches_the_device_exactly():
    """A substring match made device "d1" look connected through "d10"."""
    conn = _conn({"first_seen": None, "last_seen": None, "event_count": 0})
    with patch("blueprints.sensors.get_db_connection_simple", return_value=conn), \
         patch("blueprints.sensors.return_db_connection", create=True):
        _client().get("/api/heartbeat/check?entity_id=urn:ngsi-ld:Device:t1:d1",
                      headers={"Authorization": "Bearer x"})
    sql, params = conn.cursor.return_value.execute.call_args.args
    assert "LIKE" not in sql
    assert params[1:] == ("d1",)
