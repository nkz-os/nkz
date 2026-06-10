/**
 * useVersionCheck - Polls /version.json and notifies when a new deployment is detected.
 *
 * Detects stale-cache scenarios: when the host or modules are re-deployed
 * while the user has the app open, cached references to old JS chunks will
 * 404. Instead of waiting for the user to hit a loading error, this hook
 * proactively polls /version.json and alerts when the deployed version
 * differs from the version that was loaded at page start.
 *
 * Usage:
 *   <AppInitializer>
 *     <VersionCheck />
 *     ...
 *   </AppInitializer>
 */

import { useEffect, useRef } from 'react';
import { useToastContext } from '@/context/ToastContext';
import { logger } from '@/utils/logger';

const POLL_INTERVAL_MS = 60_000; // 1 minute

let initialVersion: string | null = null;

async function fetchVersion(): Promise<string | null> {
  try {
    const resp = await fetch('/version.json', {
      cache: 'no-store',
      headers: { 'Cache-Control': 'no-cache' },
    });
    if (!resp.ok) return null;
    const data = await resp.json();
    // Prefer buildTime over version: CI Docker builds have no git history,
    // so 'version' is always 'unknown'. buildTime is always unique per build.
    if (data?.buildTime) return data.buildTime;
    if (data?.version && data.version !== 'unknown') return data.version;
    return null;
  } catch {
    return null;
  }
}

export function useVersionCheck() {
  const { info } = useToastContext();
  const notifiedRef = useRef(false);

  useEffect(() => {
    let timer: ReturnType<typeof setInterval>;

    const check = async () => {
      const current = await fetchVersion();
      if (!current) return;

      if (initialVersion === null) {
        // First successful fetch — this is our baseline
        initialVersion = current;
        logger.info(`[version-check] Initial version: ${current}`);
        return;
      }

      if (current !== initialVersion && !notifiedRef.current) {
        notifiedRef.current = true;
        logger.warn(
          `[version-check] New version detected: ${current} (was ${initialVersion})`
        );

        info(
          '🔄 Nueva versión disponible. Recarga la página para actualizar.',
          0 // never auto-dismiss
        );
      }
    };

    // First check on mount
    check();

    // Poll periodically
    timer = setInterval(check, POLL_INTERVAL_MS);

    return () => clearInterval(timer);
  }, [info]);
}
