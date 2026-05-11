import type { ModuleViewerSlots } from './slots';

/** Registration payload a module passes to window.__NKZ__.register() */
export interface NKZModuleRegistration {
  /** Module ID — must match the ID in marketplace_modules */
  id: string;
  /** Viewer slot definitions */
  viewerSlots?: ModuleViewerSlots;
  /** Optional React provider for module-level context shared across slots */
  provider?: React.ComponentType<{ children: React.ReactNode }>;
  /** Optional main page component for standalone routing (/modules/<id>) */
  main?: React.ComponentType<any>;
  /** Module version (semver) */
  version?: string;
}

/**
 * API version contract declared by a module.
 * Checked by the host before script injection.
 */
export interface ModuleApiContract {
  /** Semver range of host API versions this module is compatible with */
  hostApiVersion: string;
  /** This module's own API version (semver) */
  moduleApiVersion: string;
  /** Minimum SDK version required */
  sdkVersion: string;
}

/** Result of a module compatibility check */
export interface ModuleCompatibilityResult {
  compatible: boolean;
  reason?: string;
  contract?: ModuleApiContract;
  hostVersion: string;
}
