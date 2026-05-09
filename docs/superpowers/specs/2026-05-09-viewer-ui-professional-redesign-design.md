# Viewer UI Professional Redesign — Design Spec

**Date:** 2026-05-09
**Scope:** Whole-platform logo replacement + Unified Viewer (`/entities`) sidebar overhaul + unified theme system.

## Problem

1. Logo is the `🌾` emoji + "Nekazari" text hardcoded in 3 components. No vector branding.
2. Dropdown menu mock buttons ("Buscar parcela", "Help") are non-functional. Theme/language controls live outside the dropdown in the nav bar, creating a fragmented UX.
3. Two independent theme systems coexist: global `ThemeContext` (light/dark/system) and viewer `useViewerTheme` (viewer/viewer-light). They can desync.
4. Left sidebar entity names use dark text (`text-slate-800`) which is illegible against the transparent sidebar over the Cesium map.
5. Right sidebar should be fully opaque with correct dark/light text adaptation.
6. Toggle button is clipped by `overflow: hidden` on the sidebar container — only half the button is visible.
7. Previous spec `2026-05-07-viewer-opaque-redesign-design.md` attempted design-token-level opacity changes but was left incomplete.

## Non-Goals

- Not touching mobile app (`nkz-mobile`).
- Not redesigning module internals — modules keep their explicit styling.
- Not changing landing page, dashboard, entity wizard, or any route outside `/entities` (except logo replacement which is global).
- Not changing design tokens (the previous spec attempted that; this spec uses explicit Tailwind classes instead).

## Design Principles

1. **Single theme source of truth.** Global `ThemeContext` (light/dark/system) drives everything. `useViewerTheme` is retired — the viewer's `ThemeProvider` reads from `ThemeContext`.
2. **Host owns the shell.** SidebarShell, Navigation, ViewerHeader set explicit opaque/transparent classes. Modules inherit through design tokens.
3. **Left transparent, right opaque.** Left panel (entity list) uses glassmorphism to stay connected to the map. Right panel (details) is fully opaque for readability of dense information.
4. **Toggle button outside overflow.** The toggle button lives in a wrapper div without overflow clipping.
5. **GitOps traceability.** Every change goes through git → CI → ArgoCD. No manual `kubectl patch`.

## Changes

### 1. Logo replacement (3 files)

**SVG:** `nkz/images/nkz-os-logo.svg` — Inkscape SVG, 109×26mm, fill `#144c4b` (dark green).

**Strategy:** `<img src="/nkz-os-logo.svg" />` with CSS filter for dark mode.

| File | Line | Change |
|------|------|--------|
| `Navigation.tsx` | 153-155 | Replace `<span>🌾</span><span>Nekazari</span>` with `<img src="/nkz-os-logo.svg" className="h-7 w-auto dark:invert" alt="Nekazari" />` |
| `ViewerHeader.tsx` | 130-133 | Same replacement |
| `MobileDrawer.tsx` | 142-143 | Same replacement |

**Copy SVG to public:** `cp nkz/images/nkz-os-logo.svg nkz/apps/host/public/nkz-os-logo.svg`

### 2. Unified theme system — retire useViewerTheme

**File:** `nkz/apps/host/src/hooks/useViewerTheme.ts`

- Delete or deprecate. The viewer no longer has its own theme state.

**File:** `nkz/apps/host/src/context/ThemeContext.tsx`

- Export a `useViewerProfile()` hook that returns `'viewer'` when `resolvedTheme === 'dark'` and `'viewer-light'` when `resolvedTheme === 'light'`.
- The viewer uses this to set the `ThemeProvider` profile, eliminating the separate localStorage key.

**File:** `nkz/apps/host/src/components/UnifiedViewer.tsx`

- Replace `const { profile, setProfile } = useViewerTheme()` with `const { resolvedTheme } = useTheme()`.
- Map: `resolvedTheme === 'dark' ? 'viewer' : 'viewer-light'` for the `ThemeProvider`.

### 3. Dropdown menu — functional controls (Navigation.tsx)

**Current state:** ThemeToggle and LanguageSelector sit in the right section of the nav bar (lines 311-315), outside the dropdown.

**Change:** Move them into a fixed bottom row inside the dropdown, after the column layout.

New dropdown structure:
```
┌─ User info ──────────────────────────┐
├─ Principal ─┬─ Módulos ─┬─ Admin ────┤
│ ...         │ ...       │ ...        │
├─ Controls ───────────────────────────┘
│  [☀️/Tema]  [🌐/ES]  
└──────────────────────────────────────
```

- Remove `ThemeToggle variant="compact"` and `LanguageSelector variant="compact"` from the right section (lines 311-315).
- Add a controls row at the bottom of the dropdown div (before closing `</div>` of the dropdown).
- Use full-width variants of both controls inside the dropdown.

**Right section cleanup:** After removing theme/language, the right section only has user info + logout. The divider (line 309) is removed if only logout remains.

### 4. Dropdown menu — functional controls (ViewerHeader.tsx)

**Current state:** Has a right-hover submenu (lines 271-308) with 6 items — 2 are mock, others are a mix of functional and redundant.

**Change:** Delete the entire submenu div (lines 271-308). Add ThemeToggle and LanguageSelector in a controls row at the bottom of the main dropdown, identical to Navigation.tsx.

The theme toggle in ViewerHeader currently calls `useViewerTheme().toggle`. After theme unification (change #2), it calls `useTheme().toggleTheme` instead.

### 5. Left sidebar — transparent with white text

**File:** `nkz/packages/viewer-kit/src/viewer/SidebarShell.tsx`

The SidebarShell is shared between left and right panels. We need to differentiate them.

Add a `variant` prop: `'glass' | 'solid'` (default `'solid'`).
- `glass`: `bg-white/70 dark:bg-slate-900/60 backdrop-blur-md`
- `solid`: `bg-white dark:bg-slate-900` (current behavior)

**File:** `nkz/apps/host/src/components/UnifiedViewer.tsx`

- Pass `variant="glass"` to the left SidebarShell.
- Pass `variant="solid"` to the right SidebarShell.

**File:** `nkz/apps/host/src/components/AssetManager/AssetManagerGrid.tsx`

- Header (line 278): `bg-white dark:bg-slate-900` → `bg-transparent`
- Header border: keep `border-slate-200 dark:border-slate-700`
- Header title: `text-slate-800 dark:text-slate-100` → `text-white`
- Header count badge: `bg-slate-200 dark:bg-slate-700 text-slate-600 dark:text-slate-200` → `bg-white/10 text-white/80`
- Search input: `bg-white dark:bg-slate-800` → `bg-white/10` with `text-white placeholder:text-white/50`
- Table header (line 526): `bg-slate-50 dark:bg-slate-800` → `bg-white/5`, text → `text-white/70`
- Hover states: `hover:bg-slate-100` → `hover:bg-white/10`

**File:** `nkz/apps/host/src/components/AssetManager/AssetRow.tsx`

- Entity name (line 149): `text-slate-800` → `text-white`
- Type badge (line 166): `text-slate-600 bg-slate-100` → `text-white/70 bg-white/10`
- Status text: use white variants
- Location text (line 189): `text-slate-500` → `text-white/50`
- Hover row (line 118-121): `hover:bg-slate-50` → `hover:bg-white/10`
- Selected row: `bg-blue-50 hover:bg-blue-100` → `bg-white/15 hover:bg-white/20`

**File:** `nkz/apps/host/src/components/AssetManager/AssetCategoryNav.tsx`

- Same white-text treatment as the grid.

### 6. Right sidebar — opaque (already correct in code)

No code changes needed. The `SidebarShell` with default `variant="solid"` gives opaque white/dark background. `CoreContextPanel` already uses correct `dark:` text variants.

Verify that `CoreContextPanel`'s internal backgrounds (`bg-slate-50 dark:bg-slate-800`) look correct over the solid shell background.

### 7. Rounded borders

`SidebarShell` already has `rounded-xl`. Add `rounded-t-xl` to:
- `AssetManagerGrid` header (to match parent shell curvature)
- Any internal panel headers that sit flush with the shell top edge.

### 8. Toggle button — extract from overflow container

**File:** `nkz/packages/viewer-kit/src/viewer/SidebarShell.tsx`

Current structure (simplified):
```tsx
<div className="... overflow-hidden ...">   // overflow clips the button
  <button toggle className="right-0 translate-x-1/2" />  // positioned to stick out
  <div>content</div>
</div>
```

New structure:
```tsx
<div className="relative">                   // wrapper, no overflow
  <button toggle className="absolute ..." /> // free to extend beyond
  <div className="... overflow-hidden ...">  // overflow only on content
    <div>content</div>
  </div>
</div>
```

The toggle button gets `z-50` (above rail z-index of 30) and is fully visible regardless of sidebar state.

When sidebar is closed:
- Button renders at the edge of the screen (`left-2` or `right-2`), fully visible.
- Wrapper div has no width restriction.

When sidebar is open:
- Button overflows the content div edge by 50%, fully visible.
- Content div has the width and overflow hidden.

### 9. AssetManagerGrid category nav

**File:** `nkz/apps/host/src/components/AssetManager/AssetCategoryNav.tsx`

Same treatment: transparent background, white/light text, white hover states.

## Files Summary

| File | Change Type |
|------|-------------|
| `nkz/apps/host/public/nkz-os-logo.svg` | NEW (copy from images/) |
| `Navigation.tsx` | Logo + dropdown controls |
| `ViewerHeader.tsx` | Logo + remove mock submenu + dropdown controls |
| `MobileDrawer.tsx` | Logo |
| `ThemeContext.tsx` | Add `useViewerProfile()` hook |
| `useViewerTheme.ts` | Deprecate/remove |
| `UnifiedViewer.tsx` | Use global theme, pass variant to SidebarShell |
| `SidebarShell.tsx` (viewer-kit) | `variant` prop + toggle button extraction |
| `AssetManagerGrid.tsx` | White text, transparent backgrounds |
| `AssetRow.tsx` | White text, transparent hover states |
| `AssetCategoryNav.tsx` | White text, transparent backgrounds |

## What We DON'T Touch

- Any module repo — modules use `@nekazari/viewer-kit` as external, inherit changes automatically.
- `SlotShell.tsx` / `SlotShellCompact.tsx` — already opaque via `Panel variant="solid"`.
- `CoreContextPanel.tsx` — already correct for opaque right panel.
- Design tokens (`tokens.config.ts`) — we use explicit Tailwind classes, not token-level changes.
- `page`, `field`, `hmi` token profiles.
- Landing page, dashboard, settings, entity wizard.

## Light/Dark Behavior

| Element | Dark Mode | Light Mode |
|---------|-----------|------------|
| Logo SVG | `dark:invert` → white | Original `#144c4b` green |
| Nav bar | `bg-white dark:bg-slate-900` | Same |
| Dropdown | `bg-white dark:bg-slate-900` | Same |
| Left sidebar | `bg-white/70 dark:bg-slate-900/60 backdrop-blur-md` | Same |
| Left sidebar text | `text-white` always (map is dark in both modes) | Same |
| Right sidebar | `bg-white dark:bg-slate-900` | Opaque white |
| Right sidebar text | `dark:text-slate-100` | `text-slate-800` |
| Toggle button | `bg-white dark:bg-slate-900` | Same |

Note: left sidebar text is always white because the Cesium map underneath is always relatively dark (aerial imagery). If this assumption proves wrong in edge cases, we can add `dark:` variants later.

## Verification

1. Open `/` or `/dashboard`: logo is the SVG, dark mode toggles invert it correctly.
2. Open mega menu: theme toggle and language selector are present and functional.
3. Open `/entities`: left panel is glassmorphism over Cesium map, entity names are white and legible.
4. Right panel is fully opaque. Toggle light/dark — right panel switches correctly.
5. Toggle buttons on both sidebars are fully visible (not clipped).
6. Click toggle button to cycle closed→compact→expanded→closed: works, button stays visible throughout.
7. Open a module widget in context panel: renders correctly with opaque background.
8. Resize sidebar via drag handle: works, toggle button unaffected.
9. Mobile: hamburger menu shows logo correctly, drawer works.

## Rollback

All changes are in the host app and viewer-kit package. No database, API, or module changes.
- `git revert` the commit.
- CI rebuilds host image, ArgoCD syncs.
