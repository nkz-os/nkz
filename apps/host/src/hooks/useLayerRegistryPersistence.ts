// =============================================================================
// useLayerRegistryPersistence — wires LayerRegistry to tenant-scoped storage
// =============================================================================
// Configures @nekazari/sdk's LayerRegistry singleton with a localStorage
// adapter namespaced under the current tenant, once per tenant. Intended to
// run as early as possible in the unified viewer's bootstrap — before module
// remotes evaluate `defineModule({ viewerLayers })` and register their
// entries — so the FIRST registration for each layer already reads its
// persisted visible/opacity from the right tenant bucket.

import { useEffect, useRef } from 'react';
import { LayerRegistry } from '@nekazari/sdk';
import { createTenantLayerStorageAdapter } from '@/utils/viewerLayerStorage';

export function useLayerRegistryPersistence(tenantId: string | null | undefined): void {
  const configuredForTenantRef = useRef<string | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    if (configuredForTenantRef.current === tenantId) return;
    configuredForTenantRef.current = tenantId;
    LayerRegistry.setStorageAdapter(createTenantLayerStorageAdapter(tenantId));
  }, [tenantId]);
}
