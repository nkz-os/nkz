# Viewer Opaque Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate glassmorphism from the unified viewer — opaque panels, rounded corners, no blur, light/dark parity.

**Architecture:** Change design-token profiles (`viewer`/`viewer-light`) to emit opaque surfaces and `glass: none`. Remove `backdrop-blur` from all host overlays. Apply `rounded-xl` consistently to panels. Modules with explicit overrides are untouched.

**Tech Stack:** TypeScript, Tailwind CSS, CSS custom properties, React

---

### Task 1: Design tokens — opaque surfaces + no glass

**Files:**
- Modify: `nkz/packages/design-tokens/src/tokens.config.ts:243-290`

- [ ] **Step 1: Make viewer profile fully opaque**

Replace the `viewer` profile colors (lines 243-268):

```typescript
  viewer: {
    colors: {
      canvas: 'transparent',
      surface: '#0F172A',
      surfaceRaised: '#1E293B',
      surfaceSunken: '#020617',
      border: 'rgba(148, 163, 184, 0.20)',
      borderStrong: 'rgba(148, 163, 184, 0.35)',
      textPrimary: '#F8FAFC',
      textSecondary: '#CBD5E1',
      textMuted: '#94A3B8',
      textOnAccent: '#FFFFFF',
      ...defaultAccent,
      ...semanticColors,
    },
    type: typeScaleViewer,
    radii,
    shadows: shadowsViewer,
    motion, zIndex, space,
    glass: 'none',
  },
```

Replace the `viewer-light` profile colors (lines 271-291):

```typescript
  'viewer-light': {
    colors: {
      canvas: 'transparent',
      surface: '#F8FAFC',
      surfaceRaised: '#FFFFFF',
      surfaceSunken: '#F1F5F9',
      border: 'rgba(15, 23, 42, 0.12)',
      borderStrong: 'rgba(15, 23, 42, 0.20)',
      textPrimary: '#0F172A',
      textSecondary: '#475569',
      textMuted: '#94A3B8',
      textOnAccent: '#FFFFFF',
      ...defaultAccent,
      ...semanticColors,
    },
    type: typeScaleViewer,
    radii,
    shadows: shadowsPage,
    motion, zIndex, space,
    glass: 'none',
  },
```

- [ ] **Step 2: Make `.nkz-glass` inert when glass is `none`**

In `nkz/packages/design-tokens/src/build-css.ts` lines 109-114, wrap the `.nkz-glass` rule so it emits no CSS when the viewer profile has `glass: 'none'`. The simplest approach: emit the rule unconditionally but with `revert` values when no profile uses glass. Actually, per spec: leave the `.nkz-glass` class defined, but with `none`. Simplest: add a media/selector guard is over-engineering. Alternative: leave as-is — the class still exists but the token profiles that used to define `glass` as a blur now define it as `none`. But `.nkz-glass` is a standalone class that always emits `backdrop-filter: blur(12px)`. It's not per-profile.

The fix: remove the standalone `.nkz-glass` class (which ignores profiles) and let each profile's `glass` property handle it. But profiles don't emit standalone CSS — they set CSS custom properties. The `.nkz-glass` class is the only mechanism that applies `backdrop-filter`.

Simplest correct fix: change `.nkz-glass` to read from a CSS custom property instead of hardcoding blur:

```typescript
  // Replace lines 109-114 with:
  parts.push('/* Glass effect — set via --nkz-glass custom property from profile */');
  parts.push('.nkz-glass {');
  parts.push('  backdrop-filter: var(--nkz-glass, none);');
  parts.push('  -webkit-backdrop-filter: var(--nkz-glass, none);');
  parts.push('  border: 1px solid var(--nkz-color-border);');
  parts.push('}');
```

And in `buildProfileCSS`, emit `--nkz-glass` from each profile's `glass` value. Find the profile CSS builder and add the property.

Read `build-css.ts` to find the `buildProfileCSS` function:

Run: `grep -n "buildProfileCSS\|--nkz-glass\|function buildProfile" /home/g/Documents/nekazari/nkz/packages/design-tokens/src/build-css.ts`

Then add inside the profile CSS builder:

```typescript
  parts.push(`  --nkz-glass: ${profile.glass};`);
```

- [ ] **Step 3: Rebuild design tokens and verify**

```bash
cd /home/g/Documents/nekazari/nkz/packages/design-tokens && pnpm run build
```

Expected: builds without error. Check `dist/tokens.css`:
- `[data-theme="viewer"]` block has `--nkz-color-surface: #0F172A` (no `rgba` transparency)
- `.nkz-glass` uses `var(--nkz-glass, none)` instead of hardcoded `blur(12px)`

- [ ] **Step 4: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add packages/design-tokens/src/tokens.config.ts packages/design-tokens/src/build-css.ts
git commit -m "fix(design-tokens): viewer profiles opaque, glass driven by CSS custom property"
```

---

### Task 2: SlotShell — rounded corners

**Files:**
- Modify: `nkz/packages/viewer-kit/src/viewer/SlotShell.tsx:96,162`

- [ ] **Step 1: Update SlotShell className**

Change line 96 from `rounded-nkz-lg` to `rounded-xl`:

```tsx
    <Panel variant="solid" className={clsx('rounded-xl shadow-nkz-md bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700', className)}>
```

- [ ] **Step 2: Update SlotShellCompact className**

Change line 162 from `rounded-nkz-lg` to `rounded-xl`:

```tsx
    <Panel variant="solid" className={clsx('rounded-xl shadow-nkz-md bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700', className)}>
```

- [ ] **Step 3: Build viewer-kit and verify**

```bash
cd /home/g/Documents/nekazari/nkz/packages/viewer-kit && pnpm run build
```

Expected: builds without TypeScript errors.

- [ ] **Step 4: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add packages/viewer-kit/src/viewer/SlotShell.tsx
git commit -m "fix(viewer-kit): SlotShell uses rounded-xl (12px) for panel corners"
```

---

### Task 3: SidebarShell — rounded-xl consistency

**Files:**
- Modify: `nkz/packages/viewer-kit/src/viewer/SidebarShell.tsx:117`

- [ ] **Step 1: Ensure SidebarShell uses rounded-xl**

Line 117 already has `rounded-nkz-lg` which is 12px. For consistency and clarity, use `rounded-xl`:

```tsx
      className={clsx(
        'relative flex flex-col h-full bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-700 z-nkz-rail shadow-nkz-lg rounded-xl pointer-events-auto',
        'transition-all duration-nkz-normal',
```

- [ ] **Step 2: Build viewer-kit and verify**

```bash
cd /home/g/Documents/nekazari/nkz/packages/viewer-kit && pnpm run build
```

- [ ] **Step 3: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add packages/viewer-kit/src/viewer/SidebarShell.tsx
git commit -m "fix(viewer-kit): SidebarShell uses rounded-xl consistently"
```

---

### Task 4: UnifiedViewer — bottom panel and floating elements rounded

**Files:**
- Modify: `nkz/apps/host/src/components/UnifiedViewer.tsx:604,611`

- [ ] **Step 1: Add rounded-xl to bottom panel toggle button**

Line 604 already has `rounded-t-lg`. Keep it — toggle buttons visually connect to the panel they open. No change needed.

- [ ] **Step 2: Ensure bottom panel container uses rounded-xl**

Line 611 already has `rounded-xl` — verified. No change needed.

- [ ] **Step 3: Ensure layer manager dropdown has rounded-xl**

Line 489 already has `rounded-xl` — verified. No change needed.

- [ ] **Step 4: Ensure location picker bar has rounded corners**

Line 409 uses `rounded-full` (pill shape) — correct for a notification bar. No change needed.

- [ ] **Step 5: Commit (no-op, all already correct)**

No changes needed — UnifiedViewer already uses `rounded-xl` and `rounded-full` appropriately.

---

### Task 5: CesiumMap — remove backdrop-blur, solid backgrounds

**Files:**
- Modify: `nkz/apps/host/src/components/CesiumMap.tsx:1635,1649,1660,1740`

- [ ] **Step 1: Remove backdrop-blur from map control buttons**

Line 1635 — zoom/home/compass buttons:
```
bg-slate-800/90 hover:bg-slate-700 text-white rounded-lg shadow-lg backdrop-blur-sm
```
→ replace with:
```
bg-slate-800 hover:bg-slate-700 text-white rounded-lg shadow-lg
```

Line 1649 — 3D terrain toggle:
```
p-2 rounded-lg shadow-lg backdrop-blur-sm transition-all border border-slate-600 ${enable3DTerrain ? 'bg-emerald-600/90 hover:bg-emerald-500 text-white' : 'bg-slate-800/90 hover:bg-slate-700 text-slate-300'}
```
→ replace with:
```
p-2 rounded-lg shadow-lg transition-all border border-slate-600 ${enable3DTerrain ? 'bg-emerald-600 hover:bg-emerald-500 text-white' : 'bg-slate-800 hover:bg-slate-700 text-slate-300'}
```

Line 1660 — drawing mode button:
```
bg-slate-800/90 hover:bg-slate-700 text-white rounded-lg shadow-lg backdrop-blur-sm
```
→ replace with:
```
bg-slate-800 hover:bg-slate-700 text-white rounded-lg shadow-lg
```

- [ ] **Step 2: Remove backdrop-blur from risk legend**

Line 1740:
```
bg-slate-900/85 backdrop-blur-sm rounded-lg
```
→ replace with:
```
bg-slate-900 rounded-lg
```

- [ ] **Step 3: Type-check host app**

```bash
cd /home/g/Documents/nekazari/nkz/apps/host && pnpm run typecheck
```

Expected: no errors (these are only className string changes, no type impact).

- [ ] **Step 4: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add apps/host/src/components/CesiumMap.tsx
git commit -m "fix(cesium): solid map controls, no backdrop-blur"
```

---

### Task 6: Floating overlays — remove backdrop-blur

**Files:**
- Modify: `nkz/apps/host/src/components/SensorInspector.tsx:272`
- Modify: `nkz/apps/host/src/components/EntityWizard/PlacementToolbar.tsx:89`
- Batch modify: all modal backdrops in host

- [ ] **Step 1: SensorInspector — remove blur**

Line 272:
```
bg-gray-900/95 backdrop-blur-md
```
→ replace with:
```
bg-gray-900
```

- [ ] **Step 2: PlacementToolbar — remove blur**

Line 89:
```
bg-white/95 backdrop-blur-md rounded-2xl shadow-2xl border border-gray-200/50
```
→ replace with:
```
bg-white rounded-2xl shadow-2xl border border-gray-200
```

- [ ] **Step 3: Modal backdrops — remove blur, keep backdrop opacity**

Replace `bg-black/50 backdrop-blur-sm` with `bg-black/60` in all modal files:
- `ParcelConfirmationModal.tsx:62`
- `AssetPropertiesDialog.tsx:74`
- `AssetRelationshipModal.tsx:190`
- `DeleteConfirmationModal.tsx:111`
- `EntityWizard/index.tsx:274`
- `EntityWizard/RobotCredentialsModal.tsx:58`
- `EntityWizard/MqttCredentialsModal.tsx:146`
- `ParcelCreationModal.tsx:37`

Replace `bg-black/60 backdrop-blur-sm` with `bg-black/60`:
- `ConnectivityPanel.tsx:350`
- `CustomRiskModal.tsx:140`
- `DeviceProfileHelpModal.tsx:86`

Replace `bg-black/50 backdrop-blur-sm` with `bg-black/60`:
- `ConnectivityPanel.tsx:450`

Replace `bg-white/80 backdrop-blur-sm` with `bg-white`:
- `AssetFiltersPanel.tsx:98`

Replace `bg-white bg-opacity-20 backdrop-blur-sm` with `bg-white/20`:
- `dashboard/MetricCard.tsx:36`

Replace `bg-white/20 rounded-lg backdrop-blur-md` with `bg-white/20 rounded-lg`:
- `DeviceProfileHelpModal.tsx:91`

- [ ] **Step 4: Type-check host app**

```bash
cd /home/g/Documents/nekazari/nkz/apps/host && pnpm run typecheck
```

- [ ] **Step 5: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add apps/host/src/components/SensorInspector.tsx \
        apps/host/src/components/EntityWizard/PlacementToolbar.tsx \
        apps/host/src/components/ParcelConfirmationModal.tsx \
        apps/host/src/components/AssetPropertiesDialog.tsx \
        apps/host/src/components/AssetManager/AssetRelationshipModal.tsx \
        apps/host/src/components/AssetManager/DeleteConfirmationModal.tsx \
        apps/host/src/components/AssetManager/AssetFiltersPanel.tsx \
        apps/host/src/components/EntityWizard/index.tsx \
        apps/host/src/components/EntityWizard/RobotCredentialsModal.tsx \
        apps/host/src/components/EntityWizard/MqttCredentialsModal.tsx \
        apps/host/src/components/parcels/ParcelCreationModal.tsx \
        apps/host/src/components/connectivity/ConnectivityPanel.tsx \
        apps/host/src/components/CustomRiskModal.tsx \
        apps/host/src/components/DeviceProfileHelpModal.tsx \
        apps/host/src/components/dashboard/MetricCard.tsx
git commit -m "fix(host): remove backdrop-blur from all floating overlays and modals"
```

---

### Task 7: Build, verify, and deploy

- [ ] **Step 1: Rebuild all affected packages**

```bash
cd /home/g/Documents/nekazari/nkz/packages/design-tokens && pnpm run build
cd /home/g/Documents/nekazari/nkz/packages/viewer-kit && pnpm run build
cd /home/g/Documents/nekazari/nkz/packages/ui-kit && pnpm run build
```

- [ ] **Step 2: Build host app**

```bash
cd /home/g/Documents/nekazari/nkz/apps/host && pnpm run build
```

Expected: all builds pass, no TS errors.

- [ ] **Step 3: Verify in browser (manual — dev server)**

```bash
cd /home/g/Documents/nekazari/nkz/apps/host && pnpm run dev
```

Open `/entities` in browser. Verify:
1. Left/right panels have 12px rounded corners, fully opaque slate-900 background
2. Toggle light/dark — panels switch to white/slate-50, still opaque
3. Map controls (zoom, compass) are solid slate-800, no blur
4. Open a module widget in right panel (VegetationLayerControl) — readable, no map bleed-through
5. Modal backdrops dim the background without blur

- [ ] **Step 4: Deploy host to production**

Push to main → CI builds `ghcr.io/nkz-os/nkz/host:latest`. On server:

```bash
sudo kubectl rollout restart deployment/frontend-host -n nekazari
sudo kubectl rollout status deployment/frontend-host -n nekazari --timeout=120s
```

- [ ] **Step 5: Commit remaining changes and push**

```bash
cd /home/g/Documents/nekazari/nkz
git push origin fix/ngsi-ld-core-compliance
```
