/**
 * ModuleGroup — sidebar accordion group header with drag handle, accent strip,
 * icon, name, count badge, hide button, and collapse chevron.
 *
 * State is persisted via useModuleGroupState (localStorage).
 * When hidden, returns null.
 */
import React, { useCallback } from 'react';
import clsx from 'clsx';
import { useModuleGroupState } from '../hooks/useModuleGroupState';

export interface ModuleGroupProps {
  moduleId: string;
  slot: string;
  name: string;
  icon?: React.ReactNode;
  accent: { base: string; soft: string; strong: string };
  count?: number;
  children: React.ReactNode;
  onDragStart?: (e: React.DragEvent) => void;
  tenantId: string;
  userId: string;
}

function ModuleGroup({
  moduleId,
  slot,
  name,
  icon,
  accent,
  count,
  children,
  onDragStart,
  tenantId,
  userId,
}: ModuleGroupProps) {
  const state = useModuleGroupState(tenantId, userId, slot, moduleId);

  const handleHide = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      state.toggleHidden();
    },
    [state.toggleHidden],
  );

  if (state.hidden) return null;

  return (
    <div
      className="flex flex-col"
      style={{ borderLeft: `3px solid ${accent.base}` }}
    >
      {/* ---------- Header ---------- */}
      <button
        onClick={state.toggleCollapsed}
        onDragStart={onDragStart}
        draggable={!!onDragStart}
        className={clsx(
          'flex items-center gap-nkz-inline w-full px-nkz-inline py-nkz-tight',
          'text-nkz-xs text-nkz-text-secondary hover:text-nkz-text-primary',
          'hover:bg-nkz-surface-sunken transition-colors duration-nkz-fast',
          'cursor-pointer select-none group',
        )}
      >
        {/* Drag handle */}
        <span
          className="text-nkz-text-muted cursor-grab opacity-0 group-hover:opacity-100 transition-opacity duration-nkz-fast select-none"
          aria-hidden="true"
        >
          ⠿
        </span>

        {/* Icon */}
        {icon && (
          <span className="flex-shrink-0 w-4 h-4 flex items-center justify-center">
            {icon}
          </span>
        )}

        {/* Name */}
        <span className="flex-1 text-left truncate font-medium">{name}</span>

        {/* Count badge */}
        {count !== undefined && count > 0 && (
          <span
            className="inline-flex items-center justify-center min-w-[18px] h-[18px] px-nkz-tight rounded-nkz-full text-nkz-2xs font-bold leading-none"
            style={{ backgroundColor: accent.soft, color: accent.strong }}
          >
            {count}
          </span>
        )}

        {/* Hide button */}
        <span
          onClick={handleHide}
          className="opacity-0 group-hover:opacity-100 transition-opacity duration-nkz-fast cursor-pointer text-nkz-text-muted hover:text-nkz-text-primary"
          title="Ocultar"
          role="button"
          aria-label={`Hide ${name}`}
        >
          👁
        </span>

        {/* Collapse chevron */}
        <svg
          width="10"
          height="10"
          viewBox="0 0 10 10"
          fill="none"
          className={clsx(
            'transition-all duration-nkz-normal',
            state.collapsed ? '-rotate-90' : 'rotate-0',
          )}
          style={{ transformOrigin: 'center' }}
          aria-hidden="true"
        >
          <path
            d="M3 4l2 2 2-2"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      {/* ---------- Body ---------- */}
      {!state.collapsed && (
        <div className="px-nkz-inline pb-nkz-tight">{children}</div>
      )}
    </div>
  );
}

export { ModuleGroup };
