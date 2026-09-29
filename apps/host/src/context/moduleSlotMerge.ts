import type { ModuleDefinition } from './ModuleContext';

/**
 * /api/modules/me never carries viewerSlots: they are filled in client-side
 * once the federated remote is loaded. A module-list refresh must therefore
 * keep the slots already resolved for the same remote, or the viewer loses
 * that module's widgets until its page is visited again.
 */
export function carryOverViewerSlots(
  prev: ModuleDefinition[],
  next: ModuleDefinition[],
): ModuleDefinition[] {
  const byId = new Map(prev.map((m) => [m.id, m]));
  return next.map((m) => {
    if (m.viewerSlots) return m;
    const old = byId.get(m.id);
    if (!old?.viewerSlots || old.remoteEntry !== m.remoteEntry) return m;
    return { ...m, viewerSlots: old.viewerSlots };
  });
}
