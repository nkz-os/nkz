# Nekazari Module Template (platform copy)

> This directory mirrors the canonical public template at
> **[nkz-os/nkz-module-template](https://github.com/nkz-os/nkz-module-template)**.
>
> External developers should clone that repo. This copy exists inside the `nkz`
> monorepo so platform contributors have a local reference.

See the canonical repo's [README](https://github.com/nkz-os/nkz-module-template/blob/main/README.md)
and [SETUP.md](https://github.com/nkz-os/nkz-module-template/blob/main/SETUP.md) for full documentation.

## Quick start (platform developers)

```bash
cp -r module-template/ ../my-module
cd ../my-module
bash scripts/init-module.sh   # interactive placeholder replacement
npm install
npm run dev
```

## Structure

```
module-template/
├── src/
│   ├── moduleEntry.ts          # export default defineModule({...}) — MF2 entry
│   ├── i18n.ts                 # i18next resource bundle registration
│   ├── locales/                # en/es filled in; ca/eu/fr/pt ship as {} skeletons
│   ├── slots/index.ts          # Slot component declarations
│   ├── components/slots/       # Slot React components (wrapped in <SlotShell>)
│   ├── services/api.ts         # API client template (VITE_API_URL base)
│   └── types/global.d.ts       # Host globals (window.__NKZ__, etc.)
├── backend/                    # FastAPI backend (optional)
│   └── app/
│       ├── middleware/         # Gateway-header auth (nkz_platform_sdk.auth) —
│       │                       # NO JWKS/JWT validation in the module.
│       └── api/internal.py     # /internal/* — X-Internal-Service-Secret only
├── k8s/
│   ├── backend-deployment.yaml
│   └── registration.sql
├── scripts/init-module.sh      # Interactive initializer
├── manifest.json
└── vite.config.ts              # Uses @nekazari/module-builder preset (MF2)
```

## Build

```bash
npm run build:module
# → dist/remoteEntry.js, dist/mf-manifest.json, dist/assets/*
#   (Module Federation 2.0 remote — upload the whole dist/ directory to MinIO)
```

See `SETUP.md` for full deployment steps.
