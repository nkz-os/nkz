import { useCallback } from 'react';
import { logger } from '@/utils/logger';
import { useToastContext } from '@/context/ToastContext';

export interface Notification {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  message: string;
}

/**
 * Notification system backed by the ToastContext Toast UI.
 * Logs via the structured logger for observability AND surfaces a Toast
 * so the user actually sees the notification. Must be called from a
 * component rendered within <ToastProvider>.
 */
export function useNotification() {
  const toast = useToastContext();

  const showNotification = useCallback(
    (opts: { type: Notification['type']; message: string }) => {
      // Log via structured logger for observability
      const logFn = opts.type === 'error' ? logger.error :
                    opts.type === 'warning' ? logger.warn :
                    logger.info;
      logFn(`[Notification] ${opts.message}`, { type: opts.type });

      // Show the toast so the user actually sees it
      toast[opts.type](opts.message);
    },
    [toast]
  );

  return { showNotification };
}
