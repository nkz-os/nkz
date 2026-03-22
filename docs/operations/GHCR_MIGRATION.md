# GHCR migration: `k8-benetis/nkz` → `nkz-os/nkz`

## Purpose

When the GitHub repository moved from `github.com/k8-benetis/nkz` to `github.com/nkz-os/nkz`, **existing container images stayed** under the old GHCR path (`ghcr.io/k8-benetis/nkz/...`). They must be copied to the canonical namespace (`ghcr.io/nkz-os/nkz/...`) used by CI ([`.github/workflows/docker-build.yml`](../../.github/workflows/docker-build.yml)) and Kubernetes manifests.

## Prerequisites

1. **Tooling**: [`crane`](https://github.com/google/go-containerregistry/blob/main/cmd/crane/README.md) (recommended) or `docker` + `docker pull` / `tag` / `push`.
2. **Authentication**: `docker login ghcr.io` using a GitHub PAT with sufficient scope:
   - Read packages on the **source** org/user.
   - Write packages on the **destination** org (`nkz-os`).
3. **Optional — freeze CI**: During the copy window, disable or pause the workflow that pushes to GHCR on `main` to avoid tag races on shared tags like `latest`.

## Production-safe copy (digest verification)

Script: [`scripts/ghcr-migrate-nkz-core.sh`](../../scripts/ghcr-migrate-nkz-core.sh)

It copies each core service image and **fails** if `crane digest` differs between source and destination after `crane copy`.

```bash
# Example: authenticate once (same registry host for both namespaces)
echo "$GHCR_TOKEN" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin

cd /path/to/nkz
chmod +x scripts/ghcr-migrate-nkz-core.sh

# Default tags: latest main — override if needed
TAGS="latest main" ./scripts/ghcr-migrate-nkz-core.sh
```

Dry run:

```bash
DRY_RUN=1 ./scripts/ghcr-migrate-nkz-core.sh
```

### Tags to migrate

CI publishes multiple tags (see `docker/metadata-action` in the workflow). Migrate at least:

- Tags referenced by your **running** Kubernetes workloads (Deployments, StatefulSets, CronJobs), not only `latest`.
- For strict audits, resolve the **image digest** actually running in the cluster (`kubectl get pod -o jsonpath='...'`) and copy by digest or verify after copy.

## Inventory via GitHub (optional)

GHCR images for this monorepo are published under the **`k8-benetis` user namespace** (`ghcr.io/k8-benetis/nkz/<service>`), not necessarily as GitHub Organization packages. List them with:

```bash
# Authenticated user must have read:packages (e.g. classic PAT or `gh auth login -s read:packages`)
gh auth status   # if active account shows "Token scopes: none", switch: gh auth switch -u <account_with_scopes>

# Container packages owned by GitHub user k8-benetis (canonical for nkz/* images)
gh api "/users/k8-benetis/packages?package_type=container" --paginate

# Filter core monorepo images only (name prefix nkz/)
gh api "/users/k8-benetis/packages?package_type=container&per_page=100" --paginate \
  -q '.[] | select(.name | startswith("nkz/")) | .name'
```

If `/orgs/k8-benetis/packages` returns **404**, the org may not exist or packages may live under a **user** account — use `/users/<owner>/packages` instead.

Repo-scoped listing (`/repos/k8-benetis/nkz/packages`) can return **404** after a repo transfer (e.g. to `nkz-os/nkz`); prefer user/org package listing above.

## Visibility and Kubernetes pulls

- **Public images**: Nodes can pull without `imagePullSecrets`. Align with platform policy (no unnecessary pull secrets on public GHCR).
- **Private images**: Configure `imagePullSecrets` and a PAT or GitHub App with `packages:read` for the destination org.

## Other repositories / modules

This script covers **only** the monorepo core services listed in `docker-build.yml`. Other images (e.g. `nkz-module-*`, different GHCR paths) need separate `crane copy` lines or a dedicated list.

## Post-migration

1. Confirm `docker pull ghcr.io/nkz-os/nkz/<service>:<tag>` for critical services.
2. Search GitOps and server manifests for `ghcr.io/k8-benetis` and update or remove.
3. Keep source packages **read-only** for a retention window (e.g. 30 days) before deletion to allow rollback.

## Cosign / SBOM

If you attach signatures or SBOMs to images, verify whether your tooling requires copying additional artifacts; plain `crane copy` preserves the image manifest referenced by the tag.
