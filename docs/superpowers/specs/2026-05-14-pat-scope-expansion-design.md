# PAT Scope Expansion — Design Spec

**Date:** 2026-05-14
**Level:** Level 1 (incremental on existing PAT system)
**Target:** Extend PAT beyond `/api/timeseries` to cover read-only data access for external applications (PowerBI, Tableau, custom apps).

## Motivation

Current PAT (ADR 003) only works on `/api/timeseries/*`. An external app with a valid PAT cannot query entities, export data, or access device telemetry. The token exists, the auth plumbing exists, but the scope gate is hardcoded to one route prefix.

## Design

### 1. Scopes

Four canonical scopes. Each scope maps to a list of `(HTTP_METHOD, path_prefix)` tuples. The gateway enforces both method and prefix — a PAT with `entities` scope cannot create or mutate entities.

| Scope | Allowed `(method, prefix)` |
|-------|---------------------------|
| `timeseries` | `("GET", "/api/timeseries/")`, `("POST", "/api/timeseries/")` |
| `entities` | `("GET", "/ngsi-ld/v1/entities")`, `("POST", "/ngsi-ld/v1/entityOperations/query")` |
| `export` | `("POST", "/api/datahub/export")`, `("POST", "/api/datahub/timeseries/align")` |
| `telemetry` | `("GET", "/api/devices/")`, `("GET", "/api/sensors")` |

**Pagination enforcement for PAT requests:** when the request matches the `entities` scope, the gateway overrides the pagination limit before forwarding to Orion-LD:

- `GET /ngsi-ld/v1/entities` — `limit` query param capped at 500; injected as 100 if absent
- `POST /ngsi-ld/v1/entityOperations/query` — `limit` field in JSON body capped at 500; injected as 100 if absent

The Orion-LD `Link` response header (RFC 5988, NGSI-LD pagination) is forwarded transparently so external clients iterate pages correctly.

**Row limit for export scope:** `POST /api/datahub/export` via PAT caps `max_rows` at 10,000 regardless of the requested value.

### 2. Data Model

```sql
ALTER TABLE api_keys ADD COLUMN scopes TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE api_keys ADD COLUMN expires_at TIMESTAMPTZ DEFAULT NULL;
```

Migration backfills `scopes = ARRAY['timeseries']` for existing PAT rows (`key_type = 'pat'`). Existing PATs get no expiry (null).

### 3. tenant-webhook Changes

**File:** `services/tenant-webhook/enhanced-tenant-webhook.py`

- `POST /api/tenant/api-keys` — accepts `scopes` (array, validated against canonical whitelist) and optional `expires_at` (ISO 8601 timestamp)
- `GET /api/tenant/api-keys` — returns `scopes` and `expires_at` in each key object
- `/internal/validate-pat` — returns `scopes` and `expires_at` alongside `tenant_id` and `valid`; returns `valid: false` if token is expired
- Validate: reject scopes not in `{"timeseries", "entities", "export", "telemetry"}` with 400

### 4. api-gateway Changes

**Files:** `services/api-gateway/gateway_pat.py`, `services/api-gateway/fiware_api_gateway.py`

- Replace `reject_pat_outside_timeseries()` with `enforce_pat_scopes()`
- `PAT_SCOPE_ROUTES` constant maps scope → `[(method, prefix), ...]`
- Redis cache (TTL 300s) stores `(tenant_id, scopes, expires_at)` keyed by SHA256 hash
- Expiry check: if `expires_at < now()`, return 401 before scope check
- Scope check: iterate PAT scopes, if any `(method, prefix)` tuple matches → allow
- 403 response includes `required_scope_hint` (map from path to scope name, no info leak)
- For `entities` scope requests: rewrite query params (GET) or JSON body (POST) to enforce `limit <= 500` (default 100 if absent)
- For `export` scope requests: cap `max_rows` at 10,000
- When JSON body is modified, `requests.request(json=...)` auto-recalculates `Content-Length`
- Orion-LD `Link` header propagates transparently (existing gateway proxy pattern)
- Sanitize logs: redact PAT token from Authorization header before logging
- Inject `g.pat_tenant_id` for downstream use (already exists for timeseries proxy)

### 5. DataHub UI Changes

**Files:** `nkz-module-datahub/backend/app/api/integrations.py`, `nkz-module-datahub/src/components/Integrations/`

- Create PAT form: add 4 scope checkboxes + optional expiry date picker
- PAT list: show scope badges + expiry date per key
- Backend proxy passes `scopes` and `expires_at` fields through to tenant-webhook transparently

## Verification

| # | Case | Expected |
|---|------|----------|
| 1 | PAT `timeseries` → `GET /api/timeseries/v2/entities/urn:x/data` | 200 |
| 2 | PAT `timeseries` → `GET /ngsi-ld/v1/entities` | 403 + scope hint |
| 3 | PAT `entities` → `GET /ngsi-ld/v1/entities?type=AgriParcel` | 200 |
| 4 | PAT `entities` → `POST /ngsi-ld/v1/entityOperations/query` with filter body | 200 |
| 5 | PAT `entities` → `POST /ngsi-ld/v1/entityOperations/create` | 403 (method+prefix mismatch) |
| 6 | PAT `entities` → `PATCH /ngsi-ld/v1/entities/urn:x/attrs` | 403 |
| 7 | PAT `export` → `POST /api/datahub/export` | 200 |
| 8 | PAT no scopes → any route | 403 |
| 9 | PAT expired → valid route for its scope | 401 |
| 10 | Normal JWT → any route | unchanged, bypasses PAT enforcement |
| 11 | `POST /api/tenant/api-keys` with `scopes: ["invalid_scope"]` | 400 |
| 12 | `GET /api/tenant/api-keys` listing | scopes + expires_at in response |
| 13 | Existing PAT (migrated) → `GET /api/timeseries/...` | 200 (backfilled `timeseries` scope) |
| 14 | PAT `entities` → `GET /ngsi-ld/v1/entities?limit=2000` | 200, limit truncated to 500 |
| 15 | PAT `entities` → `GET /ngsi-ld/v1/entities` (no limit) | 200, limit injected as 100 |
| 16 | PAT `entities` → Orion-LD returns `Link` header with next page | Link header forwarded in response |
| 17 | PAT `export` → `POST /api/datahub/export` with `max_rows: 50000` | 200, max_rows capped at 10000 |

## Security

- Token: 256-bit random (`secrets.token_urlsafe(32)`), infeasible to brute-force
- Storage: SHA256 hash in `api_keys`, raw token never persisted
- Transport: HTTPS only (Traefik + cert-manager)
- Internal validation: `/internal/validate-pat` protected by `X-Internal-Secret` shared secret
- HTTP method enforcement: scope mapping uses `(method, prefix)` tuples — a `PATCH` with an `entities`-scoped PAT is rejected even though the path matches
- Pagination cap: PAT requests to NGSI-LD endpoints are capped at 500 entities per page, preventing mass extraction
- Logs: gateway sanitizes Authorization header when PAT detected (redact token body)
- Expiry: `expires_at` column, default 365 days from creation, enforced at gateway

### Known Limitations (Level 2+)

- **Revocation window:** Redis TTL 300s. A revoked PAT may remain active for up to 5 minutes. Active cache invalidation (Redis Pub/Sub from tenant-webhook) deferred to Level 2.
- **Per-PAT rate limiting:** only per-tenant rate limiting exists today. A PAT shares the rate limit with the tenant's browser sessions.
- **PAT usage audit log:** no per-PAT access log. Forensic investigation after a token leak is limited.

## Out of Scope (Level 2+)

- Per-PAT rate limiting
- PAT usage audit log
- Keycloak M2M client for OAuth2 client_credentials
- Dynamic scope management UI in host (core)
- Redis active cache invalidation on PAT revocation
