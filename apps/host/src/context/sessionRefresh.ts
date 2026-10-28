/**
 * Refresh the Keycloak token (and re-seed the session cookie) when the tab
 * regains focus. A backgrounded tab throttles Keycloak's expiry timer, so the
 * token may already be expired on return; updateToken refreshes it as long as
 * the refresh token is valid.
 */
import { logger } from '@/utils/logger';

export interface RefreshableKeycloak {
  token?: string;
  tokenParsed?: { exp?: number };
  updateToken: (minValiditySeconds: number) => Promise<boolean>;
}

export interface SessionSeeder {
  setSession: (token: string) => Promise<unknown>;
}

export const REFRESH_THRESHOLD_SECONDS = 120;

export function createSessionRefresher(
  getKeycloak: () => RefreshableKeycloak | null | undefined,
  api: SessionSeeder,
): () => Promise<void> {
  let inFlight = false;
  return async () => {
    const kc = getKeycloak();
    if (!kc?.tokenParsed?.exp || inFlight) return;
    const remaining = kc.tokenParsed.exp - Math.floor(Date.now() / 1000);
    if (remaining >= REFRESH_THRESHOLD_SECONDS) return;
    inFlight = true;
    try {
      logger.debug('[Auth] Proactive refresh — token remaining', remaining, 's');
      const refreshed = await kc.updateToken(30);
      if (refreshed && kc.token) await api.setSession(kc.token).catch(() => {});
    } catch {
      // Expiry/401 paths own logout; nothing to do here.
    } finally {
      inFlight = false;
    }
  };
}
