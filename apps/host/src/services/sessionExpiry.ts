/**
 * Single exit for an unrecoverable 401.
 *
 * Three interceptors used to decide this independently and disagreed. The SDK
 * path dispatched `nekazari:session:expired`, which App.tsx already handles by
 * showing a notice and redirecting to /login with the current path preserved.
 * api.ts only rejected the promise, so the page kept retrying with no way out —
 * the reported "loop that never sends you to login". parcelApi.ts hard-navigated
 * to /login, dropping the return path.
 *
 * Everything now routes through here so the behaviour is decided in one place.
 * Callers signal; the host owns the navigation.
 */
import { logger } from '../utils/logger';

const EXPIRED_EVENT = 'nekazari:session:expired';

/** Guards against a burst of parallel 401s stacking notices and redirects. */
let alreadyNotified = false;

/**
 * Announce that the session is gone beyond recovery.
 *
 * Safe to call from every failing request: only the first one in a burst is
 * announced, because a dashboard that fires eight requests on load would
 * otherwise queue eight redirects.
 */
export function notifySessionExpired(source: string): void {
  if (alreadyNotified) {
    logger.debug(`[Session] expiry already announced, ignoring duplicate from ${source}`);
    return;
  }
  alreadyNotified = true;
  logger.warn(`[Session] token refresh failed in ${source} — session expired`);
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(EXPIRED_EVENT));
  }
}

/** Test seam: the guard is module-level state and must be clearable. */
export function __resetSessionExpiryForTests(): void {
  alreadyNotified = false;
}
