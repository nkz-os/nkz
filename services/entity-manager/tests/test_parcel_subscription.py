"""TDD tests for parcel_subscription module — idempotent Orion subscription."""
import sys
import os
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Stubs — must come BEFORE importing parcel_subscription
# ---------------------------------------------------------------------------
os.environ.setdefault("ORION_URL", "http://orion:1026")
os.environ.setdefault("CONTEXT_URL", "http://api-gateway-service:5000/ngsi-ld-context.json")

_services_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _services_dir not in sys.path:
    sys.path.insert(0, _services_dir)

_entity_manager_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _entity_manager_dir not in sys.path:
    sys.path.insert(0, _entity_manager_dir)

_common_mock = MagicMock()
_common_mock.inject_fiware_headers = lambda h, t=None, **kw: h
sys.modules.setdefault("common", _common_mock)
sys.modules.setdefault("common.auth_middleware", _common_mock)

from parcel_subscription import (  # noqa: E402
    build_projection_subscription,
    ensure_projection_subscription,
)

_ENDPOINT = "http://entity-manager-service:5000/internal/parcels/project"
_SECRET = "s3cret"
_TENANT = "montiko"


def test_subscription_body_watches_agriparcel_to_internal_endpoint():
    sub = build_projection_subscription(_ENDPOINT, _SECRET, _TENANT)
    assert sub["type"] == "Subscription"
    assert sub["entities"][0]["type"] == "AgriParcel"
    assert sub["notification"]["endpoint"]["uri"].endswith("/internal/parcels/project")
    ri = sub["notification"]["endpoint"]["receiverInfo"]
    assert any(h.get("key") == "X-Internal-Service-Secret" and h.get("value") == _SECRET for h in ri)


def test_subscription_body_includes_ngsild_tenant_in_receiver_info():
    """FIX 3: receiverInfo must include NGSILD-Tenant so Orion forwards it on notifications."""
    sub = build_projection_subscription(_ENDPOINT, _SECRET, _TENANT)
    ri = sub["notification"]["endpoint"]["receiverInfo"]
    assert any(
        h.get("key") == "NGSILD-Tenant" and h.get("value") == _TENANT for h in ri
    ), f"NGSILD-Tenant not found in receiverInfo: {ri}"


def test_ensure_is_idempotent_skips_when_present():
    existing = [
        {
            "id": "urn:x",
            "entities": [{"type": "AgriParcel"}],
            "notification": {"endpoint": {"uri": _ENDPOINT}},
        }
    ]
    with patch("parcel_subscription._list_subscriptions", return_value=existing), \
         patch("parcel_subscription._create_subscription") as cr:
        ensure_projection_subscription("montiko", _ENDPOINT, _SECRET)
        assert not cr.called


def test_ensure_creates_when_absent():
    with patch("parcel_subscription._list_subscriptions", return_value=[]), \
         patch("parcel_subscription._create_subscription") as cr:
        ensure_projection_subscription("montiko", _ENDPOINT, _SECRET)
        assert cr.called


def test_ensure_creates_when_uri_differs():
    existing = [
        {
            "id": "urn:y",
            "entities": [{"type": "AgriParcel"}],
            "notification": {"endpoint": {"uri": "http://other-service:5000/internal/parcels/project"}},
        }
    ]
    with patch("parcel_subscription._list_subscriptions", return_value=existing), \
         patch("parcel_subscription._create_subscription") as cr:
        ensure_projection_subscription("montiko", _ENDPOINT, _SECRET)
        assert cr.called


def test_subscription_body_format_normalized():
    sub = build_projection_subscription(_ENDPOINT, _SECRET, _TENANT)
    assert sub["notification"]["format"] == "normalized"
    assert sub["notification"]["endpoint"]["accept"] == "application/json"
