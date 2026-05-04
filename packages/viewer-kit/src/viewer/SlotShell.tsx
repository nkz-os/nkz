/**
 * SlotShell — wrapper for viewer slot widgets with optional error boundary
 * and accent scope.
 *
 * SlotShell:      Full wrapper with Header (title + icon + collapse chevron)
 *                 and Body, inside a glass Panel.
 * SlotShellCompact: Minimal wrapper, no header — just Panel + Body.
 */
import React, { Component } from 'react';
import clsx from 'clsx';
import { AccentScope, type Accent } from '@nekazari/design-tokens';
import { Panel, IconButton } from '@nekazari/ui-kit';

// ---------------------------------------------------------------------------
// ModuleErrorBoundary — class component required for React error boundaries
// ---------------------------------------------------------------------------

interface ModuleErrorBoundaryProps {
  moduleId: string;
  children: React.ReactNode;
}

interface ModuleErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

class ModuleErrorBoundary extends Component<
  ModuleErrorBoundaryProps,
  ModuleErrorBoundaryState
> {
  constructor(props: ModuleErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(
    error: Error,
  ): ModuleErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error(
      `[SlotShell] Module "${this.props.moduleId}" crashed:`,
      error,
      info,
    );
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="p-nkz-stack bg-red-50 border border-red-200 rounded-nkz-md">
          <p className="text-nkz-sm text-red-800 font-medium">
            Error en modulo: {this.props.moduleId}
          </p>
          <p className="text-nkz-xs text-red-600 mt-nkz-tight">
            {this.state.error?.message ?? 'Unknown error'}
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}

// ---------------------------------------------------------------------------
// SlotShell
// ---------------------------------------------------------------------------

export interface SlotShellProps {
  moduleId: string;
  accent?: Accent;
  title?: string;
  icon?: React.ReactNode;
  collapsible?: boolean;
  defaultCollapsed?: boolean;
  children: React.ReactNode;
  className?: string;
}

function SlotShell({
  moduleId,
  accent,
  title,
  icon,
  collapsible = false,
  defaultCollapsed = false,
  children,
  className,
}: SlotShellProps) {
  const [collapsed, setCollapsed] = React.useState(defaultCollapsed);

  const content = (
    <Panel variant="glass" className={clsx(className)}>
      {(title || icon || collapsible) && (
        <Panel.Header>
          <Panel.Title icon={icon}>{title}</Panel.Title>
          {collapsible && (
            <IconButton
              aria-label={collapsed ? 'Expand' : 'Collapse'}
              onClick={() => setCollapsed((c) => !c)}
              size="sm"
            >
              <svg
                width="12"
                height="12"
                viewBox="0 0 12 12"
                fill="none"
                className={clsx(
                  'transition-transform duration-nkz-fast',
                  collapsed && '-rotate-90',
                )}
                aria-hidden="true"
              >
                <path
                  d="M3 5l3 3 3-3"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </IconButton>
          )}
        </Panel.Header>
      )}
      {!collapsed && <Panel.Body>{children}</Panel.Body>}
    </Panel>
  );

  return (
    <ModuleErrorBoundary moduleId={moduleId}>
      {accent ? (
        <AccentScope accent={accent}>{content}</AccentScope>
      ) : (
        content
      )}
    </ModuleErrorBoundary>
  );
}

// ---------------------------------------------------------------------------
// SlotShellCompact
// ---------------------------------------------------------------------------

export interface SlotShellCompactProps {
  moduleId: string;
  accent?: Accent;
  children: React.ReactNode;
  className?: string;
}

function SlotShellCompact({
  moduleId,
  accent,
  children,
  className,
}: SlotShellCompactProps) {
  const content = (
    <Panel variant="glass" className={clsx(className)}>
      <Panel.Body>{children}</Panel.Body>
    </Panel>
  );

  return (
    <ModuleErrorBoundary moduleId={moduleId}>
      {accent ? (
        <AccentScope accent={accent}>{content}</AccentScope>
      ) : (
        content
      )}
    </ModuleErrorBoundary>
  );
}

export { SlotShell, SlotShellCompact };
