# Nekazari Helm Chart

Umbrella chart for the Nekazari platform. Installs all core services:
PostgreSQL+TimescaleDB, MongoDB, Redis, MinIO, Keycloak, Orion-LD,
API Gateway, Entity Manager, and Frontend.

## Quick start

```bash
helm install nekazari ./charts/nekazari -n nekazari --create-namespace \
  --set postgresql.auth.password=<pw> \
  --set mongodb.auth.rootPassword=<pw> \
  --set redis.auth.password=<pw> \
  --set minio.auth.rootPassword=<pw> \
  --set keycloak.auth.adminPassword=<pw>
```

See [QUICKSTART.md](../../QUICKSTART.md) for a complete walkthrough.

## Image pinning policy

**Production deployments MUST NOT use mutable tags (`:latest`, `:main`).**

Use one of:
- **SHA256 digest** (preferred): `image: repo@sha256:abc...`
- **Git commit tag**: `image: repo:sha-<commit>`

This chart ships with pinned images by default. When upgrading, pin to a
specific version rather than using `:latest`.

**Why**: Mutable tags combined with rolling updates cause cross-pod 404s in
frontend apps with hashed assets (Vite/Rspack). Old and new pods serve
incompatible asset references. See incident 2026-05-26 for details.

## Custom images

Override `image.repository`, `image.tag`, and/or `image.digest` in your
values file:

```yaml
api-gateway:
  image:
    repository: ghcr.io/my-org/api-gateway
    tag: sha-abcd123
```

The template supports both `:tag` and `@sha256:digest` formats.
Set `tag: ""` when using `digest`.

## Upgrade

See [UPGRADE.md](UPGRADE.md) for version-to-version migration steps.

## Configuration

See [values.yaml](values.yaml) for all configurable parameters.
