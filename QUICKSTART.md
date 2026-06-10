# Developer Quickstart — publish your first module in 30 minutes

Single end-to-end flow: from zero to your own module loading inside the platform,
on your local machine. No Kubernetes, no cloud account, no prior Nekazari knowledge.

## 0. Prerequisites

- **Docker** 24+ with compose plugin (`docker compose version`)
- **Node.js** 22+, **pnpm** 10+ (`corepack enable && corepack prepare pnpm@latest --activate`)
- **mc** (MinIO Client) — [install guide](https://min.io/docs/minio/linux/reference/minio-mc.html)
- **psql** (PostgreSQL client) — `apt install postgresql-client` or your package manager
- **8 GB** free RAM recommended (CI builds with less; local needs headroom)

## 1. Boot the platform

```bash
git clone https://github.com/nkz-os/nkz.git && cd nkz
cp .env.example .env
docker compose up -d
# first build ~10 min (multi-stage frontend), subsequent starts ~30 s
```

Wait until all services show `healthy`:

```bash
docker compose ps
# Keycloak is the slowest (~60-90 s on first import)
```

Open `http://localhost:3000` and log in:

| User | Password | Role | Tenant |
|------|----------|------|--------|
| `demo@nekazari.local` | `Demo1234!` | Farmer | demo-farm |

> The platform ships empty — no modules pre-installed. That's intentional. You'll publish yours next.

## 2. Clone the template

```bash
git clone https://github.com/nkz-os/nkz-module-template.git my-first-module
cd my-first-module
```

Find-and-replace the placeholders. The minimum set:

| Placeholder | Replace with |
|-------------|-------------|
| `MODULE_NAME` | `my-first-module` |
| `MODULE_DISPLAY_NAME` | `My First Module` |
| `MODULE_ROUTE` | `/my-first-module` |

```bash
# Quick sed for the impatient (adjust to your OS):
sed -i 's/MODULE_NAME/my-first-module/g' src/Module.tsx package.json
sed -i 's/MODULE_DISPLAY_NAME/My First Module/g' src/Module.tsx
sed -i 's/MODULE_ROUTE/\/my-first-module/g' src/Module.tsx
```

Install dependencies:

```bash
pnpm install
```

> **Why pnpm?** The platform monorepo uses pnpm workspaces. Module Federation 2.0 singletons (react, react-dom, @nekazari/*) rely on consistent hoisting; pnpm enforces it.

## 3. Write your module (the 60-second version)

Open `src/App.tsx`. Replace the placeholder content:

```tsx
import { useAuth, useEntities, useI18n } from '@nekazari/module-kit';

export default function App() {
  const { t } = useI18n();
  const { user } = useAuth();
  const { entities } = useEntities({ type: 'AgriParcel', limit: 10 });

  return (
    <div className="p-6 max-w-2xl mx-auto">
      <h1 className="text-nkz-2xl font-semibold mb-4">{t('My First Module')}</h1>
      <p className="text-nkz-muted mb-6">
        Logged in as <strong>{user?.email}</strong> on tenant{' '}
        <strong>{user?.tenantId}</strong>.
      </p>
      <h2 className="text-nkz-lg font-medium mb-2">Parcels in your tenant</h2>
      <ul className="space-y-2">
        {entities.map((e: any) => (
          <li key={e.id} className="p-3 rounded-nkz bg-nkz-surface border border-nkz-border">
            {e.name?.value ?? e.id}
          </li>
        ))}
      </ul>
      {entities.length === 0 && (
        <p className="text-nkz-muted">No parcels yet. The demo tenant ships with two (Olite vineyard + olive grove).</p>
      )}
    </div>
  );
}
```

## 4. Build

```bash
pnpm run build:module
```

Check that `dist/` contains the Federation artifacts:

```bash
ls dist/
# remoteEntry.js   mf-manifest.json   manifest.json   mf-stats.json   assets/
```

## 5. Upload to MinIO (local dev)

> **For production:** skip to step 6. In production, push to `main` and GitHub Actions handles everything via OIDC publish. See `PLATFORM_CONVENTIONS.md` §11.

The platform's nginx proxies `/modules/` → local MinIO. Upload your `dist/` there:

```bash
# Point mc at your local MinIO
mc alias set local-minio http://localhost:9000 minioadmin minioadmin

# Upload the entire dist/ tree
mc cp -r dist/ local-minio/nekazari-frontend/modules/my-first-module/
```

Verify it's reachable through nginx:

```bash
curl -sS http://localhost:3000/modules/my-first-module/mf-manifest.json | head -5
# Should print a JSON object with "name", "exposes", "shared", etc.
```

## 6. Register in the database (local dev)

> **For production:** this is a one-time setup. The module must be registered in `marketplace_modules`. After that, every push to `main` deploys automatically — see `PLATFORM_CONVENTIONS.md` §11.

```bash
psql -h localhost -U postgres -d nekazari <<'SQL'
INSERT INTO marketplace_modules
  (id, name, display_name, description, remote_entry_url, scope, exposed_module, version, author, category)
VALUES
  ('my-first-module', 'my-first-module', 'My First Module',
   'My first Nekazari module',
   '/modules/my-first-module/mf-manifest.json',
   'my_first_module', './Module', '0.1.0', 'Me', 'dev');

INSERT INTO tenant_installed_modules (tenant_id, module_id, is_enabled)
VALUES ('demo-farm', 'my-first-module', true);
SQL
```

> `scope` replaces hyphens with underscores (`my_first_module`) — Module Federation requires valid JS identifiers. The host handles this automatically; you just need the column to match.

## 7. See it live

Reload `http://localhost:3000`.

- **Navigation**: "My First Module" appears in the sidebar under Modules.
- **Route**: Clicking it navigates to `/my-first-module`.
- **Parcels**: The page shows the two demo parcels from your tenant (they were seeded into Orion-LD on first start).

## What just happened

1. The host fetches `/api/modules/me` → sees `my-first-module` with `remote_entry_url` pointing to MinIO.
2. `ModuleContext` calls `registerRemotes([{ name: 'my_first_module', entry: '/modules/my-first-module/mf-manifest.json', type: 'module' }])`.
3. `RemoteModuleLoader` calls `loadRemote('my_first_module/Module')` → the Federation runtime fetches `mf-manifest.json`, resolves shared singletons (react, react-dom, react-router-dom, `@nekazari/*`), and executes your `remoteEntry.js`.
4. Your `defineModule({...})` result is extracted, the route is mounted, and any declared slots appear in the viewer.

## Next steps

- **Add a slot** — declare a `map-layer`, `context-panel`, or `dashboard-widget` entry in `src/slots/index.ts`. It renders inside the 3D viewer.
- **Add a backend** — the template includes a `backend/` skeleton (FastAPI). Build it with the Dockerfile, add a `k8s/` manifest, wire through api-gateway.
- **Customize the accent** — change the `accent` block in `Module.tsx` to match your brand.
- **Multi-language** — add `i18n` keys in `src/i18n/` (min `es` + `en`).
- **Publish to npm** — `@nekazari/module-builder` already emits provenance attestations. See `CONTRIBUTING.md`.

## Troubleshooting

### "Module doesn't appear in the sidebar"

Check the DB row:

```bash
psql -h localhost -U postgres -d nekazari -c \
  "SELECT id, remote_entry_url, is_active FROM marketplace_modules WHERE id = 'my-first-module';"
```

### "mf-manifest.json returns 404"

```bash
# Check MinIO directly
mc ls local-minio/nekazari-frontend/modules/my-first-module/

# Check nginx proxy
curl -v http://localhost:3000/modules/my-first-module/mf-manifest.json
```

### "Module Federation fails to load"

Open the browser DevTools → Network tab, filter for `mf-manifest`. A 200 means the host found it. Filter for `remoteEntry` — a 200 there means Federation resolved it. Check the Console for `loadRemote` errors.

### "TypeError: Failed to fetch dynamically imported module"

Your `scope` column in `marketplace_modules` uses the wrong sanitization. It must match what the host generates: `id.replace(/[^a-zA-Z0-9_]/g, '_')`. For `my-first-module` that's `my_first_module`.

## Cleanup

```bash
docker compose down -v   # removes all containers, networks, and volumes
```
