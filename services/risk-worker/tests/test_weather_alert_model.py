"""Tests for the weather_alert risk model (pure severity→score mapping)."""

import os
import sys
from unittest.mock import MagicMock

# risk-worker dir (risk_models) + services dir (real `common`, needed by the
# factory's import of water_stress_model → common.ngsi_headers).
_RISK_WORKER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SERVICES = os.path.dirname(_RISK_WORKER)
for _p in (_RISK_WORKER, _SERVICES):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _unpoison_risk_models():
    """Drop any MagicMock risk_models entry a sibling test left in sys.modules
    so the real package (re)loads from disk."""
    for _m in [m for m in list(sys.modules) if m == "risk_models" or m.startswith("risk_models.")]:
        if isinstance(sys.modules[_m], MagicMock):
            del sys.modules[_m]


def _model(config=None):
    _unpoison_risk_models()
    from risk_models.weather_alert_model import WeatherAlertRiskModel

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
    _unpoison_risk_models()
    from risk_models.factory import RiskModelFactory

    m = RiskModelFactory.create_model("weather_alert", "agronomic", {}, "weather_alert")
    assert m.__class__.__name__ == "WeatherAlertRiskModel"
