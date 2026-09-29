import type { ModuleDefinition } from './ModuleContext';

/** User's explicit on/off choice per module id in the viewer's Layers panel. */
export type ActivationChoices = Record<string, boolean>;

type ActivatableModule = Pick<ModuleDefinition, 'id' | 'isLocal' | 'viewerSlots' | 'viewerDefaultActive'>;

const storageKey = (tenantId: string) => `nkz.viewer.moduleActive.${tenantId}`;

/**
 * Modules whose widgets the viewer renders. A module takes part only if it has
 * viewer slots (or is bundled locally); its state is the user's choice for this
 * session, else the default the module declares, else active.
 */
export function resolveActiveModules(
  modules: ActivatableModule[],
  choices: ActivationChoices,
  localModuleIds: ReadonlySet<string>,
): Set<string> {
  const active = new Set<string>(['core']);
  for (const m of modules) {
    const eligible = m.isLocal || localModuleIds.has(m.id) || !!m.viewerSlots;
    if (!eligible) continue;
    const on = choices[m.id] ?? m.viewerDefaultActive !== false;
    if (on) active.add(m.id);
  }
  return active;
}

/** Session-scoped choices; any storage failure means "no choices yet". */
export function loadChoices(tenantId: string): ActivationChoices {
  try {
    const raw = sessionStorage.getItem(storageKey(tenantId));
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const out: ActivationChoices = {};
    for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof v === 'boolean') out[k] = v;
    }
    return out;
  } catch {
    return {};
  }
}

export function saveChoices(tenantId: string, choices: ActivationChoices): void {
  try {
    sessionStorage.setItem(storageKey(tenantId), JSON.stringify(choices));
  } catch {
    /* private mode or quota: choices simply last until reload */
  }
}
