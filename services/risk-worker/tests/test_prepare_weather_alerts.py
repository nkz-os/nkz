"""Tests for RiskProcessor fetching covering weather alerts per parcel."""

import os
import sys
from unittest.mock import MagicMock, patch

# Real `common` (services/common) and the real risk_models package must stay
# importable — do NOT stub them (would poison sibling test files).
_RISK_WORKER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SERVICES = os.path.dirname(_RISK_WORKER)
for _p in (_RISK_WORKER, _SERVICES):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("ORION_URL", "http://orion-ld:1026")
os.environ.setdefault("CONTEXT_URL", "http://api-gateway:5000/ngsi-ld-context.json")

_rp = None


def _load_rp():
    """Import risk_processor lazily (at test-run time), stubbing only the
    risk-worker-local task_queue/db_helper. Deferred so this module's import
    does not pre-bind common.ngsi_headers ahead of sibling tests that rely on
    setting their tenant_utils stub first (import-order fragility)."""
    global _rp
    if _rp is not None:
        return _rp
    mock_tq = MagicMock()
    mock_tq.TaskQueue = MagicMock()
    sys.modules["task_queue"] = mock_tq
    sys.modules["db_helper"] = MagicMock()
    sys.modules["db_helper"].set_tenant_context = MagicMock()
    with (
        patch("os.path.exists", return_value=True),
        patch("importlib.util.spec_from_file_location") as mock_spec,
        patch("importlib.util.module_from_spec") as mock_mod,
    ):
        mock_mod.return_value = mock_tq
        mock_spec.return_value.loader.exec_module = MagicMock()
        import risk_processor as rp  # noqa: E402
    _rp = rp
    return rp


def _proc():
    rp = _load_rp()
    proc = rp.RiskProcessor()
    proc.postgres = None
    return proc


def test_prepare_fetches_weather_alerts():
    proc = _proc()
    risk = {"data_sources": ["weather_alerts"], "model_config": {}}
    entity = {"id": "urn:ngsi-ld:AgriParcel:t:1", "type": "AgriParcel"}
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {"alerts": [{"id": "a", "severity": "severe"}], "count": 1}
    with patch("risk_processor.requests.get", return_value=m) as g:
        ds = proc._prepare_data_sources("montiko", risk, entity)
    assert ds["weather_alerts"][0]["id"] == "a"
    assert "/parcel/urn:ngsi-ld:AgriParcel:t:1/alerts" in g.call_args[0][0]
    assert g.call_args[1]["headers"]["X-Tenant-ID"] == "montiko"


def test_prepare_weather_alerts_absent_when_not_required():
    proc = _proc()
    risk = {"data_sources": [], "model_config": {}}
    entity = {"id": "urn:ngsi-ld:AgriParcel:t:1", "type": "AgriParcel"}
    ds = proc._prepare_data_sources("montiko", risk, entity)
    assert "weather_alerts" not in ds


def test_prepare_weather_alerts_endpoint_error_is_empty_list():
    proc = _proc()
    risk = {"data_sources": ["weather_alerts"], "model_config": {}}
    entity = {"id": "urn:ngsi-ld:AgriParcel:t:1", "type": "AgriParcel"}
    m = MagicMock()
    m.status_code = 500
    m.text = "boom"
    with patch("risk_processor.requests.get", return_value=m):
        ds = proc._prepare_data_sources("montiko", risk, entity)
    assert ds["weather_alerts"] == []
