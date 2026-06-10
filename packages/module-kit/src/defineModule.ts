import type { NKZModuleRegistration, ModuleViewerSlots, SlotWidgetDefinition } from '@nekazari/sdk';
import type * as React from 'react';
import { ModuleDefinitionSchema, type ModuleDefinition } from './schema';

/**
 * Define a Nekazari module. Returns the validated configuration object.
 *
 * The returned object is consumed by:
 *   1. @nekazari/module-builder — to generate the IIFE entry and manifest.json
 *   2. The host runtime — to register routes, slots, navigation, permissions
 *   3. `nkz dev` — to wire mocks and HMR
 *
 * @example
 * export default defineModule({
 *   id: 'soil-health',
 *   displayName: 'Soil Health',
 *   hostApiVersion: '^2.0.0',
 *   accent: { base: '#A16207', soft: '#FEF3C7', strong: '#713F12' },
 *   route: '/soil-health',
 *   navigation: { section: 'modules', priority: 60 },
 *   api: { basePath: '/api/soil-health' },
 * });
 */
export function defineModule(options: ModuleDefinition): ModuleDefinition {
  const result = ModuleDefinitionSchema.safeParse(options);
  if (!result.success) {
    const issues = result.error.issues
      .map((i) => `  - ${i.path.join('.') || '<root>'}: ${i.message}`)
      .join('\n');
    throw new Error(`defineModule: invalid module definition\n${issues}`);
  }
  return result.data;
}

export type { ModuleDefinition };

/**
 * Convert a validated ModuleDefinition into the runtime NKZModuleRegistration
 * shape that `window.__NKZ__.register()` accepts.
 *
 * Intended for use by @nekazari/module-builder's generated moduleEntry.gen.ts.
 * Modules normally do not need to call this directly — they `export default
 * defineModule(...)` and the builder wires the registration.
 */
export function toNKZRegistration(def: ModuleDefinition): NKZModuleRegistration {
  const viewerSlots: ModuleViewerSlots = {};

  if (def.slots) {
    for (const [slotType, entries] of Object.entries(def.slots)) {
      if (!entries) continue;
      const widgets: SlotWidgetDefinition[] = entries.map((entry) => {
        // Legacy shape: { component: '<string-name>', localComponent: ReactComp }
        // Modern shape: { component: ReactComp } (localComponent absent)
        const legacyLocal = (entry as { localComponent?: unknown }).localComponent as
          | (React.ComponentType<any> & { displayName?: string; name?: string })
          | undefined;
        const localRef =
          legacyLocal ??
          (entry.component as React.ComponentType<any> & { displayName?: string; name?: string });
        const componentName =
          typeof entry.component === 'string'
            ? entry.component
            : localRef?.displayName || localRef?.name || entry.id;
        return {
          id: entry.id,
          moduleId: (entry as { moduleId?: string }).moduleId ?? def.id,
          component: componentName,
          priority: entry.priority ?? 50,
          showWhen: entry.showWhen,
          defaultProps: entry.defaultProps,
          localComponent: localRef,
        };
      });
      (viewerSlots as Record<string, SlotWidgetDefinition[]>)[slotType] = widgets;
    }
  }

  return {
    id: def.id,
    version: def.version,
    viewerSlots,
    main: def.main as React.ComponentType<any> | undefined,
  };
}
