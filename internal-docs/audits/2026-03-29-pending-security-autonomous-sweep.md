# Autonomous sweep: PENDING.md consistency + repo security grep (2026-03-29)

Internal audit log. No production credentials or hostnames beyond what is already in public manifests.

## PENDING.md

- **E4 / PR#87:** Row claimed "PR#87 (asyncpg pool) pending" while **IOT-2** already states PR#87 merged. **Corrected** in `PENDING.md` to match IOT-2.
- **i18n / PR#105:** Already marked Done 2026-03-29; no change required.

## Security-oriented grep (`nkz/` repo)

| Check | Result |
|-------|--------|
| `verify=False` in `*.py` | **None** |
| Hardcoded credential assignments in `*.py` (heuristic sample) | **None** in quick scan |
| `imagePullSecrets` in `*.yaml` | **No** active usage (only comment in `agrienergy-deployment.yaml` stating public GHCR) |
| Host `localStorage` + token in `apps/host/src` | **No** matches (cookie auth pattern preserved) |

## CORS `Access-Control-Allow-Origin: *`

- **API-style services** (`agrienergy`, `intelligence-service`): comments explicitly require explicit origin whitelist in env.
- **`modules-server` nginx** (`k8s/core/frontend/modules-server-deployment.yaml`): uses `*` for **static GET** module bundles (IIFE / legacy MF). This is a common pattern for anonymous read of public JS; it is **not** the api-gateway auth surface. **Risk:** low for credential exfil via CORS on that path (GET-only, no cookies required for static assets). If product policy requires strict origin even for `/modules`, consider narrowing to `nekazari.robotika.cloud` and preview origins only.

## Recommendations (for later, human-supervised)

1. **Re-run** `rg` for secrets before releases (pre-commit already mentioned in CLAUDE.md).
2. **PAT / KC-PAT-1:** No automated verification; ops must apply ADR 003 when ready.
3. **DataHub DH-DEP-1:** Deployment state remains ops-verified, not repo-verified.

## Files touched by this sweep

- `PENDING.md` (workspace root): E4 row PR#87 wording.
- This file: new audit record.
