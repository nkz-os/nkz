import React from 'react';
import type { ComponentType, FC, ReactNode } from 'react';

// ── types ───────────────────────────────────────────────────────────────────

interface SlotEntry {
  id: string;
  priority: number;
  /**
   * Typed `any` deliberately — a slot widget's real prop shape is unknown
   * here (it's whatever the owning module declared), and component prop
   * types are contravariant: any narrower placeholder would reject real,
   * specifically typed components on assignment. Mirrors
   * `SlotWidgetDefinition.localComponent` in @nekazari/sdk's types/slots.ts,
   * which documents the same tradeoff.
   */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  localComponent?: ComponentType<any>;
  [key: string]: unknown;
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
  slots: object,
): Record<string, SlotEntry[]> {
  const s = slots as Record<string, unknown>;
  const { moduleProvider: Provider, ...rawSlots } = s;

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
      // `Inner` is `ComponentType<any>` (see SlotEntry above) — the wrapper
      // forwards whatever props it's given untouched, so it takes on the
      // same defensible `any` for the same reason (contravariant component
      // props: no narrower prop type could accept every real component).
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const Wrapped: FC<any> = (props: any) =>
        React.createElement(ProviderComponent, null,
          React.createElement(Inner, props),
        );
      return { ...entry, localComponent: Wrapped };
    });
  }

  return result;
}
