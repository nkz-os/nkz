"""The reader accepts the cumulative irrigation volume of a water meter."""
import os
import sys
from unittest.mock import MagicMock

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ORION_URL", "http://orion:1026")

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

if "auth_middleware" not in sys.modules:
    _auth_stub = MagicMock()
    _auth_stub.require_auth = lambda f: f
    _auth_stub.inject_fiware_headers = lambda headers, tenant=None: dict(headers or {})
    _auth_stub.internal_error = lambda e: ({"error": "internal"}, 500)
    sys.modules["auth_middleware"] = _auth_stub

import app  # noqa: E402


def test_water_consumption_is_a_valid_telemetry_attribute():
    assert "waterConsumption" in app._telemetry_measurement_whitelist()
