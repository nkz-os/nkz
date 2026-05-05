#!/usr/bin/env python3
"""
Remediate missing Orion-LD MongoDB databases for existing tenants.

Queries PostgreSQL for all active tenants, checks which ones lack an
orion-<tenant_id> MongoDB database, and creates them.

Usage:
    # Dry-run (show what would be created)
    python3 remediate-orion-tenant-dbs.py --dry-run

    # Actually create missing databases
    python3 remediate-orion-tenant-dbs.py

    # Target specific tenants
    python3 remediate-orion-tenant-dbs.py --tenants robotika,abregoandres

Environment variables (or use defaults for in-cluster execution):
    POSTGRES_URL  — PostgreSQL connection string
    MONGODB_URI   — MongoDB connection string
"""

import argparse
import os
import sys

import psycopg2
import pymongo

POSTGRES_URL = os.getenv("POSTGRES_URL")
MONGODB_URI = os.getenv("MONGODB_URI")

if not POSTGRES_URL:
    print("ERROR: POSTGRES_URL environment variable is required.", file=sys.stderr)
    print("Example: postgresql://user:pass@host:5432/dbname", file=sys.stderr)
    sys.exit(1)
if not MONGODB_URI:
    print("ERROR: MONGODB_URI environment variable is required.", file=sys.stderr)
    print("Example: mongodb://user:pass@host:27017/?authSource=admin", file=sys.stderr)
    sys.exit(1)


def get_active_tenants() -> list[dict]:
    """Return list of active tenants from PostgreSQL."""
    conn = psycopg2.connect(POSTGRES_URL)
    cur = conn.cursor()
    cur.execute(
        "SELECT tenant_id, tenant_name, status, plan_level FROM public.tenants WHERE status = 'active' ORDER BY tenant_id"
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {"tenant_id": r[0], "tenant_name": r[1], "status": r[2], "plan_level": r[3]}
        for r in rows
    ]


def get_existing_orion_dbs(mongo_client) -> set[str]:
    """Return set of existing orion-* database names in MongoDB."""
    existing = mongo_client.list_database_names()
    return {db for db in existing if db.startswith("orion-")}


def ensure_orion_db(mongo_client, tenant_id: str) -> bool:
    """Create orion-<tenant_id> database with entities collection. Returns True if created."""
    db_name = f"orion-{tenant_id}"
    db = mongo_client.get_database(db_name)
    if "entities" in db.list_collection_names():
        return False  # already exists
    db.create_collection("entities")
    return True


def main():
    parser = argparse.ArgumentParser(description="Remediate Orion-LD tenant databases")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")
    parser.add_argument("--tenants", type=str, help="Comma-separated list of specific tenant_ids to check")
    args = parser.parse_args()

    print("Connecting to PostgreSQL...")
    try:
        all_tenants = get_active_tenants()
    except Exception as e:
        print(f"ERROR: Failed to query PostgreSQL: {e}")
        sys.exit(1)

    if args.tenants:
        requested = set(t.strip() for t in args.tenants.split(","))
        tenants = [t for t in all_tenants if t["tenant_id"] in requested]
        if not tenants:
            print(f"No active tenants found matching: {args.tenants}")
            sys.exit(0)
    else:
        tenants = all_tenants

    print(f"Found {len(tenants)} active tenant(s) to check")

    print("Connecting to MongoDB...")
    try:
        mongo_client = pymongo.MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
        mongo_client.admin.command("ping")  # verify connection
    except Exception as e:
        print(f"ERROR: Failed to connect to MongoDB: {e}")
        sys.exit(1)

    existing_dbs = get_existing_orion_dbs(mongo_client)
    print(f"Found {len(existing_dbs)} existing orion-* database(s) in MongoDB")

    missing = []
    ok = []
    for t in tenants:
        db_name = f"orion-{t['tenant_id']}"
        if db_name in existing_dbs:
            ok.append(t["tenant_id"])
        else:
            missing.append(t)

    print(f"\n  OK: {len(ok)} tenant(s) already have Orion-LD databases")
    print(f"  MISSING: {len(missing)} tenant(s) need Orion-LD databases\n")

    if not missing:
        print("Nothing to do.")
        return

    for t in missing:
        db_name = f"orion-{t['tenant_id']}"
        if args.dry_run:
            print(f"  [DRY-RUN] Would create: {db_name} (tenant: {t['tenant_name']})")
        else:
            try:
                created = ensure_orion_db(mongo_client, t["tenant_id"])
                if created:
                    print(f"  CREATED: {db_name} (tenant: {t['tenant_name']})")
                else:
                    print(f"  ALREADY EXISTS: {db_name}")
            except Exception as e:
                print(f"  FAILED: {db_name} — {e}")

    if args.dry_run:
        print(f"\nDry-run complete. {len(missing)} database(s) would be created.")
        print("Re-run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
