/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

/**
 * Slot types available in the Nekazari Unified Viewer and Dashboard.
 * These are the extension points where module widgets render.
 */
export type SlotType =
  | 'entity-tree'       // Left panel: entity tree, filters
  | 'map-layer'         // Map overlays, markers, layers
  | 'context-panel'     // Right panel: entity details, controls
  | 'bottom-panel'      // Bottom panel: timeline, charts
  | 'layer-toggle'      // Layer manager toggles
  | 'dashboard-widget'  // Dashboard: module-contributed cards
  | 'admin-tab';        // Admin Control Center: module-contributed tabs

/** Definition of a widget rendered in a slot */
export interface SlotWidgetDefinition {
  /** Unique identifier for this widget (e.g. "my-module-layer-toggle") */
  id: string;
  /** Module ID that owns this widget. Used by SlotRenderer for grouping and error isolation. */
  moduleId?: string;
  /** Component name exported by the module (for remote IIFE loading) */
  component: string;
  /** Render priority — lower numbers render first */
  priority: number;
  /** Optional visibility conditions */
  showWhen?: {
    /** Show only when the selected entity matches one of these types */
    entityType?: string[];
    /** Show only when at least one of these layers is active */
    layerActive?: string[];
  };
  /** Default props passed to the widget component */
  defaultProps?: Record<string, unknown>;
  /**
   * For local (bundled) widgets: the actual React component reference.
   * Typed `any` deliberately: a slot widget's real prop shape is unknown to
   * the host/registry (it's whatever the owning module declared), and prop
   * types are contravariant — any narrower placeholder (e.g.
   * `ComponentType<Record<string, unknown>>`) would reject real, specifically
   * typed components on assignment. This is one of the few defensible `any`s.
   */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  localComponent?: React.ComponentType<any>;
}

/**
 * Complete slot configuration for a module.
 * Each slot type maps to an array of widget definitions.
 * Also supports an optional moduleProvider for React Context sharing.
 */
export interface ModuleViewerSlots {
  'entity-tree'?: SlotWidgetDefinition[];
  'map-layer'?: SlotWidgetDefinition[];
  'context-panel'?: SlotWidgetDefinition[];
  'bottom-panel'?: SlotWidgetDefinition[];
  'layer-toggle'?: SlotWidgetDefinition[];
  'dashboard-widget'?: SlotWidgetDefinition[];
  'admin-tab'?: SlotWidgetDefinition[];
  /** Optional React Context provider wrapping all widgets from this module together */
  moduleProvider?: React.ComponentType<{ children: React.ReactNode }>;
}
