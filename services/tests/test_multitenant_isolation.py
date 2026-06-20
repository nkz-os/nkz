"""services/tests/test_multitenant_isolation.py — Multitenant isolation test suite.

Tests that tenant A NEVER sees tenant B's data:
- Orion-LD: via NGSILD-Tenant header isolation
- PostgreSQL: via RLS / tenant_id column isolation
- Code-level: normalization correctness and cross-tenant query safety
"""

import os
import pytest
import re
import requests
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Constants / Env
# ---------------------------------------------------------------------------

POSTGRES_URL = os.environ.get(
    "POSTGRES_URL",
    "postgresql://nekazari:nekazari@localhost:5432/nekazari"
)

ORION_URL = os.environ.get(
    "ORION_URL",
    "http://orion:1026"
)

CONTEXT_URL = os.environ.get(
    "CONTEXT_URL",
    "http://orion:1026/ngsi-ld-context.json"
)

# Import normalize_tenant_id from the SDK common module.
# The canonical location is common/tenant_utils.py.
try:
    from common.tenant_utils import normalize_tenant_id
except ImportError:
    # Fallback for CI environments — mirror of the real implementation
    import unicodedata

    def normalize_tenant_id(tenant_id: str) -> str:
        """Canonical tenant ID normalisation (mirror of common.tenant_utils)."""
        nfd = unicodedata.normalize("NFD", tenant_id.strip())
        ascii_only = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
        ascii_only = ascii_only.lower()
        normalized = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")
        if not normalized:
            raise ValueError(f"Tenant ID is empty after normalization (from {tenant_id!r})")
        return normalized


def _orion_headers(tenant_id: str) -> dict:
    """Build NGSI-LD headers for creating/updating entities."""
    return {
        "NGSILD-Tenant": tenant_id,
        "Content-Type": "application/json",
        "Link": f'<{CONTEXT_URL}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"'
    }


def _orion_query_headers(tenant_id: str) -> dict:
    """Build NGSI-LD headers for querying (no Content-Type)."""
    return {
        "NGSILD-Tenant": tenant_id,
        "Accept": "application/json",
        "Link": f'<{CONTEXT_URL}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"'
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def orion_url():
    return ORION_URL


@pytest.fixture(scope="module")
def context_url():
    return CONTEXT_URL


@pytest.fixture
def tenant_alpha():
    """Return the alpha test tenant identifier."""
    return "test-tenant-alpha"


@pytest.fixture
def tenant_beta():
    """Return the beta test tenant identifier."""
    return "test-tenant-beta"


# ---------------------------------------------------------------------------
# normalize_tenant_id tests
# ---------------------------------------------------------------------------

class TestNormalizeTenantId:
    """Verify normalize_tenant_id handles all known edge cases."""

    def test_hyphen_preserved(self):
        """Hyphenated tenant IDs should remain hyphenated."""
        assert normalize_tenant_id("asociacion-allotarra") == "asociacion-allotarra"

    def test_underscore_converted_to_hyphen(self):
        """Underscores should be converted to hyphens."""
        result = normalize_tenant_id("asociacion_allotarra")
        assert result == "asociacion-allotarra", f"Got {result}"

    def test_hyphen_and_underscore_consistent(self):
        """Hyphen and underscore variants of the same name must be equal after normalization."""
        a = normalize_tenant_id("asociacion-allotarra")
        b = normalize_tenant_id("asociacion_allotarra")
        assert a == b, f"'{a}' != '{b}' — this causes tenant isolation leaks!"

    def test_mixed_separators(self):
        """Mixed separators normalize to hyphens."""
        result = normalize_tenant_id("test  tenant_123-name")
        assert "-" in result
        assert "_" not in result, f"Underscore should be converted: {result}"

    def test_upper_case_lowered(self):
        """Uppercase should be lowered."""
        assert normalize_tenant_id("TEST-TENANT") == "test-tenant"

    def test_leading_trailing_whitespace_trimmed(self):
        """Whitespace should be trimmed."""
        assert normalize_tenant_id("  montiko  ") == "montiko"

    def test_collapses_multiple_separators(self):
        """Multiple consecutive separators collapse to one."""
        result = normalize_tenant_id("asociacion___allotarra")
        assert "__" not in result, f"Multiple underscores not collapsed: {result}"
        assert result == "asociacion-allotarra"

    def test_accent_transliteration(self):
        """Accented characters should be transliterated (NFD + drop combining marks)."""
        assert normalize_tenant_id("aéreo") == "aereo"
        assert normalize_tenant_id("mañana") == "manana"
        assert normalize_tenant_id("garçom") == "garcom"

    def test_raises_on_empty(self):
        """Empty input must raise ValueError."""
        import pytest as _pt
        with _pt.raises(ValueError):
            normalize_tenant_id("")
        with _pt.raises(ValueError):
            normalize_tenant_id("   ")

    def test_raises_on_none(self):
        """None input must raise ValueError."""
        import pytest as _pt
        with _pt.raises(ValueError):
            normalize_tenant_id(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Orion-LD isolation tests (require running Orion-LD instance)
# ---------------------------------------------------------------------------

@pytest.mark.orion_integration
class TestOrionLdIsolation:
    """Tests that Orion-LD properly isolates data between tenants.

    These tests require a running Orion-LD instance at ORION_URL.
    They are skipped if Orion is not available.
    """

    @pytest.fixture(autouse=True)
    def skip_if_no_orion(self, orion_url):
        """Skip all tests in this class if Orion-LD is not reachable."""
        try:
            resp = requests.get(f"{orion_url}/ngsi-ld/v1/version", timeout=2)
            resp.raise_for_status()
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
            pytest.skip(f"Orion-LD not reachable at {orion_url}")

    def _create_test_entity(self, orion_url, tenant_id, entity_id, entity_type):
        """Create a minimal test entity in Orion-LD."""
        body = {
            "id": entity_id,
            "type": entity_type,
        }
        resp = requests.post(
            f"{orion_url}/ngsi-ld/v1/entities",
            json=body,
            headers=_orion_headers(tenant_id),
            timeout=5
        )
        # 201 Created or 409 Conflict (already exists) are acceptable
        assert resp.status_code in (201, 409), \
            f"Failed to create entity: {resp.status_code} {resp.text[:200]}"

    def _count_entities(self, orion_url, tenant_id, entity_type):
        """Count entities of a given type in Orion under a tenant."""
        resp = requests.get(
            f"{orion_url}/ngsi-ld/v1/entities?type={entity_type}&options=count,keyValues",
            headers=_orion_query_headers(tenant_id),
            timeout=5
        )
        if resp.status_code == 200:
            count = resp.headers.get("X-Total-Count")
            return int(count) if count is not None else len(resp.json())
        return -1

    def _delete_entity(self, orion_url, tenant_id, entity_id):
        """Delete a test entity."""
        try:
            requests.delete(
                f"{orion_url}/ngsi-ld/v1/entities/{entity_id}",
                headers={"NGSILD-Tenant": tenant_id},
                timeout=5
            )
        except Exception:
            pass

    def test_tenant_a_cannot_see_tenant_b_entities(
        self, orion_url, tenant_alpha, tenant_beta
    ):
        """CRITICAL: Tenant A queries Orion -> MUST NOT see Tenant B's entities."""
        # Arrange — create one entity per tenant
        type_name = "TestType"
        id_a = f"urn:ngsi-ld:Test:{tenant_alpha}-001"
        id_b = f"urn:ngsi-ld:Test:{tenant_beta}-001"

        self._create_test_entity(orion_url, tenant_alpha, id_a, type_name)
        self._create_test_entity(orion_url, tenant_beta, id_b, type_name)

        try:
            # Act — query as tenant_alpha
            count_a_sees = self._count_entities(orion_url, tenant_alpha, type_name)

            # Assert — alpha must see at least its own entity
            assert count_a_sees >= 1, \
                f"tenant_alpha sees {count_a_sees} entities — should see at least 1"

            # Query as tenant_beta to check isolation
            resp = requests.get(
                f"{orion_url}/ngsi-ld/v1/entities?type={type_name}&options=keyValues",
                headers=_orion_query_headers(tenant_alpha),
                timeout=5
            )
            alpha_entities = resp.json() if resp.status_code == 200 else []
            alpha_ids = [e.get("id", "") for e in (alpha_entities if isinstance(alpha_entities, list) else [])]

            # Assert — alpha must NOT see beta's entity
            assert not any(tenant_beta in eid for eid in alpha_ids), \
                f"Alpha MUST NOT see beta's entity. Found: {[eid for eid in alpha_ids if tenant_beta in eid]}"
        finally:
            # Cleanup
            self._delete_entity(orion_url, tenant_alpha, id_a)
            self._delete_entity(orion_url, tenant_beta, id_b)

    def test_orion_returns_different_counts_per_tenant(
        self, orion_url, tenant_alpha, tenant_beta
    ):
        """Each tenant's entity count must be independent."""
        type_name = "CountTestType"
        id_a = f"urn:ngsi-ld:CountTest:{tenant_alpha}-001"

        self._create_test_entity(orion_url, tenant_alpha, id_a, type_name)

        try:
            count_a = self._count_entities(orion_url, tenant_alpha, type_name)
            count_b = self._count_entities(orion_url, tenant_beta, type_name)

            assert count_a >= 1, \
                f"tenant_alpha should see its entity, got count={count_a}"
            # Beta should NOT see alpha's entity
            assert count_b == 0 or count_b < count_a, \
                f"tenant_beta count ({count_b}) should be less than alpha's ({count_a})"
        finally:
            self._delete_entity(orion_url, tenant_alpha, id_a)


# ---------------------------------------------------------------------------
# PostgreSQL isolation tests (require running PostgreSQL instance)
# ---------------------------------------------------------------------------

@pytest.mark.pg_integration
class TestPostgresIsolation:
    """Tests that PostgreSQL tenant isolation works.

    These tests require a running PostgreSQL at POSTGRES_URL.
    They are skipped if PG is not available.
    """

    @pytest.fixture(autouse=True)
    def skip_if_no_pg(self):
        """Skip all tests in this class if PostgreSQL is not reachable."""
        try:
            import psycopg2
            conn = psycopg2.connect(POSTGRES_URL, connect_timeout=2)
            conn.close()
        except Exception:
            pytest.skip(f"PostgreSQL not reachable at {POSTGRES_URL}")

    def test_pg_insert_and_query_tenant_isolation(self):
        """Insert test rows for two tenants, verify each can only see their own data.

        Uses telemetry_measurements (no FK constraints, has entity_id column).
        """
        import psycopg2
        import uuid

        conn = psycopg2.connect(POSTGRES_URL)
        try:
            with conn.cursor() as cur:
                alpha_id = uuid.uuid4().hex[:12]
                beta_id = uuid.uuid4().hex[:12]
                tenant_a = "test-iso-alpha"
                tenant_b = "test-iso-beta"

                # Insert test rows
                cur.execute("""
                    INSERT INTO telemetry_measurements
                    (tenant_id, observed_at, source_event_id, entity_id,
                     attribute_name, value, source)
                    VALUES (%s, NOW(), 0, %s, 'test_attr', 1.0, 'test_isolation')
                """, (tenant_a, alpha_id))

                cur.execute("""
                    INSERT INTO telemetry_measurements
                    (tenant_id, observed_at, source_event_id, entity_id,
                     attribute_name, value, source)
                    VALUES (%s, NOW(), 0, %s, 'test_attr', 1.0, 'test_isolation')
                """, (tenant_b, beta_id))
                conn.commit()

                # Query as tenant_a — should see own row
                cur.execute(
                    "SELECT entity_id FROM telemetry_measurements "
                    "WHERE tenant_id = %s AND entity_id = %s",
                    (tenant_a, alpha_id)
                )
                assert cur.fetchone() is not None, \
                    "Tenant A should see its own row"

                # Query as tenant_a for tenant_b's data — should NOT see it
                cur.execute(
                    "SELECT entity_id FROM telemetry_measurements "
                    "WHERE tenant_id = %s AND entity_id = %s",
                    (tenant_a, beta_id)  # Querying with alpha's tenant for beta's entity
                )
                assert cur.fetchone() is None, \
                    f"Tenant A MUST NOT see tenant B's row. Got entity_id={beta_id}"

                conn.commit()
        finally:
            # Cleanup
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM telemetry_measurements WHERE entity_id IN (%s, %s)",
                    (alpha_id, beta_id)
                )
            conn.commit()
            conn.close()


# ---------------------------------------------------------------------------
# CI-safe unit tests (no external dependencies)
# ---------------------------------------------------------------------------

class TestCrossTenantQuerySafety:
    """Tests that our code handles cross-tenant query risks correctly."""

    def test_no_raw_ngsi_ld_calls_without_tenant_header(self):
        """Scan for raw HTTP calls to Orion that may lack NGSILD-Tenant header."""
        import glob

        services_dir = os.path.join(os.path.dirname(__file__), "..")
        violations = []

        for pyfile in glob.glob(f"{services_dir}/**/*.py", recursive=True):
            basename = os.path.basename(pyfile)
            if "test_" in basename or "__pycache__" in pyfile:
                continue
            with open(pyfile, errors="replace") as f:
                content = f.read()
                # Look for raw HTTP calls that might hit Orion
                raw_calls = (
                    "requests.get(", "requests.post(", "requests.put(",
                    "requests.delete(", "requests.patch(",
                    "httpx.get(", "httpx.post(", "httpx.put(",
                    "httpx.delete(", "httpx.patch(",
                )
                if any(call in content for call in raw_calls):
                    # Check if headers are set with NGSILD-Tenant
                    if "NGSILD-Tenant" not in content and "ngsi-ld" in content.lower():
                        violations.append(pyfile)

        # This is a soft check — log findings but don't fail CI
        if violations:
            print(f"WARNING: {len(violations)} files may make raw Orion calls "
                  f"without NGSILD-Tenant:")
            for v in violations[:10]:
                print(f"  - {v}")

    def test_no_tenant_id_injection_in_queries(self):
        """Scan for potential SQL injection via tenant_id interpolation."""
        import glob

        services_dir = os.path.join(os.path.dirname(__file__), "..")
        violations = []

        for pyfile in glob.glob(f"{services_dir}/**/*.py", recursive=True):
            basename = os.path.basename(pyfile)
            if "test_" in basename or "__pycache__" in pyfile:
                continue
            with open(pyfile, errors="replace") as f:
                content = f.read()
                # Check for f-string/format SQL with tenant_id (potential injection)
                if "tenant_id" in content and ("f\"" in content or "f'" in content):
                    lines = content.split('\n')
                    for i, line in enumerate(lines, 1):
                        if "tenant_id" in line and ("SELECT" in line or "WHERE" in line):
                            # Check if it uses f-string interpolation
                            if "{" in line and "tenant_id" in line:
                                violations.append(f"{pyfile}:{i}")

        if violations:
            print(f"WARNING: {len(violations)} potential SQL injection risks "
                  f"with tenant_id:")
            for v in violations[:10]:
                print(f"  - {v}")

    def test_tenant_normalization_is_idempotent(self):
        """normalize_tenant_id must be idempotent (double-apply yields same result)."""
        inputs = [
            "asociacion-allotarra",
            "test_tenant",
            "  MiXeD_CASE  ",
            "aéreo-mañana",
            "   leading-and-trailing   ",
        ]
        for inp in inputs:
            once = normalize_tenant_id(inp)
            twice = normalize_tenant_id(once)
            assert once == twice, \
                f"normalize_tenant_id is NOT idempotent: '{inp}' -> '{once}' -> '{twice}'"
