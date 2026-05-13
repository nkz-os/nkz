export { defineModule, toNKZRegistration } from './defineModule';
export { ModuleDefinitionSchema } from './schema';
export type { ModuleDefinition } from './schema';
export type { ModuleAccent, ModuleI18n } from './types';
export { initPlatformEvents, type PlatformEvents, type PlatformEvent } from './runtime/events';
export { usePlatformEvents, usePlatformEvent } from './hooks/usePlatformEvents';
export { useAPI } from './hooks/useAPI';
