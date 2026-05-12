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
 * Convert a ModuleDefinition to the legacy NKZModuleRegistration shape that
 * `window.__NKZ__.register()` expects. Used by the generated moduleEntry.gen.ts.
 *
 * Note: this is internal — modules should NOT call this directly. The host
 * runtime invokes it via @nekazari/module-builder's codegen.
 */
export function toNKZRegistration(def: ModuleDefinition): {
  id: string;
  version: string;
  viewerSlots?: unknown;
  main?: unknown;
} {
  return {
    id: def.id,
    version: def.version ?? '0.0.0',
    viewerSlots: def.slots,
    main: def.main,
  };
}
