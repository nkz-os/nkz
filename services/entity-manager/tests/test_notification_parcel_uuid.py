"""notify_bp: sensor→parcel link derives from the parcel URN, not cadastral_parcels.

Plan 2 / Task 3b: with uniform writes, the parcel URN is
urn:ngsi-ld:AgriParcel:<uuid4> and that uuid IS the parcel identity. The
notification handler must derive sensors.parcel_id from the URN directly — the
cadastral_parcels mirror is retired.
"""
import os
import sys
from unittest.mock import MagicMock

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")

_services_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _services_dir not in sys.path:
    sys.path.insert(0, _services_dir)
sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.auth_middleware", MagicMock())

import notification_handler as nh  # noqa: E402


def test_uuid_from_agriparcel_urn():
    assert nh._parcel_uuid_from_urn(
        "urn:ngsi-ld:AgriParcel:11111111-1111-1111-1111-111111111111"
    ) == "11111111-1111-1111-1111-111111111111"


def test_bare_uuid_passthrough():
    assert nh._parcel_uuid_from_urn(
        "22222222-2222-2222-2222-222222222222"
    ) == "22222222-2222-2222-2222-222222222222"


def test_non_uuid_returns_none():
    assert nh._parcel_uuid_from_urn("urn:ngsi-ld:AgriParcel:not-a-uuid") is None
    assert nh._parcel_uuid_from_urn("") is None
    assert nh._parcel_uuid_from_urn(None) is None
