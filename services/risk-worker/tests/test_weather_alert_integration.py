"""Integration: catalog risk → processor fetch → model → notify-worthy score.

Exercises the weather_alert path end-to-end within risk-worker (weather-api and
Orion mocked): the factory builds the model from a catalog row, the processor
fetches covering alerts from the (mocked) endpoint, and the model maps the most
severe alert to a probability score that crosses the notification threshold.
"""

import os
import sys
from unittest.mock import MagicMock, patch

_RISK_WORKER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SERVICES = os.path.dirname(_RISK_WORKER)
for _p in (_RISK_WORKER, _SERVICES):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("ORION_URL", "http://orion-ld:1026")
os.environ.setdefault("CONTEXT_URL", "http://api-gateway:5000/ngsi-ld-context.json")

_NOTIFY_THRESHOLD = 50  # RiskProcessor publishes an event only at >= 50


def _unpoison_risk_models():
    for _m in [m for m in list(sys.modules) if m == "risk_models" or m.startswith("risk_models.")]:
        if isinstance(sys.modules[_m], MagicMock):
            del sys.modules[_m]


def _load_rp():
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
    return rp


def test_weather_alert_path_produces_notify_worthy_score():
    _unpoison_risk_models()
    rp = _load_rp()
    from risk_models.factory import RiskModelFactory

    risk = {
        "risk_code": "weather_alert",
        "target_sdm_type": "AgriParcel",
        "risk_domain": "agronomic",
        "model_type": "weather_alert",
        "model_config": {},
        "data_sources": ["weather_alerts"],
        "severity_levels": {"low": 30, "medium": 60, "high": 80, "critical": 95},
    }
    entity = {"id": "urn:ngsi-ld:AgriParcel:montiko:1", "type": "AgriParcel"}

    proc = rp.RiskProcessor()
    proc.postgres = None

    endpoint = MagicMock()
    endpoint.status_code = 200
    endpoint.json.return_value = {
        "alerts": [
            {
                "id": "urn:ngsi-ld:WeatherAlert:meteoalarm:es-wind",
                "severity": "severe",
                "subCategory": ["wind"],
                "description": "Severe wind",
            }
        ],
        "count": 1,
    }

    with patch("risk_processor.requests.get", return_value=endpoint):
        data_sources = proc._prepare_data_sources("montiko", risk, entity)

    model = RiskModelFactory.create_model(
        risk["risk_code"], risk["risk_domain"], risk["model_config"], risk["model_type"]
    )
    result = model.evaluate(entity["id"], "AgriParcel", "montiko", data_sources)

    assert result["probability_score"] >= _NOTIFY_THRESHOLD
    assert result["evaluation_data"]["alert_id"].endswith(":es-wind")

    severity = proc._compute_severity(
        result["probability_score"], risk["severity_levels"]
    )
    assert severity in ("medium", "high", "critical")
