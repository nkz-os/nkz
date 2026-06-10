import { useEffect } from 'react';
import type { PlatformEvent } from '../runtime/events';
import { initPlatformEvents } from '../runtime/events';
import { useNKZRuntime } from '../runtime/NKZContext';
import type { UsePlatformEventsReturn } from './types';

/**
 * Cross-module event bus, namespaced per module.
 *
 * `emit('foo', payload)` from inside a module called 'soil-health' is internally
 * routed as `module:soil-health:foo` to prevent modules from forging
 * platform-level events (e.g. `auth:logout`).
 *
 * Event names containing `:` are rejected at emit time.
 * `on()` accepts any name — including platform-level namespaces emitted by the host.
 */
export function usePlatformEvents(): UsePlatformEventsReturn {
  const runtime = useNKZRuntime();
  return {
    emit(event, payload) {
      if (event.includes(':')) {
        throw new Error(
          `[@nekazari/module-kit] emit('${event}', ...) rejected — event names must not contain a colon. ` +
            `Use a plain identifier; module-kit will prefix it as 'module:${runtime.moduleId}:${event}'.`,
        );
      }
      runtime.events.emit(`module:${runtime.moduleId}:${event}`, payload);
    },
    on(event, handler) {
      return runtime.events.on(event, handler);
    },
  };
}

/**
 * Legacy single-event subscription helper. Kept for backward compatibility
 * with modules that imported `usePlatformEvent` from earlier versions.
 *
 * Auto-unsubscribes on unmount.
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
