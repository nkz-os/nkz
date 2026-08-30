"""Task 3: entity-manager side of the Device/ManufacturingMachine -> DeviceMeasurement
subscription + notify wiring.

Covers:
  - the SDK-based subscription list entity-manager registers for this pipeline does NOT
    include `DeviceMeasurement` (it's the type this pipeline WRITES — subscribing to it
    would feed entity-manager its own output) and DOES include `Device` +
    `ManufacturingMachine`
  - POST /api/internal/notify/measurements dispatches Device/ManufacturingMachine
    entities to the transformer + Orion write, ignores everything else, and needs no
    X-Internal-Service-Secret (the SDK's SubscriptionRegistrar body has no customHeaders
    support, so a subscription created through it structurally cannot carry one)
  - the per-entity handler resolves a sensor_profiles row via profileCode and hands the
    real result to Orion's upsert, and fails closed (no write, no crash) when there is no
    profileCode or no matching profile row

`blueprints.notifications` imports `blueprints.measurements` (which needs the real
`common.unit_codes`) LAZILY, inside the handler function body, and only once there is an
actual profile row to build against — so most of this file does not need the
`common.unit_codes` sys.modules pin that test_measurement_transformer.py needs. The one
test that exercises the real `build_measurements` (`test_builds_and_writes_real_
measurements`) does need it, applied the same way, for the same reason.
"""

import importlib.util
import json
import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault('INTERNAL_SERVICE_SECRET', 'test-secret')
os.environ.setdefault('POSTGRES_URL', 'postgresql://test:test@localhost:5432/test')
os.environ.setdefault('ORION_URL', 'http://orion:1026')

_test_dir = os.path.dirname(os.path.abspath(__file__))
_em_dir = os.path.normpath(os.path.join(_test_dir, ".."))
_services_dir = os.path.normpath(os.path.join(_em_dir, ".."))
for _p in (_em_dir, _services_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# The one submodule `blueprints.measurements` actually needs: load the real file and pin
# it under its exact dotted name so `from common.unit_codes import to_unit_code` resolves
# regardless of what `common` itself is stubbed to elsewhere (direct sys.modules hit on
# the submodule short-circuits ever consulting the parent package) — see
# tests/test_measurement_transformer.py's docstring for the full explanation.
_unit_codes_path = os.path.join(_services_dir, "common", "unit_codes.py")
_uc_spec = importlib.util.spec_from_file_location("common.unit_codes", _unit_codes_path)
_uc_mod = importlib.util.module_from_spec(_uc_spec)
assert _uc_spec.loader is not None
_uc_spec.loader.exec_module(_uc_mod)
sys.modules["common.unit_codes"] = _uc_mod

# `common` may already be a MagicMock from another test file (see test_notifications.py),
# or not stubbed at all yet — don't clobber it either way. blueprints.notifications only
# needs common.auth_middleware/common.ngsi_headers at MODULE level (require_auth,
# inject_fiware_headers); common.unit_codes is only reached lazily, inside the handler
# function under test below, which this file exercises directly.
_common_mock = MagicMock()
_common_mock.inject_fiware_headers = lambda h, t=None, **kw: h


def _require_auth_passthrough(f):
    f.__wrapped__ = f
    return f


_common_mock.require_auth = _require_auth_passthrough
sys.modules.setdefault('common', _common_mock)
sys.modules.setdefault('common.auth_middleware', _common_mock)
sys.modules.setdefault('common.ngsi_headers', _common_mock)

import pytest
from flask import Flask

from blueprints.notifications import (  # noqa: E402
    DEVICE_MEASUREMENT_SUBSCRIPTIONS,
    notifications_bp,
    _handle_device_measurement_notification,
)

_app = Flask(__name__)
_app.register_blueprint(notifications_bp)
_app.testing = True


@pytest.fixture
def client():
    with _app.test_client() as c:
        yield c


DEVICE_ENTITY = {
    "id": "urn:ngsi-ld:Device:montiko:sensor-001",
    "type": "Device",
    "profileCode": {"type": "Property", "value": "soil-sensor"},
    "leafTemperature": {
        "type": "Property",
        "value": 21.5,
        "observedAt": "2026-08-29T10:00:00Z",
    },
}

PROFILE_ROW = {
    "id": 1,
    "sdm_entity_type": "Device",
    "mapping": {
        "measurements": [
            {"type": "leafTemperature", "unit": "", "sdmAttribute": "leafTemperature"},
        ],
    },
}


class TestSubscriptionListExcludesDeviceMeasurement:
    def test_does_not_subscribe_to_device_measurement(self):
        types = {s["type"] for s in DEVICE_MEASUREMENT_SUBSCRIPTIONS}
        assert "DeviceMeasurement" not in types

    def test_subscribes_to_device_and_manufacturing_machine(self):
        types = {s["type"] for s in DEVICE_MEASUREMENT_SUBSCRIPTIONS}
        assert types == {"Device", "ManufacturingMachine"}


class TestMeasurementNotifyRoute:
    @patch("blueprints.notifications._handle_device_measurement_notification")
    def test_dispatches_device_and_manufacturing_machine(self, mock_handle, client):
        mock_handle.return_value = True
        resp = client.post(
            "/api/internal/notify/measurements",
            content_type="application/json",
            headers={"NGSILD-Tenant": "montiko"},
            data=json.dumps(
                {
                    "data": [
                        {"id": "urn:ngsi-ld:Device:montiko:s1", "type": "Device"},
                        {
                            "id": "urn:ngsi-ld:ManufacturingMachine:montiko:m1",
                            "type": "ManufacturingMachine",
                        },
                    ]
                }
            ),
        )
        assert resp.status_code == 200
        assert resp.get_json()["handled"] == 2
        assert mock_handle.call_count == 2

    @patch("blueprints.notifications._handle_device_measurement_notification")
    def test_ignores_other_entity_types(self, mock_handle, client):
        resp = client.post(
            "/api/internal/notify/measurements",
            content_type="application/json",
            headers={"NGSILD-Tenant": "montiko"},
            data=json.dumps(
                {"data": [{"id": "urn:ngsi-ld:AgriParcel:montiko:p1", "type": "AgriParcel"}]}
            ),
        )
        assert resp.status_code == 200
        assert resp.get_json()["handled"] == 0
        mock_handle.assert_not_called()

    def test_no_internal_service_secret_required(self, client):
        """Unlike /api/internal/notify (Alert), this route has no customHeaders support
        in the SDK's SubscriptionRegistrar -- namespace NetworkPolicy isolation is the
        boundary here, not this header."""
        resp = client.post(
            "/api/internal/notify/measurements",
            content_type="application/json",
            data=json.dumps({"data": []}),
        )
        assert resp.status_code == 200


class TestHandleDeviceMeasurementNotification:
    @patch("blueprints.notifications.SyncOrionClient")
    @patch("blueprints.notifications._get_sensor_profile_by_code", return_value=PROFILE_ROW)
    def test_builds_and_writes_real_measurements(self, mock_get_profile, mock_client_cls):
        mock_client = MagicMock()
        mock_client.upsert_entities_batch.return_value = {
            "upserted": 1, "errors": [], "entity_ids": [],
        }
        mock_client_cls.return_value = mock_client

        result = _handle_device_measurement_notification("montiko", DEVICE_ENTITY)

        assert result is True
        mock_get_profile.assert_called_once_with("montiko", "soil-sensor")
        mock_client.upsert_entities_batch.assert_called_once()
        (written,), _ = mock_client.upsert_entities_batch.call_args
        assert len(written) == 1
        assert written[0]["controlledProperty"]["value"] == "leafTemperature"
        assert written[0]["refDevice"]["object"] == DEVICE_ENTITY["id"]

    def test_no_profile_code_is_skipped_not_an_error(self):
        entity = {"id": "urn:ngsi-ld:Device:montiko:s1", "type": "Device"}
        assert _handle_device_measurement_notification("montiko", entity) is True

    @patch("blueprints.notifications._get_sensor_profile_by_code", return_value=None)
    def test_no_matching_profile_is_skipped_not_an_error(self, mock_get_profile):
        entity = {
            "id": "urn:ngsi-ld:Device:montiko:s1",
            "type": "Device",
            "profileCode": {"type": "Property", "value": "unknown-profile"},
        }
        assert _handle_device_measurement_notification("montiko", entity) is True
