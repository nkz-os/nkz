import { useEffect } from 'react';
import { VIEWER_SHORTCUTS } from '@/config/shortcuts';
import { logger } from '@/utils/logger';

export function ViewerKeyboardShortcuts() {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Don't trigger in inputs
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

      const match = VIEWER_SHORTCUTS.find(s => {
        const ctrlMatch = s.ctrl ? (e.ctrlKey || e.metaKey) : (!e.ctrlKey && !e.metaKey);
        const shiftMatch = s.shift ? e.shiftKey : !e.shiftKey;
        return e.key === s.key && ctrlMatch && shiftMatch;
      });

      if (match) {
        // Log the shortcut — actual handlers are registered per-component
        // via useKeyboardShortcut from viewer-kit. This is the canonical map.
        logger.debug(`[Shortcut] ${match.description} (${match.key})`);
        // Don't preventDefault here — handlers in components do that
      }
    };

    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  return null; // This component renders nothing, just registers listeners
}
