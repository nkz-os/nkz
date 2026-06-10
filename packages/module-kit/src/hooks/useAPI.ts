import { useMemo } from 'react';
import { NKZClient } from '@nekazari/sdk';

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
  const client = useMemo(() => {
    const resolvedBasePath =
      basePath ??
      ((typeof window !== 'undefined' &&
        (window as any).__NKZ_MODULE_BASE_PATH__) as string | undefined) ??
      '/api/modules/unknown';

    const getTenantId = () =>
      (typeof window !== 'undefined' &&
        (window as any).__nekazariAuthContext?.tenantId) as string | undefined;

    return new NKZClient({
      baseUrl: resolvedBasePath,
      getTenantId,
    });
  }, [basePath]);

  return client;
}
