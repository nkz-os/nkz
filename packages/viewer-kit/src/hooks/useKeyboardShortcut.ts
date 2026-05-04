/**
 * useKeyboardShortcut — global keyboard shortcut registry with collision
 * detection. Skips when focus is inside input/textarea/select elements.
 */
import { useEffect, useRef } from 'react';

export interface ShortcutDef {
  key: string;
  ctrl?: boolean;
  shift?: boolean;
  alt?: boolean;
  meta?: boolean;
  handler: (e: KeyboardEvent) => void;
  description?: string;
}

// Module-level registry for collision detection
const registry = new Map<string, string>();

function serializeCombo(
  def: Pick<ShortcutDef, 'key' | 'ctrl' | 'shift' | 'alt' | 'meta'>,
): string {
  const parts: string[] = [];
  if (def.ctrl) parts.push('Ctrl');
  if (def.shift) parts.push('Shift');
  if (def.alt) parts.push('Alt');
  if (def.meta) parts.push('Meta');
  parts.push(def.key.toUpperCase());
  return parts.join('+');
}

function isInputFocused(): boolean {
  const el = document.activeElement;
  if (!el) return false;
  const tag = el.tagName.toLowerCase();
  return (
    tag === 'input' ||
    tag === 'textarea' ||
    tag === 'select' ||
    (el as HTMLElement).isContentEditable
  );
}

export function useKeyboardShortcut(def: ShortcutDef): void {
  const handlerRef = useRef(def.handler);
  handlerRef.current = def.handler;

  const combo = serializeCombo(def);

  // Register / collision detection
  useEffect(() => {
    const existingOwner = registry.get(combo);
    if (existingOwner && existingOwner !== def.description) {
      console.warn(
        `[useKeyboardShortcut] Collision detected for "${combo}": ` +
          `"${def.description}" conflicts with "${existingOwner}"`,
      );
    }

    registry.set(combo, def.description ?? 'anonymous');
    return () => {
      if (registry.get(combo) === (def.description ?? 'anonymous')) {
        registry.delete(combo);
      }
    };
  }, [combo, def.description]);

  // Attach listener
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (isInputFocused()) return;

      const match =
        e.key.toUpperCase() === def.key.toUpperCase() &&
        !!e.ctrlKey === !!def.ctrl &&
        !!e.shiftKey === !!def.shift &&
        !!e.altKey === !!def.alt &&
        !!e.metaKey === !!def.meta;

      if (match) {
        e.preventDefault();
        e.stopPropagation();
        handlerRef.current(e);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [def.key, def.ctrl, def.shift, def.alt, def.meta]);
}
