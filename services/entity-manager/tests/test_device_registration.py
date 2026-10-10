"""A provisioned `Device` reaches the sensors table, like `AgriSensor` did.

Registration creates `Device` entities, but the registry subscription only
watched `AgriSensor`, so new sensors never got a row. The parcel now travels
in `controlledAsset`; `parcelId` is still read for legacy entities.
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")

_services_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _services_dir not in sys.path:
    sys.path.insert(0, _services_dir)
for _mod in ("common", "common.auth_middleware", "common.api_errors",
             "common.ngsi_headers", "common.subscription_health"):
    sys.modules.setdefault(_mod, MagicMock())

import notification_handler as nh  # noqa: E402
import subscription_manager as sm  # noqa: E402


def test_the_device_registration_subscription_exists():
    types = [s["entities"][0]["type"] for s in sm.SUBSCRIPTIONS]
    assert "Device" in types
    assert "AgriSensor" in types  # legacy kept until the contract phase


def test_parcel_from_controlled_asset_then_legacy_parcel_id():
    urn = "urn:ngsi-ld:AgriParcel:X"
    assert nh._parcel_ref({"controlledAsset": {"type": "Relationship", "object": urn}}) == urn
    assert nh._parcel_ref({"parcelId": {"type": "Property", "value": urn}}) == urn
    assert nh._parcel_ref({}) is None


def test_a_device_notification_is_persisted():
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(nh.notify_bp)
    device = {"id": "urn:ngsi-ld:Device:t:S1", "type": "Device",
              "externalId": {"type": "Property", "value": "S1"},
              "name": {"type": "Property", "value": "Sensor 1"}}
    with patch.object(nh, "_handle_agrisensor", return_value=1) as handler:
        res = app.test_client().post("/notify", data=json.dumps({"data": [device]}),
                                     content_type="application/json",
                                     headers={"NGSILD-Tenant": "t"})
    assert res.get_json()["persisted"] == 1
    handler.assert_called_once_with("t", [device])
