/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 * 
 * @see https://github.com/nkz-os/nekazari-public/tree/main/packages/sdk
 */

// Ambient window-global declarations (__nekazariAuthContext, __nekazariViewerContextInstance)
// Side-effect import so the `declare global` augmentation is bundled into dist/index.d.ts.
import './types/global';

export * from './auth/useAuth';
export * from './i18n/provider';
export * from './i18n/config';

// Viewer Context exports
export { useViewer, useViewerOptional, type ViewerContextValue } from './viewer/useViewer';

// Unified viewer-layer registry (contract frozen 2026-07-12, plan §B1)
export { LayerRegistry, registerViewerLayers } from './viewer/layerRegistry';
export type {
  ViewerLayerDecl,
  ViewerLayerEntry,
  ViewerLayerStatus,
  ViewerLayerStorageAdapter,
} from './viewer/layerRegistry';
export { useViewerLayer, type UseViewerLayerReturn } from './viewer/useViewerLayer';

// API Client exports
export { NKZClient, type NKZClientOptions } from './api/client';

// NGSI-LD helpers
export { getEntityDisplayName, getNGSIValue } from './ngsi/helpers';

// Slot & Module types (canonical source of truth)
export type {
  SlotType,
  SlotWidgetDefinition,
  ModuleViewerSlots,
  NKZModuleRegistration,
  ModuleApiContract,
  ModuleCompatibilityResult,
} from './types';

// Backward compatibility: Export legacy names as aliases
// These will be deprecated in v3.0.0
export { NKZClient as NekazariClient } from './api/client';
export type { NKZClientOptions as NekazariClientOptions } from './api/client';

