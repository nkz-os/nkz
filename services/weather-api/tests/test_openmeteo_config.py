"""Open-Meteo base URL/models come from settings; defaults unchanged."""

import os
import sys
from unittest.mock import patch

_SVC_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_SERVICES_DIR = os.path.dirname(_SVC_DIR)
for _p in [_SVC_DIR, _SERVICES_DIR, os.path.join(_SERVICES_DIR, "common")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from app.config import Settings, with_models, settings


def test_defaults(monkeypatch):
    monkeypatch.delenv("OPENMETEO_API_URL", raising=False)
    monkeypatch.delenv("OPENMETEO_MODELS", raising=False)
    s = Settings()
    assert s.openmeteo_api_url == "https://api.open-meteo.com/v1"
    assert s.openmeteo_models == ""


def test_env_override(monkeypatch):
    monkeypatch.setenv("OPENMETEO_API_URL", "http://om.local/v1/")
    monkeypatch.setenv("OPENMETEO_MODELS", " icon_eu ")
    s = Settings()
    assert s.openmeteo_api_url == "http://om.local/v1"
    assert s.openmeteo_models == "icon_eu"


def test_with_models_omits_when_empty():
    with patch.object(settings, "openmeteo_models", ""):
        assert with_models({"a": 1}) == {"a": 1}


def test_with_models_adds_when_set():
    with patch.object(settings, "openmeteo_models", "icon_eu"):
        assert with_models({"a": 1}) == {"a": 1, "models": "icon_eu"}


def test_routers_have_no_hardcoded_forecast_url():
    import app.routers.coordinates as co
    import app.routers.municipality_forecast as mf
    assert not hasattr(co, "OPENMETEO_FORECAST_URL")
    assert not hasattr(mf, "OPENMETEO_FORECAST_URL")
