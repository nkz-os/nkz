import { existsSync } from 'node:fs';
import { join } from 'node:path';

export type EntryStrategy = 'modern' | 'legacy';

/**
 * Detect which entry strategy a module uses.
 *
 * - "modern": src/Module.tsx with `export default defineModule(...)`.
 * - "legacy": src/moduleEntry.ts written by hand, must `export default`
 *   a defineModule result.
 *
 * If both files exist, modern wins (the legacy file is ignored).
 */
export function detectEntryStrategy(projectRoot: string): EntryStrategy {
  const hasModern = existsSync(join(projectRoot, 'src/Module.tsx'));
  const hasLegacy = existsSync(join(projectRoot, 'src/moduleEntry.ts'));

  if (hasModern) return 'modern';
  if (hasLegacy) return 'legacy';

  throw new Error(
    `[@nekazari/module-builder] Neither src/Module.tsx (modern) nor src/moduleEntry.ts (legacy) found in ${projectRoot}. ` +
      `Create one of them. The modern form is preferred.`,
  );
}

import type { ModuleDefinition } from '@nekazari/module-kit';

/**
 * Strip runtime-only fields from a ModuleDefinition and return a plain JSON
 * object suitable for writing as `dist/manifest.json`.
 *
 * The manifest is what api-gateway reads from MinIO when enforcing CSP-of-data:
 * it must be pure data (no functions, no React components).
 */
export function generateManifest(def: ModuleDefinition): Record<string, unknown> {
  const manifest: Record<string, unknown> = {
    id: def.id,
    displayName: def.displayName,
    version: def.version,
    hostApiVersion: def.hostApiVersion,
    description: def.description,
    accent: def.accent,
    icon: def.icon,
    route: def.route,
    navigation: def.navigation,
    api: def.api,
    requiredRoles: def.requiredRoles,
    requiredPlan: def.requiredPlan,
    data: def.data,
  };

  if (def.slots) {
    const slotsOut: Record<string, Array<Record<string, unknown>>> = {};
    for (const [slotType, entries] of Object.entries(def.slots)) {
      slotsOut[slotType] = (entries as Array<Record<string, unknown>>).map((entry) => {
        const stripped: Record<string, unknown> = { id: entry.id };
        if (entry.priority !== undefined) stripped.priority = entry.priority;
        if (entry.showWhen !== undefined) stripped.showWhen = entry.showWhen;
        if (entry.defaultProps !== undefined) stripped.defaultProps = entry.defaultProps;
        return stripped;
      });
    }
    manifest.slots = slotsOut;
  }

  if (def.i18n) {
    manifest.i18nLangs = Object.keys(def.i18n);
  }

  for (const key of Object.keys(manifest)) {
    if (manifest[key] === undefined) delete manifest[key];
  }

  return manifest;
}
