# Timescale Toolkit SOTA Rollout Plan V2

Status: Ready for implementation kickoff  
Date: 2026-04-20  
Scope: `nkz` core (`config/timescaledb/migrations`, `services/timeseries-reader`)  
Audience: Backend, Data, Platform/Ops

## 1) Purpose and non-negotiable constraints

This document defines the launch-ready implementation plan for statistically correct, high-performance, multi-resolution telemetry analytics in core.

Non-negotiable constraints:

1. No direct business writes to historical DB from API/worker logic. Historical DB is fed by NGSI-LD subscription ingestion path already in place.
2. Statistical hierarchy must be algebraically correct:
   - Use Toolkit two-step aggregates (`stats_agg`, percentile sketch aggregate).
   - Use `rollup(...)` between hierarchical continuous aggregates.
   - Never recompute non-linear metrics from already materialized scalar metrics.
3. Reader API must materialize scalar values at SQL query time via accessor functions; it must not expose or process raw sketch payloads.
4. Operational freshness requirements must be explicit (`materialized_only` strategy + refresh policies), not default-assumed.

## 2) Current-state audit summary (validated in code)

Current core uses direct SQL aggregation over raw tables:

- `services/timeseries-reader/app.py` uses `time_bucket` / `time_bucket_gapfill` with scalar `AVG/MIN/MAX` patterns.
- There is no `timescaledb_toolkit` usage in current core code or current migrations.
- Current migrations include compression/index tuning for `telemetry_events`, but no CAGG hierarchy for telemetry statistics.

Implication:

- The plan can be introduced incrementally and safely, but requires schema + query-layer evolution first.

## 3) Mandatory low-level directives (implementation guardrails)

These directives are required in all PRs:

1. Hierarchical CAGGs must use `rollup(...)` on intermediate states.
2. SQL accessors must be used in final `SELECT`:
   - Mean, stddev, min, max, quantiles are materialized from sketch/state columns in DB.
3. `materialized_only` behavior must be explicitly defined per CAGG.
4. All dynamic view/table references in Python are whitelist-mapped (no free-form SQL interpolation).
5. Capacity controls (compression, chunking, retention) are part of the same rollout, not deferred.

## 4) Canonical data model decision

### 4.1 Problem

Current `telemetry_events` stores measurements in JSON payload shape, while Toolkit CAGGs are most efficient on long-format numeric rows.

### 4.2 Decision

Introduce a canonical long-format telemetry measurement hypertable for analytics:

- `telemetry_measurements` (new)
  - `tenant_id TEXT`
  - `observed_at TIMESTAMPTZ`
  - `entity_id TEXT`
  - `entity_type TEXT`
  - `attribute_name TEXT`
  - `value DOUBLE PRECISION`
  - Optional provenance columns: `device_id`, `sensor_id`, `task_id`, `source`

Ingestion strategy:

- Backfill from existing `telemetry_events` payload.
- Keep incremental sync path from new incoming events.

Rationale:

- Avoid repeated JSON extraction at query time.
- Improve indexing/compression/selectivity.
- Keep analytics pipeline deterministic and debuggable.

## 5) PR breakdown (launch sequence)

## PR-0 — Schema bridge and compatibility baseline

Goal: establish canonical long-format table and ingestion bridge without changing existing API behavior.

Deliverables:

- Migration:
  - Create `telemetry_measurements` hypertable.
  - Indexes: `(tenant_id, entity_id, attribute_name, observed_at DESC)` and `(tenant_id, observed_at DESC)`.
  - Compression setup on raw measurements table.
- Backfill job/script (idempotent, tenant-safe).
- Incremental write/sync logic from `telemetry_events` to `telemetry_measurements`.
- Validation script:
  - Row count parity checks per tenant/range.
  - Sample numeric parity checks per attribute.

Execution model (mandatory):

- Do not run historical backfill as a single migration transaction.
- Keep SQL migration scope limited to DDL and lightweight metadata operations.
- Execute historical backfill as an asynchronous cluster job (separate from migration runner), with:
  - bounded batch windows (day/week chunks),
  - commit per batch,
  - throttling pauses between batches,
  - resumable checkpoints (last processed range/tenant).

WAL safety directives (mandatory):

- Never run one-shot `INSERT INTO ... SELECT ...` across full history.
- Batch by bounded time range and tenant to cap WAL generation and lock pressure.
- Include explicit operational controls: sleep interval, max rows/time per batch, and runtime stop/resume markers.
- Run during controlled windows with WAL/disk monitoring and abort thresholds.

Acceptance:

- No API behavior changes.
- Backfill + incremental feed stable.

## PR-1 — Toolkit enablement and 10m base CAGG

Goal: install Toolkit primitives and create first-level aggregate state.

Deliverables:

- Migration:
  - `CREATE EXTENSION IF NOT EXISTS timescaledb_toolkit;`
  - `telemetry_10m` continuous aggregate with two-step states:
    - `stats_summary`
    - percentile summary state
- Explicit view options:
  - `timescaledb.continuous = true`
  - explicit `materialized_only` policy decision (default recommended: real-time enabled)
- Refresh policy for 10m.
- Compression policy for underlying materialization hypertable.

SQL skeleton (pattern, adapt function names to installed Toolkit version):

```sql
CREATE MATERIALIZED VIEW telemetry_10m
WITH (timescaledb.continuous) AS
SELECT
  time_bucket(INTERVAL '10 minutes', observed_at) AS bucket,
  tenant_id,
  entity_id,
  attribute_name,
  stats_agg(value) AS stats_summary,
  percentile_agg(value) AS pct_summary
FROM telemetry_measurements
GROUP BY bucket, tenant_id, entity_id, attribute_name
WITH NO DATA;
```

Acceptance:

- CAGG refreshes correctly.
- Querying accessor materialization works for 10m.

## PR-2 — Hierarchical CAGGs with rollups (1h, 1d)

Goal: build mathematically correct hierarchy from 10m states.

Deliverables:

- Migrations for `telemetry_1h` and `telemetry_1d`.
- Use `rollup(stats_summary)` and `rollup(pct_summary)` only.
- Refresh/compression/retention policies per layer.

SQL skeleton:

```sql
CREATE MATERIALIZED VIEW telemetry_1h
WITH (timescaledb.continuous) AS
SELECT
  time_bucket(INTERVAL '1 hour', bucket) AS bucket,
  tenant_id,
  entity_id,
  attribute_name,
  rollup(stats_summary) AS stats_summary,
  rollup(pct_summary) AS pct_summary
FROM telemetry_10m
GROUP BY bucket, tenant_id, entity_id, attribute_name
WITH NO DATA;
```

Acceptance:

- Hierarchy refreshes without type errors.
- No scalar re-aggregation anti-pattern exists in SQL.

Timezone semantics for daily buckets (mandatory):

- Daily aggregation must not assume UTC-midnight semantics for agronomic/business daily metrics.
- For multi-tenant multi-region deployments, daily rollups must use tenant-aware timezone semantics:
  - prefer timezone-aware `time_bucket` signatures when available in deployed Timescale version,
  - otherwise apply explicit query-time/business-layer timezone compensation with documented limitations.
- Tenant timezone source must be explicitly defined (authoritative config field), and tests must validate DST transitions.

## PR-3 — Reader API refactor (`timeseries-reader`)

Goal: switch analytical read path to canonical CAGGs + accessors.

Deliverables:

- Resolution-to-view whitelist map:
  - `raw` -> raw measurements table
  - `10m` -> `telemetry_10m`
  - `1h` -> `telemetry_1h`
  - `1d` -> `telemetry_1d`
- New query functions with accessor materialization in SQL final projection.
- Feature flag `TIMESERIES_STATS_ENGINE=v2` for controlled rollout.
- Compatibility adapter so old endpoint contracts keep working.

SQL projection pattern (adapt accessor names to installed Toolkit version):

```sql
SELECT
  bucket AS timestamp,
  average(stats_summary) AS mean,
  stddev(stats_summary, 'pop') AS stddev,
  min_val(stats_summary) AS min,
  max_val(stats_summary) AS max,
  approx_percentile(0.50, pct_summary) AS p50,
  approx_percentile(0.95, pct_summary) AS p95
FROM telemetry_1h
WHERE tenant_id = $1
  AND entity_id = $2
  AND attribute_name = $3
  AND bucket >= $4
  AND bucket < $5
ORDER BY bucket ASC;
```

Acceptance:

- API returns numeric scalar fields only.
- No binary sketch payload leaves DB layer.

Execution status (2026-04-20):

- Implemented in `services/timeseries-reader/app.py` for `POST /api/timeseries/v2/query`.
- Added feature flag `TIMESERIES_STATS_ENGINE` (`v1` default, `v2` enables Toolkit/CAGG path).
- `v2` telemetry read routing:
  - sub-hour buckets (`10m/15m/30m`) -> `telemetry_10m`
  - hour-family buckets (`1h/2h/6h/12h`) -> `telemetry_1h`
  - day-family buckets (`1d/1w/1month`) -> `telemetry_1d_tenant_localized` when `TIMESERIES_USE_TENANT_LOCAL_DAILY=true`, else `telemetry_1d`
- Compatibility maintained with raw fallback:
  - sub-10m buckets continue on `telemetry_events`
  - any CAGG-routing-disabled path remains on raw `telemetry_events`
- Accessor materialization enforced in SQL (`average(stats_summary)`) for scalar output.
- Pending: deploy updated timeseries-reader image + env vars in cluster and execute endpoint-level validation/perf checks.

## PR-4 — Validation, performance gates, and rollout

Goal: production-safe launch with quantitative guarantees.

Deliverables:

- Statistical correctness suite:
  - Compare against offline baseline for sample windows.
  - Tolerance thresholds for mean/stddev/percentiles.
- Performance suite:
  - p50/p95 latency before/after.
  - Throughput under concurrent tenant queries.
- Operational runbook:
  - refresh lag SLO
  - backfill retry policy
  - rollback switch

Acceptance:

- Correctness and latency gates pass.
- Feature flag rollout completed progressively.

## 6) Capacity planning and storage controls

### 6.1 Risks

Sketch/state columns can increase intermediate storage footprint relative to simple scalar aggregates.

### 6.2 Required controls

1. Compression for materialization hypertables by analytic cardinality:
   - Segment by `tenant_id, entity_id, attribute_name`
   - Order by `bucket DESC`
2. Retention per layer:
   - keep short-granularity shorter (10m),
   - keep long-granularity longer (1d).
3. Chunk interval tuning per layer (10m/1h/1d) based on write/query profile.
4. Weekly storage review dashboard:
   - raw size
   - cagg size
   - compression ratio
   - refresh lag

### 6.3 Suggested policy baseline (tune per server limits)

- `telemetry_10m`: compress after 3 days, retain 90 days.
- `telemetry_1h`: compress after 7 days, retain 365 days.
- `telemetry_1d`: compress after 30 days, retain multi-year according to product policy.

## 7) Runtime freshness policy

Define explicitly per view:

- Default for operational dashboards: real-time aggregation enabled (non-materialized tail included).
- For heavy back-office workloads, allow an opt-in materialized-only query path if needed.

This must be an explicit decision and documented in migration comments and API docs.

## 8) Security and multi-tenant constraints

- All queries must remain tenant-filtered by `tenant_id`.
- No cross-tenant rollups.
- Reader endpoints continue cookie/JWT tenant propagation as currently implemented.

## 9) Rollback strategy

1. Keep old read path behind feature flag until stability confirmed.
2. Rollback on incident:
   - switch flag to v1
   - keep CAGGs in place for postmortem (do not drop immediately)
3. Only deprecate v1 after sustained error/latency stability window.

## 10) Execution checklist (go/no-go)

- [ ] Toolkit extension verified in target environment.
- [ ] Long-format table created and backfilled.
- [ ] 10m CAGG healthy with policies.
- [ ] 1h/1d rollups healthy.
- [ ] Accessor-based reader queries merged behind feature flag.
- [ ] Correctness benchmark passed.
- [ ] Performance benchmark passed.
- [ ] Storage growth within budget.
- [ ] Rollback drill performed.

## 11) PR-0 implementation artifacts (delivered)

- Migration:
  - `config/timescaledb/migrations/066_telemetry_measurements_long_format.sql`
- Async backfill + parity validation job:
  - `scripts/backfill_telemetry_measurements.py`

Recommended first run sequence:

1. Apply migration 066 (DDL only).
2. Dry-run validation window (small range):
   - `python3 scripts/backfill_telemetry_measurements.py --mode validate --start-time <iso> --end-time <iso> --tenant-id <tenant>`
3. Run controlled backfill:
   - `python3 scripts/backfill_telemetry_measurements.py --mode backfill --batch-hours 24 --sleep-seconds 0.3 --max-batches 10`
4. Repeat validate on expanded windows and tenant samples.

## 12) PR-1 implementation artifacts (delivered)

- Migration:
  - `config/timescaledb/migrations/067_telemetry_10m_toolkit_cagg.sql`

Server execution notes:

- Applied in `nekazari` DB (Timescale `2.10.2` + Toolkit `1.16.0`).
- `telemetry_10m` CAGG created with:
  - `stats_agg(value)` as `stats_summary`
  - `percentile_agg(value)` as `pct_summary`
- Explicit realtime preference set (`materialized_only = false`).
- Policies active:
  - refresh policy (`start_offset=30 days`, `end_offset=5 minutes`, `schedule=5 minutes`)
  - compression policy (`compress_after=45 days`)

Validation snapshot:

- Accessor query using `average(stats_summary)`, `stddev(stats_summary, 'pop')`, `approx_percentile(0.95, pct_summary)` returned expected numeric rows.

Important correction applied during rollout:

- Compression policy must be greater than refresh start offset for CAGG jobs.
- Initial `compress_after='3 days'` was rejected by Timescale policy constraints and corrected to `45 days`.

## 13) PR-2 implementation artifacts (delivered)

- Part A migration:
  - `config/timescaledb/migrations/068_telemetry_1h_rollup_cagg.sql`
- Part B migration:
  - `config/timescaledb/migrations/069_telemetry_1d_timezone_layer.sql`

Server execution notes:

- `telemetry_1h` hierarchical CAGG created from `telemetry_10m` using `rollup(...)`.
- `telemetry_1d` UTC hierarchical CAGG created from `telemetry_1h`.
- Tenant timezone catalog introduced:
  - `admin_platform.tenant_timezones` (seeded from `tenants`, default `UTC`).
- Tenant-local daily semantics exposed via:
  - `telemetry_1d_tenant_localized` view (timezone-aware `time_bucket` at query time).

Validation snapshot:

- Metadata confirms `telemetry_10m`, `telemetry_1h`, `telemetry_1d` with `finalized=true`.
- Accessor queries on `telemetry_1h`, `telemetry_1d`, and `telemetry_1d_tenant_localized`
  returned expected numeric outputs.
- Policy jobs active:
  - `1006` / `1007` for `telemetry_1h` refresh/compression.
  - `1008` / `1009` for `telemetry_1d` refresh/compression.

Important correction applied during rollout:

- For CAGG validator compliance, the upper-layer CAGG definition must use explicit
  `GROUP BY time_bucket(...)` expression (not ambiguous alias grouping).
- Direct timezone-parameterized hierarchical CAGG bucketing failed validator checks
  in this Timescale version; therefore persisted daily layer is UTC CAGG, with
  tenant-local daily grouping provided by dedicated timezone-aware view.

