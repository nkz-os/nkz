/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 *
 * LayerRegistry — singleton keyed store of viewer layers.
 *
 * Modules declare their viewer layers via `defineModule({ viewerLayers: [...] })`
 * (see @nekazari/module-kit), which calls `registerViewerLayers()` on mount. The
 * host's unified Layers panel subscribes to this registry to render a single
 * toggle/opacity/status UI for every layer across every module.
 *
 * HARD CUT (2026-07-12, plan §B): there is no fallback to module-local layer
 * contexts. A layer that isn't registered here does not exist for toggle
 * purposes — modules must migrate their rendering to read from this registry
 * (via `useViewerLayer`) instead of keeping their own local toggle state.
 *
 * @nekazari/sdk is a Module Federation shared singleton (never bundled per
 * module — see PLATFORM_CONVENTIONS.md), so this module-level instance is the
 * same object in memory for the host and every remote module at runtime.
 */

export type ViewerLayerStatus = 'idle' | 'loading' | 'ready' | 'empty' | 'error' | 'noSelection';

/** What a module declares at registration time (`defineModule({ viewerLayers })`). */
export interface ViewerLayerDecl {
  id: string;
  titleKey: string;
  group?: string;
  supportsOpacity?: boolean;
  defaultVisible?: boolean;
}

/** A registry entry: the module's declaration plus live, mutable toggle state. */
export interface ViewerLayerEntry {
  id: string;
  moduleId: string;
  titleKey: string;
  group?: string;
  supportsOpacity: boolean;
  defaultVisible: boolean;
  visible: boolean;
  opacity: number;
  status: ViewerLayerStatus;
}

/**
 * Storage backend for persisting visible/opacity across sessions. The host
 * supplies a localStorage-backed adapter (typically namespaced per tenant);
 * the registry defaults to an in-memory adapter (nothing survives a reload).
 */
export interface ViewerLayerStorageAdapter {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

interface PersistedLayerState {
  visible: boolean;
  opacity: number;
}

const STORAGE_KEY_PREFIX = 'nkz.viewerLayer.';
const DEFAULT_OPACITY = 100;

function isPersistedLayerState(value: unknown): value is PersistedLayerState {
  return (
    !!value &&
    typeof value === 'object' &&
    typeof (value as PersistedLayerState).visible === 'boolean' &&
    typeof (value as PersistedLayerState).opacity === 'number'
  );
}

function createInMemoryStorageAdapter(): ViewerLayerStorageAdapter {
  const store = new Map<string, string>();
  return {
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, value) => {
      store.set(key, value);
    },
  };
}

function clampOpacity(opacity: number): number {
  if (Number.isNaN(opacity)) return DEFAULT_OPACITY;
  return Math.min(100, Math.max(0, opacity));
}

const EMPTY_SNAPSHOT: readonly ViewerLayerEntry[] = Object.freeze([]);

class LayerRegistryImpl {
  private layers = new Map<string, ViewerLayerEntry>();
  private byModule = new Map<string, Set<string>>();
  private listeners = new Set<() => void>();
  private storage: ViewerLayerStorageAdapter = createInMemoryStorageAdapter();
  private snapshot: readonly ViewerLayerEntry[] = EMPTY_SNAPSHOT;
  private warnedUnknownIds = new Set<string>();

  /** Configure the persistence backend (e.g. a localStorage adapter from the host). */
  setStorageAdapter = (adapter: ViewerLayerStorageAdapter): void => {
    this.storage = adapter;
  };

  private readPersisted(id: string): Partial<PersistedLayerState> {
    try {
      const raw = this.storage.getItem(STORAGE_KEY_PREFIX + id);
      if (!raw) return {};
      const parsed: unknown = JSON.parse(raw);
      return isPersistedLayerState(parsed) ? parsed : {};
    } catch {
      return {};
    }
  }

  private writePersisted(id: string, state: PersistedLayerState): void {
    try {
      this.storage.setItem(STORAGE_KEY_PREFIX + id, JSON.stringify(state));
    } catch {
      // Storage may be unavailable (quota exceeded, private browsing) — non-fatal.
    }
  }

  /**
   * Register the viewer layers owned by `moduleId`. Idempotent per module:
   * calling this again for the same `moduleId` replaces its previous entries
   * (layers dropped from the new list are removed; layers kept restore their
   * visible/opacity from storage rather than resetting to declared defaults).
   */
  registerViewerLayers = (moduleId: string, decls: ViewerLayerDecl[]): void => {
    const previousIds = this.byModule.get(moduleId);
    if (previousIds) {
      for (const id of previousIds) {
        this.layers.delete(id);
      }
    }

    const nextIds = new Set<string>();
    for (const decl of decls) {
      const persisted = this.readPersisted(decl.id);
      const defaultVisible = decl.defaultVisible ?? true;
      const entry: ViewerLayerEntry = {
        id: decl.id,
        moduleId,
        titleKey: decl.titleKey,
        group: decl.group,
        supportsOpacity: decl.supportsOpacity ?? false,
        defaultVisible,
        visible: persisted.visible ?? defaultVisible,
        opacity: clampOpacity(persisted.opacity ?? DEFAULT_OPACITY),
        status: 'idle',
      };
      this.layers.set(decl.id, entry);
      nextIds.add(decl.id);
    }
    this.byModule.set(moduleId, nextIds);

    this.notify();
  };

  /** Look up a single layer entry by id. Returns undefined if not registered. */
  getLayer = (id: string): ViewerLayerEntry | undefined => {
    return this.layers.get(id);
  };

  /** Snapshot of every registered layer, stable across calls until the next change. */
  getAllLayers = (): readonly ViewerLayerEntry[] => {
    return this.snapshot;
  };

  setVisible = (id: string, visible: boolean): void => {
    const entry = this.layers.get(id);
    if (!entry) return;
    if (entry.visible === visible) return;
    this.layers.set(id, { ...entry, visible });
    this.writePersisted(id, { visible, opacity: entry.opacity });
    this.notify();
  };

  setOpacity = (id: string, opacity: number): void => {
    const entry = this.layers.get(id);
    if (!entry) return;
    const clamped = clampOpacity(opacity);
    if (entry.opacity === clamped) return;
    this.layers.set(id, { ...entry, opacity: clamped });
    this.writePersisted(id, { visible: entry.visible, opacity: clamped });
    this.notify();
  };

  setStatus = (id: string, status: ViewerLayerStatus): void => {
    const entry = this.layers.get(id);
    if (!entry) return;
    if (entry.status === status) return;
    this.layers.set(id, { ...entry, status });
    this.notify();
  };

  /**
   * Subscribe to any change (register/replace, visible, opacity, status).
   * Compatible with React's `useSyncExternalStore`.
   */
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  /**
   * True only the first time it's called for a given id since the last
   * `reset()` — lets callers (e.g. `useViewerLayer`) emit a one-time warning
   * for an unknown layer id without spamming the console on every render.
   */
  consumeUnknownWarning = (id: string): boolean => {
    if (this.warnedUnknownIds.has(id)) return false;
    this.warnedUnknownIds.add(id);
    return true;
  };

  /**
   * Clear all registered layers, warning state, and the storage adapter
   * (back to the in-memory default). Primarily for tests and full
   * tenant/session resets.
   */
  reset = (): void => {
    this.layers = new Map();
    this.byModule = new Map();
    this.warnedUnknownIds = new Set();
    this.storage = createInMemoryStorageAdapter();
    this.notify();
  };

  private notify(): void {
    this.snapshot = Object.freeze(Array.from(this.layers.values()));
    for (const listener of this.listeners) {
      listener();
    }
  }
}

/** The singleton viewer-layer registry. Shared across the host and every module. */
export const LayerRegistry = new LayerRegistryImpl();

/**
 * Register (or idempotently replace) the viewer layers owned by `moduleId`.
 * Thin function wrapper around `LayerRegistry.registerViewerLayers` — the
 * shape `defineModule({ viewerLayers })` in @nekazari/module-kit calls into.
 */
export function registerViewerLayers(moduleId: string, layers: ViewerLayerDecl[]): void {
  LayerRegistry.registerViewerLayers(moduleId, layers);
}
