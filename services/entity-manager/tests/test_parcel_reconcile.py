"""Tests for parcel_reconcile — convergence engine. Orion + DB are mocked."""

import os
import sys
import unittest.mock
from unittest.mock import MagicMock, patch

os.environ.setdefault("POSTGRES_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("CONTEXT_URL", "http://ngsi-context.test/ngsi-ld-context.json")
os.environ.setdefault("INTERNAL_SERVICE_SECRET", "test-secret")

_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _dir)
sys.path.insert(0, os.path.join(_dir, "..", "common"))

# parcel_reconcile imports parcel_activation (common.tier_quotas) and, lazily,
# common.auth_middleware.inject_fiware_headers — stub both so the real packages
# are not required for unit tests.
unittest.mock.patch.dict(
    "sys.modules",
    {"common": MagicMock(), "common.tier_quotas": MagicMock(),
     "common.auth_middleware": MagicMock()},
).start()

from datetime import datetime, timedelta, timezone

import parcel_reconcile as pr


def _resp(status, payload):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    return r


def test_get_live_parcel_ids_returns_set_of_urns():
    page = [
        {"id": "urn:ngsi-ld:AgriParcel:aaa", "type": "AgriParcel"},
        {"id": "urn:ngsi-ld:AgriParcel:bbb", "type": "AgriParcel"},
    ]
    with patch.object(pr.requests, "get", side_effect=[_resp(200, page), _resp(200, [])]):
        ids = pr.get_live_parcel_ids("montiko")
    assert ids == {"urn:ngsi-ld:AgriParcel:aaa", "urn:ngsi-ld:AgriParcel:bbb"}


def test_get_live_parcel_ids_query_does_not_send_attrs_id():
    """Regression (incident 2026-06-22): attrs=id makes Orion-LD return 0
    entities (no AgriParcel has an 'id' attribute) -> false-empty live set ->
    backstop deletes all derived entities. The query must NOT send attrs=id."""
    captured = {}

    def _get(url, **kw):
        captured.update(kw)
        return _resp(200, [])

    with patch.object(pr.requests, "get", side_effect=_get):
        pr.get_live_parcel_ids("montiko")
    assert captured["params"].get("attrs") != "id"


def test_get_live_parcel_ids_returns_none_on_http_error():
    """CRITICAL false-zero guard: a non-200 must NOT look like 'zero parcels'."""
    with patch.object(pr.requests, "get", return_value=_resp(500, {})):
        assert pr.get_live_parcel_ids("montiko") is None


def test_get_live_parcel_ids_returns_none_on_exception():
    with patch.object(pr.requests, "get", side_effect=pr.requests.RequestException("boom")):
        assert pr.get_live_parcel_ids("montiko") is None


def test_get_live_parcel_ids_none_when_context_url_missing():
    """SEMANTIC false-zero guard: without CONTEXT_URL the @context Link is absent,
    so a context-less AgriParcel query returns a FALSE empty list. The engine must
    refuse to query (return None / skip) rather than treat it as zero parcels."""
    with patch.object(pr, "CONTEXT_URL", ""), patch.object(pr.requests, "get") as get:
        assert pr.get_live_parcel_ids("montiko") is None
    get.assert_not_called()  # must not even hit Orion


def test_get_auto_provision_modules():
    cur = MagicMock()
    cur.fetchall.return_value = [("weather",)]
    conn = MagicMock()
    conn.cursor.return_value = cur
    with patch.object(pr, "_get_db", return_value=conn):
        assert pr.get_auto_provision_modules() == ["weather"]


def test_get_rows_returns_dicts():
    cur = MagicMock()
    cur.description = [("tenant_id",), ("parcel_id",), ("module_id",),
                       ("setup_status",), ("retry_count",), ("next_retry_at",)]
    cur.fetchall.return_value = [
        ("montiko", "urn:ngsi-ld:AgriParcel:aaa", "weather", "ok", 0, None),
    ]
    conn = MagicMock()
    conn.cursor.return_value = cur
    with patch.object(pr, "_get_db", return_value=conn):
        rows = pr.get_rows("montiko")
    assert rows[0]["module_id"] == "weather"
    assert rows[0]["setup_status"] == "ok"


def test_backoff_seconds_schedule():
    # capped exponential: 30s, 120s, 600s, 3600s, then capped
    assert pr.backoff_seconds(0) == 30
    assert pr.backoff_seconds(1) == 120
    assert pr.backoff_seconds(2) == 600
    assert pr.backoff_seconds(3) == 3600
    assert pr.backoff_seconds(99) == 3600


def test_is_due_for_retry():
    now = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
    past = {"next_retry_at": now - timedelta(seconds=1)}
    future = {"next_retry_at": now + timedelta(seconds=60)}
    none_row = {"next_retry_at": None}
    assert pr.is_due_for_retry(past, now) is True
    assert pr.is_due_for_retry(future, now) is False
    assert pr.is_due_for_retry(none_row, now) is True


def test_resolve_parcel_ref_relationship():
    e = {"id": "x", "hasAgriParcel": {"type": "Relationship",
                                      "object": "urn:ngsi-ld:AgriParcel:aaa"}}
    spec = {"type": "VegetationIndex", "ref_keys": ["hasAgriParcel"]}
    assert pr.resolve_parcel_ref(e, spec) == "urn:ngsi-ld:AgriParcel:aaa"


def test_registry_includes_zone_assessment():
    # Zone assessments of a deleted parcel must be swept like the parcel-level
    # CropHealthAssessment (they reference the parcel via hasAgriParcel).
    types = {s["type"] for s in pr.DERIVED_TYPE_REGISTRY}
    assert "CropHealthZoneAssessment" in types
    spec = next(s for s in pr.DERIVED_TYPE_REGISTRY if s["type"] == "CropHealthZoneAssessment")
    assert "hasAgriParcel" in spec["ref_keys"]


def test_resolve_parcel_ref_risk_requires_target_type():
    spec = {"type": "RiskAssessment", "ref_keys": ["targetEntityId"],
            "require_target_type": "AgriParcel"}
    parcel = {"id": "r1",
              "targetEntityId": {"value": "urn:ngsi-ld:AgriParcel:aaa"},
              "targetEntityType": {"value": "AgriParcel"}}
    crop = {"id": "r2",
            "targetEntityId": {"value": "urn:ngsi-ld:AgriCrop:zzz"},
            "targetEntityType": {"value": "AgriCrop"}}
    assert pr.resolve_parcel_ref(parcel, spec) == "urn:ngsi-ld:AgriParcel:aaa"
    assert pr.resolve_parcel_ref(crop, spec) is None  # not parcel-typed -> ignored


def test_find_backstop_orphans_skips_live_and_owned():
    live = {"urn:ngsi-ld:AgriParcel:live"}
    owned = {"urn:ngsi-ld:AgriParcel:owned"}
    entities = {
        "VegetationIndex": [
            {"id": "vi-live", "hasAgriParcel": {"object": "urn:ngsi-ld:AgriParcel:live"}},
            {"id": "vi-owned", "hasAgriParcel": {"object": "urn:ngsi-ld:AgriParcel:owned"}},
            {"id": "vi-orphan", "hasAgriParcel": {"object": "urn:ngsi-ld:AgriParcel:gone"}},
        ],
    }
    with patch.object(pr, "DERIVED_TYPE_REGISTRY",
                      [{"type": "VegetationIndex", "ref_keys": ["hasAgriParcel"]}]), \
         patch.object(pr, "query_entities", side_effect=lambda t, ty: entities.get(ty, [])):
        orphans = pr.find_backstop_orphans("montiko", live, owned)
    assert orphans == ["vi-orphan"]


def test_reconcile_tenant_skips_on_false_zero():
    """If live parcels is None, NOTHING is torn down or deleted."""
    with patch.object(pr, "get_live_parcel_ids", return_value=None), \
         patch.object(pr, "get_rows") as rows, \
         patch.object(pr, "dispatch_to_module") as disp, \
         patch.object(pr, "find_backstop_orphans") as bs:
        result = pr.reconcile_tenant("montiko")
    assert result["skipped"] is True
    rows.assert_not_called()
    disp.assert_not_called()
    bs.assert_not_called()


def test_reconcile_tenant_provisions_auto_module():
    with patch.object(pr, "get_live_parcel_ids",
                      return_value={"urn:ngsi-ld:AgriParcel:p1"}), \
         patch.object(pr, "get_rows", return_value=[]), \
         patch.object(pr, "get_auto_provision_modules", return_value=["weather"]), \
         patch.object(pr, "insert_pending") as ins, \
         patch.object(pr, "dispatch_to_module", return_value=(200, {})) as disp, \
         patch.object(pr, "mark_ok") as ok, \
         patch.object(pr, "find_backstop_orphans", return_value=[]):
        result = pr.reconcile_tenant("montiko")
    ins.assert_called_once_with("montiko", "urn:ngsi-ld:AgriParcel:p1", "weather")
    disp.assert_called_once()
    assert disp.call_args.kwargs["action"] == "activate"
    ok.assert_called_once()
    assert result["provisioned"] == 1


def test_reconcile_tenant_tears_down_dead_parcel_and_deletes_row_on_ok():
    rows = [{"parcel_id": "urn:ngsi-ld:AgriParcel:dead", "module_id": "crop-health",
             "setup_status": "ok", "retry_count": 0, "next_retry_at": None}]
    with patch.object(pr, "get_live_parcel_ids", return_value=set()), \
         patch.object(pr, "get_rows", return_value=rows), \
         patch.object(pr, "get_auto_provision_modules", return_value=[]), \
         patch.object(pr, "dispatch_to_module", return_value=(200, {})) as disp, \
         patch.object(pr, "delete_row") as drow, \
         patch.object(pr, "find_backstop_orphans", return_value=[]):
        result = pr.reconcile_tenant("montiko")
    assert disp.call_args.kwargs["action"] == "teardown"
    drow.assert_called_once_with("montiko", "urn:ngsi-ld:AgriParcel:dead", "crop-health")
    assert result["torn_down"] == 1


def test_reconcile_tenant_keeps_row_on_teardown_failure():
    rows = [{"parcel_id": "urn:ngsi-ld:AgriParcel:dead", "module_id": "crop-health",
             "setup_status": "ok", "retry_count": 0, "next_retry_at": None}]
    with patch.object(pr, "get_live_parcel_ids", return_value=set()), \
         patch.object(pr, "get_rows", return_value=rows), \
         patch.object(pr, "get_auto_provision_modules", return_value=[]), \
         patch.object(pr, "dispatch_to_module", return_value=(502, {"error": "no teardown"})), \
         patch.object(pr, "delete_row") as drow, \
         patch.object(pr, "mark_error") as merr, \
         patch.object(pr, "find_backstop_orphans", return_value=[]):
        pr.reconcile_tenant("montiko")
    drow.assert_not_called()
    merr.assert_called_once()


def test_run_once_reconciles_each_tenant():
    with patch.object(pr, "get_active_tenants", return_value=["t1", "t2"]), \
         patch.object(pr, "reconcile_tenant") as rec:
        pr.run_once()
    assert rec.call_count == 2
    rec.assert_any_call("t1")
    rec.assert_any_call("t2")


def test_run_once_isolates_tenant_failure():
    """One tenant raising must not stop the others."""
    with patch.object(pr, "get_active_tenants", return_value=["bad", "good"]), \
         patch.object(pr, "reconcile_tenant",
                      side_effect=[RuntimeError("boom"), {"tenant": "good"}]) as rec:
        pr.run_once()  # must not raise
    assert rec.call_count == 2
