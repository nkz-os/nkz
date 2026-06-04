import { useCallback } from 'react';
import { logger } from '@/utils/logger';

export interface Notification {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  message: string;
}

type Listener = (n: Notification) => void;
let listeners: Listener[] = [];

/**
 * Simple notification system. Replace with ui-kit Toast component in V1.4.
 * For now, dispatches notifications and logs them via structured logger.
 */
export function useNotification() {
  const showNotification = useCallback(
    (opts: { type: Notification['type']; message: string }) => {
      const notification: Notification = {
        id: Math.random().toString(36).slice(2),
        type: opts.type,
        message: opts.message,
      };

      // Log via structured logger for observability
      const logFn = opts.type === 'error' ? logger.error :
                    opts.type === 'warning' ? logger.warn :
                    logger.info;
      logFn(`[Notification] ${opts.message}`, { type: opts.type });

      // Notify listeners (for future Toast component integration)
      listeners.forEach((fn) => fn(notification));
    },
    []
  );

  return { showNotification };
}

export function subscribeToNotifications(fn: Listener): () => void {
  listeners.push(fn);
  return () => {
    listeners = listeners.filter((l) => l !== fn);
  };
}
