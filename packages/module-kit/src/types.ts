export type {
  SlotType,
  SlotWidgetDefinition,
  ModuleViewerSlots,
  NKZModuleRegistration,
  ModuleApiContract,
} from '@nekazari/sdk';

/** Accent color definition for a module's visual identity */
export interface ModuleAccent {
  base: string;
  soft: string;
  strong: string;
}

/** i18n resource bundles keyed by language code */
export type ModuleI18n = Record<string, () => Promise<Record<string, unknown>>>;
