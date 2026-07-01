"""Regression: tenant IDs must reach Orion AS-IS (hyphen-canonical).

The platform's canonical tenant format is hyphenated — see
services/common/tenant_utils.py (`^[a-z0-9]+(?:-[a-z0-9]+)*$`). The SDK
OrionClient sends the tenant verbatim in NGSILD-Tenant / Fiware-Service.

Several worker `_make_headers` helpers historically underscored the tenant
(`tenant_id.replace("-", "_")` + an `[^a-z0-9_]` scrub that also drops
hyphens), routing Orion writes to a phantom underscore tenant — e.g.
`asociacion-allotarra` -> `asociacion_allotarra`. This silently corrupts
data for every hyphenated (paying) tenant. These tests pin the fix: the
tenant must reach the Orion headers unchanged.
"""

import os
import sys

_SERVICES_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _SERVICES_DIR)
sys.path.insert(0, os.path.join(_SERVICES_DIR, "weather-worker"))

HYPHEN_TENANT = "asociacion-allotarra"


# --- Functional: modules that import without infra deps -------------------

def test_parcel_engine_preserves_hyphen():
    from types import SimpleNamespace

    from weather_worker.parcel_engine import ParcelWeatherEngine

    # _make_headers only reads self.context_url; a stub suffices.
    stub = SimpleNamespace(context_url=None)
    headers = ParcelWeatherEngine._make_headers(stub, HYPHEN_TENANT)
    assert headers["NGSILD-Tenant"] == HYPHEN_TENANT
    assert headers["Fiware-Service"] == HYPHEN_TENANT


def test_meteo_alerts_preserves_hyphen():
    from weather_worker.meteo_alerts_engine import _make_headers

    headers = _make_headers(HYPHEN_TENANT)
    assert headers["NGSILD-Tenant"] == HYPHEN_TENANT
    assert headers["Fiware-Service"] == HYPHEN_TENANT


def test_aemet_alerts_preserves_hyphen():
    from weather_worker.aemet_alerts_engine import _make_headers

    headers = _make_headers(HYPHEN_TENANT)
    assert headers["NGSILD-Tenant"] == HYPHEN_TENANT
    assert headers["Fiware-Service"] == HYPHEN_TENANT


# --- Source inspection: modules with lazy/heavy infra imports -------------
# (mirrors the existing test_subscription_manager.py approach)

_SOURCE_TARGETS = [
    "weather-worker/weather_worker/storage/orion_writer.py",
    "telemetry-worker/telemetry_worker/sdm.py",
    "telemetry-worker/telemetry_worker/subscription_manager.py",
    "risk-worker/subscription_manager.py",
    "entity-manager/subscription_manager.py",
    "sdm-integration/auth_middleware.py",
    "tenant-user-api/tenant_user_api.py",
]


def _read(rel_path: str) -> str:
    with open(os.path.join(_SERVICES_DIR, rel_path)) as f:
        return f.read()


def test_no_tenant_underscore_conversion_in_header_builders():
    """No header/subscription builder may underscore the tenant id."""
    offenders = []
    for rel_path in _SOURCE_TARGETS:
        src = _read(rel_path)
        if 'replace("-", "_")' in src or "replace('-', '_')" in src:
            offenders.append(rel_path)
    assert not offenders, (
        "Tenant hyphen->underscore conversion still present in: "
        + ", ".join(offenders)
    )
