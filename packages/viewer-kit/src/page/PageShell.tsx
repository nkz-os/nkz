/**
 * PageShell — compound component for module pages.
 *
 * Wraps content in ThemeProvider (profile="page") and AccentScope.
 * Includes branding strip (4px accent bar) and calls useScrollHeaderState().
 *
 * Sub-components:
 *   PageShell.Header  — sticky header slot
 *   PageShell.Nav     — sticky sidebar nav (240px), positioned below host+page headers
 *   PageShell.Content  — flex-1 main content
 */
import React from 'react';
import clsx from 'clsx';
import {
  ThemeProvider,
  AccentScope,
  type Accent,
} from '@nekazari/design-tokens';
import { useScrollHeaderState } from '../hooks/useScrollHeaderState';

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

interface PageShellRootProps {
  module: string;
  accent?: Accent;
  children: React.ReactNode;
}

function PageShellRoot({ module: _module, accent, children }: PageShellRootProps) {
  useScrollHeaderState();

  return (
    <ThemeProvider profile="page">
      <div className="relative min-h-screen bg-nkz-canvas">
        {/* Branding strip (4px accent bar) */}
        <div
          className="h-1 w-full flex-shrink-0"
          style={{
            backgroundColor:
              accent?.base ?? 'var(--nkz-color-accent-base, #10B981)',
          }}
        />

        {accent ? (
          <AccentScope accent={accent}>
            <div className="flex flex-col">{children}</div>
          </AccentScope>
        ) : (
          <div className="flex flex-col">{children}</div>
        )}
      </div>
    </ThemeProvider>
  );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function PageShellHeader({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx('sticky top-0 z-nkz-header', className)}>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Nav
// ---------------------------------------------------------------------------

function PageShellNav({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <nav
      className={clsx(
        'w-[240px] flex-shrink-0 overflow-y-auto',
        'border-r border-nkz-border bg-nkz-surface',
        className,
      )}
      style={{
        position: 'sticky',
        top: 'var(--nkz-page-header-h, 96px)',
        height: 'calc(100vh - var(--nkz-page-header-h, 96px))',
      }}
    >
      {children}
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Content
// ---------------------------------------------------------------------------

function PageShellContent({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <main className={clsx('flex-1 min-w-0', className)}>{children}</main>;
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

export const PageShell = Object.assign(PageShellRoot, {
  Header: PageShellHeader,
  Nav: PageShellNav,
  Content: PageShellContent,
});
