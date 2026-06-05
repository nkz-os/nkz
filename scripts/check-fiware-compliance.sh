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
#   - config/timescaledb/migrations/ (schema migrations)
#   - docker/*.sql                   (seed data)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MODE="${1:-staged}"

EXCLUDE_FILES='notification_handler\.py|subscription_manager\.py|db_helper\.py|audit_logger\.py'
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
    echo "✅ No Python files to check."
    exit 0
fi

check_pattern() {
    local name="$1"
    local pattern="$2"
    local hits

    hits=$(echo "$files" | xargs grep -lE "$pattern" 2>/dev/null | grep -vE "$EXCLUDE" || true)

    if [ -n "$hits" ]; then
        echo "❌ FOUND: $name in:"
        echo "$hits" | while read -r f; do
            echo "   $f"
            grep -nE "$pattern" "$f" 2>/dev/null | head -5 | while read -r line; do
                echo "     $line"
            done
        done
        echo ""
        violations=$((violations + 1))
    fi
}

check_pattern "Direct INSERT INTO" "INSERT[[:space:]]+INTO"
check_pattern "execute(INSERT...)" "execute[[:space:]]*\([[:space:]]*['\"]INSERT"
check_pattern "Raw psycopg2.connect" "psycopg2\.connect[[:space:]]*\("
check_pattern "Raw asyncpg.create_pool" "asyncpg\.create_pool[[:space:]]*\("

if [ "$violations" -eq 0 ]; then
    echo "✅ All checks passed. No direct DB write violations found."
    exit 0
else
    echo "❌ Found $violations violation(s)."
    echo ""
    echo "Fix: Data must flow through Orion-LD. Use NGSI-LD entity operations,"
    echo "     then let subscriptions feed the database via notification handlers."
    echo "     See AGENTS.md for architecture rules."
    exit 1
fi
