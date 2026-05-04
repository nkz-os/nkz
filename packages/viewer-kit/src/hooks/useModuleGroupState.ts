/**
 * useModuleGroupState — localStorage-persisted sidebar module state.
 *
 * Key format: `nkz:sidebar:{tenantId}:{userId}:{slot}:{moduleId}`
 * Tracks collapsed, hidden, order, and optional custom width per module group.
 */
import { useState, useCallback, useMemo } from 'react';

export interface ModuleGroupState {
  collapsed: boolean;
  hidden: boolean;
  order: number;
  customWidth?: number;
}

export interface UseModuleGroupStateReturn extends ModuleGroupState {
  toggleCollapsed: () => void;
  toggleHidden: () => void;
  setOrder: (order: number) => void;
  setCustomWidth: (width: number) => void;
}

const STORAGE_PREFIX = 'nkz:sidebar';

function buildKey(
  tenantId: string,
  userId: string,
  slot: string,
  moduleId: string,
): string {
  return `${STORAGE_PREFIX}:${tenantId}:${userId}:${slot}:${moduleId}`;
}

function loadState(key: string): ModuleGroupState {
  try {
    const raw = localStorage.getItem(key);
    if (raw) return JSON.parse(raw) as ModuleGroupState;
  } catch {
    // Ignore parse errors
  }
  return { collapsed: false, hidden: false, order: 0 };
}

function saveState(key: string, state: ModuleGroupState): void {
  try {
    localStorage.setItem(key, JSON.stringify(state));
  } catch {
    // Storage full or unavailable — silently ignore
  }
}

export function useModuleGroupState(
  tenantId: string,
  userId: string,
  slot: string,
  moduleId: string,
): UseModuleGroupStateReturn {
  const storageKey = useMemo(
    () => buildKey(tenantId, userId, slot, moduleId),
    [tenantId, userId, slot, moduleId],
  );

  const [state, setState] = useState<ModuleGroupState>(() => loadState(storageKey));

  const persist = useCallback(
    (updater: (prev: ModuleGroupState) => ModuleGroupState) => {
      setState((prev) => {
        const next = updater(prev);
        saveState(storageKey, next);
        return next;
      });
    },
    [storageKey],
  );

  const toggleCollapsed = useCallback(
    () => persist((s) => ({ ...s, collapsed: !s.collapsed })),
    [persist],
  );

  const toggleHidden = useCallback(
    () => persist((s) => ({ ...s, hidden: !s.hidden })),
    [persist],
  );

  const setOrder = useCallback(
    (order: number) => persist((s) => ({ ...s, order })),
    [persist],
  );

  const setCustomWidth = useCallback(
    (customWidth: number) => persist((s) => ({ ...s, customWidth })),
    [persist],
  );

  return useMemo(
    () => ({
      ...state,
      toggleCollapsed,
      toggleHidden,
      setOrder,
      setCustomWidth,
    }),
    [state, toggleCollapsed, toggleHidden, setOrder, setCustomWidth],
  );
}
