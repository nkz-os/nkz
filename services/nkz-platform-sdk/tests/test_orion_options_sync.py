"""SyncOrionClient query_entities/get_entity honor the `options` param.

SyncOrionClient wraps requests.Session (not httpx), so respx cannot intercept
its traffic. These tests patch the session's get() and assert the params kwarg,
verifying the same options wiring as the async tests in test_orion_options.py.
"""

from unittest.mock import MagicMock, patch

from nkz_platform_sdk.orion import SyncOrionClient

ORION = "http://orion-ld-service:1026"


def _ok(json_body):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = json_body
    return resp


def test_query_entities_appends_options():
    client = SyncOrionClient("default", base_url=ORION)
    with patch.object(client._session, "get", return_value=_ok([])) as mock_get:
        client.query_entities(type="AgriCrop", options="keyValues")
    assert mock_get.call_args.kwargs["params"].get("options") == "keyValues"


def test_query_entities_omits_options_when_none():
    client = SyncOrionClient("default", base_url=ORION)
    with patch.object(client._session, "get", return_value=_ok([])) as mock_get:
        client.query_entities(type="AgriCrop")
    assert "options" not in mock_get.call_args.kwargs["params"]


def test_get_entity_appends_options():
    client = SyncOrionClient("default", base_url=ORION)
    uri = "urn:ngsi-ld:AgriParcel:P1"
    with patch.object(client._session, "get", return_value=_ok({"id": uri})) as mock_get:
        client.get_entity(uri, options="keyValues")
    assert mock_get.call_args.kwargs["params"] == {"options": "keyValues"}


def test_get_entity_omits_options_when_none():
    client = SyncOrionClient("default", base_url=ORION)
    uri = "urn:ngsi-ld:AgriParcel:P1"
    with patch.object(client._session, "get", return_value=_ok({"id": uri})) as mock_get:
        client.get_entity(uri)
    assert mock_get.call_args.kwargs["params"] is None
