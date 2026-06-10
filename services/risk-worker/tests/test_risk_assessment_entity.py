"""Test that risk evaluations create RiskAssessment entities in Orion-LD."""
import sys
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

# ── Setup mocks BEFORE importing risk_processor ──────────────────────────
# Pre-register task_queue with a mock TaskQueue class
_mock_tq_module = MagicMock()
_mock_tq_module.TaskQueue = MagicMock()
sys.modules["task_queue"] = _mock_tq_module
sys.modules["db_helper"] = MagicMock()
sys.modules["db_helper"].set_tenant_context = MagicMock()
sys.modules["tenant_utils"] = MagicMock()
sys.modules["tenant_utils"].normalize_tenant_id = lambda x: x.replace("-", "_").replace(" ", "_")
sys.modules["risk_models"] = MagicMock()
sys.modules["risk_models.factory"] = MagicMock()

import os
os.environ.setdefault("ORION_URL", "http://orion-ld:1026")
os.environ.setdefault("CONTEXT_URL", "http://api-gateway:5000/ngsi-ld-context.json")

# Patch os.path.exists to find our mock AND patch importlib to not load from disk
with (
    patch("os.path.exists", return_value=True),
    patch("importlib.util.spec_from_file_location") as mock_spec,
    patch("importlib.util.module_from_spec") as mock_mod,
):
    mock_mod.return_value = _mock_tq_module
    mock_spec.return_value.loader.exec_module = MagicMock()
    from risk_processor import RiskProcessor, _make_orion_headers  # noqa: E402


class TestRiskAssessmentEntityCreation:
    @patch("risk_processor.requests.post")
    def test_store_creates_entity_in_orion(self, mock_post):
        mock_post.return_value.status_code = 201
        processor = RiskProcessor()
        processor.postgres = None

        result = processor._store_risk_evaluation(
            tenant_id="test-tenant",
            entity_id="urn:ngsi-ld:AgriCrop:parcel-42",
            entity_type="AgriCrop",
            risk_code="FROST_DAMAGE",
            probability_score=75.5,
            evaluation_data={"temp_min": -2.0},
            confidence=0.9,
        )
        assert result is True
        mock_post.assert_called_once()

        url = mock_post.call_args[0][0]
        assert "ngsi-ld/v1/entityOperations/upsert" in url

        entity = mock_post.call_args[1]["json"][0]
        assert entity["type"] == "RiskAssessment"
        assert entity["riskCode"]["value"] == "FROST_DAMAGE"
        assert entity["probabilityScore"]["value"] == 75.5
        assert entity["targetEntityId"]["value"] == "urn:ngsi-ld:AgriCrop:parcel-42"
        assert entity["confidence"]["value"] == 0.9

    @patch("risk_processor.requests.post")
    def test_store_handles_orion_failure(self, mock_post):
        mock_post.side_effect = Exception("Connection refused")
        processor = RiskProcessor()
        processor.postgres = None

        result = processor._store_risk_evaluation(
            tenant_id="test-tenant",
            entity_id="urn:ngsi-ld:AgriCrop:parcel-42",
            entity_type="AgriCrop",
            risk_code="FROST_DAMAGE",
            probability_score=75.5,
            evaluation_data={},
            confidence=0.9,
        )
        assert result is False

    def test_headers_include_tenant(self):
        headers = _make_orion_headers("test-tenant")
        assert headers["NGSILD-Tenant"] == "test_tenant"
        assert headers["Fiware-Service"] == "test_tenant"
        assert headers["Content-Type"] == "application/ld+json"
