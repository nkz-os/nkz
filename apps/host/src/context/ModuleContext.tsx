// =============================================================================
// Module Context — Runtime Module Registry (IIFE Script Injection)
// =============================================================================
// Manages loading and state of modules for the tenant.
// Remote modules are loaded via <script> tags (IIFE bundles) that self-register
// through window.__NKZ__.register(). See utils/nkzRuntime.ts.

import React, { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';
import { NekazariClient, type ModuleApiContract } from '@nekazari/sdk';
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
  version: string;
  routePath: string;
  label: string;
  // Local modules (bundled) - these fields are optional
  isLocal?: boolean;
  // Remote modules - required if isLocal is false
  remoteEntry?: string;
  scope?: string;
  module?: string;
  // Module classification
  moduleType?: 'CORE' | 'ADDON_FREE' | 'ADDON_PAID' | 'ADDON_ENTERPRISE';
  // Optional metadata
  icon?: string;
  metadata?: Record<string, any>;
  tenantConfig?: Record<string, any>;
  navigationItems?: Array<{
    path: string;
    label: string;
    icon?: string;
    roles?: string[];
    adminOnly?: boolean;
  }>;
  // Slot system: widgets that this module contributes to the unified viewer
  viewerSlots?: ModuleViewerSlots;
  // API contract for host compatibility checking (progressive feature)
  apiContract?: ModuleApiContract;
}

/**
 * Validates and sanitizes a module definition to prevent sidebar crashes.
 * Returns null if the module is invalid.
 */
const validateAndSanitizeModule = (module: any): ModuleDefinition | null => {
  // Must be an object
  if (!module || typeof module !== 'object') {
    console.warn('[ModuleContext] Invalid module: not an object', module);
    return null;
  }

  // Required fields must be strings
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

  // Sanitize and provide defaults for optional fields
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
    icon: typeof module.icon === 'string' ? module.icon : undefined,
    metadata: module.metadata && typeof module.metadata === 'object' ? module.metadata : undefined,
    tenantConfig: module.tenantConfig && typeof module.tenantConfig === 'object' ? module.tenantConfig : undefined,
    navigationItems: Array.isArray(module.navigationItems) ? module.navigationItems : undefined,
    viewerSlots: module.viewerSlots && typeof module.viewerSlots === 'object' ? module.viewerSlots : undefined,
    // API contract from backend response (camelCase) or manifest.json (snake_case)
    apiContract: (() => {
      const raw = module.apiContract ?? module.api_contract;
      return raw && typeof raw === 'object' ? raw : undefined;
    })(),
  };
};


interface ModuleContextType {
  modules: ModuleDefinition[];
  isLoading: boolean;
  error: Error | null;
  refresh: () => Promise<void>;
  getModuleById: (id: string) => ModuleDefinition | undefined;
  getModuleByRoute: (path: string) => ModuleDefinition | undefined;
  visibilityRules: Record<string, { hiddenRoles: string[] }>;
  incompatibleModules: ReadonlyMap<string, string>;
  /** Inject the IIFE <script> for a module on demand (idempotent — no-op if already loaded). */
  ensureModuleScript: (id: string, bundleUrl: string) => Promise<void>;
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
  // Use config API base URL if not explicitly provided
  const effectiveApiBaseUrl = apiBaseUrl || getConfig().api.baseUrl || '/api';
  const { isAuthenticated, getToken, tenantId } = useAuth();
  const [modules, setModules] = useState<ModuleDefinition[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [visibilityRules, setVisibilityRules] = useState<Record<string, { hiddenRoles: string[] }>>({});
  const [incompatibleModules, setIncompatibleModules] = useState<Map<string, string>>(new Map());

  const loadModules = useCallback(async () => {
    if (!isAuthenticated || !tenantId) {
      setModules([]);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      // Load local modules from manifest first
      // NOTE: This file may not exist in production (modules come from backend)
      // If it fails, we silently continue - remote modules will be loaded from backend
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
        // Load tenant-specific visibility rules (UI only)
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
          // Visibility is an optional enhancement; log and continue on failure
          console.warn('[ModuleContext] Failed to load module visibility rules:', visibilityError);
        }
      } catch (remoteError) {
        console.warn('[ModuleContext] Failed to load remote modules:', remoteError);
      }

      // Merge modules - for local modules, use local definition (which has viewerSlots)
      // Remote modules override local only if they're not local modules
      const moduleMap = new Map<string, ModuleDefinition>();

      // First add local modules from registry (these have viewerSlots)
      try {
        const { LOCAL_MODULE_REGISTRY } = await import('@/modules/registry');
        Object.values(LOCAL_MODULE_REGISTRY).forEach(m => {
          moduleMap.set(m.id, m);
        });
      } catch {
        // Local module registry not available
      }

      // Then add local modules from manifest (if any)
      localModules.forEach(m => {
        // Only add if not already in map (registry takes precedence)
        if (!moduleMap.has(m.id)) {
          moduleMap.set(m.id, m);
        }
      });

      // Finally add remote modules (but don't override local modules that have viewerSlots)
      // Validate each module before adding to prevent sidebar crashes
      remoteModules.forEach(rawModule => {
        // Validate and sanitize the module
        const m = validateAndSanitizeModule(rawModule);
        if (!m) {
          console.warn('[ModuleContext] Skipping invalid remote module:', rawModule);
          return; // Skip invalid modules
        }

        const existing = moduleMap.get(m.id);
        // If it's a local module with viewerSlots, keep the local version
        if (existing?.isLocal && existing?.viewerSlots) {
          // Merge remote metadata but keep local viewerSlots
          moduleMap.set(m.id, {
            ...existing,
            ...m,
            viewerSlots: existing.viewerSlots, // Keep local viewerSlots
          });
        } else {
          // For remote modules or local modules without slots, use remote version
          moduleMap.set(m.id, m);
        }
      });

      // =============================================================================
      // Check API version contracts before loading module scripts
      // =============================================================================
      const incompatibleReasons = new Map<string, string>();

      for (const [modId, modDef] of moduleMap.entries()) {
        if (!modDef.remoteEntry && modDef.isLocal) continue;
        const apiContract = modDef.apiContract;
        if (!apiContract) {
          // Module without a contract: log a warning but allow loading (progressive feature)
          console.warn(
            `[ModuleContext] Module "${modId}" does not declare an API contract. ` +
            'Consider adding api_contract to manifest.json for compatibility guarantees.'
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
      // Subscribe to IIFE registrations before any script loads
      // =============================================================================
      // Each IIFE calls window.__NKZ__.register({ id, viewerSlots }) on execution.
      // Scripts are injected ON DEMAND (lazy) by RemoteModuleLoader via
      // ensureModuleScript(), not eagerly at startup.
      // The listener stays active for the entire lifecycle to handle late registrations.
      window.__NKZ__?.onRegister((registeredId, registration) => {
        const existingModule = moduleMap.get(registeredId);
        if (existingModule) {
          if (registration.viewerSlots) {
            existingModule.viewerSlots = registration.viewerSlots;
          }
          setModules(prevModules =>
            prevModules.map(m =>
              m.id === registeredId
                ? { ...m, viewerSlots: registration.viewerSlots || m.viewerSlots }
                : m
            )
          );
        }
      });

      // Set modules without waiting for script loading — scripts are lazy
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

  // Load modules when authenticated or tenant changes
  useEffect(() => {
    loadModules();
  }, [loadModules]);

  const getModuleById = useCallback((id: string): ModuleDefinition | undefined => {
    return modules.find(m => m.id === id);
  }, [modules]);

  const getModuleByRoute = useCallback((path: string): ModuleDefinition | undefined => {
    return modules.find(m => m.routePath === path || path.startsWith(m.routePath));
  }, [modules]);

  const ensureModuleScript = useCallback(async (id: string, bundleUrl: string) => {
    const { loadModuleScript } = await import('@/utils/moduleLoader');
    await loadModuleScript(bundleUrl, id);
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
    ensureModuleScript,
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

