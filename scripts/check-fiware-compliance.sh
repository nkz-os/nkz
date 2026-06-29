#!/usr/bin/env bash
# =============================================================================
# FIWARE Compliance Check
# =============================================================================
# Scans Python files for direct database writes that violate the
# "Zero Direct DB Writes" rule.
#
# Usage:
#   bash check-fiware-compliance.sh          # Check staged files (pre-commit)
#   bash check-fiware-compliance.sh --all    # Check all files (CI, with baseline)
#
# Allowed paths (exempt from check):
#   - */notification_handler.py      (subscription-fed writes)
#   - */subscription_manager.py      (tenant query only, no state writes)
#   - */ingest/*.py                  (reference data loading)
#   - db_helper.py                   (DB connection utilities)
#   - audit_logger.py                (audit trail, not entity state)
#   - parcel_activation.py           (admin/activation state: tenant_parcel_modules)
#   - parcel_reconcile.py            (admin/activation state: tenant_parcel_modules)
#   - config/timescaledb/migrations/ (schema migrations)
#   - docker/*.sql                   (seed data)
#
# Deprecation exemption:
#   INSERT statements inside a function whose docstring contains "DEPRECATED"
#   are skipped (kept for rollback safety, not called).
#
# Note: tenant_parcel_modules is admin/activation-state metadata (CLAUDE.md §1:
# "Admin/metadata writes (tenants, modules, …) are correct in PostgreSQL"), NOT
# observational/timeseries data. Direct writes to it are sanctioned, like
# tenant_weather_locations (locations.py) and tenant_limits.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MODE="${1:-staged}"

EXCLUDE_FILES='notification_handler\.py|subscription_manager\.py|db_helper\.py|audit_logger\.py|locations\.py|parcel_projection\.py|parcel_activation\.py|parcel_reconcile\.py|enhanced-tenant-webhook\.py'
EXCLUDE_DIRS='/ingest/|/tests/|/migrations/|/docker/|__pycache__|\.git|\.worktrees'
EXCLUDE="$EXCLUDE_FILES|$EXCLUDE_DIRS"

violations=0

echo "=== FIWARE Compliance Check ==="
if [ "$MODE" = "--all" ]; then
    echo "Mode: Full scan (CI)"
else
    echo "Mode: Staged files only (pre-commit)"
fi
echo ""

get_files() {
    if [ "$MODE" = "--all" ]; then
        find "$REPO_ROOT/services" -name "*.py" -type f 2>/dev/null
    else
        # Get staged Python files
        cd "$REPO_ROOT"
        git diff --cached --name-only --diff-filter=ACM 2>/dev/null | grep '\.py$' | sed "s|^|$REPO_ROOT/|" || true
    fi
}

files=$(get_files)

if [ -z "$files" ]; then
    echo "No Python files to check."
    exit 0
fi

# ── Shared check for exact pattern matches (simple, non-exempt) ──
check_pattern() {
    local name="$1"
    local pattern="$2"
    local hits

    hits=$(echo "$files" | xargs grep -lE "$pattern" 2>/dev/null | grep -vE "$EXCLUDE" || true)

    if [ -n "$hits" ]; then
        echo "FOUND: $name in:"
        for f in $hits; do
            echo "   $f"
            grep -nE "$pattern" "$f" 2>/dev/null | head -5 | while read -r line; do
                echo "     $line"
            done
        done
        echo ""
        violations=$((violations + 1))
    fi
}

# ── Check 1: Direct INSERT INTO with deprecation exemption ──
# INSERTs inside functions marked DEPRECATED are kept for rollback safety.
# INSERTs into administrative tables (module registry, tenants, etc.) are ALLOWED.
ADMIN_TABLES='marketplace_modules|tenant_installed_modules|tenant_module_visibility|module_uploads|sensor_profiles|tenant_limits|tenants|calibration_periods|notification_config|activation_codes|api_keys|farmer_activations|farmers|tenant_invitations'
check_exempt_insert() {
    local name="$1"
    local pattern="$2"
    local hits

    hits=$(echo "$files" | xargs grep -lE "$pattern" 2>/dev/null | grep -vE "$EXCLUDE" || true)

    if [ -n "$hits" ]; then
        for f in $hits; do
            while IFS=: read -r linenum rest; do
                # For execute() patterns, verify SQL is actually an INSERT
                # (catches cur.execute("INSERT INTO ...") but not UPDATE/SELECT/DELETE)
                if [[ "$pattern" == *"execute("* ]]; then
                    if ! echo "$rest" | grep -qiE 'INSERT[[:space:]]+INTO'; then
                        next_line=$(sed -n "$((linenum + 1))p" "$f" 2>/dev/null)
                        if ! echo "$next_line" | grep -qiE 'INSERT[[:space:]]+INTO'; then
                            continue
                        fi
                    fi
                fi
                # Skip writes into admin tables (allowed)
                if echo "$rest" | grep -qiE "INTO[[:space:]]+($ADMIN_TABLES)([[:space:](]|$)"; then
                    continue
                fi
                ctx_before=$(sed -n "$((linenum - 8)),$((linenum - 1))p" "$f" 2>/dev/null)
                if echo "$ctx_before" | grep -qE '"""DEPRECATED|"""Deprecated|"""deprecated'; then
                    continue
                fi
                violations=$((violations + 1))
                echo "FOUND: $name in $f:$linenum"
                echo "     $rest"
            done < <(grep -nE "$pattern" "$f" 2>/dev/null)
        done
    fi
}

check_exempt_insert "Direct INSERT INTO" 'INSERT[[:space:]]+INTO'
check_exempt_insert "execute INSERT" '\.execute(\|\.executemany(\|session\.execute('

# ── Raw DB connections that ALSO have writes ──
raw_conns=$(echo "$files" | xargs grep -lE 'psycopg2\.connect|asyncpg\.create_pool' 2>/dev/null | grep -vE "$EXCLUDE" || true)
if [ -n "$raw_conns" ]; then
    for f in $raw_conns; do
        if grep -qE 'INSERT[[:space:]]+INTO|UPDATE[[:space:]]+' "$f" 2>/dev/null; then
            echo "FOUND: DB connection WITH writes in $f"
            violations=$((violations + 1))
        fi
    done
fi

# ── Check 2: verify=False (SSL bypass — BLOCKING) ──
check_pattern "verify=False" 'verify[[:space:]]*=[[:space:]]*False'

# ── Check 3: Deprecated ref<Type> relationship names (warning only) ──
ref_pattern="'ref[A-Z][a-zA-Z]*'"
ref_hits=$(echo "$files" | xargs grep -nE "$ref_pattern" 2>/dev/null | grep -vE "$EXCLUDE" || true)
if [ -n "$ref_hits" ]; then
    echo "WARNING: Deprecated 'ref<Type>' relationship patterns found (use 'has<Type>' instead):"
    echo "$ref_hits" | head -10
    echo ""
fi

# ── Check 4: Raw Orion-LD calls without canonical import (warning only) ──
orion_files=$(echo "$files" | xargs grep -lE 'ORION_URL|orion-ld-service|/ngsi-ld/v1' 2>/dev/null | grep -vE "$EXCLUDE" || true)
if [ -n "$orion_files" ]; then
    for f in $orion_files; do
        has_ngsi_headers=$(grep -cE 'ngsi_headers|OrionClient|SyncOrionClient|inject_fiware_headers' "$f" 2>/dev/null || true)
        if [ "$has_ngsi_headers" -eq 0 ]; then
            echo "WARNING: $f uses Orion-LD but doesn't import ngsi_headers/OrionClient"
        fi
    done
fi

if [ "$violations" -eq 0 ]; then
    echo "All checks passed. No direct DB write violations found."
    exit 0
else
    echo "Found $violations violation(s)."
    echo ""
    echo "Fix: Data must flow through Orion-LD. Use NGSI-LD entity operations,"
    echo "     then let subscriptions feed the database via notification handlers."
    echo "     See AGENTS.md for architecture rules."
    exit 1
fi
