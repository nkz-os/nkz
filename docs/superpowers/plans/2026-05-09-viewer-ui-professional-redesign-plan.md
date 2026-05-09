# Viewer UI Professional Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace logo with SVG, unify theme system, add functional controls to dropdown, make left sidebar glassmorphism with white text, make right sidebar opaque, fix toggle button clipping, and add rounded borders — across Navigation, ViewerHeader, UnifiedViewer, SidebarShell, and AssetManager components.

**Architecture:** Global `ThemeContext` becomes the single theme source of truth — `useViewerTheme` is retired and the viewer's `ThemeProvider` profile derives from `resolvedTheme`. `SidebarShell` gains a `variant` prop (`glass` | `solid`) to differentiate left vs right panels. Toggle button is extracted from the `overflow: hidden` container. AssetManager components switch from dark-on-light text to white-on-transparent.

**Tech Stack:** TypeScript, React, Tailwind CSS, `@nekazari/viewer-kit`, `@nekazari/design-tokens`

---

### Task 1: Copy SVG to public/ + logo replacement in 3 files

**Files:**
- Create: `nkz/apps/host/public/nkz-os-logo.svg` (copy)
- Modify: `nkz/apps/host/src/components/Navigation.tsx:150-160`
- Modify: `nkz/apps/host/src/components/viewer/ViewerHeader.tsx:122-138`
- Modify: `nkz/apps/host/src/components/layout/MobileDrawer.tsx:141-143`

- [ ] **Step 1: Copy SVG to public/**

```bash
cp /home/g/Documents/nekazari/nkz/images/nkz-os-logo.svg /home/g/Documents/nekazari/nkz/apps/host/public/nkz-os-logo.svg
```

- [ ] **Step 2: Replace logo in Navigation.tsx**

In `Navigation.tsx`, replace lines 150-156:

```tsx
                <button
                  className={`flex items-center gap-2 px-3 py-2 rounded-xl transition-all duration-200 ${isMenuOpen ? 'bg-slate-100 dark:bg-slate-800' : 'hover:bg-slate-50 dark:hover:bg-slate-800'}`}
                >
                  <span className="text-2xl">🌾</span>
                  <span className="text-xl font-bold text-gray-900 dark:text-gray-100 tracking-tight">
                    Nekazari
                  </span>
                  <ChevronDown
                    className={`w-4 h-4 text-gray-500 dark:text-gray-400 transition-transform duration-300 ${isMenuOpen ? 'rotate-180' : ''}`}
                  />
                </button>
```

Replace with:

```tsx
                <button
                  className={`flex items-center gap-2 px-3 py-2 rounded-xl transition-all duration-200 ${isMenuOpen ? 'bg-slate-100 dark:bg-slate-800' : 'hover:bg-slate-50 dark:hover:bg-slate-800'}`}
                >
                  <img
                    src="/nkz-os-logo.svg"
                    alt="Nekazari"
                    className="h-7 w-auto dark:invert"
                  />
                  <ChevronDown
                    className={`w-4 h-4 text-gray-500 dark:text-gray-400 transition-transform duration-300 ${isMenuOpen ? 'rotate-180' : ''}`}
                  />
                </button>
```

- [ ] **Step 3: Replace logo in ViewerHeader.tsx**

In `ViewerHeader.tsx`, replace lines 122-138:

```tsx
                <button
                    type="button"
                    onClick={() => {
                        setIsMenuOpen(false);
                        navigate('/dashboard');
                    }}
                    className={`flex items-center gap-2 px-4 py-2.5 rounded-xl ${surfaceStyles.base} ${surfaceStyles.hover} transition-all duration-300 group`}
                >
                    <span className="text-2xl">🌾</span>
                    <span className="text-lg font-bold text-slate-800 dark:text-slate-100 tracking-tight">
                        Nekazari
                    </span>
                    <ChevronDown
                        className={`w-4 h-4 text-slate-500 dark:text-slate-400 transition-transform duration-300 ${isMenuOpen ? 'rotate-180' : ''
                            }`}
                    />
                </button>
```

Replace with:

```tsx
                <button
                    type="button"
                    onClick={() => {
                        setIsMenuOpen(false);
                        navigate('/dashboard');
                    }}
                    className={`flex items-center gap-2 px-4 py-2.5 rounded-xl ${surfaceStyles.base} ${surfaceStyles.hover} transition-all duration-300 group`}
                >
                    <img
                        src="/nkz-os-logo.svg"
                        alt="Nekazari"
                        className="h-7 w-auto dark:invert"
                    />
                    <ChevronDown
                        className={`w-4 h-4 text-slate-500 dark:text-slate-400 transition-transform duration-300 ${isMenuOpen ? 'rotate-180' : ''
                            }`}
                    />
                </button>
```

- [ ] **Step 4: Replace logo in MobileDrawer.tsx**

In `MobileDrawer.tsx`, replace lines 141-143:

```tsx
          <Link to="/dashboard" onClick={onClose} className="flex items-center">
            <span className="text-2xl mr-2">🌾</span>
            <span className="text-xl font-bold text-gray-900 dark:text-gray-100">Nekazari</span>
          </Link>
```

Replace with:

```tsx
          <Link to="/dashboard" onClick={onClose} className="flex items-center">
            <img
              src="/nkz-os-logo.svg"
              alt="Nekazari"
              className="h-7 w-auto dark:invert"
            />
          </Link>
```

- [ ] **Step 5: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add apps/host/public/nkz-os-logo.svg \
        apps/host/src/components/Navigation.tsx \
        apps/host/src/components/viewer/ViewerHeader.tsx \
        apps/host/src/components/layout/MobileDrawer.tsx
git commit -m "feat: replace 🌾 emoji logo with nkz-os-logo.svg across all components"
```

---

### Task 2: Theme unification — retire useViewerTheme, add useViewerProfile to ThemeContext

**Files:**
- Modify: `nkz/apps/host/src/context/ThemeContext.tsx:132`
- Modify: `nkz/apps/host/src/hooks/useViewerTheme.ts` (entire file)
- Modify: `nkz/apps/host/src/components/UnifiedViewer.tsx:26,398`
- Modify: `nkz/apps/host/src/components/viewer/ViewerHeader.tsx:18,56-57`

- [ ] **Step 1: Add useViewerProfile hook to ThemeContext.tsx**

At the bottom of `ThemeContext.tsx`, after the `useTheme` hook (after line 131), add:

```typescript
import type { TokenProfile } from '@nekazari/design-tokens';

/**
 * Derive viewer token profile from global theme.
 * 'dark' → 'viewer' (dark opaque surfaces)
 * 'light' → 'viewer-light' (light opaque surfaces)
 */
export const useViewerProfile = (): { profile: TokenProfile } => {
  const { resolvedTheme } = useTheme();
  const profile: TokenProfile = resolvedTheme === 'dark' ? 'viewer' : 'viewer-light';
  return { profile };
};
```

Add the import at the top of the file (after line 6):

```typescript
import type { TokenProfile } from '@nekazari/design-tokens';
```

- [ ] **Step 2: Deprecate useViewerTheme.ts**

Replace the entire content of `useViewerTheme.ts`:

```typescript
// =============================================================================
// useViewerTheme — DEPRECATED: use useViewerProfile from ThemeContext instead
// =============================================================================
// This hook is kept as a re-export for backward compatibility.
// New code should use `useViewerProfile` from '@/context/ThemeContext'.

import { useViewerProfile } from '@/context/ThemeContext';

export { useViewerProfile as useViewerTheme };
```

- [ ] **Step 3: Update UnifiedViewer.tsx to use useViewerProfile**

In `UnifiedViewer.tsx`:
- Remove line 26: `import { useViewerTheme } from '@/hooks/useViewerTheme';`
- Add line 26: `import { useViewerProfile } from '@/context/ThemeContext';`
- Replace line 398: `const { profile, setProfile } = useViewerTheme();`
- With: `const { profile } = useViewerProfile();`
- Replace line 401: `<ThemeProvider profile={profile} onChange={setProfile}>`
- With: `<ThemeProvider profile={profile}>`

Also remove the unused import of `useViewerTheme` (the old import). And remove unused `ThemeProvider` import if `setProfile` was the only reason `ThemeProvider` needed `onChange`. The `ThemeProvider` from `@nekazari/design-tokens` may still need `onChange` — check the type. If optional, we can drop it.

Simplest safe approach — check the ThemeProvider signature:

Run: `grep -n "ThemeProvider" /home/g/Documents/nekazari/nkz/packages/design-tokens/src/index.ts`

If `onChange` is optional, just pass `profile`. If required, pass a no-op.

- [ ] **Step 4: Update ViewerHeader.tsx to use useTheme instead of useViewerTheme**

In `ViewerHeader.tsx`:
- Remove line 18: `import { useViewerTheme } from '@/hooks/useViewerTheme';`
- Add import: `import { useTheme } from '@/context/ThemeContext';`
- Replace lines 56-57:
  ```typescript
  const { profile, toggle } = useViewerTheme();
  const isLight = profile === 'viewer-light';
  ```
  With:
  ```typescript
  const { resolvedTheme, toggleTheme } = useTheme();
  const isLight = resolvedTheme === 'light';
  ```
- Replace line 287 (in submenu): `onClick={toggle}` → `onClick={toggleTheme}`

- [ ] **Step 5: Type-check**

```bash
cd /home/g/Documents/nekazari/nkz/apps/host && pnpm run typecheck
```

- [ ] **Step 6: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add apps/host/src/context/ThemeContext.tsx \
        apps/host/src/hooks/useViewerTheme.ts \
        apps/host/src/components/UnifiedViewer.tsx \
        apps/host/src/components/viewer/ViewerHeader.tsx
git commit -m "refactor: unify theme system — viewer follows global ThemeContext, useViewerTheme deprecated"
```

---

### Task 3: SidebarShell — variant prop + toggle button extraction

**Files:**
- Modify: `nkz/packages/viewer-kit/src/viewer/SidebarShell.tsx` (entire component)

- [ ] **Step 1: Add variant prop and refactor toggle button outside overflow**

Replace the entire `SidebarShellRoot` function (lines 43-216) with the new version:

```tsx
export type SidebarVariant = 'glass' | 'solid';

interface SidebarShellRootProps {
  side: 'left' | 'right';
  state: SidebarState;
  onStateChange: (state: SidebarState) => void;
  variant?: SidebarVariant;
  compactWidth?: number;
  expandedWidth?: number;
  minWidth?: number;
  maxWidth?: number;
  children: React.ReactNode;
  className?: string;
}

function SidebarShellRoot({
  side,
  state,
  onStateChange,
  variant = 'solid',
  compactWidth = 380,
  expandedWidth = 650,
  minWidth = 320,
  maxWidth = 720,
  children,
  className,
}: SidebarShellRootProps) {
  const isOpen = state !== 'closed';
  const [width, setWidth] = useState(
    state === 'expanded' ? expandedWidth : compactWidth,
  );
  const resizingRef = useRef(false);

  useEffect(() => {
    setWidth(state === 'expanded' ? expandedWidth : compactWidth);
  }, [state, compactWidth, expandedWidth]);

  const handleCycle = useCallback(() => {
    const idx = STATE_CYCLE.indexOf(state);
    onStateChange(STATE_CYCLE[(idx + 1) % STATE_CYCLE.length]);
  }, [state, onStateChange]);

  const handleResizeStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      resizingRef.current = true;
      const startX = e.clientX;
      const startWidth = width;
      const handleMouseMove = (me: MouseEvent) => {
        if (!resizingRef.current) return;
        const delta =
          side === 'right' ? startX - me.clientX : me.clientX - startX;
        const newWidth = Math.min(
          maxWidth,
          Math.max(minWidth, startWidth + delta),
        );
        setWidth(newWidth);
        if (newWidth > compactWidth + 40 && state === 'compact') {
          onStateChange('expanded');
        }
      };
      const handleMouseUp = () => {
        resizingRef.current = false;
        document.removeEventListener('mousemove', handleMouseMove);
        document.removeEventListener('mouseup', handleMouseUp);
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      };
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = 'ew-resize';
      document.body.style.userSelect = 'none';
    },
    [side, width, minWidth, maxWidth, compactWidth, state, onStateChange],
  );

  const surfaceClass = variant === 'glass'
    ? 'bg-white/70 dark:bg-slate-900/60 backdrop-blur-md'
    : 'bg-white dark:bg-slate-900';

  return (
    <div
      className={clsx(
        'relative z-nkz-rail pointer-events-auto',
        !isOpen && 'border-0 shadow-none bg-transparent',
      )}
      style={{
        width: isOpen ? `${width}px` : 'auto',
        minWidth: isOpen ? `${width}px` : undefined,
      }}
    >
      {/* Toggle button — outside overflow container, always fully visible */}
      <button
        onClick={handleCycle}
        className={clsx(
          'absolute top-1/2 -translate-y-1/2 z-50',
          'p-2 rounded-full',
          'bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 shadow-xl',
          'text-slate-600 dark:text-slate-300 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-slate-50 dark:hover:bg-slate-800 hover:scale-110',
          'active:scale-95 transition-all duration-300',
          'flex items-center justify-center',
          isOpen
            ? (side === 'left' ? 'right-0 translate-x-1/2' : 'left-0 -translate-x-1/2')
            : (side === 'left' ? 'left-2' : 'right-2'),
        )}
        title={
          isOpen
            ? (state === 'compact' ? 'Expandir panel' : 'Cerrar panel')
            : 'Abrir panel'
        }
        aria-label={
          isOpen
            ? (state === 'compact' ? 'Expand sidebar' : 'Close sidebar')
            : 'Open sidebar'
        }
      >
        <svg
          width="20"
          height="20"
          viewBox="0 0 20 20"
          fill="none"
          aria-hidden="true"
          className={
            !isOpen
              ? (side === 'left' ? '' : 'rotate-180')
              : state === 'compact'
                ? (side === 'left' ? 'rotate-0' : 'rotate-180')
                : (side === 'left' ? 'rotate-180' : 'rotate-0')
          }
        >
          <path
            d={isOpen && state === 'expanded'
              ? (side === 'left' ? 'M12 6l-4 4 4 4' : 'M8 6l4 4-4 4')
              : (side === 'left' ? 'M8 6l4 4-4 4' : 'M12 6l-4 4 4 4')
            }
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        <span className={clsx(
          'absolute top-1/2 -translate-y-1/2 px-2 py-1 bg-slate-800 text-white text-xs rounded',
          'opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap pointer-events-none',
          side === 'left' ? 'left-full ml-2' : 'right-full mr-2',
        )}>
          {!isOpen
            ? 'Abrir panel'
            : state === 'compact'
              ? 'Expandir'
              : 'Cerrar panel'
          }
        </span>
      </button>

      {/* Content container with overflow hidden */}
      {isOpen && (
        <div
          className={clsx(
            'flex flex-col h-full',
            surfaceClass,
            'border border-slate-200 dark:border-slate-700',
            side === 'left' ? 'border-r ml-4' : 'border-l mr-4',
            'shadow-nkz-lg rounded-xl',
            'transition-all duration-nkz-normal overflow-hidden',
            className,
          )}
        >
          <div className="flex flex-col flex-1 min-h-0">{children}</div>

          {/* Resize handle */}
          <div
            onMouseDown={handleResizeStart}
            className={clsx(
              'absolute top-0 bottom-0 w-1 cursor-ew-resize z-20 group',
              side === 'left' ? '-right-0.5' : '-left-0.5',
            )}
          >
            <div className="w-full h-full opacity-0 group-hover:opacity-100 transition-opacity bg-nkz-accent-base rounded-full" />
          </div>
        </div>
      )}
    </div>
  );
}
```

Note: `group` class removed from outer wrapper since toggle button tooltip references it — add `group` back to the button if tooltip needs it. The tooltip uses `group-hover:opacity-100` on the span.

Actually the toggle button needs `group` itself:

```tsx
<button className={clsx(..., 'group')}>
```

- [ ] **Step 2: Build viewer-kit**

```bash
cd /home/g/Documents/nekazari/nkz/packages/viewer-kit && pnpm run build
```

Expected: builds without TypeScript errors.

- [ ] **Step 3: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add packages/viewer-kit/src/viewer/SidebarShell.tsx
git commit -m "fix(viewer-kit): add variant prop (glass/solid), extract toggle button outside overflow container"
```

---

### Task 4: Navigation.tsx — move ThemeToggle + LanguageSelector into dropdown

**Files:**
- Modify: `nkz/apps/host/src/components/Navigation.tsx:286-325`

- [ ] **Step 1: Remove ThemeToggle and LanguageSelector from right section**

Remove lines 309-315 (divider + ThemeToggle + LanguageSelector):

```tsx
              <div className="h-6 w-px bg-gray-200 dark:bg-gray-700 mx-1 hidden md:block"></div>

              {/* Theme Toggle */}
              <ThemeToggle variant="compact" />

              {/* Language Selector */}
              <LanguageSelector variant="compact" />
```

Keep the logout button after the user info section.

- [ ] **Step 2: Add controls row at bottom of dropdown**

Before the closing `</div>` of the dropdown (before line 279, right before `</div>` that closes the mega menu `div` at line 279), add:

```tsx
                  {/* Controls: Theme + Language */}
                  <div className="flex items-center gap-2 px-3 py-2.5 border-t border-gray-100 dark:border-gray-700/50">
                    <ThemeToggle variant="compact" />
                    <LanguageSelector variant="compact" />
                  </div>
```

This goes right before the `</div>` that closes `className={`absolute top-full left-0 mt-2 flex rounded-xl...`}`.

Looking at the structure, the dropdown div starts at line 163 and ends at line 279. The controls row goes after the last column (Admin, lines 250-278) and before line 279 `</div>`.

- [ ] **Step 3: Type-check**

```bash
cd /home/g/Documents/nekazari/nkz/apps/host && pnpm run typecheck
```

- [ ] **Step 4: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add apps/host/src/components/Navigation.tsx
git commit -m "feat(nav): move theme toggle and language selector into mega menu dropdown"
```

---

### Task 5: ViewerHeader.tsx — remove mock submenu, add controls row

**Files:**
- Modify: `nkz/apps/host/src/components/viewer/ViewerHeader.tsx:271-308`

- [ ] **Step 1: Remove the mock submenu**

Delete lines 271-308 (the entire right hover submenu div):

```tsx
                    {/* Right hover submenu for global quick actions */}
                    <div className="absolute left-full top-0 ml-2 w-56 rounded-xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 shadow-lg p-2 opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto transition-opacity duration-200">
                        ... (38 lines of mock/partially-functional buttons)
                    </div>
```

- [ ] **Step 2: Add controls row at bottom of dropdown**

Before the closing `</div>` of the dropdown (before line 309 `</div>` which closes the dropdown container), add:

```tsx
                    {/* Controls: Theme + Language */}
                    <div className="flex items-center gap-2 px-3 py-2.5 border-t border-slate-200 dark:border-slate-700">
                        <button
                            onClick={toggleTheme}
                            className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
                        >
                            {isLight ? (
                                <Sun className="w-4 h-4" />
                            ) : (
                                <Moon className="w-4 h-4" />
                            )}
                            <span>{isLight ? 'Claro' : 'Oscuro'}</span>
                        </button>
                        <LanguageSelector variant="compact" />
                    </div>
```

Add the missing imports at the top of the file (lines 26-33):

```typescript
import {
    ChevronDown,
    LogOut,
    Puzzle,
    Sun,
    Moon,
} from 'lucide-react';
```

Remove the unused imports: `Search`, `Layers`, `Settings`, `CircleHelp` (they were only used in the removed submenu).

- [ ] **Step 3: Type-check**

```bash
cd /home/g/Documents/nekazari/nkz/apps/host && pnpm run typecheck
```

- [ ] **Step 4: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add apps/host/src/components/viewer/ViewerHeader.tsx
git commit -m "feat(viewer-header): remove mock submenu, add functional theme+language controls in dropdown"
```

---

### Task 6: UnifiedViewer.tsx — pass variant prop to SidebarShell

**Files:**
- Modify: `nkz/apps/host/src/components/UnifiedViewer.tsx:523-538,542-591`

- [ ] **Step 1: Pass variant="glass" to left SidebarShell**

In `UnifiedViewer.tsx`, line 524, add `variant="glass"`:

```tsx
              <SidebarShell
                  side="left"
                  state={sidebarState}
                  onStateChange={handleLeftStateChange}
                  variant="glass"
              >
```

- [ ] **Step 2: Pass variant="solid" to right SidebarShell**

In `UnifiedViewer.tsx`, line 543, add `variant="solid"` (explicit, though it's the default):

```tsx
              <SidebarShell
                  side="right"
                  state={rightSidebarState}
                  onStateChange={handleRightStateChange}
                  variant="solid"
              >
```

- [ ] **Step 3: Type-check**

```bash
cd /home/g/Documents/nekazari/nkz/apps/host && pnpm run typecheck
```

- [ ] **Step 4: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add apps/host/src/components/UnifiedViewer.tsx
git commit -m "feat(viewer): pass glass variant to left panel, solid to right panel"
```

---

### Task 7: AssetManager white text — Grid, Row, CategoryNav

**Files:**
- Modify: `nkz/apps/host/src/components/AssetManager/AssetManagerGrid.tsx:278-384,526-565`
- Modify: `nkz/apps/host/src/components/AssetManager/AssetRow.tsx:118-214`
- Modify: `nkz/apps/host/src/components/AssetManager/AssetCategoryNav.tsx:76-152`

- [ ] **Step 1: AssetManagerGrid — header background to transparent, text to white**

In `AssetManagerGrid.tsx`, line 278, change the header div:

```tsx
      <div className="flex-shrink-0 px-4 py-3 border-b border-white/10 bg-transparent">
```

Line 282: change title text:
```tsx
            <h2 className="font-semibold text-white">Assets</h2>
```

Line 283: change count badge:
```tsx
            <span className="text-xs px-2 py-0.5 rounded-full bg-white/10 text-white/70">
```

Line 287-289: change action button icons:
```tsx
              className="p-1.5 rounded-lg hover:bg-white/10 text-white/60 hover:text-white transition-colors"
```

Line 303-308: same for filter button. And for view mode buttons (line 318-348).

Line 373: search input:
```tsx
            className="w-full pl-9 pr-8 py-2 text-sm border border-white/10 rounded-lg bg-white/5 text-white placeholder:text-white/40 focus:outline-none focus:ring-2 focus:ring-white/20 focus:border-white/30"
```

Search icon (line 367): `text-white/40`

- [ ] **Step 2: AssetManagerGrid — table header to transparent with white text**

Line 526: table header row:
```tsx
              <div className="sticky top-0 z-10 flex items-center gap-2 px-4 py-2 bg-white/5 border-b border-white/10 text-xs font-medium text-white/60 uppercase tracking-wider">
```

Remove `dark:` variants from the sort buttons — use `text-white/60` and `hover:text-white`.

- [ ] **Step 3: AssetManagerGrid — bulk actions bar**

Line 409: keep the blue bar as-is — it's a functional alert, not part of the transparent aesthetic.

Line 450: loading state stays as-is (it's centered text).

Line 496: empty state text: use `text-white/60`.

- [ ] **Step 4: AssetRow — entity name and text to white**

In `AssetRow.tsx`, line 118-122, row container:
```tsx
      className={`flex items-center gap-2 px-4 py-2.5 cursor-pointer transition-colors ${
        isSelected
          ? 'bg-white/15 hover:bg-white/20'
          : 'hover:bg-white/10'
      }`}
```

Line 149: entity name:
```tsx
            <span className="font-medium text-sm text-white truncate" title={asset.name}>
```

Line 157: description:
```tsx
            <p className="text-xs text-white/50 truncate" title={asset.description}>
```

Line 166: type badge:
```tsx
        <span className="text-xs text-white/60 bg-white/10 px-2 py-0.5 rounded-full truncate inline-block max-w-full" ...>
```

Line 173: status — keep the colored badges (green, amber, red) as they have enough contrast.

Line 189: location:
```tsx
          <span className="text-xs text-white/50 truncate block" ...>
```

Line 197-201: last seen:
```tsx
          <span className="text-xs text-white/40" ...>
```

Line 210: action button:
```tsx
        className="flex-shrink-0 p-1 rounded hover:bg-white/10 text-white/40 hover:text-white/70"
```

Checkbox icons (lines 136-137): `text-white/50` and `text-blue-400` for selected.

- [ ] **Step 5: AssetCategoryNav — background to transparent**

In `AssetCategoryNav.tsx`, line 76:
```tsx
    <div className="flex-shrink-0 px-4 py-2 border-b border-white/10 bg-transparent">
```

The category pills have their own colored backgrounds (green, teal, etc.) which provide enough contrast. The "all" inactive pill (line 89): `bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-200` → `bg-white/10 text-white/70 hover:bg-white/20`.

The "all" active pill (line 88): `bg-slate-800 text-white` → already good.

The infrastructure inactive pill (line 105): `bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-200` → `bg-white/10 text-white/70 hover:bg-white/20`.

- [ ] **Step 6: Type-check**

```bash
cd /home/g/Documents/nekazari/nkz/apps/host && pnpm run typecheck
```

- [ ] **Step 7: Commit**

```bash
cd /home/g/Documents/nekazari/nkz
git add apps/host/src/components/AssetManager/AssetManagerGrid.tsx \
        apps/host/src/components/AssetManager/AssetRow.tsx \
        apps/host/src/components/AssetManager/AssetCategoryNav.tsx
git commit -m "feat(asset-manager): white text on transparent background for glass sidebar"
```

---

### Task 8: Build and verify

- [ ] **Step 1: Rebuild viewer-kit**

```bash
cd /home/g/Documents/nekazari/nkz/packages/viewer-kit && pnpm run build
```

Expected: builds without errors.

- [ ] **Step 2: Build host app**

```bash
cd /home/g/Documents/nekazari/nkz/apps/host && pnpm run build
```

Expected: Vite builds without errors.

- [ ] **Step 3: Type-check full project**

```bash
cd /home/g/Documents/nekazari/nkz/apps/host && pnpm run typecheck
```

Expected: no TypeScript errors.

- [ ] **Step 4: Push to main — triggers CI + ArgoCD deploy**

```bash
cd /home/g/Documents/nekazari/nkz
git push origin fix/all-platform-headers
```

Then create PR to merge into `main`. CI builds `ghcr.io/nkz-os/nkz/host:latest`. ArgoCD syncs.

- [ ] **Step 5: Verify in production**

After deploy:
1. Open `nekazari.robotika.cloud` — logo is SVG, dark mode inverts it correctly.
2. Open mega menu — theme toggle and language selector are inside the dropdown.
3. Open `/entities` — left panel is glassmorphism, entity names are white and legible.
4. Right panel is fully opaque — toggle light/dark, right panel switches correctly.
5. Toggle buttons on both sidebars are fully visible (not clipped).
6. Cycle toggle: closed → compact → expanded → closed — button visible throughout.
