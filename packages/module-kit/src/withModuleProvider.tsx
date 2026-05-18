import React from 'react';
import type { ComponentType, FC, ReactNode } from 'react';

// ── types ───────────────────────────────────────────────────────────────────

interface SlotEntry {
  id: string;
  priority: number;
  localComponent?: ComponentType<any>;
  [key: string]: any;
}

// ── helper ──────────────────────────────────────────────────────────────────

/**
 * Strip `moduleProvider` from a slots object and wrap every widget's
 * `localComponent` with the provider so each widget mounts inside its own
 * provider scope when loaded remotely via Module Federation 2.0.
 *
 * Usage in `src/Module.tsx`:
 *
 *   import { defineModule, withModuleProvider } from '@nekazari/module-kit';
 *   import { moduleSlots } from './slots';
 *
 *   export default defineModule({
 *     ...
 *     slots: withModuleProvider(moduleSlots),
 *   });
 *
 * The slots file still declares `moduleProvider` as a top-level convenience:
 *
 *   export const moduleSlots = {
 *     'map-layer': [...],
 *     moduleProvider: MyProvider,
 *   };
 */
export function withModuleProvider(
  slots: Record<string, unknown>,
): Record<string, SlotEntry[]> {
  const { moduleProvider: Provider, ...rawSlots } = slots;

  if (!Provider || typeof Provider !== 'function') {
    return rawSlots as Record<string, SlotEntry[]>;
  }

  const ProviderComponent = Provider as ComponentType<{ children: ReactNode }>;
  const result: Record<string, SlotEntry[]> = {};

  for (const [slotKey, entries] of Object.entries(rawSlots)) {
    if (!Array.isArray(entries)) continue;
    result[slotKey] = (entries as SlotEntry[]).map((entry) => {
      const Inner = entry.localComponent;
      if (!Inner) return entry;
      const Wrapped: FC<any> = (props: any) =>
        React.createElement(ProviderComponent, null,
          React.createElement(Inner, props),
        );
      return { ...entry, localComponent: Wrapped };
    });
  }

  return result;
}
