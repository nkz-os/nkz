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
        'relative flex flex-col h-full bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-700 z-nkz-rail shadow-nkz-lg rounded-xl pointer-events-auto',
        'transition-all duration-nkz-normal',
        side === 'left' ? 'border-r ml-4' : 'border-l mr-4',
        !isOpen && 'border-0 shadow-none bg-transparent',
        isOpen && className,
      )}
      style={{
        width: isOpen ? `${width}px` : '0px',
        minWidth: isOpen ? `${width}px` : '0px',
        overflow: isOpen ? 'hidden' : 'visible',
      }}
    >
      {/* ---------- Toggle button — always visible on the edge ---------- */}
      {/* Mimics the original LeftPanel/RightPanel: round button, white glass, tooltip */}
      <button
        onClick={handleCycle}
        className={clsx(
          'absolute top-1/2 -translate-y-1/2 z-40',
          'p-2 rounded-full',
          'bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 shadow-xl',
          'text-slate-600 dark:text-slate-300 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-slate-50 dark:hover:bg-slate-800 hover:scale-110',
          'active:scale-95 transition-all duration-300',
          'flex items-center justify-center pointer-events-auto',
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
        {/* Chevron pointing in the appropriate direction */}
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

        {/* Tooltip on hover */}
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
