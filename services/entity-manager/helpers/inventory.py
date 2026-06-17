"""14-system parallel inventory for tenant deletion preview."""
import logging
import os
import concurrent.futures
import requests
from typing import Any

from tenant_utils import normalize_tenant_id

from common.ngsi_headers import inject_fiware_headers

logger = logging.getLogger(__name__)

INVENTORY_TIMEOUT = 30
ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")
POSTGRES_URL = os.getenv("POSTGRES_URL", "")
TENANT_WEBHOOK_URL = os.getenv("TENANT_WEBHOOK_URL", "http://tenant-webhook-service:5000")
TIMESCALE_URL = os.getenv("TIMESCALE_URL", "")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
HEADSCALE_URL = os.getenv("HEADSCALE_URL", "")
HEADSCALE_API_KEY = os.getenv("HEADSCALE_API_KEY", "")


def _system_entry(status: str, summary: dict = None, error: str = None) -> dict:
    entry = {"status": status, "summary": summary or {}}
    if error:
        entry["error"] = error
    return entry


def _check_postgresql(tenant_id: str) -> dict:
    """Count tables and rows with tenant_id across all public tables."""
    import psycopg2
    try:
        conn = psycopg2.connect(POSTGRES_URL, connect_timeout=10)
        cur = conn.cursor()
        cur.execute("""
            SELECT table_name FROM information_schema.columns
            WHERE column_name = 'tenant_id' AND table_schema = 'public'
        """)
        tables = [r[0] for r in cur.fetchall()]
        total_rows = 0
        for t in tables:
            try:
                cur.execute('SELECT count(*) FROM "%s" WHERE tenant_id = %%s' % t, (tenant_id,))
                total_rows += cur.fetchone()[0]
            except Exception:
                pass
        cur.close()
        conn.close()
        return _system_entry("found", {"tables": len(tables), "rows_approx": total_rows})
    except Exception as e:
        return _system_entry("error", error=f"PostgreSQL: {str(e)}")


def _check_orion_ld(tenant_id: str) -> dict:
    """Discover entity types and counts using dynamic type discovery, plus subscription count."""
    try:
        base = f"{ORION_URL}/ngsi-ld/v1"
        headers = inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False)

        r = requests.get(f"{base}/types", headers=headers, timeout=INVENTORY_TIMEOUT)
        type_counts = {}
        if r.status_code == 200:
            data = r.json()
            types = data.get("typeList", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            for etype in types:
                r2 = requests.get(f"{base}/entities", headers=headers,
                                 params={"type": etype, "limit": 1, "options": "count"},
                                 timeout=INVENTORY_TIMEOUT)
                count_header = r2.headers.get("NGSILD-Results-Count")
                if count_header:
                    type_counts[etype] = int(count_header)
                else:
                    r3 = requests.get(f"{base}/entities", headers=headers,
                                     params={"type": etype, "limit": 1000},
                                     timeout=INVENTORY_TIMEOUT)
                    if r3.status_code == 200:
                        type_counts[etype] = len(r3.json())

        r_subs = requests.get(f"{base}/subscriptions", headers=headers, timeout=INVENTORY_TIMEOUT)
        sub_count = len(r_subs.json()) if r_subs.status_code == 200 else 0

        return _system_entry("found", {"entity_types": type_counts, "subscriptions": sub_count})
    except Exception as e:
        return _system_entry("error", error=f"Orion-LD: {str(e)}")


def _check_mongodb(tenant_id: str) -> dict:
    """Check if Orion MongoDB database exists for tenant."""
    try:
        mongo_uri = os.getenv("ORION_MONGO_URI", "mongodb://mongodb-service:27017")
        from pymongo import MongoClient
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        db_name = f"orion-{normalize_tenant_id(tenant_id)}"
        db_names = client.list_database_names()
        client.close()
        if db_name in db_names:
            return _system_entry("found", {"database": db_name})
        return _system_entry("not_found")
    except Exception as e:
        return _system_entry("error", error=f"MongoDB: {str(e)}")


def _check_keycloak(tenant_id: str) -> dict:
    """Count users with tenant_id attribute via tenant-webhook internal endpoint."""
    try:
        r = requests.get(
            f"{TENANT_WEBHOOK_URL}/internal/inventory/keycloak/{tenant_id}",
            timeout=INVENTORY_TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            return _system_entry("found", data.get("summary", {}))
        return _system_entry("not_found")
    except Exception as e:
        return _system_entry("error", error=f"Keycloak: {str(e)}")


def _check_kubernetes(tenant_id: str) -> dict:
    """Check K8s namespace existence via tenant-webhook internal endpoint."""
    try:
        r = requests.get(
            f"{TENANT_WEBHOOK_URL}/internal/inventory/kubernetes/{tenant_id}",
            timeout=INVENTORY_TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            return _system_entry("found", data.get("summary", {}))
        return _system_entry("not_found")
    except Exception as e:
        return _system_entry("error", error=f"Kubernetes: {str(e)}")


def _check_stripe(tenant_id: str) -> dict:
    """Check Stripe subscription status."""
    try:
        if not STRIPE_SECRET_KEY:
            return _system_entry("not_provisioned")
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY
        subs = stripe.Subscription.list(limit=1, metadata={"tenant_id": tenant_id})
        for sub in subs.auto_paging_iter():
            return _system_entry("found", {
                "subscription_id": sub.id,
                "status": sub.status,
            })
        return _system_entry("not_found")
    except Exception as e:
        return _system_entry("error", error=f"Stripe: {str(e)}")


def _check_timescaledb(tenant_id: str) -> dict:
    """Count telemetry hypertable rows."""
    try:
        if not TIMESCALE_URL:
            return _system_entry("not_provisioned")
        import psycopg2
        conn = psycopg2.connect(TIMESCALE_URL, connect_timeout=10)
        cur = conn.cursor()
        cur.execute("""
            SELECT hypertable_name FROM timescaledb_information.hypertables
            WHERE hypertable_name LIKE %s
        """, (f"%{tenant_id}%",))
        hypertables = [r[0] for r in cur.fetchall()]
        rows = 0
        for ht in hypertables:
            try:
                cur.execute('SELECT count(*) FROM "%s" WHERE tenant_id = %%s' % ht, (tenant_id,))
                rows += cur.fetchone()[0]
            except Exception:
                pass
        cur.close()
        conn.close()
        if hypertables:
            return _system_entry("found", {"hypertables": len(hypertables), "rows_approx": rows})
        return _system_entry("not_found")
    except Exception as e:
        return _system_entry("error", error=f"TimescaleDB: {str(e)}")


def _check_table_group(tenant_id: str, tables: list[str], label: str) -> dict:
    """Count rows across a group of named tables for the tenant."""
    try:
        import psycopg2
        conn = psycopg2.connect(POSTGRES_URL, connect_timeout=10)
        cur = conn.cursor()
        results = {}
        for t in tables:
            try:
                cur.execute('SELECT count(*) FROM "%s" WHERE tenant_id = %%s' % t, (tenant_id,))
                c = cur.fetchone()[0]
                if c > 0:
                    results[t] = c
            except Exception:
                pass
        cur.close()
        conn.close()
        return _system_entry("found", results) if results else _system_entry("not_found")
    except Exception as e:
        return _system_entry("error", error=str(e))


def _check_headscale(tenant_id: str) -> dict:
    """Check VPN nodes."""
    try:
        if not HEADSCALE_URL or not HEADSCALE_API_KEY:
            return _system_entry("not_provisioned")
        headers = {"Authorization": f"Bearer {HEADSCALE_API_KEY}"}
        r = requests.get(f"{HEADSCALE_URL}/api/v1/node", headers=headers, timeout=10)
        if r.status_code == 200:
            nodes = [n for n in r.json().get("nodes", [])
                    if n.get("givenName", "").startswith(tenant_id)]
            if nodes:
                routes = sum(len(n.get("routes", [])) for n in nodes)
                return _system_entry("found", {"nodes": len(nodes), "routes": routes})
        return _system_entry("not_found")
    except Exception as e:
        return _system_entry("error", error=f"Headscale: {str(e)}")


def _check_neo4j(tenant_id: str) -> dict:
    """Check Neo4j for BioOrchestrator nodes belonging to this tenant."""
    try:
        neo4j_url = os.getenv("NEO4J_URL", "")
        neo4j_user = os.getenv("NEO4J_USER", "neo4j")
        neo4j_password = os.getenv("NEO4J_PASSWORD", "")
        if not neo4j_url:
            return _system_entry("not_provisioned")
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(neo4j_url, auth=(neo4j_user, neo4j_password))
        with driver.session(database="neo4j") as session:
            result = session.run(
                "MATCH (n) WHERE n.tenant_id = $tenant_id RETURN count(n) AS cnt",
                tenant_id=tenant_id,
            )
            node_count = result.single()["cnt"]
        driver.close()
        if node_count > 0:
            return _system_entry("found", {"nodes": node_count})
        return _system_entry("not_found")
    except ImportError:
        return _system_entry("not_provisioned", error="neo4j driver not installed")
    except Exception as e:
        return _system_entry("error", error=f"Neo4j: {str(e)}")


def _check_minio(tenant_id: str) -> dict:
    """Check MinIO for buckets and objects belonging to this tenant."""
    try:
        minio_endpoint = os.getenv("MINIO_ENDPOINT", "")
        minio_access_key = os.getenv("MINIO_ACCESS_KEY", "")
        minio_secret_key = os.getenv("MINIO_SECRET_KEY", "")
        if not minio_endpoint or not minio_access_key:
            return _system_entry("not_provisioned")
        from minio import Minio
        client = Minio(
            minio_endpoint,
            access_key=minio_access_key,
            secret_key=minio_secret_key,
            secure=False,
        )
        buckets = client.list_buckets()
        tenant_buckets = []
        total_objects = 0
        for bucket in buckets:
            if normalize_tenant_id(tenant_id) in bucket.name.lower():
                tenant_buckets.append(bucket.name)
                try:
                    objects = client.list_objects(bucket.name, recursive=True)
                    total_objects += sum(1 for _ in objects)
                except Exception:
                    pass
        if tenant_buckets:
            return _system_entry("found", {"buckets": len(tenant_buckets), "objects_approx": total_objects})
        return _system_entry("not_found")
    except ImportError:
        return _system_entry("not_provisioned", error="minio client not installed")
    except Exception as e:
        return _system_entry("error", error=f"MinIO: {str(e)}")


def gather_tenant_inventory(tenant_id: str) -> dict:
    """Query all 14 systems in parallel. Returns inventory dict with systems, impact, and warnings."""
    systems = {}
    warnings = []

    checks = {
        "postgresql": lambda: _check_postgresql(tenant_id),
        "orion_ld": lambda: _check_orion_ld(tenant_id),
        "mongodb": lambda: _check_mongodb(tenant_id),
        "keycloak": lambda: _check_keycloak(tenant_id),
        "kubernetes": lambda: _check_kubernetes(tenant_id),
        "stripe": lambda: _check_stripe(tenant_id),
        "timescaledb": lambda: _check_timescaledb(tenant_id),
        "n8n": lambda: _check_table_group(tenant_id, ["installed_nodes", "installed_packages"], "n8n"),
        "neo4j": lambda: _check_neo4j(tenant_id),
        "headscale": lambda: _check_headscale(tenant_id),
        "iot_devices": lambda: _check_table_group(tenant_id, ["provisioned_devices", "sensor_profiles", "api_keys"], "iot"),
        "datahub": lambda: _check_table_group(tenant_id, ["oauth_access_tokens", "external_api_credentials", "webhook_entity"], "datahub"),
        "odoo": lambda: _check_table_group(tenant_id, ["odoo_entity_mappings", "odoo_tenant_info"], "odoo"),
        "minio": lambda: _check_minio(tenant_id),
    }

    with concurrent.futures.ThreadPoolExecutor(max_workers=14) as executor:
        futures = {executor.submit(fn): name for name, fn in checks.items()}
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            try:
                systems[name] = future.result(timeout=INVENTORY_TIMEOUT + 5)
            except Exception as e:
                systems[name] = _system_entry("error", error=str(e))

    found_count = sum(1 for s in systems.values() if s["status"] == "found")
    if found_count >= 10:
        impact = "Alto"
    elif found_count >= 5:
        impact = "Medio"
    elif found_count >= 1:
        impact = "Bajo"
    else:
        impact = "Ninguno"

    if systems.get("stripe", {}).get("status") == "found":
        warnings.append("Stripe: la suscripcion activa sera cancelada")
    ts = systems.get("timescaledb", {}).get("summary", {})
    if ts.get("rows_approx", 0) > 100000:
        warnings.append(f"TimescaleDB: ~{ts['rows_approx']} filas de telemetria seran eliminadas permanentemente")

    return {
        "systems": systems,
        "estimated_impact": impact,
        "warnings": warnings,
    }
