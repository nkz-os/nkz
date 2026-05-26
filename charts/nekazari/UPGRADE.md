# Upgrade Guide

## v1.0.0 → v1.1.0

### Breaking change: image tags pinned by SHA

v1.1.0 pins all Nekazari container images by SHA256 digest or git commit tag
instead of `:latest`. This prevents accidental rollouts and cross-pod asset
mismatches during rolling updates.

**Before (v1.0.0):**
```yaml
api-gateway:
  image:
    tag: latest
```

**After (v1.1.0):**
```yaml
api-gateway:
  image:
    tag: sha-548550f6e5c4475ce1bb659191cc713d898f5ce1
```

### Upgrade steps

1. Pull the updated chart:
   ```bash
   git pull origin main
   cd charts/nekazari
   ```

2. Review the new default values. If you have a custom values file, compare it
   against `values.yaml` — the image sections now include `digest` or pinned
   `tag` fields.

3. Dry-run the upgrade:
   ```bash
   helm upgrade nekazari ./charts/nekazari -n nekazari \
     --values my-values.yaml --dry-run
   ```

4. Apply the upgrade:
   ```bash
   helm upgrade nekazari ./charts/nekazari -n nekazari \
     --values my-values.yaml
   ```

5. Verify all pods are running with pinned images:
   ```bash
   kubectl -n nekazari get pods -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.containers[0].image}{"\n"}{end}'
   ```

### Custom images

If you build your own images, update the `image.repository` and `image.tag` (or
`image.digest`) fields in your values override file. The template supports both
formats:

```yaml
# Git commit tag format:
image:
  repository: ghcr.io/my-org/nkz/api-gateway
  tag: sha-abcd123
  pullPolicy: IfNotPresent

# SHA256 digest format (truly immutable):
image:
  repository: ghcr.io/my-org/nkz/api-gateway
  tag: ""
  digest: sha256:abcdef...
  pullPolicy: IfNotPresent
```

### Rollback

To rollback to the previous release:
```bash
helm rollback nekazari -n nekazari
```

Or to a specific revision:
```bash
helm rollback nekazari -n nekazari <revision>
```

**Important**: rolling back the Helm release does NOT revert the container
images if you manually updated them outside Helm. Use `kubectl set image` or
re-apply the v1.0.0 manifests if needed.
