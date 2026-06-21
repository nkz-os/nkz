/**
 * SidebarShell — compound component for left/right sidebar rails.
 *
 * State machine: closed → compact → expanded → closed (click cycle).
 * Supports drag-resize on the inner edge.
 *
 * Sub-components:
 *   SidebarShell.Pinned   — top pinned content (always visible)
 *   SidebarShell.Groups   — scrollable module groups (flex-1)
 *   SidebarShell.Hidden   — bottom hidden-modules drawer
 */
import React, { useState, useCallback, useEffect, useRef } from 'react';
import clsx from 'clsx';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type SidebarState = 'closed' | 'compact' | 'expanded';

export type SidebarVariant = 'glass' | 'solid';

export interface SidebarLabels {
  /** Tooltip for the toggle button when sidebar is closed */
  openLabel?: string;
  /** Tooltip for the toggle button when sidebar is open (compact) */
  expandLabel?: string;
  /** Tooltip for the toggle button when sidebar is open (expanded) */
  closeLabel?: string;
  /** Tooltip in the popover when sidebar is closed */
  openTooltip?: string;
  /** Tooltip in the popover when sidebar is compact */
  expandTooltip?: string;
  /** Tooltip in the popover when sidebar is expanded */
  closeTooltip?: string;
}

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
  /** Localised labels for the toggle button. Defaults to Spanish. */
  labels?: SidebarLabels;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STATE_CYCLE: SidebarState[] = ['closed', 'compact', 'expanded', 'closed'];

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

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
  labels: labelsProp,
}: SidebarShellRootProps) {
  const labels: Required<SidebarLabels> = {
    openLabel: 'Abrir panel',
    expandLabel: 'Expandir panel',
    closeLabel: 'Cerrar panel',
    openTooltip: 'Abrir panel',
    expandTooltip: 'Expandir',
    closeTooltip: 'Cerrar panel',
    ...labelsProp,
  };
  const isOpen = state !== 'closed';
  const [width, setWidth] = useState(
    state === 'expanded' ? expandedWidth : compactWidth,
  );
  const resizingRef = useRef(false);

  // Sync width when state changes
  useEffect(() => {
    setWidth(state === 'expanded' ? expandedWidth : compactWidth);
  }, [state, compactWidth, expandedWidth]);

  const handleCycle = useCallback(() => {
    const idx = STATE_CYCLE.indexOf(state);
    onStateChange(STATE_CYCLE[(idx + 1) % STATE_CYCLE.length]);
  }, [state, onStateChange]);

  // --------------- Drag resize ---------------

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

        // Auto-promote to expanded when dragged past compact threshold
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
    ? 'bg-white/30 dark:bg-slate-900/40 backdrop-blur-md'
    : 'bg-white dark:bg-slate-900';

  // --------------- Render ---------------

  return (
    <div
      className={clsx(
        'relative h-full z-nkz-rail pointer-events-auto',
        isOpen ? 'overflow-visible' : 'border-0 shadow-none bg-transparent',
      )}
      style={{
        width: isOpen ? `${width}px` : 'auto',
        minWidth: isOpen ? `${width}px` : undefined,
      }}
    >
      {/* Toggle button — anchored with fixed offset to avoid clipping */}
      <button
        onClick={handleCycle}
        className={clsx(
          'absolute top-1/2 -translate-y-1/2 z-50 group',
          'p-2 rounded-full',
          'bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 shadow-xl',
          'text-slate-600 dark:text-slate-300 hover:text-nkz-accent-base hover:bg-slate-50 dark:hover:bg-slate-800 hover:scale-110',
          'active:scale-95 transition-all duration-300',
          'flex items-center justify-center',
          isOpen
            ? (side === 'left' ? '-right-4' : '-left-4')
            : (side === 'left' ? 'left-2' : 'right-2'),
        )}
        title={
          isOpen
            ? (state === 'compact' ? labels.expandLabel : labels.closeLabel)
            : labels.openLabel
        }
        aria-label={
          isOpen
            ? (state === 'compact' ? labels.expandLabel : labels.closeLabel)
            : labels.openLabel
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
            ? labels.openTooltip
            : state === 'compact'
              ? labels.expandTooltip
              : labels.closeTooltip
          }
        </span>
      </button>

      {/* Content container with overflow hidden — only rendered when open */}
      {isOpen && (
        <div
          className={clsx(
            'flex flex-col h-full',
            surfaceClass,
            'border border-slate-200 dark:border-slate-700',
            side === 'left' ? 'border-r ml-4' : 'border-l mr-4',
            'shadow-nkz-lg rounded-xl',
            'transition-all duration-nkz-normal overflow-y-auto',
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

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SidebarShellPinned({ children }: { children: React.ReactNode }) {
  return (
    <div className="border-b border-nkz-border flex-shrink-0">{children}</div>
  );
}

function SidebarShellGroups({ children }: { children: React.ReactNode }) {
  return <div className="flex-1 overflow-y-auto">{children}</div>;
}

function SidebarShellHidden({ children }: { children: React.ReactNode }) {
  return (
    <div className="border-t border-nkz-border flex-shrink-0">{children}</div>
  );
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

export const SidebarShell = Object.assign(SidebarShellRoot, {
  Pinned: SidebarShellPinned,
  Groups: SidebarShellGroups,
  Hidden: SidebarShellHidden,
});
