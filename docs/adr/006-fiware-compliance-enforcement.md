# ADR-006: FIWARE Compliance Enforcement

**Date:** 2026-06-17
**Status:** Accepted

## Context

FIWARE seal certification requires strict NGSI-LD compliance across all services.
The platform had multiple violations in May 2026 which were remediated, but without
permanent guardrails they will recur. Independent module repos (crop-health,
vegetation-health, weather-map, agrienergy, catastro-spain) are especially at risk
because they operate outside the monorepo's CI and conventions.

## Decision

### 1. Use OrionClient / SyncOrionClient as PRIMARY path for new code

All new Orion-LD communication MUST use `OrionClient` (async) or `SyncOrionClient`
(sync) from `nkz-platform-sdk`. These auto-inject NGSILD-Tenant, Fiware-Service,
and @context headers correctly.

The `ngsi_headers.inject_fiware_headers()` function is the SECONDARY path,
reserved for legacy Flask-based services (entity-manager, risk-worker, sdm-integration)
that use raw `requests` and cannot easily adopt the SDK client class.

Raw `requests.get/post` to Orion-LD with manually constructed headers is FORBIDDEN.

### 2. Zero direct DB writes for entity state

Entity state changes MUST flow through Orion-LD:

```
Service → Orion-LD (NGSI-LD entity create/update)
       → NGSI-LD subscription
       → Notification Handler → PostgreSQL/TimescaleDB (query-optimized replica)
```

Notification handlers (`/notify` endpoints) are the ONLY exception for creating
query-friendly replicas. Direct `INSERT`/`UPDATE`/`DELETE` from API routes, workers,
or batch processors is forbidden.

Approved exceptions (operational metadata, not entity state):
- `notification_handler.py` — subscription-fed writes
- `config/timescaledb/migrations/*.sql` — schema migrations
- `*/ingest/*.py` — reference data loading (LUCAS, ESDB, etc.)
- `docker/*.sql` — seed data

### 3. CI enforcement per repo

Every repo (nkz monorepo + nkz-module-*) MUST have a FIWARE compliance CI job
that runs on push/PR to main. The job executes `scripts/check-fiware-compliance.sh`
and blocks merge on violations (verify=False, direct INSERT).

Pre-commit hooks are installed via `.githooks/pre-commit` and `core.hooksPath`.

### 4. Module registry compliance contract

Every module registered in `marketplace_modules` MUST declare a `fiware_compliance`
metadata block before it can be published:

```json
{
  "fiware_compliance": {
    "status": "compliant",
    "orion_client": "sdk",
    "direct_db_writes": false,
    "verification_date": "2026-06-17"
  }
}
```

The publish endpoint validates this declaration at upload time.

## Consequences

- All existing manual-header callers must be migrated to canonical layer (ngsi_headers
  or SDK client) — tracked in the remediation plan.
- Module repos need CI workflow additions for FIWARE compliance.
- Team must maintain the canonical `ngsi_headers.py` in nkz-platform-sdk.
- New modules are born-compliant by registry contract.
- Pre-commit hook may cause friction for developers unfamiliar with the rules —
  override is available via `--no-verify` for emergencies.

## Exceptions

- `telemetry-worker/sdm.py` raw time-series `INSERT INTO telemetry`: function kept
  for rollback safety but marked DEPRECATED (no longer called). Remove entirely
  after 2026-07-01 if no rollback needed.
- Services that cannot adopt the SDK (pure-Flask, no async) use `inject_fiware_headers`
  from `ngsi_headers.py` instead of `OrionClient`. This is valid but legacy.

## References

- AGENTS.md §3 (Technical Constraints — Orion-LD, NGSI-LD compliance)
- AGENTS.md §8 (Module Architecture Decisions)
- internal-docs-local/2026-06-17-ngsi-ld-inventory-fiware-seal.md
- internal-docs-local/plans/2026-06-17-fiware-seal-remediation-enforcement.md
- ETSI NGSI-LD spec (GS CIM 009): @context mutual exclusivity rule
