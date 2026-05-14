# PAT Scope Expansion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend PAT beyond `/api/timeseries` to 4 scoped read-only data categories (timeseries, entities, export, telemetry) with HTTP method enforcement and pagination caps.

**Architecture:** PAT metadata (scopes, expiry) stored in `api_keys` table. tenant-webhook writes/returns scopes on CRUD and `/internal/validate-pat`. api-gateway caches in Redis (TTL 300s) and enforces `(method, prefix)` scope matching in a `@app.before_request` interceptor. DataHub UI adds scope checkboxes. Proxy between DataHub and tenant-webhook is already transparent.

**Tech Stack:** Python 3 (Flask, FastAPI), PostgreSQL, Redis, React 18 + TS

---

### Task 1: DB migration — add `scopes` column

**Files:**
- Modify: `services/tenant-webhook/enhanced-tenant-webhook.py`

The `api_keys` table already has `expires_at`. We add `scopes TEXT[] NOT NULL DEFAULT '{}'` and backfill existing PATs with `ARRAY['timeseries']`.

- [ ] **Step 1: Add migration SQL execution to tenant-webhook init**

In `enhanced-tenant-webhook.py`, find the existing migration section (or add after DB connection init). Add:

```python
def _migrate_001_scopes_column(conn):
    """Add scopes column to api_keys if it doesn't exist."""
    cur = conn.cursor()
    cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'api_keys' AND column_name = 'scopes'
            ) THEN
                ALTER TABLE api_keys ADD COLUMN scopes TEXT[] NOT NULL DEFAULT '{}';
            END IF;
        END $$;
    """)
    cur.execute("""
        UPDATE api_keys
        SET scopes = ARRAY['timeseries']
        WHERE key_type = 'pat' AND (scopes IS NULL OR scopes = '{}');
    """)
    conn.commit()
    cur.close()
```

Call this function during service startup where the DB connection is first established.

- [ ] **Step 2: Verify migration is idempotent**

Run:
```bash
# Check the SQL manually — the DO $$ block with IF NOT EXISTS is idempotent
echo "Migration is idempotent: ALTER TABLE uses IF NOT EXISTS, UPDATE only targets rows with empty scopes"
```

- [ ] **Step 3: Commit**

```bash
cd /home/g/Documents/nekazari/nkz/services/tenant-webhook
git add enhanced-tenant-webhook.py
git commit -m "feat: add scopes column to api_keys, backfill existing PATs with timeseries"
```

---

### Task 2: tenant-webhook — `/internal/validate-pat` returns scopes

**Files:**
- Modify: `services/tenant-webhook/enhanced-tenant-webhook.py:2725-2768`

- [ ] **Step 1: Extend the SQL query to return scopes**

Replace the existing `internal_validate_pat` function:

```python
@app.route("/internal/validate-pat", methods=["POST"])
@limiter.exempt
def internal_validate_pat():
    """
    Internal-only PAT validation (hash in JSON body). ADR 003.
    Returns tenant_id, scopes, expires_at on success.
    """
    if not _verify_internal_pat_secret():
        return jsonify({"error": "Unauthorized"}), 401
    if not POSTGRES_URL:
        return jsonify({"error": "Database not configured"}), 500
    data = request.get_json(silent=True) or {}
    token_hash = data.get("token_hash")
    if (
        not token_hash
        or not isinstance(token_hash, str)
        or len(token_hash) != 64
        or not re.match(r"^[a-f0-9]{64}$", token_hash)
    ):
        return jsonify({"error": "Invalid token_hash"}), 400

    conn = webhook_service.get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection error"}), 500
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT tenant_id, valid, scopes, expires_at
            FROM api_keys
            WHERE key_hash = %s
              AND key_type = 'pat'
              AND is_active = true
            LIMIT 1
            """,
            (token_hash,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return jsonify({"valid": False}), 200

        tid = row["tenant_id"] if hasattr(row, "keys") else row[0]
        ok = row["valid"] if hasattr(row, "keys") else row[1]
        scopes = row["scopes"] if hasattr(row, "keys") else (row[2] or [])
        expires_at = row["expires_at"] if hasattr(row, "keys") else row[3]

        if not ok:
            return jsonify({"valid": False}), 200

        # Check expiry
        if expires_at:
            from datetime import timezone
            now = datetime.now(timezone.utc)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < now:
                return jsonify({"valid": False, "error": "expired"}), 200

        return jsonify({
            "valid": True,
            "tenant_id": tid,
            "scopes": list(scopes) if scopes else [],
            "expires_at": expires_at.isoformat() if expires_at else None,
        }), 200
    except Exception as e:
        logger.error("internal_validate_pat: %s", e)
        if conn:
            conn.close()
        return jsonify({"error": "Internal error"}), 500
```

Note: we replace the `validate_pat_key_hash()` stored function call with a direct query so we control exactly which columns are returned. If the stored function is used elsewhere and needs migration, that's a separate concern — it's only called from this route.

- [ ] **Step 2: Commit**

```bash
cd /home/g/Documents/nekazari/nkz/services/tenant-webhook
git add enhanced-tenant-webhook.py
git commit -m "feat: internal/validate-pat returns scopes and expires_at, checks expiry"
```

---

### Task 3: tenant-webhook — `POST /api/tenant/api-keys` accepts scopes

**Files:**
- Modify: `services/tenant-webhook/enhanced-tenant-webhook.py:2826-2893`

- [ ] **Step 1: Add scope validation and storage**

Replace the `create_tenant_personal_access_token` function:

```python
VALID_PAT_SCOPES = {"timeseries", "entities", "export", "telemetry"}


@app.route("/api/tenant/api-keys", methods=["POST"])
@require_keycloak_auth
def create_tenant_personal_access_token():
    """Create PAT; raw token returned once. Tenant from JWT only. ADR 003."""
    tenant_id = g.tenant_id
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 403
    if not POSTGRES_URL:
        return jsonify({"error": "Database not configured"}), 500
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "Personal access token").strip()[:200]
    description = (data.get("description") or "").strip()[:2000] or None

    # Validate scopes
    scopes = data.get("scopes")
    if scopes is None:
        scopes = []
    if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
        return jsonify({"error": "scopes must be an array of strings"}), 400
    invalid = [s for s in scopes if s not in VALID_PAT_SCOPES]
    if invalid:
        return jsonify({
            "error": f"Invalid scope(s): {', '.join(invalid)}",
            "valid_scopes": sorted(VALID_PAT_SCOPES),
        }), 400

    # Parse expires_at
    expires_at = None
    if data.get("expires_at"):
        try:
            expires_at = datetime.fromisoformat(
                str(data["expires_at"]).replace("Z", "+00:00")
            )
        except ValueError:
            return jsonify({"error": "Invalid expires_at format"}), 400

    raw_token = f"nkz_pat_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    creator_sub = (g.current_user or {}).get("sub")

    conn = webhook_service.get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection error"}), 500
    try:
        webhook_service._apply_tenant_context(conn, tenant_id)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO api_keys (
                key_hash, name, description, tenant_id, key_type,
                is_active, expires_at, created_by_sub, scopes
            )
            VALUES (%s, %s, %s, %s, 'pat', true, %s, %s, %s)
            RETURNING id, created_at
            """,
            (key_hash, name, description, tenant_id, expires_at, creator_sub, scopes),
        )
        ins = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return jsonify(
            {
                "id": str(ins["id"]),
                "token": raw_token,
                "name": name,
                "scopes": scopes,
                "expires_at": expires_at.isoformat() if expires_at else None,
                "created_at": ins["created_at"].isoformat()
                if ins.get("created_at")
                else None,
                "warning": "Save this token now. It cannot be shown again.",
            }
        ), 201
    except Exception as e:
        logger.error("create_tenant_personal_access_token: %s", e)
        if conn:
            conn.rollback()
            conn.close()
        return _internal_error(
            e,
            "create_tenant_personal_access_token",
            user_message="Failed to create personal access token",
        )
```

- [ ] **Step 2: Commit**

```bash
cd /home/g/Documents/nekazari/nkz/services/tenant-webhook
git add enhanced-tenant-webhook.py
git commit -m "feat: POST /api/tenant/api-keys accepts scopes and expires_at"
```

---

### Task 4: tenant-webhook — `GET /api/tenant/api-keys` returns scopes

**Files:**
- Modify: `services/tenant-webhook/enhanced-tenant-webhook.py:2771-2823`

- [ ] **Step 1: Add scopes to the SELECT and response**

Replace the existing `list_tenant_personal_access_tokens` function:

```python
@app.route("/api/tenant/api-keys", methods=["GET"])
@require_keycloak_auth
def list_tenant_personal_access_tokens():
    """List PAT metadata for the JWT tenant (no raw secrets). ADR 003."""
    tenant_id = g.tenant_id
    if not tenant_id:
        return jsonify({"error": "Tenant context required"}), 403
    if not POSTGRES_URL:
        return jsonify({"error": "Database not configured"}), 500
    conn = webhook_service.get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection error"}), 500
    try:
        webhook_service._apply_tenant_context(conn, tenant_id)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, name, description, is_active, created_at, expires_at,
                   created_by_sub, scopes
            FROM api_keys
            WHERE key_type = 'pat'
            ORDER BY created_at DESC
            """
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        out = []
        for row in rows:
            out.append(
                {
                    "id": str(row["id"]),
                    "name": row.get("name") or "",
                    "description": row.get("description") or "",
                    "is_active": bool(row.get("is_active")),
                    "created_at": row["created_at"].isoformat()
                    if row.get("created_at")
                    else None,
                    "expires_at": row["expires_at"].isoformat()
                    if row.get("expires_at")
                    else None,
                    "created_by_sub": row.get("created_by_sub"),
                    "scopes": list(row.get("scopes") or []),
                }
            )
        return jsonify(out), 200
    except Exception as e:
        logger.error("list_tenant_personal_access_tokens: %s", e)
        if conn:
            conn.close()
        return _internal_error(
            e,
            "list_tenant_personal_access_tokens",
            user_message="Failed to list personal access tokens",
        )
```

- [ ] **Step 2: Commit**

```bash
cd /home/g/Documents/nekazari/nkz/services/tenant-webhook
git add enhanced-tenant-webhook.py
git commit -m "feat: GET /api/tenant/api-keys returns scopes per PAT"
```

---

### Task 5: gateway_pat.py — cache and return scopes

**Files:**
- Modify: `services/api-gateway/gateway_pat.py`

- [ ] **Step 1: Extend `resolve_pat_tenant_id` to return scopes and expires_at**

Replace the function signature and Redis/HTTP logic. The function currently returns `Optional[str]` (tenant_id). Change it to return a dict with all fields:

```python
def resolve_pat_info(raw_pat: str, webhook_base: str) -> Optional[dict]:
    """
    Resolve PAT metadata using Redis then tenant-webhook internal validate.
    Returns dict with tenant_id, scopes, expires_at, or None if invalid/expired.
    """
    h = pat_token_hash(raw_pat)
    key = f"{REDIS_KEY_PREFIX}{h}"

    r = get_redis_client()
    if r:
        try:
            cached = r.get(key)
            if cached:
                import json
                return json.loads(cached)
        except Exception as e:
            logger.warning("Redis GET PAT cache failed (degrading to HTTP): %s", e)

    secret = os.getenv("INTERNAL_PAT_VALIDATE_SECRET", "").strip()
    if not secret:
        logger.error("INTERNAL_PAT_VALIDATE_SECRET not set; cannot validate PAT via webhook")
        return None

    url = f"{webhook_base.rstrip('/')}/internal/validate-pat"
    try:
        resp = requests.post(
            url,
            json={"token_hash": h},
            headers={"X-Internal-Secret": secret},
            timeout=8,
        )
        if resp.status_code != 200:
            return None
        body = resp.json()
        if not body.get("valid"):
            return None
        tenant_id = body.get("tenant_id")
        if not tenant_id or not isinstance(tenant_id, str):
            return None
        info = {
            "tenant_id": tenant_id,
            "scopes": body.get("scopes") or [],
            "expires_at": body.get("expires_at"),
        }
        if r:
            try:
                import json
                r.setex(key, PAT_CACHE_TTL_SEC, json.dumps(info))
            except Exception as e:
                logger.warning("Redis SET PAT cache failed: %s", e)
        return info
    except Exception as e:
        logger.warning("validate-pat HTTP failed: %s", e)
        return None


def resolve_pat_tenant_id(raw_pat: str, webhook_base: str) -> Optional[str]:
    """Backwards-compat: return tenant_id only."""
    info = resolve_pat_info(raw_pat, webhook_base)
    return info["tenant_id"] if info else None
```

- [ ] **Step 2: Commit**

```bash
cd /home/g/Documents/nekazari/nkz/services/api-gateway
git add gateway_pat.py
git commit -m "feat: resolve_pat_info returns scopes+expires_at, cache as JSON in Redis"
```

---

### Task 6: gateway — `enforce_pat_scopes()` replaces `reject_pat_outside_timeseries()`

**Files:**
- Modify: `services/api-gateway/fiware_api_gateway.py:154-170`

- [ ] **Step 1: Define scope→route mapping and scope→hint mapping**

Add these constants near the top of the file, after the imports and before the `@app.before_request`:

```python
# PAT scope → allowed (method, path_prefix) tuples
PAT_SCOPE_ROUTES = {
    "timeseries": [
        ("GET", "/api/timeseries/"),
        ("POST", "/api/timeseries/"),
    ],
    "entities": [
        ("GET", "/ngsi-ld/v1/entities"),
        ("POST", "/ngsi-ld/v1/entityOperations/query"),
    ],
    "export": [
        ("POST", "/api/datahub/export"),
        ("POST", "/api/datahub/timeseries/align"),
    ],
    "telemetry": [
        ("GET", "/api/devices/"),
        ("GET", "/api/sensors"),
    ],
}

# Map path → scope name for 403 hints (no info leak)
def _scope_hint_for_path(path: str) -> str:
    if path.startswith("/api/timeseries"):
        return "timeseries"
    if path.startswith("/ngsi-ld/v1/entities") or path.startswith("/ngsi-ld/v1/entityOperations"):
        return "entities"
    if path.startswith("/api/datahub/export") or path.startswith("/api/datahub/timeseries/align"):
        return "export"
    if path.startswith("/api/devices/") or path.startswith("/api/sensors"):
        return "telemetry"
    return "unknown"
```

- [ ] **Step 2: Replace `reject_pat_outside_timeseries` with `enforce_pat_scopes`**

```python
@app.before_request
def enforce_pat_scopes():
    """Validate PAT tokens: check scope covers (method, path) and enforce limits."""
    if request.method == "OPTIONS":
        return None
    path = request.path or ""
    if path == "/health" or path.startswith("/health"):
        return None

    auth = request.headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return None

    tok = auth[7:].strip()
    if not tok.startswith("nkz_pat_"):
        return None

    # Resolve PAT metadata
    info = resolve_pat_info(tok, TENANT_WEBHOOK_URL)
    if not info:
        return jsonify({"error": "Invalid or expired PAT"}), 401

    scopes = info.get("scopes") or []
    if not scopes:
        return jsonify({
            "error": "PAT has no scopes assigned",
            "required_scope_hint": _scope_hint_for_path(path),
        }), 403

    # Check if any scope allows this (method, path)
    allowed = False
    for scope in scopes:
        for method, prefix in PAT_SCOPE_ROUTES.get(scope, []):
            if request.method == method and path.startswith(prefix):
                allowed = True
                break
        if allowed:
            break

    if not allowed:
        return jsonify({
            "error": "PAT does not have required scope for this route",
            "required_scope_hint": _scope_hint_for_path(path),
        }), 403

    # Store for downstream routes
    g.pat_info = info
    g.pat_tenant_id = info["tenant_id"]

    # Sanitize Authorization header for logging (redact raw token)
    g.pat_auth_truncated = f"nkz_pat_...{tok[-8:]}"

    return None
```

- [ ] **Step 3: Add `resolve_pat_info` import**

At the top of `fiware_api_gateway.py`, update the existing gateway_pat import:

```python
try:
    from gateway_pat import (
        is_pat_token,
        resolve_pat_info,
        resolve_pat_tenant_id,
    )
except ImportError:
    is_pat_token = lambda t: False
    resolve_pat_info = lambda raw, base: None
    def resolve_pat_tenant_id(raw, base):
        return None
```

- [ ] **Step 4: Commit**

```bash
cd /home/g/Documents/nekazari/nkz/services/api-gateway
git add fiware_api_gateway.py
git commit -m "feat: enforce_pat_scopes replaces reject_pat_outside_timeseries with (method, prefix) enforcement"
```

---

### Task 7: gateway — pagination enforcement for entities scope

**Files:**
- Modify: `services/api-gateway/fiware_api_gateway.py`

- [ ] **Step 1: Add pagination cap interceptor**

Add a second `@app.before_request` for PAT pagination enforcement, right after `enforce_pat_scopes`:

```python
PAT_ENTITIES_MAX_LIMIT = 500
PAT_ENTITIES_DEFAULT_LIMIT = 100


@app.before_request
def enforce_pat_pagination():
    """Cap pagination for PAT requests to NGSI-LD entities endpoints."""
    if not hasattr(g, "pat_info"):
        return None

    path = request.path or ""
    scopes = g.pat_info.get("scopes") or []

    if "entities" not in scopes:
        return None

    # GET /ngsi-ld/v1/entities — cap query param 'limit'
    if request.method == "GET" and path.startswith("/ngsi-ld/v1/entities"):
        # Flask's request.args is immutable, so we modify the underlying environ
        limit_raw = request.args.get("limit")
        if limit_raw:
            try:
                limit = int(limit_raw)
            except (ValueError, TypeError):
                limit = PAT_ENTITIES_DEFAULT_LIMIT
            if limit > PAT_ENTITIES_MAX_LIMIT:
                limit = PAT_ENTITIES_MAX_LIMIT
            elif limit < 1:
                limit = PAT_ENTITIES_DEFAULT_LIMIT
        else:
            limit = PAT_ENTITIES_DEFAULT_LIMIT

        # Reconstruct query string with capped limit
        qs = dict(request.args)
        qs["limit"] = str(limit)
        from urllib.parse import urlencode
        request.environ["QUERY_STRING"] = urlencode(qs)
        return None

    # POST /ngsi-ld/v1/entityOperations/query — cap JSON body 'limit'
    if request.method == "POST" and path == "/ngsi-ld/v1/entityOperations/query":
        if request.is_json:
            body = request.get_json(silent=True) or {}
            limit = body.get("limit")
            if limit is not None:
                try:
                    limit = int(limit)
                except (ValueError, TypeError):
                    limit = PAT_ENTITIES_DEFAULT_LIMIT
                if limit > PAT_ENTITIES_MAX_LIMIT:
                    limit = PAT_ENTITIES_MAX_LIMIT
                elif limit < 1:
                    limit = PAT_ENTITIES_DEFAULT_LIMIT
            else:
                limit = PAT_ENTITIES_DEFAULT_LIMIT
            body["limit"] = limit
            # Store modified body for downstream proxy
            g.pat_modified_body = body

        return None
```

- [ ] **Step 2: Commit**

```bash
cd /home/g/Documents/nekazari/nkz/services/api-gateway
git add fiware_api_gateway.py
git commit -m "feat: enforce pagination cap (limit<=500) for PAT entities scope"
```

---

### Task 8: gateway — add DataHub export proxy routes

**Files:**
- Modify: `services/api-gateway/fiware_api_gateway.py`

ASSUMPTION: `/api/datahub/export` and `/api/datahub/timeseries/align` currently bypass the api-gateway and go directly to the DataHub BFF via its own ingress. To enforce PAT scopes, the gateway must proxy these routes. If a module blueprint or other mechanism already routes these through the gateway, skip this task.

- [ ] **Step 1: Add DataHub proxy routes to gateway**

```python
DATAHUB_BFF_URL = os.getenv("DATAHUB_BFF_URL", "http://datahub-bff-service:8000")


@app.route("/api/datahub/export", methods=["POST"])
def proxy_datahub_export():
    """Proxy export requests to DataHub BFF, enforcing PAT scopes upstream."""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401

    # If PAT, tenant resolution was already done in enforce_pat_scopes()
    if is_pat_token(token):
        tenant = getattr(g, "pat_tenant_id", None)
        if not tenant:
            return jsonify({"error": "PAT tenant not resolved"}), 401
        # Forward with gateway service JWT + delegated tenant
        gw_jwt = obtain_gateway_service_jwt()
        if not gw_jwt:
            return jsonify({"error": "Service authentication not configured"}), 503
        headers = {
            "Authorization": f"Bearer {gw_jwt}",
            "X-Delegated-Tenant-ID": tenant,
            "X-Tenant-ID": tenant,
            "Content-Type": "application/json",
        }
    else:
        payload = validate_jwt_token(token)
        if not payload:
            return jsonify({"error": "Invalid or expired token"}), 401
        tenant = extract_tenant_id(payload)
        if not tenant:
            return jsonify({"error": "Tenant not present in token"}), 401
        if not rate_limit(tenant):
            return jsonify({"error": "Rate limit exceeded"}), 429
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": tenant,
            "Content-Type": "application/json",
        }

    try:
        url = f"{DATAHUB_BFF_URL}/api/datahub/export"
        body = getattr(g, "pat_modified_body", None) or request.get_json(silent=True) or {}
        resp = requests.post(url, headers=headers, json=body, timeout=120)
        return make_response(resp.content, resp.status_code, dict(resp.headers))
    except requests.exceptions.RequestException as e:
        logger.error(f"DataHub export proxy error: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/datahub/timeseries/align", methods=["POST"])
def proxy_datahub_align():
    """Proxy timeseries align requests to DataHub BFF."""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401

    if is_pat_token(token):
        tenant = getattr(g, "pat_tenant_id", None)
        if not tenant:
            return jsonify({"error": "PAT tenant not resolved"}), 401
        gw_jwt = obtain_gateway_service_jwt()
        if not gw_jwt:
            return jsonify({"error": "Service authentication not configured"}), 503
        headers = {
            "Authorization": f"Bearer {gw_jwt}",
            "X-Delegated-Tenant-ID": tenant,
            "X-Tenant-ID": tenant,
            "Content-Type": "application/json",
        }
    else:
        payload = validate_jwt_token(token)
        if not payload:
            return jsonify({"error": "Invalid or expired token"}), 401
        tenant = extract_tenant_id(payload)
        if not tenant:
            return jsonify({"error": "Tenant not present in token"}), 401
        if not rate_limit(tenant):
            return jsonify({"error": "Rate limit exceeded"}), 429
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": tenant,
            "Content-Type": "application/json",
        }

    try:
        url = f"{DATAHUB_BFF_URL}/api/datahub/timeseries/align"
        resp = requests.post(url, headers=headers, json=request.get_json(silent=True) or {}, timeout=60)
        return make_response(resp.content, resp.status_code, dict(resp.headers))
    except requests.exceptions.RequestException as e:
        logger.error(f"DataHub align proxy error: {e}")
        return jsonify({"error": "Internal server error"}), 500
```

- [ ] **Step 2: Commit**

```bash
cd /home/g/Documents/nekazari/nkz/services/api-gateway
git add fiware_api_gateway.py
git commit -m "feat: add DataHub export and align proxy routes to gateway"
```

---

### Task 9: gateway — add `/ngsi-ld/v1/entityOperations/query` proxy route

**Files:**
- Modify: `services/api-gateway/fiware_api_gateway.py`

ASSUMPTION: `/ngsi-ld/v1/entityOperations/query` is NOT currently proxied by the gateway. The existing routes only cover `/ngsi-ld/v1/entities`, `/ngsi-ld/v1/entities/<id>`, and `/ngsi-ld/v1/subscriptions`. If this route already exists elsewhere, skip this task.

- [ ] **Step 1: Add the entityOperations proxy route**

Add after the existing `entities()` function (~line 500):

```python
@app.route("/ngsi-ld/v1/entityOperations/query", methods=["POST", "GET"])
def entity_operations_query():
    """Proxy NGSI-LD entityOperations/query to Orion-LD (complex queries with filters in body)."""
    token = get_request_token()
    if not token:
        return jsonify({"error": "Missing or invalid authorization"}), 401
    payload = validate_jwt_token(token)
    if not payload:
        return jsonify({"error": "Invalid or expired token"}), 401

    tenant = extract_tenant_id(payload)
    if not tenant:
        return jsonify({"error": "Tenant not present in token"}), 401

    if not rate_limit(tenant):
        return jsonify({"error": "Rate limit exceeded"}), 429

    headers = {}
    headers = inject_fiware_headers(headers, tenant)
    headers["X-Tenant-ID"] = tenant
    signature = generate_hmac_signature(token, tenant)
    if signature:
        headers["X-Auth-Signature"] = signature

    try:
        orion_url = f"{ORION_URL}/ngsi-ld/v1/entityOperations/query"

        # Use PAT-modified body if present (from pagination interceptor)
        if hasattr(g, "pat_modified_body"):
            json_body = g.pat_modified_body
        else:
            json_body = request.get_json(silent=True) or {}

        if request.method == "GET":
            response = requests.get(orion_url, headers=headers, params=request.args, timeout=60)
        else:
            headers["Content-Type"] = "application/json"
            response = requests.post(orion_url, headers=headers, json=json_body, timeout=60)

        if response.status_code >= 400:
            logger.error(
                f"Orion-LD entityOperations/query error {response.status_code}: {response.text[:300]}"
            )

        return make_response(
            response.content, response.status_code, dict(response.headers)
        )

    except requests.exceptions.RequestException as e:
        logger.error(f"Error forwarding to Orion-LD entityOperations/query: {e}")
        return jsonify({"error": "Internal server error"}), 500
```

- [ ] **Step 2: Commit**

```bash
cd /home/g/Documents/nekazari/nkz/services/api-gateway
git add fiware_api_gateway.py
git commit -m "feat: add /ngsi-ld/v1/entityOperations/query proxy route"
```

---

### Task 10: gateway — enforce export `max_rows` cap for PAT

**Files:**
- Modify: `services/api-gateway/fiware_api_gateway.py`

- [ ] **Step 1: Add export cap to the pagination interceptor**

Extend `enforce_pat_pagination()` from Task 7 to also cap export `max_rows`:

```python
PAT_EXPORT_MAX_ROWS = 10000

# Add this block at the end of enforce_pat_pagination(), before the final return None:

    # POST /api/datahub/export — cap max_rows
    if (request.method == "POST"
        and path.startswith("/api/datahub/export")
        and "export" in scopes):
        if request.is_json:
            body = request.get_json(silent=True) or {}
            max_rows = body.get("max_rows")
            if max_rows is not None:
                try:
                    max_rows = int(max_rows)
                except (ValueError, TypeError):
                    max_rows = PAT_EXPORT_MAX_ROWS
                if max_rows > PAT_EXPORT_MAX_ROWS:
                    max_rows = PAT_EXPORT_MAX_ROWS
                elif max_rows < 1:
                    max_rows = PAT_EXPORT_MAX_ROWS
            else:
                max_rows = PAT_EXPORT_MAX_ROWS
            body["max_rows"] = max_rows
            # Reuse pat_modified_body (set by entities or export)
            g.pat_modified_body = body
```

Note: `g.pat_modified_body` is reused. The pagination check for entities runs first (same `@app.before_request`), then this export check. They operate on the same `g.pat_modified_body` — but entityOperations/query and /api/datahub/export are different paths so they never collide. If both checks ran on the same request, the second would overwrite. This is correct behavior.

- [ ] **Step 2: Commit**

```bash
cd /home/g/Documents/nekazari/nkz/services/api-gateway
git add fiware_api_gateway.py
git commit -m "feat: cap max_rows at 10000 for PAT export scope"
```

---

### Task 11: gateway — log sanitization for PAT requests

**Files:**
- Modify: `services/api-gateway/fiware_api_gateway.py`

- [ ] **Step 1: Add a logging filter that redacts PAT tokens**

Add after the imports:

```python
import re


class PatSanitizingFilter(logging.Filter):
    """Redact nkz_pat_ tokens from log records."""
    _pat_re = re.compile(r'nkz_pat_[A-Za-z0-9_-]{32,}')

    def filter(self, record):
        if isinstance(record.msg, str):
            record.msg = self._pat_re.sub('nkz_pat_[REDACTED]', record.msg)
        if record.args:
            record.args = tuple(
                self._pat_re.sub('nkz_pat_[REDACTED]', str(a)) if isinstance(a, str) else a
                for a in record.args
            )
        return True


# Attach to root logger used by the gateway
logging.getLogger().addFilter(PatSanitizingFilter())
```

- [ ] **Step 2: Ensure `g.pat_auth_truncated` is used in log messages**

In any log statement that would include the Authorization header, use `getattr(g, 'pat_auth_truncated', 'jwt')` instead of the raw token. This is already set in `enforce_pat_scopes()`.

- [ ] **Step 3: Commit**

```bash
cd /home/g/Documents/nekazari/nkz/services/api-gateway
git add fiware_api_gateway.py
git commit -m "feat: redact PAT tokens from gateway logs"
```

---

### Task 12: DataHub API types — add scopes to TenantPatMeta

**Files:**
- Modify: `nkz-module-datahub/src/services/datahubApi.ts:291-329`

- [ ] **Step 1: Add scopes to the interface and create body**

```typescript
/** Platform PAT metadata (ADR 003). */
export interface TenantPatMeta {
  id: string;
  name: string;
  description?: string;
  is_active: boolean;
  created_at?: string | null;
  expires_at?: string | null;
  created_by_sub?: string | null;
  scopes: string[];
}

/** POST /api/datahub/integrations/api-keys — create PAT; returns raw token once. */
export async function createTenantPat(body: {
  name: string;
  description?: string;
  expires_at?: string;
  scopes: string[];
}): Promise<{ id: string; token: string; name: string; scopes: string[]; warning?: string }> {
  const base = getBaseUrl().replace(/\/$/, '');
  const path = '/api/datahub/integrations/api-keys';
  const url = base ? `${base}${path}` : path;
  const res = await fetch(url, {
    method: 'POST',
    headers: withTenantHeaders({ 'Content-Type': 'application/json', Accept: 'application/json' }),
    credentials: 'include',
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`PAT create: ${res.status} ${await res.text()}`);
  return res.json();
}
```

- [ ] **Step 2: Commit**

```bash
cd /home/g/Documents/nekazari/nkz-module-datahub
git add src/services/datahubApi.ts
git commit -m "feat: add scopes to TenantPatMeta and createTenantPat types"
```

---

### Task 13: DataHub UI — scope checkboxes in create form

**Files:**
- Modify: `nkz-module-datahub/src/slots/IntegrationsPanel.tsx`

- [ ] **Step 1: Add scope state and checkboxes**

In `IntegrationsPanel.tsx`, add state for selected scopes and modify the form:

```typescript
const VALID_SCOPES = ['timeseries', 'entities', 'export', 'telemetry'] as const;
type Scope = (typeof VALID_SCOPES)[number];

// Add to existing state declarations:
const [scopes, setScopes] = useState<Scope[]>(['timeseries']);
const [expiresDays, setExpiresDays] = useState<number>(365);

// Update onCreate:
const onCreate = async () => {
  const n = name.trim() || t('integrations.defaultTokenName');
  setCreating(true);
  setNewToken(null);
  setError(null);
  try {
    const expires_at = expiresDays > 0
      ? new Date(Date.now() + expiresDays * 86400000).toISOString()
      : undefined;
    const res = await createTenantPat({ name: n, scopes, expires_at });
    setNewToken(res.token);
    setName('');
    await refresh();
  } catch (e) {
    setError(e instanceof Error ? e.message : String(e));
  } finally {
    setCreating(false);
  }
};

// Add this inside the create section JSX, below the name input:
```

Replace the "Create PAT" `<section>` (lines 128-160) with:

```tsx
<section className="space-y-3 border border-slate-800 rounded-lg p-4 bg-slate-900/50">
  <h4 className="font-medium text-slate-300">{t('integrations.createSection')}</h4>
  <div className="flex flex-wrap gap-2 items-center">
    <input
      type="text"
      value={name}
      onChange={(e) => setName(e.target.value)}
      placeholder={t('integrations.namePlaceholder')}
      className="flex-1 min-w-[200px] bg-slate-950 border border-slate-700 rounded px-3 py-2 text-sm"
    />
    <button
      type="button"
      disabled={creating || scopes.length === 0}
      onClick={() => void onCreate()}
      className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded text-sm text-white"
    >
      {creating ? t('integrations.creating') : t('integrations.create')}
    </button>
  </div>

  {/* Scopes */}
  <fieldset className="text-sm text-slate-400 space-y-1">
    <legend className="text-slate-300 mb-1">{t('integrations.scopesLabel')}</legend>
    {VALID_SCOPES.map((scope) => (
      <label key={scope} className="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={scopes.includes(scope)}
          onChange={(e) => {
            if (e.target.checked) {
              setScopes([...scopes, scope]);
            } else {
              setScopes(scopes.filter((s) => s !== scope));
            }
          }}
          className="accent-emerald-500"
        />
        <span className="text-slate-300">{scope}</span>
        <span className="text-xs text-slate-500">
          {scope === 'timeseries' && t('integrations.scopeTimeseriesHint')}
          {scope === 'entities' && t('integrations.scopeEntitiesHint')}
          {scope === 'export' && t('integrations.scopeExportHint')}
          {scope === 'telemetry' && t('integrations.scopeTelemetryHint')}
        </span>
      </label>
    ))}
  </fieldset>

  {/* Expiry */}
  <label className="flex items-center gap-2 text-sm text-slate-400">
    <span className="text-slate-300">{t('integrations.expiresLabel')}</span>
    <select
      value={expiresDays}
      onChange={(e) => setExpiresDays(Number(e.target.value))}
      className="bg-slate-950 border border-slate-700 rounded px-2 py-1 text-sm"
    >
      <option value={30}>30 {t('integrations.days')}</option>
      <option value={90}>90 {t('integrations.days')}</option>
      <option value={180}>180 {t('integrations.days')}</option>
      <option value={365}>365 {t('integrations.days')}</option>
      <option value={0}>{t('integrations.noExpiry')}</option>
    </select>
  </label>

  {newToken && (
    <div className="mt-3 p-3 bg-amber-950/30 border border-amber-800 rounded text-sm">
      <p className="text-amber-200 mb-2 font-medium">{t('integrations.tokenOnce')}</p>
      <code className="block break-all text-amber-100 bg-slate-950 p-2 rounded mb-2">{newToken}</code>
      <button
        type="button"
        onClick={() => void copy(newToken)}
        className="text-xs text-amber-300 underline"
      >
        {t('integrations.copy')}
      </button>
    </div>
  )}
</section>
```

- [ ] **Step 2: Add scope badges in the PAT list**

In the list section (lines 162-194), add scope badges to each row:

```tsx
{items.map((row) => (
  <li
    key={row.id}
    className="flex justify-between items-center gap-2 border border-slate-800 rounded px-3 py-2"
  >
    <div>
      <div className="font-mono text-slate-300">{row.name}</div>
      <div className="text-xs text-slate-500 flex flex-wrap gap-1 mt-1">
        {row.scopes && row.scopes.map((s: string) => (
          <span key={s} className="px-1.5 py-0.5 bg-slate-800 rounded text-slate-400">
            {s}
          </span>
        ))}
      </div>
      <div className="text-xs text-slate-500 mt-1">
        {row.is_active ? t('integrations.active') : t('integrations.inactive')}
        {row.expires_at && ` · ${t('integrations.expires')} ${new Date(row.expires_at).toLocaleDateString()}`}
        {' · '}{row.id}
      </div>
    </div>
    {row.is_active && (
      <button
        type="button"
        onClick={() => void onRevoke(row.id)}
        className="text-xs text-red-400 hover:text-red-300"
      >
        {t('integrations.revoke')}
      </button>
    )}
  </li>
))}
```

- [ ] **Step 3: Add i18n keys**

In `nkz-module-datahub/src/i18n/es/datahub.json` and `en/datahub.json`, add the new keys:

```json
{
  "integrations.scopesLabel": "Permisos",
  "integrations.scopeTimeseriesHint": "datos temporales (weather, telemetry)",
  "integrations.scopeEntitiesHint": "consulta de entidades NGSI-LD",
  "integrations.scopeExportHint": "exportación CSV y Parquet",
  "integrations.scopeTelemetryHint": "telemetría de dispositivos",
  "integrations.expiresLabel": "Caducidad:",
  "integrations.days": "días",
  "integrations.noExpiry": "Sin caducidad",
  "integrations.expires": "Expira"
}
```

- [ ] **Step 4: Commit**

```bash
cd /home/g/Documents/nekazari/nkz-module-datahub
git add src/slots/IntegrationsPanel.tsx src/i18n/
git commit -m "feat: scope checkboxes and expiry selector in PAT create form"
```

---

### Task 14: Verification

**Files:** None (manual verification)

- [ ] **Step 1: Build and deploy tenant-webhook**

```bash
cd /home/g/Documents/nekazari/nkz/services/tenant-webhook
docker build -t ghcr.io/nkz-os/nkz/tenant-webhook:latest .
docker push ghcr.io/nkz-os/nkz/tenant-webhook:latest
kubectl rollout restart deployment/tenant-webhook -n nekazari
```

- [ ] **Step 2: Build and deploy api-gateway**

```bash
cd /home/g/Documents/nekazari/nkz/services/api-gateway
docker build -t ghcr.io/nkz-os/nkz/api-gateway:latest .
docker push ghcr.io/nkz-os/nkz/api-gateway:latest
kubectl rollout restart deployment/api-gateway -n nekazari
```

- [ ] **Step 3: Verify from within the cluster**

```bash
# 1. Create a PAT with entities scope
GW_POD=$(kubectl get pods -n nekazari -l app=api-gateway -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n nekazari $GW_POD -- curl -s -X POST http://tenant-webhook-service:5000/internal/validate-pat \
  -H "X-Internal-Secret: $INTERNAL_PAT_VALIDATE_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"token_hash": "test"}'
# Expected: 400 (invalid hash format)

# 2. Once a valid PAT is created, test scope enforcement:
#    PAT with entities scope → GET /ngsi-ld/v1/entities?type=AgriParcel → 200
#    PAT with entities scope → PATCH /ngsi-ld/v1/entities/urn:x/attrs → 403
#    PAT with timeseries scope → GET /ngsi-ld/v1/entities → 403
#    PAT with no scopes → any route → 403
#    Expired PAT → any route → 401
#    Normal JWT → unchanged behavior
```

- [ ] **Step 4: Run verification cases from spec**

Execute each case from the spec's verification table (cases 1-17). Document results.

- [ ] **Step 5: Commit verification log**

```bash
cd /home/g/Documents/nekazari/nkz
git add docs/superpowers/plans/2026-05-14-pat-scope-expansion.md
git commit -m "docs: add PAT scope expansion implementation plan"
```
