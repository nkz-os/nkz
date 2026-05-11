import type {
  SlotWidgetDefinition,
  ModuleViewerSlots,
  NKZModuleRegistration,
} from '@nekazari/sdk';
import type { ModuleAccent, ModuleI18n } from './types';

interface ViewerSlotConfig {
  id: string;
  component: React.ComponentType<any>;
  priority?: number;
  showWhen?: SlotWidgetDefinition['showWhen'];
  defaultProps?: Record<string, any>;
}

export interface DefineModuleOptions {
  /** Module identifier — must match marketplace_modules.id */
  id: string;
  /** Human-readable name */
  displayName: string;
  /** Visual accent colors */
  accent: ModuleAccent;
  /** Semver range of host API versions this module is compatible with */
  hostApiVersion?: string;
  /** Viewer slot definitions */
  viewerSlots?: Partial<Record<string, ViewerSlotConfig[]>>;
  /** Standalone page component (lazy import) */
  main?: () => Promise<{ default: React.ComponentType<any> }>;
  /** React context provider shared across all viewer slots */
  provider?: React.ComponentType<{ children: React.ReactNode }>;
  /** i18n resource bundles keyed by language code */
  i18n?: ModuleI18n;
  /** Preconfigured API client path */
  api?: { basePath: string };
}

/**
 * Define a Nekazari module. Returns a configuration object that drives
 * IIFE registration, Vite config generation, i18n setup, and manifest generation.
 *
 * This is THE single entry point for module creation.
 *
 * @example
 * export default defineModule({
 *   id: 'my-module',
 *   displayName: 'My Module',
 *   accent: { base: '#3B82F6', soft: '#DBEAFE', strong: '#1D4ED8' },
 *   hostApiVersion: '^2.0.0',
 *   viewerSlots: { 'context-panel': [{ id: 'my-panel', component: MyPanel, priority: 10 }] },
 *   api: { basePath: '/api/my-module' },
 * });
 */
export function defineModule(options: DefineModuleOptions): DefineModuleOptions {
  // Validate required fields
  if (!options.id || !/^[a-z][a-z0-9-]*$/.test(options.id)) {
    throw new Error(
      `defineModule: id "${options.id}" must be lowercase alphanumeric with hyphens, starting with a letter`
    );
  }

  return options;
}

/**
 * Convert defineModule() options into the NKZRuntime registration payload
 * that window.__NKZ__.register() expects.
 */
export function toNKZRegistration(
  options: DefineModuleOptions,
): NKZModuleRegistration {
  const viewerSlots: ModuleViewerSlots = {};

  if (options.viewerSlots) {
    for (const [slotType, widgets] of Object.entries(options.viewerSlots)) {
      if (!widgets) continue;
      const defs: SlotWidgetDefinition[] = widgets.map((w) => ({
        id: w.id,
        moduleId: options.id,
        component: w.id,
        priority: w.priority ?? 50,
        localComponent: w.component,
        showWhen: w.showWhen,
        defaultProps: w.defaultProps,
      }));
      (viewerSlots as any)[slotType] = defs;
    }
  }

  return {
    id: options.id,
    version: '0.1.0',
    viewerSlots,
    provider: options.provider,
  };
}
