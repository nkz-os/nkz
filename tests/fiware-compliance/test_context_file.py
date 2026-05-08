"""Test NGSI-LD @context file validity and accessibility.

The @context JSON-LD file is the cornerstone of NGSI-LD compliance.
It must be:
  1. Served at a stable URL
  2. Valid JSON-LD
  3. Include all platform entity types
  4. Be accessible without authentication (public @context)
"""

import json
import pytest
import requests
from conftest import CONTEXT_URL, REQUEST_TIMEOUT


class TestContextFileAccessibility:
    """Verify the @context file is publicly accessible."""

    def test_context_url_returns_200(self):
        """GET /ngsi-ld-context.json returns HTTP 200."""
        r = requests.get(CONTEXT_URL, timeout=REQUEST_TIMEOUT)
        assert r.status_code == 200, (
            f"CONTEXT_URL={CONTEXT_URL} returned {r.status_code}. "
            "The @context file MUST be publicly accessible."
        )

    def test_context_url_returns_valid_json(self):
        """Response is valid JSON."""
        r = requests.get(CONTEXT_URL, timeout=REQUEST_TIMEOUT)
        assert r.status_code == 200
        try:
            data = r.json()
        except json.JSONDecodeError as e:
            pytest.fail(f"@context is not valid JSON: {e}")

    def test_context_has_at_context_key(self):
        """JSON-LD must have a @context key at the root."""
        r = requests.get(CONTEXT_URL, timeout=REQUEST_TIMEOUT)
        assert r.status_code == 200
        data = r.json()
        assert "@context" in data, (
            "Root object MUST have a '@context' key. "
            "This is the JSON-LD context wrapper."
        )

    def test_context_content_type(self):
        """Should serve with appropriate content type."""
        r = requests.get(CONTEXT_URL, timeout=REQUEST_TIMEOUT)
        assert r.status_code == 200
        ct = r.headers.get("Content-Type", "")
        # Either application/ld+json or application/json is acceptable
        assert any(t in ct for t in ["application/ld+json", "application/json"]), (
            f"Content-Type should be application/ld+json or application/json, got {ct}"
        )

    def test_context_cors_headers(self):
        """If CORS is enabled, @context should be accessible cross-origin."""
        r = requests.options(CONTEXT_URL, timeout=REQUEST_TIMEOUT)
        # Not a hard failure — CORS is deployment-specific
        if r.status_code in (200, 204):
            acao = r.headers.get("Access-Control-Allow-Origin", "")
            if not acao:
                pytest.skip("No CORS headers — ok for internal deployments")
            # If CORS IS configured, @context should be publicly readable
            r2 = requests.get(CONTEXT_URL, timeout=REQUEST_TIMEOUT)
            assert r2.status_code == 200


class TestContextContent:
    """Verify the @context file content is complete and correct."""

    REQUIRED_PREFIXES = [
        "schema",
        "ngsi-ld",
        "saref",
        "saref4agri",
        "nkz",
    ]

    # Every entity type registered in the platform MUST appear in the @context.
    # These are the canonical types from the SDM catalog + code audit.
    REQUIRED_ENTITY_TYPES = [
        "AgriParcel",
        "AgriCrop",
        "AgriSensor",
        "WeatherObserved",
        "Device",
        "Building",
        "AgriParcelOperation",
        "AgriculturalRobot",
        "AgriculturalTractor",
        "AgriculturalImplement",
        "AgriEquipment",
        "AgriOperation",
        "AgriCropObservation",
        "SatelliteImageObservation",
        "VegetationIndex",
        "LivestockAnimal",
        "LivestockGroup",
        "LivestockFarm",
        "LivestockProduction",
        "PhotovoltaicInstallation",
        "EnergyStorageSystem",
        "CropHealthAssessment",
        "DiseaseRiskAssessment",
        "CarbonStock",
        "DigitalAsset",
        "AgriEnergyTracker",
        "EnergyMeter",
        "AgriDevice",
        "AgriculturalMachine",
        "AgriRobot",
    ]

    def test_context_file_loads(self):
        """Parse and validate the @context structure."""
        r = requests.get(CONTEXT_URL, timeout=REQUEST_TIMEOUT)
        assert r.status_code == 200
        data = r.json()
        ctx = data.get("@context", data)
        assert isinstance(ctx, (dict, list)), "Expected dict or list for @context"
        if isinstance(ctx, dict):
            assert len(ctx) > 5, (
                f"@context has only {len(ctx)} entries. "
                "Expected namespace prefixes + entity type definitions."
            )

    def test_required_prefixes_present(self):
        """All standard namespace prefixes must be defined."""
        r = requests.get(CONTEXT_URL, timeout=REQUEST_TIMEOUT)
        assert r.status_code == 200
        data = r.json()
        ctx = data.get("@context", data)

        if isinstance(ctx, list):
            # List form — merge all dicts
            merged = {}
            for item in ctx:
                if isinstance(item, dict):
                    merged.update(item)
            ctx = merged

        missing = [p for p in self.REQUIRED_PREFIXES if p not in ctx]
        assert not missing, (
            f"Missing namespace prefixes in @context: {missing}. "
            "These prefixes are required for NGSI-LD entity type expansion."
        )

    def test_namespace_prefixes_are_valid_uris(self):
        """Prefix values must be valid URIs (https:// or http://)."""
        r = requests.get(CONTEXT_URL, timeout=REQUEST_TIMEOUT)
        assert r.status_code == 200
        data = r.json()
        ctx = data.get("@context", data)

        if isinstance(ctx, list):
            merged = {}
            for item in ctx:
                if isinstance(item, dict):
                    merged.update(item)
            ctx = merged

        invalid = []
        for prefix in self.REQUIRED_PREFIXES:
            val = ctx.get(prefix, "")
            if isinstance(val, str) and not (val.startswith("http://") or val.startswith("https://")):
                invalid.append(f"{prefix}={val}")

        assert not invalid, (
            f"Prefix values must be valid URIs (http[s]://): {invalid}"
        )

    def test_core_entity_types_registered(self):
        """Verify all core entity types have @context definitions."""
        r = requests.get(CONTEXT_URL, timeout=REQUEST_TIMEOUT)
        assert r.status_code == 200
        data = r.json()
        ctx = data.get("@context", data)

        if isinstance(ctx, list):
            merged = {}
            for item in ctx:
                if isinstance(item, dict):
                    merged.update(item)
            ctx = merged

        missing = [t for t in self.REQUIRED_ENTITY_TYPES if t not in ctx]
        if missing:
            pytest.fail(
                f"Entity types not registered in @context: {missing}. "
                "Every entity type published to Orion-LD MUST have a JSON-LD "
                "namespace mapping. Without it, Orion-LD cannot expand short "
                "type names to full URIs, breaking interoperability."
            )

    def test_each_entity_type_has_at_id(self):
        """Each entity type definition must include @id (the full URI)."""
        r = requests.get(CONTEXT_URL, timeout=REQUEST_TIMEOUT)
        assert r.status_code == 200
        data = r.json()
        ctx = data.get("@context", data)

        if isinstance(ctx, list):
            merged = {}
            for item in ctx:
                if isinstance(item, dict):
                    merged.update(item)
            ctx = merged

        bad = []
        for etype in self.REQUIRED_ENTITY_TYPES:
            entry = ctx.get(etype)
            if isinstance(entry, dict) and "@id" not in entry:
                bad.append(etype)

        assert not bad, (
            f"Entity types without @id in @context: {bad}. "
            "Each type MUST have an @id with the full namespace URI."
        )
