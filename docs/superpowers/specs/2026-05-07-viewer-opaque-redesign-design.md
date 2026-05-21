# Viewer Opaque Redesign — Design Spec

**Date:** 2026-05-07
**Scope:** Unified Viewer (`/entities`) — eliminate glassmorphism, enforce opaque surfaces with rounded corners, light/dark parity.

## Problem

1. Viewer panels and module widgets inherit translucent surfaces from `viewer`/`viewer-light` design-token profiles (`bg-nkz-surface` → `rgba(15,23,42,0.78)`). Content over the Cesium map becomes illegible.
2. Panels are square-cornered (`rounded-nkz-lg` = 8px at most, often none), which reads as unfinished.
3. Previous global change broke modules that already had careful opaque styling, reverting them to a "basic, unpolished" look.

## Non-Goals

- Not touching the mobile app (`nkz-mobile`).
- Not redesigning module internals — modules with explicit Tailwind classes keep their look.
- Not changing the landing page or any route outside `/entities`.

## Design Principles

1. **Host owns the shell, tokens own the defaults.** SidebarShell and UnifiedViewer wrappers set explicit opaque classes. Design tokens set the fallback for modules that don't override.
2. **Explicit classes win.** Modules that already use `bg-white`, `bg-slate-50`, etc. are unaffected because Tailwind utility classes have higher specificity than CSS custom properties.
3. **Dark/light via `data-theme`.** The existing `useViewerTheme` toggle and `prefers-color-scheme` media query already switch profiles. We only change what values those profiles emit.

## Changes

### 1. Design Tokens — `viewer` and `viewer-light` profiles

**File:** `nkz/packages/design-tokens/src/tokens.config.ts`

| Property | Current | New |
|----------|---------|-----|
| `viewer.surface` | `rgba(15, 23, 42, 0.78)` | `#0F172A` (slate-900, fully opaque) |
| `viewer.surface-raised` | `rgba(30, 41, 59, 0.82)` | `#1E293B` (slate-800) |
| `viewer.surface-sunken` | `rgba(2, 6, 23, 0.90)` | `#020617` (slate-950) |
| `viewer.glass` | `backdrop-filter: blur(12px)...` | `none` |
| `viewer-light.surface` | `rgba(248, 250, 252, 0.85)` | `#F8FAFC` (slate-50) |
| `viewer-light.surface-raised` | `rgba(255, 255, 255, 0.90)` | `#FFFFFF` |
| `viewer-light.surface-sunken` | `rgba(241, 245, 249, 0.92)` | `#F1F5F9` (slate-100) |
| `viewer-light.glass` | `backdrop-filter: blur(12px)...` | `none` |

Add new token: `--nkz-radius-panel: 12px` (maps to `rounded-nkz-panel` in Tailwind).

**CSS generation** (`build-css.ts`): `.nkz-glass` class is left in place but emits nothing when `glass` is `none`. No breaking change — consumers that apply `.nkz-glass` simply get no effect.

### 2. SlotShell — use panel radius

**File:** `nkz/packages/viewer-kit/src/viewer/SlotShell.tsx`

- Change `rounded-nkz-lg` → `rounded-nkz-panel` (12px).
- No color/opacity changes — `Panel variant="solid"` already delegates to `bg-nkz-surface` which will now be opaque via the token change above.

### 3. SidebarShell — opaque + rounded

**File:** `nkz/packages/viewer-kit/src/viewer/SidebarShell.tsx`

- Outer container: change `bg-white dark:bg-slate-900` → keep (already opaque). Add `rounded-xl` (12px) to the panel container.
- Toggle button: already uses opaque classes; add `rounded-xl`.
- Inner edge resize handle: transparent (no change needed).

### 4. UnifiedViewer — bottom panel and floating elements

**File:** `nkz/apps/host/src/components/UnifiedViewer.tsx`

- Bottom panel container (`overlayPanel.base`): already `bg-white dark:bg-slate-900`. Add `rounded-xl`.
- Bottom panel toggle button: add `rounded-xl`.
- Layer manager dropdown: already uses `overlayPanel.base`, inherits rounded-xl.
- Location picker bar: add `rounded-xl`.

### 5. CesiumMap — map controls

**File:** `nkz/apps/host/src/components/CesiumMap.tsx`

- Control buttons: replace `bg-slate-800/90 backdrop-blur-sm` with `bg-slate-800` (solid), keep `rounded-lg`.
- Risk legend: replace `bg-slate-900/85 backdrop-blur-sm` with `bg-slate-900` (solid), keep `rounded-lg`.

### 6. Floating overlays (host-wide)

**Files:** `SensorInspector.tsx`, `PlacementToolbar.tsx`, modal backdrops

- `SensorInspector`: `bg-gray-900/95 backdrop-blur-md` → `bg-gray-900`.
- `PlacementToolbar`: `bg-white/95 backdrop-blur-md` → `bg-white`.
- Modal backdrops: `bg-black/50 backdrop-blur-sm` → `bg-black/60` (keep semi-transparency for overlay effect, but drop the blur which is the expensive/illegible part).

## What We DON'T Touch

- Any module repo (`vegetation-health-nkz`, `crop-health-nkz`, `cue-nkz`, etc.). Their explicit Tailwind classes (`bg-slate-50`, `bg-white`, `bg-nkz-surface`) either already work or will pick up the new opaque tokens if they use `bg-nkz-surface`.
- `nkz-glass` CSS class — stays defined, emits nothing.
- `page`, `field`, `hmi` token profiles — unchanged.
- Landing page, dashboard, entity wizard — out of scope.
- `nkz-mobile` — separate app.

## Light/Dark Behavior

- The `useViewerTheme` hook already toggles `data-theme` between `viewer` and `viewer-light`.
- `prefers-color-scheme` media query in `build-css.ts` already sets the initial profile.
- After this change, both profiles emit fully opaque surfaces. The toggle works identically — only the color values change between dark slate and light slate.
- Module widgets using `dark:` Tailwind variants continue working.

## Verification

1. Open `/entities` in browser, dark mode: left/right/bottom panels are solid slate-900, fully opaque over the Cesium map, with 12px rounded corners.
2. Toggle to light mode: panels switch to solid slate-50/white, same opacity and radius.
3. Open a module widget in the right panel (VegetationLayerControl): content is readable, no map bleeding through.
4. Hover over Cesium map: control buttons appear solid (no blur), fully legible.
5. Open a module that has its own explicit styling (crop-health, CUE): appearance unchanged from before.
6. `prefers-reduced-motion`: no backdrop-filter applied anywhere (already handled by existing media query).

## Rollback

- Restore tokens.config.ts values to previous translucent versions.
- Revert `rounded-xl` additions in SidebarShell and UnifiedViewer.
- All changes are in 4–5 files, no database or API changes.
