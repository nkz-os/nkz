import type { Geometry, Polygon, MultiPolygon } from 'geojson';

// Re-export from existing sub-components for central access
export type { ParentEntity } from './ParentEntitySelector';

// ─── EntityWizard public props ────────────────────────────────────────────────

export interface EntityWizardProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: () => void;
  initialEntityType?: string;
}

// ─── Geometry helpers ─────────────────────────────────────────────────────────

export type GeometryType = 'Point' | 'Polygon' | 'LineString' | 'MultiLineString';

/** GeoJSON geometry stored in NGSI-LD as GeoProperty */
export type EntityGeometry = Geometry;

/** Parent entity geometry is always an area type */
export type ParentGeometry = Polygon | MultiPolygon;

// ─── Form data — discriminated union per macro category ──────────────────────
//
// Rules:
//   - `macroCategory` is the TypeScript discriminator — never omit it.
//   - `geometry` is the committed GeoJSON result (Point/Polygon/…).
//     lat/lon are NOT stored separately; derive them from geometry when needed.
//   - `additionalAttributes` holds dynamic SDM schema fields (schema-driven
//     inputs rendered from /api/sdm/entities/<type>/schema).
//   - `placementState` lives in the shell (index.tsx) as a local useReducer,
//     NOT here — it is ephemeral UI state, not backend payload.

interface BaseFormData {
  name: string;
  description?: string;
  geometry: EntityGeometry | null;
  defaultIconKey?: string;
  iconUrl?: string;
  model3DUrl?: string;
  modelScale?: number;
  modelRotation?: [number, number, number];
}

export interface GeoAssetFormData extends BaseFormData {
  macroCategory: 'assets';
  geometryType: GeometryType;
  parentEntity: import('./ParentEntitySelector').ParentEntity | null;
  isSubdivision: boolean;

  // AgriParcel first-class fields (previously buried in [key: string]: any)
  municipality?: string;
  province?: string;
  cadastralReference?: string;
  cropType?: string;

  // Dynamic attributes from SDM schema for all other asset types
  additionalAttributes: Record<string, string | number>;
}

export interface IoTSensorFormData extends BaseFormData {
  macroCategory: 'sensors';
  geometryType: 'Point'; // Sensors are always point locations
  deviceProfileId: string | null;
  additionalAttributes: Record<string, string | number>;

  // Sensor health monitoring & calibration configuration
  // (stored in form state for submission to POST /api/sensors)
  healthConfig?: Record<string, unknown> & { communicationTimeoutHours?: number };
  calibrationConfig?: Record<string, unknown>;
}

export interface FleetFormData extends BaseFormData {
  macroCategory: 'fleet';
  geometryType: 'Point'; // Fleet base/current location
  robotType?: string;
  rosNamespace?: string;
  manufacturer?: string;
  serialNumber?: string;
  isobusCompatible?: boolean;
  additionalAttributes: Record<string, string | number>;
}

export type WizardFormData = GeoAssetFormData | IoTSensorFormData | FleetFormData;

/** Inferred from the discriminated union — avoids manual 'assets' | 'sensors' | 'fleet' */
export type MacroCategory = WizardFormData['macroCategory'];

// ─── Step configuration ───────────────────────────────────────────────────────

export type StepId =
  | 'type'
  | 'geo-config'
  | 'iot-config'
  | 'fleet-config'
  | 'geometry'
  | 'visualization'
  | 'summary';

export interface StepConfig {
  id: StepId;
  label: string;
}

export const STEP_CONFIGS: Record<MacroCategory, StepConfig[]> = {
  assets: [
    { id: 'type', label: 'wizard.steps.type' },
    { id: 'geo-config', label: 'wizard.steps.data' },
    { id: 'geometry', label: 'wizard.steps.geometry' },
    { id: 'visualization', label: 'wizard.steps.visual' },
    { id: 'summary', label: 'wizard.steps.summary' },
  ],
  sensors: [
    { id: 'type', label: 'wizard.steps.type' },
    { id: 'iot-config', label: 'wizard.steps.data' },
    { id: 'geometry', label: 'wizard.steps.location' },
    { id: 'summary', label: 'wizard.steps.summary' },
  ],
  fleet: [
    { id: 'type', label: 'wizard.steps.type' },
    { id: 'fleet-config', label: 'wizard.steps.data' },
    { id: 'geometry', label: 'wizard.steps.location' },
    { id: 'visualization', label: 'wizard.steps.visual' },
    { id: 'summary', label: 'wizard.steps.summary' },
  ],
};

/** Steps shown before a category is known (only type selection) */
export const INITIAL_STEPS: StepConfig[] = [{ id: 'type', label: 'wizard.steps.type' }];
