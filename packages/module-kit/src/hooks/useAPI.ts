import { useMemo } from 'react';
import { NKZClient } from '@nekazari/sdk';
import { useNKZRuntime } from '../runtime/NKZContext';
import '../runtime/globals';

/** See runtime/NKZProvider.tsx's HostAuthBridge — only the field this hook reads. */
interface HostAuthBridgeTenant {
  tenantId?: string;
}

/**
 * Preconfigured NKZClient hook. Reads basePath from the module
 * definition and tenant context from the host bridge.
 *
 * The client automatically:
 * - Sends credentials: 'include' for httpOnly cookie auth
 * - Adds X-Tenant-ID header from the host auth bridge
 * - Prepends basePath to all requests
 */
export function useAPI(basePath?: string): NKZClient {
  const { moduleApi } = useNKZRuntime();

  const client = useMemo(() => {
    const resolvedBasePath =
      basePath ??
      moduleApi.basePath ??
      (typeof window !== 'undefined' ? window.__NKZ_MODULE_BASE_PATH__ : undefined) ??
      '/api/modules/unknown';

    const getTenantId = (): string | undefined =>
      typeof window !== 'undefined'
        ? (window.__nekazariAuthContext as HostAuthBridgeTenant | undefined)?.tenantId
        : undefined;

    return new NKZClient({
      baseUrl: resolvedBasePath,
      getTenantId,
    });
  }, [basePath, moduleApi.basePath]);

  return client;
}
