"""Tests for parcel_activation — quota via tier_quotas, fail-open, dispatch contract."""

import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
# entity-manager adds ../common to path — replicate for tests
_entity_mgr_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _entity_mgr_dir)
sys.path.insert(0, os.path.join(_entity_mgr_dir, "..", "common"))

# Mock tier_quotas before importing parcel_activation (entity-manager runs
# in a container with /app/common on sys.path; for local tests we patch).
import unittest.mock
unittest.mock.patch.dict(
    "sys.modules",
    {"common": unittest.mock.MagicMock(),
     "common.tier_quotas": unittest.mock.MagicMock()},
).start()

import parcel_activation as pa


def test_max_parcels_uses_tier_quotas():
    with patch.object(pa, "_get_db") as db:
        cur = MagicMock()
        cur.fetchone.return_value = {"plan_level": 2}
        db.return_value.cursor.return_value = cur
        with patch.object(pa, "LEVEL_TO_TIER", {2: "pro"}), \
             patch.object(pa, "quotas_for_tier", return_value={"max_parcels": 20}) as q:
            assert pa._max_parcels_for_tenant("t1") == 20
            q.assert_called_once_with("pro")


def test_check_parcel_limit_fail_open_on_db_error():
    """Platform convention: quota enforcement is fail-open."""
    with patch.object(pa, "_get_db", side_effect=RuntimeError("db down")):
        ok, reason = pa.check_parcel_limit("t1", "crop-health")
    assert ok is True
    assert reason == ""


def test_check_parcel_limit_blocks_at_quota():
    with patch.object(pa, "_max_parcels_for_tenant", return_value=5), \
         patch.object(pa, "_count_active_parcels", return_value=5):
        ok, reason = pa.check_parcel_limit("t1", "crop-health")
    assert ok is False
    assert "5/5" in reason


def test_check_parcel_limit_unlimited_tier():
    with patch.object(pa, "_max_parcels_for_tenant", return_value=None), \
         patch.object(pa, "_count_active_parcels", return_value=10_000):
        ok, _ = pa.check_parcel_limit("t1", "crop-health")
    assert ok is True


def test_dispatch_requires_explicit_url():
    """No convention-based URL guessing — explicit contract or fail."""
    with patch.object(pa, "_get_setup_url", return_value=None):
        status, body = pa.dispatch_to_module("crop-health", "t1", "p1")
    assert status == 502
    assert "setup_parcel_url" in body["error"]


def test_dispatch_posts_payload_with_secret():
    with patch.object(pa, "_get_setup_url",
                      return_value="http://crop-health-api-service:8000/api/crop-health/internal/setup-parcel"), \
         patch.object(pa, "INTERNAL_SERVICE_SECRET", "s3cret"), \
         patch.object(pa.requests, "post") as post:
        post.return_value.status_code = 201
        post.return_value.content = b"{}"
        post.return_value.json.return_value = {}
        status, _ = pa.dispatch_to_module(
            "crop-health", "t1", "urn:ngsi-ld:AgriParcel:t1:P1", parcel_name="P1"
        )
    assert status == 201
    _, kwargs = post.call_args
    assert kwargs["headers"]["X-Internal-Service-Secret"] == "s3cret"
    assert kwargs["json"]["action"] == "activate"
    assert kwargs["timeout"] == 5
