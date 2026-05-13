export type {
  SlotType,
  SlotWidgetDefinition,
  ModuleViewerSlots,
  NKZModuleRegistration,
  ModuleApiContract,
} from '@nekazari/sdk';

export type { ModuleDefinition } from './schema';

/** Accent color definition for a module's visual identity (re-exported from schema for backward compat) */
export interface ModuleAccent {
  base: string;
  soft: string;
  strong: string;
}

/** i18n resource bundles keyed by language code */
export type ModuleI18n = Record<string, () => Promise<Record<string, unknown>>>;
