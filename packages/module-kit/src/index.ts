// Ambient window-global declarations (__NKZ__, __NKZ_SDK__, __NKZ_MODULE_ID__,
// __NKZ_MODULE_BASE_PATH__, __nekazariAuthContext). Side-effect import so the
// `declare global` augmentation is bundled into dist/index.d.ts.
import './runtime/globals';

// defineModule / schema (A.1)
export { defineModule, toNKZRegistration } from './defineModule';
export { withModuleProvider } from './withModuleProvider';
export { ModuleDefinitionSchema } from './schema';
export type { ModuleDefinition } from './schema';
export type { ModuleAccent, ModuleI18n } from './types';

// Runtime provider (real)
export { NKZProvider } from './runtime/NKZProvider';

// Hooks
export { useAuth } from './hooks/useAuth';
export { useI18n } from './hooks/useI18n';
export { usePlatformEvents, usePlatformEvent } from './hooks/usePlatformEvents';
export { useAPI } from './hooks/useAPI';

// Hook return types (useful for typing component props)
export type {
  AuthInfo,
  PlanTier,
  UseAuthReturn,
  UseI18nReturn,
  UsePlatformEventsReturn,
} from './hooks/types';

// Legacy event bus (preserved for backward compat)
export { initPlatformEvents, type PlatformEvents, type PlatformEvent } from './runtime/events';

// Data hooks (A.2.1b)
export { useEntity, useEntities, useCreateEntity, useUpdateEntity, useDeleteEntity } from './hooks/useOrion';
export { useGet, usePost, usePatch, useDelete } from './hooks/useModuleAPI';
export type { NgsiLdEntity, QueryResult, OrionTransport, ModuleAPITransport } from './hooks/types';

// File storage hook (A.2.3)
export { useFiles } from './hooks/useFiles';
export type { FilesTransport } from './hooks/types';

// Time series hook (A.2.1c)
export { useTimeseries } from './hooks/useTimeseries';
export type { TimeseriesPoint, TimeseriesQuery, TimeseriesTransport } from './hooks/types';

// Shared viewer components
export { LayerMenuRow } from './components/LayerMenuRow';
export type { LayerScope, LayerMenuRowProps } from './components/LayerMenuRow';

// Unified viewer layers (contract frozen 2026-07-12, plan §B1) — declared via
// defineModule({ viewerLayers }); live state is read with useViewerLayer from
// @nekazari/sdk. Types re-exported here so modules don't need a direct sdk import.
export type { ViewerLayerDecl, ViewerLayerStatus } from '@nekazari/sdk';
