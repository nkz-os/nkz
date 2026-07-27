"""Tests for the weather_alert risk model (pure severity→score mapping)."""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The factory imports all models in one try-block; water_stress_model imports
# common.ngsi_headers, absent in the test env. Stub it so the factory dispatch
# test can import every sibling (pattern used across nkz worker tests).
sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.ngsi_headers", MagicMock())

from risk_models.weather_alert_model import WeatherAlertRiskModel


def _model(config=None):
    return WeatherAlertRiskModel("weather_alert", config or {})


def test_no_alerts_scores_zero():
    r = _model().evaluate("p", "AgriParcel", "t", {"weather_alerts": []})
    assert r["probability_score"] == 0.0
    assert r["evaluation_data"]["condition"] == "no_active_alert"


def test_missing_source_scores_zero():
    r = _model().evaluate("p", "AgriParcel", "t", {})
    assert r["probability_score"] == 0.0


def test_severe_alert_crosses_notify_threshold():
    alerts = [
        {
            "id": "urn:ngsi-ld:WeatherAlert:meteoalarm:x",
            "severity": "severe",
            "subCategory": ["wind"],
            "description": "Severe wind",
        }
    ]
    r = _model().evaluate("p", "AgriParcel", "t", {"weather_alerts": alerts})
    assert r["probability_score"] >= 50
    assert r["evaluation_data"]["alert_id"].endswith(":x")
    assert r["evaluation_data"]["severity"] == "severe"
    assert r["evaluation_data"]["covering_alert_count"] == 1


def test_minor_alert_below_notify_threshold():
    alerts = [{"id": "m", "severity": "minor", "subCategory": ["fog"]}]
    r = _model().evaluate("p", "AgriParcel", "t", {"weather_alerts": alerts})
    assert r["probability_score"] < 50


def test_highest_severity_wins():
    alerts = [
        {"id": "a", "severity": "minor", "subCategory": ["fog"]},
        {"id": "b", "severity": "critical", "subCategory": ["wind"]},
    ]
    r = _model().evaluate("p", "AgriParcel", "t", {"weather_alerts": alerts})
    assert r["evaluation_data"]["alert_id"] == "b"
    assert r["probability_score"] >= 90


def test_severity_as_keyvalues_dict_shape():
    alerts = [{"id": "k", "severity": {"value": "severe"}, "subCategory": {"value": ["wind"]}}]
    r = _model().evaluate("p", "AgriParcel", "t", {"weather_alerts": alerts})
    assert r["probability_score"] >= 50
    assert r["evaluation_data"]["subCategory"] == ["wind"]


def test_config_overrides_severity_score():
    alerts = [{"id": "c", "severity": "minor", "subCategory": ["fog"]}]
    r = _model({"severity_scores": {"minor": 70.0}}).evaluate(
        "p", "AgriParcel", "t", {"weather_alerts": alerts}
    )
    assert r["probability_score"] == 70.0


def test_factory_dispatches_weather_alert():
    from risk_models.factory import RiskModelFactory

    m = RiskModelFactory.create_model("weather_alert", "agronomic", {}, "weather_alert")
    assert m.__class__.__name__ == "WeatherAlertRiskModel"
