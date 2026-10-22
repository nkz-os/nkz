export interface PlatformEvent {
  type: string;
  payload: unknown;
  source: string;
  timestamp: number;
}

type EventCallback = (event: PlatformEvent) => void;

export interface PlatformEvents {
  emit(type: string, payload: unknown): void;
  on(type: string, callback: EventCallback): () => void;
  off(type: string, callback: EventCallback): void;
}

/**
 * Initialize the platform event bus on window.__NKZ__.events.
 * Idempotent — safe to call multiple times.
 */
export function initPlatformEvents(): PlatformEvents {
  const bridge = window.__NKZ__;
  if (bridge?.events) {
    return bridge.events;
  }

  const listeners = new Map<string, Set<EventCallback>>();

  const events: PlatformEvents = {
    emit(type: string, payload: unknown) {
      const event: PlatformEvent = {
        type,
        payload,
        source: window.__NKZ_MODULE_ID__ ?? 'unknown',
        timestamp: Date.now(),
      };

      const callbacks = listeners.get(type);
      if (callbacks) {
        for (const cb of callbacks) {
          try {
            cb(event);
          } catch (err) {
            console.error(`[PlatformEvents] Error in listener for "${type}":`, err);
          }
        }
      }
    },

    on(type: string, callback: EventCallback) {
      if (!listeners.has(type)) {
        listeners.set(type, new Set());
      }
      listeners.get(type)!.add(callback);
      return () => {
        listeners.get(type)?.delete(callback);
      };
    },

    off(type: string, callback: EventCallback) {
      listeners.get(type)?.delete(callback);
    },
  };

  if (bridge) {
    bridge.events = events;
  }

  return events;
}
