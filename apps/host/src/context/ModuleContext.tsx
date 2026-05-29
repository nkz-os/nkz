// =============================================================================
// Module Context — Module Federation 2.0 Runtime
// =============================================================================
// Manages loading and state of modules for the tenant. Remote modules are
// loaded via the Module Federation runtime: registerRemotes() declares the
// available remotes (URLs from the backend), and RemoteModuleLoader calls
// loadRemote('<id>/Module') on demand to retrieve the validated definition.

import React, { createContext, useContext, useState, useEffect, useCallback, useRef, ReactNode } from 'react';
import { registerRemotes, loadRemote } from '@module-federation/runtime';
import { toNKZRegistration } from '@nekazari/module-kit';
import { QueryClient } from '@tanstack/react-query';
import { NekazariClient, type ModuleApiContract, type NKZModuleRegistration } from '@nekazari/sdk';
import type { ModuleViewerSlots } from '@nekazari/sdk';
import { useAuth } from '@/context/KeycloakAuthContext';
import { getConfig } from '@/config/environment';
import { checkModuleContract } from '@/utils/moduleContract';

// =============================================================================
// Module Definition
// =============================================================================

export interface ModuleDefinition {
  id: string;
  name: string;
  displayName: string;
  description?: string;
  version: string;
  routePath: string;
  label: string;
  // Local modules (bundled) - these fields are optional
  isLocal?: boolean;
  // Remote modules - required if isLocal is false
  // For Federation modules: URL to mf-manifest.json on MinIO.
  remoteEntry?: string;
  scope?: string;
  module?: string;
  // Module classification
  moduleType?: 'CORE' | 'ADDON_FREE' | 'ADDON_PAID' | 'ADDON_ENTERPRISE';
  // Optional metadata
  icon?: string;
  icon_url?: string;
  metadata?: Record<string, any>;
  tenantConfig?: Record<string, any>;
  navigationItems?: Array<{
    path: string;
    label: string;
    icon?: string;
    roles?: string[];
    adminOnly?: boolean;
  }>;
  // Slot system: widgets that this module contributes to the unified viewer.
  // Pre-load: the metadata-only shape from the manifest. Post-load (after
  // RemoteModuleLoader has called loadRemote): the full definition with
  // localComponent refs filled in.
  viewerSlots?: ModuleViewerSlots;
  // API contract for host compatibility checking (progressive feature)
  apiContract?: ModuleApiContract;
  // Module's backend api details
  api?: { basePath?: string };
  // Shared QueryClient for this module's components
  queryClient?: QueryClient;
}

/**
 * Validates and sanitizes a module definition to prevent sidebar crashes.
 * Returns null if the module is invalid.
 */
const validateAndSanitizeModule = (module: any): ModuleDefinition | null => {
  if (!module || typeof module !== 'object') {
    console.warn('[ModuleContext] Invalid module: not an object', module);
    return null;
  }

  const id = typeof module.id === 'string' ? module.id.trim() : '';
  const routePath = typeof module.routePath === 'string' ? module.routePath.trim() : '';

  if (!id) {
    console.warn('[ModuleContext] Invalid module: missing id', module);
    return null;
  }

  if (!routePath) {
    console.warn('[ModuleContext] Invalid module: missing routePath for module', id);
    return null;
  }

  return {
    id,
    routePath,
    name: typeof module.name === 'string' ? module.name : id,
    displayName: typeof module.displayName === 'string' ? module.displayName : (module.name || id),
    version: typeof module.version === 'string' ? module.version : '1.0.0',
    label: typeof module.label === 'string' ? module.label : (module.displayName || module.name || id),
    isLocal: Boolean(module.isLocal),
    remoteEntry: typeof module.remoteEntry === 'string' ? module.remoteEntry : undefined,
    scope: typeof module.scope === 'string' ? module.scope : undefined,
    module: typeof module.module === 'string' ? module.module : undefined,
    description: typeof module.description === 'string' ? module.description : undefined,
    icon: typeof module.icon === 'string' ? module.icon : undefined,
    icon_url: typeof module.icon_url === 'string' ? module.icon_url : undefined,
    metadata: module.metadata && typeof module.metadata === 'object' ? module.metadata : undefined,
    tenantConfig: module.tenantConfig && typeof module.tenantConfig === 'object' ? module.tenantConfig : undefined,
    navigationItems: Array.isArray(module.navigationItems) ? module.navigationItems : undefined,
    viewerSlots: module.viewerSlots && typeof module.viewerSlots === 'object' ? module.viewerSlots : undefined,
    api: module.api && typeof module.api === 'object' ? module.api : undefined,
    apiContract: (() => {
      const raw = module.apiContract ?? module.api_contract;
      return raw && typeof raw === 'object' ? raw : undefined;
    })(),
  };
};


/** Federation alias must be a valid JS identifier; mirrors module-builder's fedName. */
const toFederationAlias = (id: string): string => id.replace(/[^a-zA-Z0-9_]/g, '_');

interface ModuleContextType {
  modules: ModuleDefinition[];
  isLoading: boolean;
  error: Error | null;
  refresh: () => Promise<void>;
  getModuleById: (id: string) => ModuleDefinition | undefined;
  getModuleByRoute: (path: string) => ModuleDefinition | undefined;
  visibilityRules: Record<string, { hiddenRoles: string[] }>;
  incompatibleModules: ReadonlyMap<string, string>;
  /**
   * Federation alias for a module id. Use as the first segment of loadRemote
   * paths: `loadRemote(\`${aliasFor(id)}/Module\`)`.
   */
  aliasFor: (id: string) => string;
  /**
   * Apply a registration produced by toNKZRegistration() after a remote has
   * been loaded. Propagates viewerSlots so the slot system can render widgets.
   */
  applyModuleRegistration: (id: string, registration: NKZModuleRegistration) => void;
}

const ModuleContext = createContext<ModuleContextType | undefined>(undefined);

interface ModuleProviderProps {
  children: ReactNode;
  apiBaseUrl?: string;
}

export const ModuleProvider: React.FC<ModuleProviderProps> = ({
  children,
  apiBaseUrl
}) => {
  const effectiveApiBaseUrl = apiBaseUrl || getConfig().api.baseUrl || '/api';
  const { isAuthenticated, getToken, tenantId } = useAuth();
  const [modules, setModules] = useState<ModuleDefinition[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [visibilityRules, setVisibilityRules] = useState<Record<string, { hiddenRoles: string[] }>>({});
  const [incompatibleModules, setIncompatibleModules] = useState<Map<string, string>>(new Map());
  const registeredFingerprintRef = useRef<string>('');

  const loadModules = useCallback(async () => {
    if (!isAuthenticated || !tenantId) {
      setModules([]);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      // Load local modules from manifest first
      let localModules: ModuleDefinition[] = [];
      try {
        const manifestResponse = await fetch('/modules-manifest.json', {
          headers: {
            'Accept': 'application/json',
            'Cache-Control': 'no-cache'
          }
        });
        if (manifestResponse.ok) {
          const contentType = manifestResponse.headers.get('content-type');
          if (contentType && contentType.includes('application/json')) {
            const manifest = await manifestResponse.json();
            localModules = (manifest.modules || []).map((m: any) => ({
              ...m,
              isLocal: true,
            }));
          }
        }
      } catch {
        // Manifest is optional — modules come from backend
      }

      // Load remote modules from backend
      let remoteModules: ModuleDefinition[] = [];
      let visibility: Record<string, { hiddenRoles: string[] }> = {};
      try {
        const client = new NekazariClient({
          baseUrl: effectiveApiBaseUrl,
          getToken: getToken,
          getTenantId: () => tenantId,
        });
        const data = await client.get<ModuleDefinition[]>('/api/modules/me');
        remoteModules = Array.isArray(data) ? data : [];
        try {
          const visibilityResponse = await client.get<Record<string, { hiddenRoles?: string[] }>>(
            '/api/modules/visibility'
          );
          if (visibilityResponse && typeof visibilityResponse === 'object') {
            const normalised: Record<string, { hiddenRoles: string[] }> = {};
            Object.entries(visibilityResponse).forEach(([moduleId, cfg]) => {
              if (!moduleId || !cfg) return;
              const rawHidden = Array.isArray(cfg.hiddenRoles) ? cfg.hiddenRoles : [];
              normalised[moduleId] = { hiddenRoles: rawHidden.filter((r): r is string => typeof r === 'string') };
            });
            visibility = normalised;
          }
        } catch (visibilityError) {
          console.warn('[ModuleContext] Failed to load module visibility rules:', visibilityError);
        }
      } catch (remoteError) {
        console.warn('[ModuleContext] Failed to load remote modules:', remoteError);
      }

      const moduleMap = new Map<string, ModuleDefinition>();

      try {
        const { LOCAL_MODULE_REGISTRY } = await import('@/modules/registry');
        Object.values(LOCAL_MODULE_REGISTRY).forEach(m => {
          moduleMap.set(m.id, m);
        });
      } catch {
        // Local module registry not available
      }

      localModules.forEach(m => {
        if (!moduleMap.has(m.id)) {
          moduleMap.set(m.id, m);
        }
      });

      remoteModules.forEach(rawModule => {
        const m = validateAndSanitizeModule(rawModule);
        if (!m) {
          console.warn('[ModuleContext] Skipping invalid remote module:', rawModule);
          return;
        }

        const existing = moduleMap.get(m.id);
        
        m.queryClient = existing?.queryClient ?? new QueryClient({
          defaultOptions: { queries: { staleTime: 30000, retry: 1, refetchOnWindowFocus: false } }
        });

        if (existing?.isLocal && existing?.viewerSlots) {
          moduleMap.set(m.id, {
            ...existing,
            ...m,
            viewerSlots: existing.viewerSlots,
            queryClient: m.queryClient
          });
        } else {
          moduleMap.set(m.id, m);
        }
      });

      // =============================================================================
      // Check API version contracts before registering federated remotes
      // =============================================================================
      const incompatibleReasons = new Map<string, string>();

      for (const [modId, modDef] of moduleMap.entries()) {
        if (!modDef.remoteEntry && modDef.isLocal) continue;
        const apiContract = modDef.apiContract;
        if (!apiContract) {
          console.debug(
            `[ModuleContext] Module "${modId}" does not declare an API contract. ` +
            'Consider adding api_contract to manifest.json for compatibility guarantees.',
          );
          continue;
        }
        const result = checkModuleContract(apiContract);
        if (!result.compatible) {
          incompatibleReasons.set(modId, result.reason || 'Unknown incompatibility reason');
          moduleMap.delete(modId);
          console.warn(
            `[ModuleContext] Module "${modId}" is incompatible with host: ${result.reason}`
          );
        }
      }
      setIncompatibleModules(incompatibleReasons);

      // =============================================================================
      // Register federated remotes
      // =============================================================================
      // Each remote is keyed by its alias (sanitized id). The entry URL must
      // point to the federation manifest (mf-manifest.json) emitted by
      // @nekazari/module-builder. Registration is cheap — no fetch happens
      // until loadRemote() is called.
      const remotesToRegister = Array.from(moduleMap.values())
        .filter(m => m.remoteEntry && !m.isLocal)
        .map(m => ({
          name: toFederationAlias(m.id),
          alias: toFederationAlias(m.id),
          entry: m.remoteEntry!,
          type: 'module' as const,
        }));

      // Avoid re-registering identical remotes — the federation runtime warns
      // with "already registered" when force:true replaces unchanged entries.
      const nextFingerprint = remotesToRegister
        .map(r => `${r.name}:${r.entry}`)
        .sort()
        .join(',');
      if (remotesToRegister.length > 0 && nextFingerprint !== registeredFingerprintRef.current) {
        try {
          registerRemotes(remotesToRegister, { force: true });
          registeredFingerprintRef.current = nextFingerprint;
        } catch (regError) {
          console.error('[ModuleContext] Failed to register federated remotes:', regError);
        }
      }

      setModules(Array.from(moduleMap.values()));
      setVisibilityRules(visibility);
    } catch (err) {
      const error = err instanceof Error ? err : new Error('Failed to load modules');
      console.error('[ModuleContext] Error loading modules:', error);
      setError(error);
      setModules([]);
    } finally {
      setIsLoading(false);
    }
  }, [isAuthenticated, tenantId, getToken, effectiveApiBaseUrl]);

  useEffect(() => {
    loadModules();
  }, [loadModules]);

  // ===========================================================================
  // Eager preload of remote modules so their viewerSlots are populated before
  // the user visits the module's own route. Without this, federated modules
  // do not appear in the Unified Viewer until they have been opened at least
  // once — the SlotRegistry activation gate keys off `viewerSlots`, which is
  // only filled in by RemoteModuleLoader's `loadRemote` call.
  // ===========================================================================
  const preloadedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    preloadedRef.current = new Set();
    registeredFingerprintRef.current = '';
  }, [tenantId]);

  useEffect(() => {
    const pending = modules.filter(
      (m) =>
        !m.isLocal &&
        m.remoteEntry &&
        !m.viewerSlots &&
        !preloadedRef.current.has(m.id),
    );
    if (pending.length === 0) return;

    pending.forEach((m) => preloadedRef.current.add(m.id));

    pending.forEach(async (m) => {
      try {
        const alias = toFederationAlias(m.id);
        const exposed = await loadRemote<{ default?: unknown }>(`${alias}/Module`);
        if (!exposed) return;
        const moduleDef = (exposed as { default?: unknown }).default ?? exposed;
        if (!moduleDef || typeof moduleDef !== 'object') return;
        const registration = toNKZRegistration(
          moduleDef as Parameters<typeof toNKZRegistration>[0],
        );
        const slots = registration.viewerSlots;
        if (!slots || Object.keys(slots).length === 0) return;
        setModules((prev) =>
          prev.map((mod) =>
            mod.id === m.id ? { ...mod, viewerSlots: slots } : mod,
          ),
        );
      } catch (err) {
        console.warn(`[ModuleContext] Slot preload failed for module "${m.id}":`, err);
      }
    });
  }, [modules]);

  const getModuleById = useCallback((id: string): ModuleDefinition | undefined => {
    return modules.find(m => m.id === id);
  }, [modules]);

  const getModuleByRoute = useCallback((path: string): ModuleDefinition | undefined => {
    return modules.find(m => m.routePath === path || path.startsWith(m.routePath));
  }, [modules]);

  const aliasFor = useCallback((id: string): string => toFederationAlias(id), []);

  const applyModuleRegistration = useCallback((id: string, registration: NKZModuleRegistration) => {
    if (!registration.viewerSlots) return;
    setModules(prev =>
      prev.map(m =>
        m.id === id
          ? { ...m, viewerSlots: registration.viewerSlots }
          : m
      )
    );
  }, []);

  const value: ModuleContextType = {
    modules,
    isLoading,
    error,
    refresh: loadModules,
    getModuleById,
    getModuleByRoute,
    visibilityRules,
    incompatibleModules,
    aliasFor,
    applyModuleRegistration,
  };

  return (
    <ModuleContext.Provider value={value}>
      {children}
    </ModuleContext.Provider>
  );
};

export const useModules = (): ModuleContextType => {
  const context = useContext(ModuleContext);
  if (context === undefined) {
    throw new Error('useModules must be used within a ModuleProvider');
  }
  return context;
};
