"""agro_status_service Open-Meteo fallback honours OPENMETEO_API_URL / OPENMETEO_MODELS."""

import os
import sys
from unittest.mock import MagicMock, patch

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
_COMMON_DIR = os.path.join(_SERVICES_DIR, "common")

for _p in [_SVC_DIR, _SERVICES_DIR, _COMMON_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from telemetry_worker.services import agro_status_service as mod


def _calls(monkeypatch, url=None, models=None):
    for k, v in (("OPENMETEO_API_URL", url), ("OPENMETEO_MODELS", models)):
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    fail = MagicMock(status_code=500)  # priority 1 falls through
    ok = MagicMock(status_code=200)
    ok.json.return_value = {}
    svc = mod.AgroStatusService(MagicMock(), "t")
    with patch.object(mod.requests, "get", side_effect=[fail, ok]) as get:
        svc.fetch_openmeteo_data(1.0, 2.0)
    return get.call_args_list[1]


def test_defaults_unchanged(monkeypatch):
    c = _calls(monkeypatch)
    assert c.args[0] == "https://api.open-meteo.com/v1/forecast"
    assert "models" not in c.kwargs["params"]


def test_custom_base_and_models(monkeypatch):
    c = _calls(monkeypatch, "http://om.local/v1/", "icon_eu")
    assert c.args[0] == "http://om.local/v1/forecast"
    assert c.kwargs["params"]["models"] == "icon_eu"
