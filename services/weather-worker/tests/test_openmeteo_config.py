"""Open-Meteo base URL and models are configurable; defaults unchanged."""

import os
import sys
from unittest.mock import MagicMock, patch

_ww = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ww)
sys.path.insert(0, os.path.dirname(_ww))

import weather_worker.parcel_engine as pe
from weather_worker.providers.openmeteo_provider import OpenMeteoProvider


def _ok():
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {}
    return r


def _engine(**kw):
    return pe.ParcelWeatherEngine(orion_url="http://o.test", **kw)


def test_engine_default_url_and_no_models_param(monkeypatch):
    monkeypatch.delenv("OPENMETEO_API_URL", raising=False)
    monkeypatch.delenv("OPENMETEO_MODELS", raising=False)
    with patch.object(pe.requests, "get", return_value=_ok()) as get:
        _engine()._fetch_openmeteo(1.0, 2.0)
    assert get.call_args.args[0] == "https://api.open-meteo.com/v1/forecast"
    assert "models" not in get.call_args.kwargs["params"]


def test_engine_custom_url_and_models():
    with patch.object(pe.requests, "get", return_value=_ok()) as get:
        _engine(
            openmeteo_url="http://om.local/v1", openmeteo_models="icon_eu,ecmwf_ifs"
        )._fetch_openmeteo(1.0, 2.0)
    assert get.call_args.args[0] == "http://om.local/v1/forecast"
    assert get.call_args.kwargs["params"]["models"] == "icon_eu,ecmwf_ifs"


def _provider_params(provider):
    with patch.object(provider.session, "get", return_value=_ok()) as get:
        provider.get_forecast(1.0, 2.0, days=3)
    return get.call_args.args[0], get.call_args.kwargs["params"]


def test_provider_omits_models_when_empty():
    url, params = _provider_params(OpenMeteoProvider())
    assert url == "https://api.open-meteo.com/v1/forecast"
    assert "models" not in params


def test_provider_sends_models_and_custom_base():
    url, params = _provider_params(
        OpenMeteoProvider(api_url="http://om.local/v1/", models="icon_eu")
    )
    assert url == "http://om.local/v1/forecast"
    assert params["models"] == "icon_eu"
