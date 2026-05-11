import { useEffect } from 'react';
import type { PlatformEvent, PlatformEvents } from '../runtime/events';
import { initPlatformEvents } from '../runtime/events';

export function usePlatformEvents(): PlatformEvents {
  useEffect(() => {
    initPlatformEvents();
  }, []);

  return initPlatformEvents();
}

/**
 * Subscribe to a specific event type. Auto-unsubscribes on unmount.
 */
export function usePlatformEvent(
  type: string,
  callback: (event: PlatformEvent) => void,
  deps: unknown[] = [],
): void {
  useEffect(() => {
    const events = initPlatformEvents();
    return events.on(type, callback);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type, ...deps]);
}
