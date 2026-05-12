# Module System Fase A.1 — defineModule v2 + module-builder codegen

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `defineModule({...})` in `@nekazari/module-kit` the single declarative source for a module's identity, routing, slots, permissions, i18n, and data needs — and have `@nekazari/module-builder` derive `moduleEntry.gen.ts` and `manifest.json` from it automatically. Validated end-to-end by migrating `nkz-module-datahub` as the pilot.

**Architecture:** Extend the existing `defineModule()` (which today only validates `id` and returns options unchanged) with the full schema from the [design spec §3](../specs/2026-05-12-module-system-redesign-design.md). Add Zod-based validation. In `module-builder`, add a codegen pass that detects `src/Module.tsx` exporting `defineModule()`, then writes a generated entry to `node_modules/.nkz/moduleEntry.gen.ts` and a `manifest.json` to `dist/`. Keep the legacy `src/moduleEntry.ts` path working with a compat fallback so existing 17 modules continue to build unchanged.

**Tech Stack:** TypeScript 5, vitest 2.1, Zod 3, Vite 5, pnpm 10 workspace, React 18, lucide-react, `@nekazari/sdk` v1.1.0 types.

**Spec reference:** `nkz/internal-docs/specs/2026-05-12-module-system-redesign-design.md` (sections 3 and 9).

---

## File structure

### `@nekazari/module-kit` package

```
nkz/packages/module-kit/
├── package.json                ← bump to 0.2.0; add zod, vitest
├── tsconfig.json
├── vitest.config.ts            ← NEW: vitest config
├── src/
│   ├── index.ts                ← MODIFY: export new schema types
│   ├── types.ts                ← MODIFY: new ModuleDefinition type
│   ├── defineModule.ts         ← MODIFY: full schema + Zod validation
│   ├── schema.ts               ← NEW: Zod schemas (single source of truth)
│   ├── runtime/
│   │   └── events.ts           ← UNCHANGED
│   └── hooks/
│       ├── useAPI.ts           ← UNCHANGED
│       └── usePlatformEvents.ts ← UNCHANGED
└── tests/                      ← NEW: vitest tests
    ├── defineModule.test.ts    ← NEW
    └── schema.test.ts          ← NEW
```

### `@nekazari/module-builder` package

```
nkz/packages/module-builder/
├── package.json                ← bump to 1.1.0; add ts-morph (for codegen), vitest
├── tsconfig.json
├── vitest.config.ts            ← NEW
├── src/
│   ├── index.ts                ← MODIFY: export new helpers
│   ├── codegen.ts              ← NEW: generate moduleEntry.gen.ts + manifest.json
│   ├── codegen-plugin.ts       ← NEW: Vite plugin wrapping codegen
│   └── (existing nkzModulePreset stays)
└── tests/
    ├── codegen.test.ts         ← NEW
    └── fixtures/
        ├── module-with-define.tsx        ← NEW: test fixture
        └── module-legacy-entry.ts        ← NEW: test fixture
```

### Pilot migration (`nkz-module-datahub`)

```
nkz-module-datahub/
├── src/
│   ├── Module.tsx              ← NEW: defineModule({...}) for datahub
│   ├── moduleEntry.ts          ← DELETED (replaced by Module.tsx + codegen)
│   └── (everything else unchanged)
├── vite.config.ts              ← MODIFY: use new preset signature
├── manifest.json               ← DELETED (now auto-generated to dist/)
└── package.json                ← bump module-kit + module-builder deps
```

---

## Pre-flight checks

Before touching any code, verify the workspace is clean and dependencies are current.

- [ ] **Step 0.1: Verify clean workspace**

```bash
cd /home/g/Documents/nekazari/nkz
git status
```

Expected: `On branch main`, working tree clean (the `ngsi-ld-proposed-models.md` untracked file is acceptable).

- [ ] **Step 0.2: Pull latest main**

```bash
cd /home/g/Documents/nekazari/nkz
git pull origin main
```

Expected: `Already up to date.` or fast-forward.

- [ ] **Step 0.3: Create feature branch**

```bash
git checkout -b feat/module-kit-v0.2-define-module
```

- [ ] **Step 0.4: Install workspace deps**

```bash
pnpm install
```

Expected: `Lockfile is up to date` or minimal changes. No errors.

---

## Task 1: Add vitest infrastructure to module-kit

**Files:**
- Modify: `nkz/packages/module-kit/package.json`
- Create: `nkz/packages/module-kit/vitest.config.ts`

- [ ] **Step 1.1: Add vitest + zod to module-kit devDependencies**

Edit `nkz/packages/module-kit/package.json` to add:

```json
{
  "scripts": {
    "build": "tsup src/index.ts --format esm --dts --external react --external react-dom --external @nekazari/sdk --external @nekazari/ui-kit --external @nekazari/viewer-kit --external @nekazari/design-tokens --external @nekazari/module-builder",
    "prepublishOnly": "pnpm run build",
    "test": "vitest run",
    "test:watch": "vitest",
    "typecheck": "tsc --noEmit"
  },
  "dependencies": {
    "zod": "^3.23.0"
  },
  "devDependencies": {
    "@nekazari/module-builder": "workspace:*",
    "@nekazari/sdk": "workspace:*",
    "@types/react": "^18.3.0",
    "react": "^18.3.0",
    "tsup": "^8.0.0",
    "typescript": "^5.5.0",
    "vitest": "^2.1.8"
  }
}
```

Keep all other fields (`name`, `version`, `description`, `license`, `type`, `main`, `types`, `exports`, `files`, `publishConfig`, `peerDependencies`) untouched.

- [ ] **Step 1.2: Run install**

```bash
cd /home/g/Documents/nekazari/nkz
pnpm install
```

Expected: vitest and zod resolved without errors.

- [ ] **Step 1.3: Create vitest config**

Create `nkz/packages/module-kit/vitest.config.ts`:

```ts
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
  },
});
```

- [ ] **Step 1.4: Create empty tests directory + sanity test**

Create `nkz/packages/module-kit/tests/sanity.test.ts`:

```ts
import { describe, it, expect } from 'vitest';

describe('sanity', () => {
  it('vitest is wired up', () => {
    expect(1 + 1).toBe(2);
  });
});
```

- [ ] **Step 1.5: Run sanity test**

```bash
cd /home/g/Documents/nekazari/nkz/packages/module-kit
pnpm test
```

Expected: `1 passed`.

- [ ] **Step 1.6: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add packages/module-kit/package.json packages/module-kit/vitest.config.ts packages/module-kit/tests/sanity.test.ts pnpm-lock.yaml
git commit -m "chore(module-kit): add vitest + zod for v0.2 schema work"
```

---

## Task 2: Define the full `ModuleDefinition` type and Zod schema

**Files:**
- Create: `nkz/packages/module-kit/src/schema.ts`
- Test: `nkz/packages/module-kit/tests/schema.test.ts`

This is the single source of truth for what a module declaration looks like. All later code (defineModule, codegen, manifest generation) reads from here.

- [ ] **Step 2.1: Write the failing test for the schema shape**

Create `nkz/packages/module-kit/tests/schema.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { ModuleDefinitionSchema } from '../src/schema';

describe('ModuleDefinitionSchema', () => {
  const minimalValid = {
    id: 'soil-health',
    displayName: 'Soil Health',
    hostApiVersion: '^2.0.0',
    accent: { base: '#A16207', soft: '#FEF3C7', strong: '#713F12' },
  };

  it('accepts a minimal valid module', () => {
    const result = ModuleDefinitionSchema.safeParse(minimalValid);
    expect(result.success).toBe(true);
  });

  it('rejects an id that is not kebab-case', () => {
    const result = ModuleDefinitionSchema.safeParse({ ...minimalValid, id: 'SoilHealth' });
    expect(result.success).toBe(false);
  });

  it('rejects an id with underscores', () => {
    const result = ModuleDefinitionSchema.safeParse({ ...minimalValid, id: 'soil_health' });
    expect(result.success).toBe(false);
  });

  it('rejects accent without all three fields', () => {
    const result = ModuleDefinitionSchema.safeParse({
      ...minimalValid,
      accent: { base: '#A16207' },
    });
    expect(result.success).toBe(false);
  });

  it('rejects accent with non-hex colors', () => {
    const result = ModuleDefinitionSchema.safeParse({
      ...minimalValid,
      accent: { base: 'red', soft: '#FEF3C7', strong: '#713F12' },
    });
    expect(result.success).toBe(false);
  });

  it('accepts a full module with all optional fields', () => {
    const full = {
      ...minimalValid,
      version: '1.0.0',
      description: 'desc',
      icon: 'sprout',
      route: '/soil-health',
      navigation: {
        section: 'modules' as const,
        priority: 60,
        label: { es: 'Suelo', en: 'Soil' },
      },
      api: { basePath: '/api/soil-health' },
      requiredRoles: ['Farmer'],
      requiredPlan: 'basic' as const,
      data: { entities: ['AgriParcel'], timeseries: ['soil_observations'] },
    };
    const result = ModuleDefinitionSchema.safeParse(full);
    expect(result.success).toBe(true);
  });

  it('rejects route that does not start with /', () => {
    const result = ModuleDefinitionSchema.safeParse({
      ...minimalValid,
      route: 'soil-health',
    });
    expect(result.success).toBe(false);
  });

  it('rejects requiredPlan with an unknown tier', () => {
    const result = ModuleDefinitionSchema.safeParse({
      ...minimalValid,
      requiredPlan: 'gold',
    });
    expect(result.success).toBe(false);
  });

  it('rejects navigation.section with an unknown value', () => {
    const result = ModuleDefinitionSchema.safeParse({
      ...minimalValid,
      navigation: { section: 'random', priority: 10 },
    });
    expect(result.success).toBe(false);
  });

  it('rejects api.basePath that does not start with /api/', () => {
    const result = ModuleDefinitionSchema.safeParse({
      ...minimalValid,
      api: { basePath: 'soil-health' },
    });
    expect(result.success).toBe(false);
  });

  it('rejects hostApiVersion that is not a semver range', () => {
    const result = ModuleDefinitionSchema.safeParse({
      ...minimalValid,
      hostApiVersion: 'latest',
    });
    expect(result.success).toBe(false);
  });

  it("accepts data.entities containing '*' as wildcard", () => {
    const result = ModuleDefinitionSchema.safeParse({
      ...minimalValid,
      data: { entities: ['*'] },
    });
    expect(result.success).toBe(true);
  });
});
```

- [ ] **Step 2.2: Run the test (should fail with import error)**

```bash
cd /home/g/Documents/nekazari/nkz/packages/module-kit
pnpm test
```

Expected: FAIL with `Cannot find module '../src/schema'`.

- [ ] **Step 2.3: Implement the schema**

Create `nkz/packages/module-kit/src/schema.ts`:

```ts
import { z } from 'zod';

const HexColor = z.string().regex(/^#[0-9A-Fa-f]{6}$/, 'must be a 6-digit hex color (e.g. #A16207)');
const SemverRange = z.string().regex(/^[\^~]?\d+(\.\d+){0,2}(\.x)?$/, 'must be a semver range (e.g. ^2.0.0)');
const KebabCase = z.string().regex(/^[a-z][a-z0-9]*(-[a-z0-9]+)*$/, 'must be kebab-case starting with a letter');
const RoutePath = z.string().regex(/^\/[a-z0-9-/]*$/, 'must start with / and be lowercase');
const ApiBasePath = z.string().regex(/^\/api\/[a-z0-9-/]+$/, 'must start with /api/');
const Lang = z.string().regex(/^[a-z]{2}$/, 'must be a 2-letter language code');

const AccentSchema = z.object({
  base: HexColor,
  soft: HexColor,
  strong: HexColor,
});

const NavigationSchema = z.object({
  section: z.enum(['modules', 'admin', 'tools']),
  priority: z.number().int().nonnegative(),
  label: z.record(Lang, z.string()).optional(),
});

const SlotEntrySchema = z.object({
  id: KebabCase,
  // component is a runtime React component reference — Zod can't validate it; accepted as any function/object
  component: z.any(),
  priority: z.number().int().optional(),
  showWhen: z.any().optional(),
  defaultProps: z.record(z.string(), z.any()).optional(),
});

const SlotsSchema = z.record(z.string(), z.array(SlotEntrySchema));

const ApiSchema = z.object({
  basePath: ApiBasePath,
});

const DataSchema = z.object({
  entities: z.array(z.string()).optional(),
  timeseries: z.array(z.string()).optional(),
});

const I18nSchema = z.record(Lang, z.any()); // values are () => Promise<unknown> — Zod can't validate functions

export const ModuleDefinitionSchema = z.object({
  // Identity
  id: KebabCase,
  displayName: z.string().min(1),
  version: z.string().regex(/^\d+\.\d+\.\d+$/).optional(),
  hostApiVersion: SemverRange,
  description: z.string().optional(),

  // UI
  accent: AccentSchema,
  icon: z.string().optional(),
  main: z.any().optional(),

  // Host integration
  route: RoutePath.optional(),
  navigation: NavigationSchema.optional(),
  slots: SlotsSchema.optional(),

  // Backend
  api: ApiSchema.optional(),

  // Permissions
  requiredRoles: z.array(z.string()).optional(),
  requiredPlan: z.enum(['basic', 'pro', 'premium', 'enterprise']).optional(),

  // i18n
  i18n: I18nSchema.optional(),

  // Data dependencies
  data: DataSchema.optional(),
});

export type ModuleDefinition = z.infer<typeof ModuleDefinitionSchema>;
```

- [ ] **Step 2.4: Run the test (should pass)**

```bash
pnpm test
```

Expected: `13 passed` (the 12 schema tests plus the sanity test).

- [ ] **Step 2.5: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add packages/module-kit/src/schema.ts packages/module-kit/tests/schema.test.ts
git commit -m "feat(module-kit): add ModuleDefinitionSchema with Zod validation"
```

---

## Task 3: Rewrite `defineModule()` to use the schema and update types export

**Files:**
- Modify: `nkz/packages/module-kit/src/defineModule.ts`
- Modify: `nkz/packages/module-kit/src/types.ts`
- Modify: `nkz/packages/module-kit/src/index.ts`
- Test: `nkz/packages/module-kit/tests/defineModule.test.ts`

- [ ] **Step 3.1: Write failing tests for the new defineModule**

Create `nkz/packages/module-kit/tests/defineModule.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { defineModule } from '../src/defineModule';

describe('defineModule', () => {
  const minimal = {
    id: 'test-module',
    displayName: 'Test',
    hostApiVersion: '^2.0.0',
    accent: { base: '#A16207', soft: '#FEF3C7', strong: '#713F12' },
  };

  it('returns the options unchanged for a valid module', () => {
    const result = defineModule(minimal);
    expect(result.id).toBe('test-module');
    expect(result.displayName).toBe('Test');
  });

  it('throws with a clear message for invalid id', () => {
    expect(() => defineModule({ ...minimal, id: 'BadCase' })).toThrowError(/id.*kebab-case/i);
  });

  it('throws when accent is missing', () => {
    const { accent, ...withoutAccent } = minimal;
    expect(() => defineModule(withoutAccent as never)).toThrowError(/accent/i);
  });

  it('throws when displayName is empty', () => {
    expect(() => defineModule({ ...minimal, displayName: '' })).toThrowError(/displayName/);
  });

  it('preserves all optional fields when valid', () => {
    const full = {
      ...minimal,
      version: '1.0.0',
      route: '/test',
      api: { basePath: '/api/test' },
      requiredPlan: 'basic' as const,
      data: { entities: ['Test'] },
    };
    const result = defineModule(full);
    expect(result.version).toBe('1.0.0');
    expect(result.api?.basePath).toBe('/api/test');
    expect(result.requiredPlan).toBe('basic');
  });
});
```

- [ ] **Step 3.2: Run tests (should fail because defineModule still has old API)**

```bash
cd /home/g/Documents/nekazari/nkz/packages/module-kit
pnpm test tests/defineModule.test.ts
```

Expected: FAIL — either `id` regex error or `accent`-not-validated errors. The old code only validates `id` with a different regex.

- [ ] **Step 3.3: Replace `defineModule.ts` with the schema-backed implementation**

Replace the contents of `nkz/packages/module-kit/src/defineModule.ts` with:

```ts
import { ModuleDefinitionSchema, type ModuleDefinition } from './schema';

/**
 * Define a Nekazari module. Returns the validated configuration object.
 *
 * The returned object is consumed by:
 *   1. @nekazari/module-builder — to generate the IIFE entry and manifest.json
 *   2. The host runtime — to register routes, slots, navigation, permissions
 *   3. `nkz dev` — to wire mocks and HMR
 *
 * @example
 * export default defineModule({
 *   id: 'soil-health',
 *   displayName: 'Soil Health',
 *   hostApiVersion: '^2.0.0',
 *   accent: { base: '#A16207', soft: '#FEF3C7', strong: '#713F12' },
 *   route: '/soil-health',
 *   navigation: { section: 'modules', priority: 60 },
 *   api: { basePath: '/api/soil-health' },
 * });
 */
export function defineModule(options: ModuleDefinition): ModuleDefinition {
  const result = ModuleDefinitionSchema.safeParse(options);
  if (!result.success) {
    const issues = result.error.issues
      .map((i) => `  - ${i.path.join('.') || '<root>'}: ${i.message}`)
      .join('\n');
    throw new Error(`defineModule: invalid module definition\n${issues}`);
  }
  return result.data;
}

export type { ModuleDefinition };

/**
 * Convert a ModuleDefinition to the legacy NKZModuleRegistration shape that
 * `window.__NKZ__.register()` expects. Used by the generated moduleEntry.gen.ts.
 *
 * Note: this is internal — modules should NOT call this directly. The host
 * runtime invokes it via @nekazari/module-builder's codegen.
 */
export function toNKZRegistration(def: ModuleDefinition): {
  id: string;
  version: string;
  viewerSlots?: unknown;
  main?: unknown;
} {
  return {
    id: def.id,
    version: def.version ?? '0.0.0',
    viewerSlots: def.slots,
    main: def.main,
  };
}
```

- [ ] **Step 3.4: Update `types.ts` to re-export ModuleDefinition**

Replace the contents of `nkz/packages/module-kit/src/types.ts` with:

```ts
export type {
  SlotType,
  SlotWidgetDefinition,
  ModuleViewerSlots,
  NKZModuleRegistration,
  ModuleApiContract,
} from '@nekazari/sdk';

export type { ModuleDefinition } from './schema';

/** Accent color definition for a module's visual identity (re-exported from schema for backward compat) */
export interface ModuleAccent {
  base: string;
  soft: string;
  strong: string;
}

/** i18n resource bundles keyed by language code */
export type ModuleI18n = Record<string, () => Promise<Record<string, unknown>>>;
```

- [ ] **Step 3.5: Update `index.ts` to export the schema**

Replace the contents of `nkz/packages/module-kit/src/index.ts` with:

```ts
export { defineModule, toNKZRegistration } from './defineModule';
export { ModuleDefinitionSchema } from './schema';
export type { ModuleDefinition } from './schema';
export type { ModuleAccent, ModuleI18n } from './types';
export { initPlatformEvents, type PlatformEvents, type PlatformEvent } from './runtime/events';
export { usePlatformEvents, usePlatformEvent } from './hooks/usePlatformEvents';
export { useAPI } from './hooks/useAPI';
```

- [ ] **Step 3.6: Run all module-kit tests**

```bash
pnpm test
```

Expected: all 18 tests pass (sanity + 12 schema + 5 defineModule).

- [ ] **Step 3.7: Run typecheck**

```bash
pnpm run typecheck
```

Expected: no errors.

- [ ] **Step 3.8: Run build (verifies tsup still works)**

```bash
pnpm run build
```

Expected: `ESM ⚡️ Build success`, `DTS ⚡️ Build success`, output in `dist/`.

- [ ] **Step 3.9: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add packages/module-kit/src/defineModule.ts packages/module-kit/src/types.ts packages/module-kit/src/index.ts packages/module-kit/tests/defineModule.test.ts
git commit -m "feat(module-kit): defineModule v2 with full schema and Zod validation"
```

---

## Task 4: Bump `module-kit` version to 0.2.0 (no publish yet)

We'll publish at the end of the plan after pilot validation.

- [ ] **Step 4.1: Bump version in package.json**

Edit `nkz/packages/module-kit/package.json` and change `"version": "0.1.1"` to `"version": "0.2.0"`.

- [ ] **Step 4.2: Commit the bump**

```bash
cd /home/g/Documents/nekazari/nkz
git add packages/module-kit/package.json
git commit -m "chore(module-kit): bump to 0.2.0"
```

---

## Task 5: Add vitest infrastructure to module-builder

**Files:**
- Modify: `nkz/packages/module-builder/package.json`
- Create: `nkz/packages/module-builder/vitest.config.ts`
- Create: `nkz/packages/module-builder/tests/sanity.test.ts`

- [ ] **Step 5.1: Add vitest to module-builder devDependencies**

Edit `nkz/packages/module-builder/package.json`. Replace its `devDependencies` and `scripts` blocks with:

```json
{
    "scripts": {
        "build": "tsc",
        "prepublishOnly": "pnpm run build",
        "test": "vitest run",
        "test:watch": "vitest",
        "typecheck": "tsc --noEmit"
    },
    "devDependencies": {
        "vite": "^5.4.0",
        "typescript": "^5.5.0",
        "vitest": "^2.1.8",
        "ts-morph": "^23.0.0"
    }
}
```

Keep all other fields untouched.

- [ ] **Step 5.2: Run install**

```bash
cd /home/g/Documents/nekazari/nkz
pnpm install
```

Expected: ts-morph and vitest resolve.

- [ ] **Step 5.3: Create vitest config**

Create `nkz/packages/module-builder/vitest.config.ts`:

```ts
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
  },
});
```

- [ ] **Step 5.4: Create sanity test**

Create `nkz/packages/module-builder/tests/sanity.test.ts`:

```ts
import { describe, it, expect } from 'vitest';

describe('sanity', () => {
  it('vitest is wired up', () => {
    expect(1 + 1).toBe(2);
  });
});
```

- [ ] **Step 5.5: Run sanity test**

```bash
cd /home/g/Documents/nekazari/nkz/packages/module-builder
pnpm test
```

Expected: `1 passed`.

- [ ] **Step 5.6: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add packages/module-builder/package.json packages/module-builder/vitest.config.ts packages/module-builder/tests/sanity.test.ts pnpm-lock.yaml
git commit -m "chore(module-builder): add vitest + ts-morph for codegen work"
```

---

## Task 6: Implement codegen — detect `Module.tsx` and emit `moduleEntry.gen.ts`

**Files:**
- Create: `nkz/packages/module-builder/src/codegen.ts`
- Create: `nkz/packages/module-builder/tests/codegen.test.ts`
- Create: `nkz/packages/module-builder/tests/fixtures/module-with-define.tsx`
- Create: `nkz/packages/module-builder/tests/fixtures/module-legacy-entry.ts`

The codegen reads the module source root and decides:
1. If `src/Module.tsx` exists → modern path. Generate `moduleEntry.gen.ts` that imports the default export, validates it via `defineModule`, and calls `window.__NKZ__.register()`.
2. If only `src/moduleEntry.ts` exists → legacy path. Return the existing entry path unchanged.

- [ ] **Step 6.1: Write the failing test for codegen detection**

Create `nkz/packages/module-builder/tests/codegen.test.ts`:

```ts
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mkdtempSync, rmSync, writeFileSync, mkdirSync, existsSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { detectEntryStrategy, generateModuleEntry } from '../src/codegen';

describe('detectEntryStrategy', () => {
  let projectRoot: string;

  beforeEach(() => {
    projectRoot = mkdtempSync(join(tmpdir(), 'nkz-codegen-'));
    mkdirSync(join(projectRoot, 'src'), { recursive: true });
  });

  afterEach(() => {
    rmSync(projectRoot, { recursive: true, force: true });
  });

  it('returns "modern" when src/Module.tsx exists', () => {
    writeFileSync(join(projectRoot, 'src/Module.tsx'), 'export default {}');
    expect(detectEntryStrategy(projectRoot)).toBe('modern');
  });

  it('returns "legacy" when only src/moduleEntry.ts exists', () => {
    writeFileSync(join(projectRoot, 'src/moduleEntry.ts'), '// legacy');
    expect(detectEntryStrategy(projectRoot)).toBe('legacy');
  });

  it('prefers "modern" if both exist (modern wins)', () => {
    writeFileSync(join(projectRoot, 'src/Module.tsx'), 'export default {}');
    writeFileSync(join(projectRoot, 'src/moduleEntry.ts'), '// legacy');
    expect(detectEntryStrategy(projectRoot)).toBe('modern');
  });

  it('throws if neither exists', () => {
    expect(() => detectEntryStrategy(projectRoot)).toThrow(/Module\.tsx.*moduleEntry/);
  });
});

describe('generateModuleEntry', () => {
  let projectRoot: string;

  beforeEach(() => {
    projectRoot = mkdtempSync(join(tmpdir(), 'nkz-codegen-'));
    mkdirSync(join(projectRoot, 'src'), { recursive: true });
    writeFileSync(join(projectRoot, 'src/Module.tsx'), 'export default {}');
  });

  afterEach(() => {
    rmSync(projectRoot, { recursive: true, force: true });
  });

  it('writes moduleEntry.gen.ts into node_modules/.nkz/', () => {
    const outPath = generateModuleEntry(projectRoot);
    expect(outPath).toBe(join(projectRoot, 'node_modules/.nkz/moduleEntry.gen.ts'));
    expect(existsSync(outPath)).toBe(true);
  });

  it('the generated file imports from src/Module', () => {
    const outPath = generateModuleEntry(projectRoot);
    const content = readFileSync(outPath, 'utf-8');
    expect(content).toMatch(/import\s+moduleConfig\s+from\s+['"].*\/src\/Module['"];?/);
  });

  it('the generated file calls window.__NKZ__.register', () => {
    const outPath = generateModuleEntry(projectRoot);
    const content = readFileSync(outPath, 'utf-8');
    expect(content).toMatch(/window\.__NKZ__\??\.register/);
  });

  it('the generated file has a banner comment', () => {
    const outPath = generateModuleEntry(projectRoot);
    const content = readFileSync(outPath, 'utf-8');
    expect(content).toMatch(/GENERATED.*do not edit/i);
  });
});
```

- [ ] **Step 6.2: Run test to verify it fails**

```bash
cd /home/g/Documents/nekazari/nkz/packages/module-builder
pnpm test tests/codegen.test.ts
```

Expected: FAIL with `Cannot find module '../src/codegen'`.

- [ ] **Step 6.3: Implement codegen**

Create `nkz/packages/module-builder/src/codegen.ts`:

```ts
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { join, relative, posix } from 'node:path';

export type EntryStrategy = 'modern' | 'legacy';

/**
 * Detect which entry strategy a module uses.
 *
 * - "modern": src/Module.tsx with `export default defineModule(...)`.
 *   The builder will codegen moduleEntry.gen.ts.
 * - "legacy": src/moduleEntry.ts written by hand.
 *   The builder uses it as-is.
 *
 * If both files exist, modern wins (the legacy file is ignored).
 */
export function detectEntryStrategy(projectRoot: string): EntryStrategy {
  const hasModern = existsSync(join(projectRoot, 'src/Module.tsx'));
  const hasLegacy = existsSync(join(projectRoot, 'src/moduleEntry.ts'));

  if (hasModern) return 'modern';
  if (hasLegacy) return 'legacy';

  throw new Error(
    `[@nekazari/module-builder] Neither src/Module.tsx (modern) nor src/moduleEntry.ts (legacy) found in ${projectRoot}. ` +
      `Create one of them. The modern form is preferred; see https://github.com/nkz-os/nkz/blob/main/internal-docs/specs/2026-05-12-module-system-redesign-design.md`,
  );
}

/**
 * Generate `node_modules/.nkz/moduleEntry.gen.ts` from `src/Module.tsx`.
 * Returns the absolute path of the generated file (used as Vite's input).
 */
export function generateModuleEntry(projectRoot: string): string {
  const outDir = join(projectRoot, 'node_modules/.nkz');
  const outFile = join(outDir, 'moduleEntry.gen.ts');

  mkdirSync(outDir, { recursive: true });

  // Resolve the import path relative to the output file
  const moduleAbs = join(projectRoot, 'src/Module');
  const relPath = posix.normalize(relative(outDir, moduleAbs)).replace(/\\/g, '/');
  const importSpec = relPath.startsWith('.') ? relPath : `./${relPath}`;

  const sourceRel = relative(projectRoot, join(projectRoot, 'src/Module.tsx'));

  // Built as concatenation to keep the inner template literal in the generated
  // file readable. The generated file uses its own backtick string for the
  // error message — we cannot nest backticks naively.
  const code =
    `/* eslint-disable */\n` +
    `// =============================================================================\n` +
    `// GENERATED by @nekazari/module-builder — do not edit.\n` +
    `// Source: ${sourceRel}\n` +
    `// Run \`nkz why moduleEntry.gen.ts\` to see how this file was derived.\n` +
    `// =============================================================================\n` +
    `import moduleConfig from '${importSpec}';\n` +
    `import { toNKZRegistration } from '@nekazari/module-kit';\n` +
    `\n` +
    `declare global {\n` +
    `  interface Window {\n` +
    `    __NKZ__?: { register: (registration: ReturnType<typeof toNKZRegistration>) => void };\n` +
    `  }\n` +
    `}\n` +
    `\n` +
    `if (typeof window !== 'undefined' && window.__NKZ__) {\n` +
    `  window.__NKZ__.register(toNKZRegistration(moduleConfig));\n` +
    `} else if (typeof window !== 'undefined') {\n` +
    "  console.error(`[${moduleConfig.id}] window.__NKZ__ not found. Is this bundle loaded inside an NKZ host?`);\n" +
    `}\n` +
    `\n` +
    `export default moduleConfig;\n`;

  writeFileSync(outFile, code, 'utf-8');
  return outFile;
}
```

- [ ] **Step 6.4: Run codegen tests (should pass)**

```bash
pnpm test
```

Expected: 9 passing (sanity + 4 detect + 4 generate).

- [ ] **Step 6.5: Inspect the generated file manually for sanity**

Create a quick fixture:

```bash
mkdir -p /tmp/nkz-codegen-smoke/src
echo "export default { id: 'smoke' };" > /tmp/nkz-codegen-smoke/src/Module.tsx
node -e "
const { generateModuleEntry } = require('./dist/codegen.js');
const path = generateModuleEntry('/tmp/nkz-codegen-smoke');
console.log('generated:', path);
console.log('---');
console.log(require('fs').readFileSync(path, 'utf-8'));
"
```

Expected: prints the generated file path and its content. The content has the banner, the `import moduleConfig from '...src/Module'` line, and `window.__NKZ__.register(toNKZRegistration(moduleConfig))`.

Cleanup:

```bash
rm -rf /tmp/nkz-codegen-smoke
```

- [ ] **Step 6.6: Run module-builder typecheck and build**

```bash
pnpm run typecheck
pnpm run build
```

Expected: both pass without errors.

- [ ] **Step 6.7: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add packages/module-builder/src/codegen.ts packages/module-builder/tests/codegen.test.ts
git commit -m "feat(module-builder): codegen for moduleEntry.gen.ts from src/Module.tsx"
```

---

## Task 7: Implement `manifest.json` generation

**Files:**
- Modify: `nkz/packages/module-builder/src/codegen.ts` (add `generateManifest`)
- Modify: `nkz/packages/module-builder/tests/codegen.test.ts` (add tests)

The manifest is read by the host when discovering modules. It contains only the **public** fields (no runtime references, no React components, no functions).

- [ ] **Step 7.1: Add failing tests for manifest generation**

Append to `nkz/packages/module-builder/tests/codegen.test.ts`:

```ts
import { generateManifest } from '../src/codegen';

describe('generateManifest', () => {
  const moduleConfig = {
    id: 'soil-health',
    displayName: 'Soil Health',
    version: '1.0.0',
    hostApiVersion: '^2.0.0',
    description: 'Soil module',
    accent: { base: '#A16207', soft: '#FEF3C7', strong: '#713F12' },
    icon: 'sprout',
    route: '/soil-health',
    navigation: { section: 'modules' as const, priority: 60 },
    api: { basePath: '/api/soil-health' },
    requiredRoles: ['Farmer'],
    requiredPlan: 'basic' as const,
    data: { entities: ['AgriParcel'] },
    // Non-serialisable fields (must be stripped):
    main: () => null,
    slots: { 'context-panel': [{ id: 'x', component: () => null }] },
    i18n: { es: () => Promise.resolve({}) },
  };

  it('writes a JSON file with public fields only', () => {
    const json = generateManifest(moduleConfig as never);
    expect(json.id).toBe('soil-health');
    expect(json.displayName).toBe('Soil Health');
    expect(json.version).toBe('1.0.0');
    expect(json.hostApiVersion).toBe('^2.0.0');
    expect(json.accent).toEqual({ base: '#A16207', soft: '#FEF3C7', strong: '#713F12' });
    expect(json.route).toBe('/soil-health');
    expect(json.api).toEqual({ basePath: '/api/soil-health' });
  });

  it('strips runtime-only fields (main, slot components, i18n loaders)', () => {
    const json = generateManifest(moduleConfig as never) as Record<string, unknown>;
    expect(json.main).toBeUndefined();
    expect(json.i18n).toBeUndefined();
    // slots are kept but components are stripped
    const slots = json.slots as Record<string, Array<{ id: string; component?: unknown }>>;
    expect(slots['context-panel'][0].id).toBe('x');
    expect(slots['context-panel'][0].component).toBeUndefined();
  });

  it('preserves slot metadata (id, priority) but not component fn', () => {
    const cfg = {
      ...moduleConfig,
      slots: {
        'bottom-panel': [
          { id: 'widget-a', component: () => null, priority: 10 },
          { id: 'widget-b', component: () => null, priority: 20 },
        ],
      },
    };
    const json = generateManifest(cfg as never) as Record<string, unknown>;
    const slots = json.slots as Record<string, Array<{ id: string; priority?: number; component?: unknown }>>;
    expect(slots['bottom-panel'][0]).toEqual({ id: 'widget-a', priority: 10 });
    expect(slots['bottom-panel'][1]).toEqual({ id: 'widget-b', priority: 20 });
  });

  it('includes only languages from i18n keys (not the loader functions)', () => {
    const json = generateManifest(moduleConfig as never) as Record<string, unknown>;
    expect(json.i18nLangs).toEqual(['es']);
  });
});
```

- [ ] **Step 7.2: Run tests (should fail with import error)**

```bash
pnpm test
```

Expected: FAIL with `generateManifest is not exported`.

- [ ] **Step 7.3: Implement `generateManifest`**

Append to `nkz/packages/module-builder/src/codegen.ts`:

```ts
import type { ModuleDefinition } from '@nekazari/module-kit';

/**
 * Strip runtime-only fields from a ModuleDefinition and return a plain JSON
 * object suitable for writing as `dist/manifest.json`.
 *
 * The manifest is what the host reads from MinIO when discovering a module —
 * it must be pure data (no functions, no React components).
 */
export function generateManifest(def: ModuleDefinition): Record<string, unknown> {
  const manifest: Record<string, unknown> = {
    id: def.id,
    displayName: def.displayName,
    version: def.version,
    hostApiVersion: def.hostApiVersion,
    description: def.description,
    accent: def.accent,
    icon: def.icon,
    route: def.route,
    navigation: def.navigation,
    api: def.api,
    requiredRoles: def.requiredRoles,
    requiredPlan: def.requiredPlan,
    data: def.data,
  };

  // Slots: keep id + priority + showWhen + defaultProps, drop component fn
  if (def.slots) {
    const slotsOut: Record<string, Array<Record<string, unknown>>> = {};
    for (const [slotType, entries] of Object.entries(def.slots)) {
      slotsOut[slotType] = (entries as Array<Record<string, unknown>>).map((entry) => {
        const stripped: Record<string, unknown> = { id: entry.id };
        if (entry.priority !== undefined) stripped.priority = entry.priority;
        if (entry.showWhen !== undefined) stripped.showWhen = entry.showWhen;
        if (entry.defaultProps !== undefined) stripped.defaultProps = entry.defaultProps;
        return stripped;
      });
    }
    manifest.slots = slotsOut;
  }

  // i18n: keep only the language codes (the loader functions are runtime-only)
  if (def.i18n) {
    manifest.i18nLangs = Object.keys(def.i18n);
  }

  // Strip keys whose value is undefined for a cleaner manifest
  for (const key of Object.keys(manifest)) {
    if (manifest[key] === undefined) delete manifest[key];
  }

  return manifest;
}
```

- [ ] **Step 7.4: Run tests**

```bash
pnpm test
```

Expected: 13 passing (sanity + 4 detect + 4 generate entry + 4 manifest).

- [ ] **Step 7.5: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add packages/module-builder/src/codegen.ts packages/module-builder/tests/codegen.test.ts
git commit -m "feat(module-builder): generateManifest strips runtime fields for dist/manifest.json"
```

---

## Task 8: Integrate codegen into `nkzModulePreset` (Vite plugin)

**Files:**
- Modify: `nkz/packages/module-builder/src/index.ts`

The preset must: (1) detect strategy, (2) for modern: generate moduleEntry.gen.ts before build and use it as the input, (3) emit manifest.json into `dist/` after the bundle is written, (4) for legacy: behave exactly as today.

- [ ] **Step 8.1: Read the current `nkzModulePreset` to understand what to extend**

```bash
cat /home/g/Documents/nekazari/nkz/packages/module-builder/src/index.ts | tail -100
```

Note: the function currently takes `moduleId` and `entry` as required options. We're making `moduleId` and `entry` optional when modern mode is detected (we read them from the module config).

- [ ] **Step 8.2: Modify `nkzModulePreset` to support modern mode**

Replace the contents of `nkz/packages/module-builder/src/index.ts` with:

```ts
// =============================================================================
// @nekazari/module-builder — Vite Preset for NKZ Module IIFE Bundles
// =============================================================================

import type { Plugin, UserConfig } from 'vite';
import { existsSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import react from '@vitejs/plugin-react';
import { detectEntryStrategy, generateModuleEntry, generateManifest } from './codegen';

export { detectEntryStrategy, generateModuleEntry, generateManifest } from './codegen';

const NKZ_EXTERNALS: Record<string, string> = {
  react: 'React',
  'react-dom': 'ReactDOM',
  'react-dom/client': 'ReactDOM',
  'react-router-dom': 'ReactRouterDOM',
  '@nekazari/sdk': '__NKZ_SDK__',
  '@nekazari/ui-kit': '__NKZ_UI__',
  '@nekazari/design-tokens': '__NKZ_THEME__',
  '@nekazari/viewer-kit': '__NKZ_VIEWER__',
};

export interface NKZModulePresetOptions {
  /** Module identifier. REQUIRED in legacy mode; ignored in modern mode (read from Module.tsx). */
  moduleId?: string;
  /** Entry point file. Default: 'src/moduleEntry.ts' (legacy) or auto-generated (modern). */
  entry?: string;
  /** Output filename. Default: 'nkz-module.js'. */
  outputFile?: string;
  /** Additional Vite config to merge. */
  viteConfig?: Partial<UserConfig>;
  /** Additional externals beyond the defaults. */
  additionalExternals?: Record<string, string>;
  /** Project root. Default: process.cwd(). */
  root?: string;
}

/**
 * Creates a Vite config for building a Nekazari module as an IIFE bundle.
 *
 * Modern mode (preferred): the project has `src/Module.tsx` exporting
 *   `export default defineModule({...})`. The preset auto-generates
 *   `node_modules/.nkz/moduleEntry.gen.ts` and `dist/manifest.json`.
 *
 * Legacy mode: the project has `src/moduleEntry.ts` written by hand. The
 *   preset uses it as the input and does not generate a manifest. This is
 *   the path existing modules use; new modules should use modern mode.
 *
 * @example modern mode
 * ```ts
 * // vite.config.ts
 * import { defineConfig } from 'vite';
 * import { nkzModulePreset } from '@nekazari/module-builder';
 * export default defineConfig(nkzModulePreset());
 * ```
 *
 * @example legacy mode (backward compat)
 * ```ts
 * export default defineConfig(nkzModulePreset({ moduleId: 'my-module' }));
 * ```
 */
export function nkzModulePreset(options: NKZModulePresetOptions = {}): UserConfig {
  const {
    outputFile = 'nkz-module.js',
    viteConfig = {},
    additionalExternals = {},
    root = process.cwd(),
  } = options;

  const strategy = detectEntryStrategy(root);

  let entry: string;
  let moduleId: string;
  let manifestPlugin: Plugin | null = null;

  if (strategy === 'modern') {
    entry = generateModuleEntry(root);

    // Eagerly read the module config to get the id (needed for filenames and manifest)
    // We use a dynamic import at the Vite plugin's buildStart phase, but for the static
    // config we read it via a lightweight regex check on src/Module.tsx.
    // For now: require the user to pass moduleId in modern mode if codegen needs to know it
    // at config time, OR read it from package.json#nkz.moduleId.
    moduleId = options.moduleId ?? readModuleIdFromPackage(root);

    // Vite plugin that writes manifest.json into dist/ after build
    manifestPlugin = {
      name: 'nkz-module-builder:manifest',
      apply: 'build',
      async closeBundle() {
        const { default: moduleConfig } = (await import(/* @vite-ignore */ join(root, 'src/Module.tsx'))) as { default: import('@nekazari/module-kit').ModuleDefinition };
        const manifest = generateManifest(moduleConfig);
        const outDir = resolve(root, viteConfig.build?.outDir ?? 'dist');
        writeFileSync(join(outDir, 'manifest.json'), JSON.stringify(manifest, null, 2), 'utf-8');
      },
    };
  } else {
    entry = options.entry ?? 'src/moduleEntry.ts';
    if (!options.moduleId) {
      throw new Error(`[@nekazari/module-builder] Legacy mode requires moduleId option in nkzModulePreset({ moduleId: '...' })`);
    }
    moduleId = options.moduleId;
  }

  const externals = { ...NKZ_EXTERNALS, ...additionalExternals };

  const config: UserConfig = {
    plugins: [react({ jsxRuntime: 'classic' }), ...(manifestPlugin ? [manifestPlugin] : [])],
    build: {
      lib: {
        entry: resolve(root, entry),
        formats: ['iife'],
        name: '__NKZ_MODULE__',
        fileName: () => outputFile,
      },
      rollupOptions: {
        external: Object.keys(externals),
        output: {
          globals: externals,
          extend: true,
        },
      },
      sourcemap: true,
      ...viteConfig.build,
    },
    ...viteConfig,
  };

  // Merge plugins if user passed any in viteConfig
  if (viteConfig.plugins) {
    config.plugins = [...(config.plugins ?? []), ...viteConfig.plugins];
  }

  return config;
}

function readModuleIdFromPackage(root: string): string {
  const pkgPath = join(root, 'package.json');
  if (!existsSync(pkgPath)) {
    throw new Error(`[@nekazari/module-builder] package.json not found at ${pkgPath}`);
  }
  const pkg = JSON.parse(require('node:fs').readFileSync(pkgPath, 'utf-8')) as {
    nkz?: { moduleId?: string };
    name?: string;
  };
  if (pkg.nkz?.moduleId) return pkg.nkz.moduleId;
  throw new Error(
    `[@nekazari/module-builder] Modern mode needs a moduleId. Add "nkz": { "moduleId": "your-id" } to package.json, or pass moduleId in nkzModulePreset({...}).`,
  );
}
```

- [ ] **Step 8.3: Run module-builder build and typecheck**

```bash
cd /home/g/Documents/nekazari/nkz/packages/module-builder
pnpm run build
pnpm run typecheck
```

Expected: both pass without errors.

- [ ] **Step 8.4: Run all module-builder tests**

```bash
pnpm test
```

Expected: 13 tests passing.

- [ ] **Step 8.5: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add packages/module-builder/src/index.ts
git commit -m "feat(module-builder): integrate codegen into nkzModulePreset with modern/legacy detection"
```

---

## Task 9: Bump `module-builder` version to 1.1.0

- [ ] **Step 9.1: Bump version**

Edit `nkz/packages/module-builder/package.json` and change `"version": "1.0.0"` to `"version": "1.1.0"`.

- [ ] **Step 9.2: Add `@nekazari/module-kit` as a peer dependency**

In `nkz/packages/module-builder/package.json`, update `peerDependencies` to:

```json
"peerDependencies": {
    "vite": ">=5.0.0",
    "@vitejs/plugin-react": ">=4.0.0",
    "@nekazari/module-kit": ">=0.2.0"
},
```

And add to `devDependencies` (for the workspace to resolve types):

```json
"@nekazari/module-kit": "workspace:*",
```

- [ ] **Step 9.3: Reinstall and rebuild**

```bash
cd /home/g/Documents/nekazari/nkz
pnpm install
cd packages/module-builder
pnpm run build
```

Expected: clean.

- [ ] **Step 9.4: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add packages/module-builder/package.json pnpm-lock.yaml
git commit -m "chore(module-builder): bump to 1.1.0 with module-kit peer dep"
```

---

## Task 10: Publish both packages to npm

We need real npm versions in the registry before the pilot (datahub) can consume them.

- [ ] **Step 10.1: Verify npm auth**

```bash
npm whoami
```

Expected: `gillen` (or the user's npm handle).

- [ ] **Step 10.2: Publish `@nekazari/module-kit@0.2.0`**

```bash
cd /home/g/Documents/nekazari/nkz/packages/module-kit
pnpm publish
```

Expected: `+ @nekazari/module-kit@0.2.0`. No 2FA prompt thanks to the granular token. `prepublishOnly` runs the build automatically.

- [ ] **Step 10.3: Verify in registry**

```bash
npm view @nekazari/module-kit@0.2.0 dependencies
```

Expected: shows `zod` in dependencies. No `workspace:*` strings.

- [ ] **Step 10.4: Publish `@nekazari/module-builder@1.1.0`**

```bash
cd /home/g/Documents/nekazari/nkz/packages/module-builder
pnpm publish
```

Expected: `+ @nekazari/module-builder@1.1.0`.

- [ ] **Step 10.5: Verify in registry**

```bash
npm view @nekazari/module-builder@1.1.0 peerDependencies
```

Expected: includes `@nekazari/module-kit: ">=0.2.0"`.

---

## Task 11: Pilot — migrate `nkz-module-datahub` to modern mode

This validates the whole chain end-to-end.

**Files:**
- Create: `nkz-module-datahub/src/Module.tsx`
- Delete: `nkz-module-datahub/src/moduleEntry.ts`
- Modify: `nkz-module-datahub/vite.config.ts`
- Delete: `nkz-module-datahub/manifest.json`
- Modify: `nkz-module-datahub/package.json`

- [ ] **Step 11.1: Create branch on datahub**

```bash
cd /home/g/Documents/nekazari/nkz-module-datahub
git checkout -b feat/migrate-to-define-module-v2
```

- [ ] **Step 11.2: Read the current datahub manifest to extract data**

```bash
cat manifest.json
```

Note the values for: id, display_name, route_path, required_roles, required_plan_type, navigation.section, navigation.priority, metadata.color, dependencies, permissions.

- [ ] **Step 11.3: Create `src/Module.tsx`**

Create `nkz-module-datahub/src/Module.tsx`:

```tsx
import { defineModule } from '@nekazari/module-kit';
import { lazy } from 'react';
import './i18n';
import { moduleSlots } from './slots';
import pkg from '../package.json';

const DataHubPage = lazy(() => import('./DataHubPage'));

export default defineModule({
  id: 'datahub',
  displayName: 'DataHub',
  version: pkg.version,
  hostApiVersion: '^2.0.0',
  description: 'High-performance analytical canvas to cross variables from any source, export ranges, and run predictive models via Intelligence',
  accent: { base: '#0EA5E9', soft: '#E0F2FE', strong: '#0369A1' },
  icon: 'line-chart',
  main: DataHubPage,
  route: '/datahub',
  navigation: {
    section: 'modules',
    priority: 55,
  },
  api: { basePath: '/api/datahub' },
  requiredRoles: ['Farmer', 'TenantAdmin', 'PlatformAdmin'],
  requiredPlan: 'basic',
  slots: moduleSlots as never,
});
```

- [ ] **Step 11.4: Delete the old moduleEntry.ts**

```bash
rm src/moduleEntry.ts
```

- [ ] **Step 11.5: Update `vite.config.ts`**

Replace contents of `vite.config.ts` with:

```ts
import { defineConfig } from 'vite';
import { nkzModulePreset } from '@nekazari/module-builder';
import path from 'path';

export default defineConfig(
  nkzModulePreset({
    viteConfig: {
      resolve: {
        alias: { '@': path.resolve(__dirname, './src') },
      },
      server: {
        port: 5004,
        proxy: {
          '/api': {
            target: process.env.VITE_PROXY_TARGET || 'http://localhost:8000',
            changeOrigin: true,
            secure: process.env.VITE_PROXY_TARGET?.startsWith('https') ?? false,
          },
        },
      },
    },
  }),
);
```

- [ ] **Step 11.6: Add nkz.moduleId to package.json**

Edit `package.json` and add at the top level:

```json
"nkz": {
  "moduleId": "datahub"
},
```

Also bump the `@nekazari/module-kit` dep to `^0.2.0` and `@nekazari/module-builder` to `^1.1.0`:

```json
"devDependencies": {
    ...
    "@nekazari/module-builder": "^1.1.0",
    "@nekazari/module-kit": "^0.2.0",
    ...
}
```

- [ ] **Step 11.7: Delete the hand-written manifest.json**

```bash
rm manifest.json
```

(The new manifest is auto-emitted into `dist/manifest.json` at build time.)

- [ ] **Step 11.8: Reinstall**

```bash
pnpm install
```

Expected: resolves module-kit 0.2.0 and module-builder 1.1.0.

- [ ] **Step 11.9: Run typecheck**

```bash
pnpm run typecheck
```

Expected: passes (the only TS change is the new Module.tsx file).

- [ ] **Step 11.10: Run build**

```bash
pnpm run build:module
```

Expected: `vite build` succeeds. Output includes both `dist/nkz-module.js` and `dist/manifest.json` (the latter is new — generated by our plugin).

- [ ] **Step 11.11: Inspect the generated manifest**

```bash
cat dist/manifest.json
```

Expected: JSON with `id: 'datahub'`, `displayName: 'DataHub'`, `route: '/datahub'`, `api.basePath: '/api/datahub'`, `requiredRoles: ['Farmer', 'TenantAdmin', 'PlatformAdmin']`, etc. **No functions, no React components.**

- [ ] **Step 11.12: Inspect the generated moduleEntry to verify it imports Module.tsx**

```bash
cat node_modules/.nkz/moduleEntry.gen.ts
```

Expected: has banner comment, imports `moduleConfig` from `../../src/Module`, calls `window.__NKZ__.register(toNKZRegistration(moduleConfig))`.

- [ ] **Step 11.13: Commit the pilot migration**

```bash
git add src/Module.tsx vite.config.ts package.json pnpm-lock.yaml
git rm src/moduleEntry.ts manifest.json
git commit -m "feat(datahub): migrate to defineModule v2 + module-builder codegen

Pilot for the module-kit v0.2 redesign:
- Replace src/moduleEntry.ts with src/Module.tsx exporting defineModule()
- Delete hand-written manifest.json (now auto-generated into dist/)
- Use nkzModulePreset() without explicit moduleId (read from package.json#nkz.moduleId)

Validates the codegen chain end-to-end before the rest of the modules migrate."
```

- [ ] **Step 11.14: Push branch and open PR**

```bash
git push -u origin feat/migrate-to-define-module-v2
gh pr create --title "feat(datahub): migrate to defineModule v2" --body "$(cat <<'EOF'
## Summary

Pilot migration of datahub to the new `defineModule()` v2 + `module-builder` codegen path, as defined in [the redesign spec](https://github.com/nkz-os/nkz/blob/main/internal-docs/specs/2026-05-12-module-system-redesign-design.md).

## Changes

- `src/Module.tsx` (new) — single declarative source: `export default defineModule({...})`
- `src/moduleEntry.ts` (deleted) — generated automatically into `node_modules/.nkz/moduleEntry.gen.ts`
- `manifest.json` (deleted) — auto-generated into `dist/manifest.json` at build time
- `vite.config.ts` — uses `nkzModulePreset()` without explicit moduleId
- `package.json` — bumps deps to module-kit@^0.2.0, module-builder@^1.1.0; adds `nkz.moduleId`

## Test plan

- [ ] CI green (typecheck, build IIFE)
- [ ] `dist/manifest.json` contains the public fields only — no functions, no React components
- [ ] `dist/nkz-module.js` is byte-similar in size to the previous build (no major regressions)
- [ ] Smoke test in production after merge: datahub still loads, routes work, slots render
EOF
)"
```

- [ ] **Step 11.15: Verify CI passes**

```bash
gh run watch --exit-status
```

Expected: typecheck + IIFE build green.

- [ ] **Step 11.16: Merge the pilot PR**

```bash
gh pr merge --squash --delete-branch
```

Expected: merged. CI runs on main → uploads new IIFE to MinIO automatically.

---

## Task 12: Update the spec to mark Fase A.1 done and open the PR on nkz

- [ ] **Step 12.1: Return to the nkz repo**

```bash
cd /home/g/Documents/nekazari/nkz
git checkout feat/module-kit-v0.2-define-module
```

- [ ] **Step 12.2: Push the branch**

```bash
git push -u origin feat/module-kit-v0.2-define-module
```

- [ ] **Step 12.3: Open PR**

```bash
gh pr create --title "feat(module-kit,module-builder): defineModule v2 + codegen (Fase A.1)" --body "$(cat <<'EOF'
## Summary

Implements Fase A.1 of the [module system redesign](../internal-docs/specs/2026-05-12-module-system-redesign-design.md).

After this:
- `defineModule({...})` validates the full schema (id, displayName, version, hostApiVersion, accent, route, navigation, slots, api, requiredRoles, requiredPlan, i18n, data) via Zod
- `@nekazari/module-builder@1.1.0` codegen generates `moduleEntry.gen.ts` and `manifest.json` from `src/Module.tsx`
- Backward compatibility maintained: existing modules with `src/moduleEntry.ts` keep building unchanged
- Pilot: datahub migrated to validate end-to-end (PR link in the merge comment)

## What's NOT in this PR (deferred to Fase A.2)

- New hooks (`useAuth`, `useOrion`, `useTimeseries`, `useFiles`, `useI18n`, `useModuleAPI`)
- Mock layer for `nkz dev`
- CLI: `nkz init`, `nkz dev` improvements
- Gateway changes (X-Module-Id, presigned URLs, event-bus namespacing)
- Template oficial rewrite
- CLAUDE.md update

## Published versions

- `@nekazari/module-kit@0.2.0` — already on npm
- `@nekazari/module-builder@1.1.0` — already on npm

## Test plan

- [ ] CI green (typecheck, tests, build)
- [ ] Datahub pilot PR merges and CI green
- [ ] No regression in any of the 17 modules still on legacy mode (they should continue to build via the compat path)
EOF
)"
```

- [ ] **Step 12.4: Wait for CI**

```bash
gh pr checks --watch
```

Expected: all green.

- [ ] **Step 12.5: Merge**

```bash
gh pr merge --squash --delete-branch
```

---

## Task 13: Post-merge smoke checks

- [ ] **Step 13.1: Pull main on nkz**

```bash
cd /home/g/Documents/nekazari/nkz
git checkout main && git pull origin main
```

- [ ] **Step 13.2: Verify datahub bundle in MinIO**

```bash
ssh g@109.123.252.120 -- 'sudo /usr/local/bin/mc ls minio/nekazari-frontend/modules/datahub/ 2>&1' | tail -5
```

Expected: shows `nkz-module.js` with a recent timestamp (post-merge of the datahub PR).

- [ ] **Step 13.3: Manually verify datahub in production**

Open `https://nekazari.robotika.cloud/datahub` in a logged-in browser session. Expected: the DataHub page renders. Check the browser console for any new errors that weren't there before.

If any console error related to module registration appears, **stop** and roll back the datahub PR. Document the issue and re-plan.

- [ ] **Step 13.4: Verify at least one of the 17 untouched modules still builds**

Pick `nkz-module-soil` (or any module not yet migrated):

```bash
cd /home/g/Documents/nekazari/nkz-module-soil
pnpm install
pnpm run typecheck
pnpm run build:module
```

Expected: clean build (legacy mode still works because `src/moduleEntry.ts` exists in this module).

- [ ] **Step 13.5: Update the memory file with completion**

Append a line to `~/.claude/projects/-home-g-Documents-nekazari/memory/module-system-audit-2026-05-12.md`:

```
**Fase A.1 complete (2026-05-12)**: module-kit@0.2.0 published with full Zod schema. module-builder@1.1.0 published with codegen. Datahub migrated as pilot. 17 modules remain on legacy mode (compat path active).
```

---

## Self-review checklist

After implementing all tasks, verify before declaring done:

- [ ] **Spec coverage**: defineModule schema (§3), codegen for moduleEntry + manifest (§3, §9), backward compatibility via module-builder (§9). All covered.
- [ ] **No placeholders**: every step has either a command, code, or a specific check.
- [ ] **Type consistency**: `ModuleDefinition` exported from schema.ts is imported by codegen.ts, types.ts, and index.ts. `defineModule` and `toNKZRegistration` use the same `ModuleDefinition` type. Verified.
- [ ] **Frequent commits**: 13 separate commits (one per task).
- [ ] **Tests precede implementation**: each task has the test step before the implementation step. TDD respected.
- [ ] **Reversibility**: at any point, the work can be rolled back by reverting the relevant commit without breaking other modules.

---

## What's next (Fase A.2)

After A.1 is merged and validated, the follow-up plan implements:

1. Frontend hooks: `useAuth`, `useOrion`, `useTimeseries`, `useFiles`, `useI18n`, `useModuleAPI`
2. Mock layer for `nkz dev`
3. CLI commands: `nkz init`, improved `nkz dev`, `nkz build`, `nkz publish`, `nkz why`
4. Gateway changes: `X-Module-Id` header injection, `/api/storage/presigned-url`, event-bus namespacing
5. Template oficial (`nkz-module-template`) rewritten with the new pattern
6. CLAUDE.md updated to reflect the canonical pattern

Estimated 1-1.5 weeks. Plan to be written after A.1 merges.
