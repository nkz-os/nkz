// =============================================================================
// Viewer Layer Storage — tenant-namespaced localStorage adapter
// =============================================================================
// Backs @nekazari/sdk's LayerRegistry persistence (visible/opacity per layer)
// with the browser's localStorage, namespaced per tenant so that switching
// tenants (or different users on the same browser) never leaks/collides on
// layer toggle state.

import type { ViewerLayerStorageAdapter } from '@nekazari/sdk';
import { logger } from '@/utils/logger';

/**
 * Build a localStorage-backed adapter for LayerRegistry, namespaced under the
 * given tenant id. Keys received from the registry already carry its own
 * `nkz.viewerLayer.<id>` prefix — this adapter just adds the tenant segment
 * in front of it.
 */
export function createTenantLayerStorageAdapter(tenantId: string): ViewerLayerStorageAdapter {
  const prefix = `nkz.viewerLayers.${tenantId}.`;

  return {
    getItem(key: string): string | null {
      try {
        return window.localStorage.getItem(prefix + key);
      } catch (error) {
        logger.warn('[viewerLayerStorage] getItem failed (storage unavailable):', error);
        return null;
      }
    },
    setItem(key: string, value: string): void {
      try {
        window.localStorage.setItem(prefix + key, value);
      } catch (error) {
        // Quota exceeded or private browsing — non-fatal, matches the
        // registry's own in-method try/catch around writePersisted().
        logger.warn('[viewerLayerStorage] setItem failed (storage unavailable):', error);
      }
    },
  };
}
