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
import { IconButton } from '@nekazari/ui-kit';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type SidebarState = 'closed' | 'compact' | 'expanded';

interface SidebarShellRootProps {
  side: 'left' | 'right';
  state: SidebarState;
  onStateChange: (state: SidebarState) => void;
  compactWidth?: number;
  expandedWidth?: number;
  minWidth?: number;
  maxWidth?: number;
  children: React.ReactNode;
  className?: string;
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

  // --------------- Render ---------------

  return (
    <div
      className={clsx(
        'relative flex flex-col bg-nkz-surface border-nkz-border z-nkz-rail',
        'transition-all duration-nkz-normal',
        side === 'left' ? 'border-r' : 'border-l',
        !isOpen && 'border-0',
        isOpen && className,
      )}
      style={{
        width: isOpen ? `${width}px` : '0px',
        minWidth: isOpen ? `${width}px` : '0px',
        overflow: isOpen ? 'hidden' : 'visible',
      }}
    >
      {/* ---------- Closed state: toggle button ---------- */}
      {!isOpen && (
        <button
          onClick={handleCycle}
          className={clsx(
            'absolute top-1/2 -translate-y-1/2 z-10',
            'w-6 h-12 flex items-center justify-center',
            'bg-nkz-surface border border-nkz-border rounded-nkz-md',
            'text-nkz-text-muted hover:text-nkz-text-primary',
            'shadow-nkz-sm transition-colors duration-nkz-fast',
            side === 'left' ? 'right-0 translate-x-1/2' : 'left-0 -translate-x-1/2',
          )}
          aria-label={
            side === 'left' ? 'Open left sidebar' : 'Open right sidebar'
          }
        >
          <svg
            width="8"
            height="12"
            viewBox="0 0 8 12"
            fill="none"
            aria-hidden="true"
            className={side === 'right' ? 'rotate-180' : ''}
          >
            <path
              d="M6 2L2 6l4 4"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      )}

      {/* ---------- Open state: content ---------- */}
      {isOpen && (
        <>
          <div className="flex flex-col flex-1 min-h-0">{children}</div>

          {/* Resize handle — sits on the inner edge */}
          <div
            onMouseDown={handleResizeStart}
            className={clsx(
              'absolute top-0 bottom-0 w-1 cursor-ew-resize z-20 group',
              side === 'left' ? '-right-0.5' : '-left-0.5',
            )}
          >
            <div className="w-full h-full opacity-0 group-hover:opacity-100 transition-opacity bg-nkz-accent-base rounded-full" />
          </div>

          {/* Cycle button at bottom-right corner */}
          <div className="flex justify-end px-nkz-inline py-nkz-tight border-t border-nkz-border">
            <IconButton
              aria-label={
                state === 'compact' ? 'Expand sidebar' : 'Compact sidebar'
              }
              size="sm"
              onClick={handleCycle}
            >
              <svg
                width="12"
                height="12"
                viewBox="0 0 12 12"
                fill="none"
                className={clsx(
                  'transition-transform duration-nkz-fast',
                  state === 'expanded' && side === 'left' && 'rotate-180',
                  state === 'compact' && side === 'right' && 'rotate-180',
                )}
                aria-hidden="true"
              >
                <path
                  d="M5 3l3 3-3 3"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </IconButton>
          </div>
        </>
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
