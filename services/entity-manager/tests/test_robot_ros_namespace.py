"""Each provisioned robot gets a ROS namespace no live robot already holds.

The index used to come from a broken lookup that always answered 1, so every robot of a
tenant got robot_001. A plain count would still collide after a deletion, so the next index
is one above the highest one in use.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")
os.environ.setdefault("INTERNAL_SERVICE_SECRET", "test-secret")

_test_dir = os.path.dirname(os.path.abspath(__file__))
_svc_dir = os.path.normpath(os.path.join(_test_dir, ".."))
_services_dir = os.path.normpath(os.path.join(_svc_dir, ".."))
for _p in (_svc_dir, _services_dir, os.path.join(_services_dir, "common")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_common_mock = MagicMock()
_common_mock.require_auth = lambda f=None, **kw: f if f else (lambda g: g)
_common_mock.inject_fiware_headers = lambda h, *a, **kw: dict(h)
for _mod in ("common", "common.auth_middleware", "common.api_errors", "common.ngsi_headers",
             "common.ngsi_payload_guard"):
    sys.modules.setdefault(_mod, _common_mock)

from blueprints import entities  # noqa: E402


def _resp(items, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = items
    return r


def _orion(by_type):
    """requests.get stub answering per entity type (robot machines need the category q)."""

    def _get(url, params=None, **kw):
        etype = params["type"]
        if etype == "ManufacturingMachine":
            assert params.get("q") == 'category=="robot"'
        return _resp(by_type.get(etype, []))

    return _get


def _ns(n):
    return {"rosNamespace": {"type": "Property", "value": f"/t1/robot_{n:03d}"}}


def test_first_robot_is_one():
    with patch.object(entities.requests, "get", side_effect=_orion({})):
        assert entities._get_next_robot_index("t1") == 1


def test_index_is_above_the_highest_in_use_not_the_count():
    """robot_002 was deleted: two robots remain, but the next free index is 4."""
    live = {"AgriculturalRobot": [_ns(1), _ns(3)]}
    with patch.object(entities.requests, "get", side_effect=_orion(live)):
        assert entities._get_next_robot_index("t1") == 4


def test_robot_machines_count_too():
    live = {"AgriculturalRobot": [_ns(1)], "ManufacturingMachine": [_ns(7)]}
    with patch.object(entities.requests, "get", side_effect=_orion(live)):
        assert entities._get_next_robot_index("t1") == 8


def test_robots_without_a_namespace_are_ignored():
    live = {"AgriculturalRobot": [{"id": "r"}, {"rosNamespace": "/t1/custom"}, _ns(2)]}
    with patch.object(entities.requests, "get", side_effect=_orion(live)):
        assert entities._get_next_robot_index("t1") == 3


def test_an_unreachable_broker_raises_instead_of_guessing():
    with patch.object(entities.requests, "get", return_value=_resp({}, status=503)):
        with pytest.raises(RuntimeError):
            entities._get_next_robot_index("t1")
