// =============================================================================
// Unified Asset Types for Asset Manager Grid
// =============================================================================
// Provides a normalized view of all entity types for the asset management UI.

import i18n from 'i18next';
import { logger } from '@/utils/logger';
import { resolveParentRelationship } from '@/utils/entityRelations';
import { readParcelLinks } from '@/utils/parcelLinks';

// =============================================================================
// Asset Categories
// =============================================================================

export type AssetCategory = 
/* eslint-disable @typescript-eslint/no-explicit-any */
  | 'parcels'      // Parcelas y zonas
  | 'sensors'      // Sensores IoT
  | 'fleet'        // Robots y maquinaria
  | 'infrastructure' // Edificios e infraestructura
  | 'vegetation'   // Árboles y cultivos
  | 'livestock'    // Ganadería
  | 'water'        // Recursos hídricos
  | 'weather';     // Estaciones meteorológicas

export type AssetStatus = 
  | 'active'
  | 'inactive'
  | 'maintenance'
  | 'error'
  | 'offline'
  | 'unknown';

// =============================================================================
// Unified Asset Interface
// =============================================================================

export interface UnifiedAsset {
  // Core identification
  id: string;
  type: string;              // Original NGSI-LD type
  name: string;
  category: AssetCategory;
  
  // Status and health
  status: AssetStatus;
  statusLabel?: string;      // Human-readable status
  lastSeen?: Date;
  healthScore?: number;      // 0-100
  
  // Location
  hasLocation: boolean;
  coordinates?: [number, number]; // [lon, lat]
  geometryType?: 'Point' | 'Polygon' | 'LineString';
  municipality?: string;
  
  // Hierarchy
  parentId?: string;
  parentName?: string;
  childCount?: number;
  /** Parcels linked beyond the primary one (the asset sits under the first). */
  extraParentCount?: number;
  
  // Metadata
  createdAt?: Date;
  updatedAt?: Date;
  description?: string;
  tags?: string[];
  
  // Visualization
  icon?: string;             // Icon key or URL
  model3d?: string;          // 3D model URL
  color?: string;            // Display color
  
  // Type-specific data (kept for detail views)
  rawEntity: any;
  
  // Telemetry summary (optional, for sensors/devices)
  telemetrySummary?: {
    lastValue?: number;
    unit?: string;
    trend?: 'up' | 'down' | 'stable';
  };
}

// =============================================================================
// Asset Type Metadata
// =============================================================================

// Labels live in the locales: entities.assets.types.<type> and entities.categories.<id>.
export interface AssetTypeInfo {
  type: string;
  category: AssetCategory;
  icon: string;              // Lucide icon name
  color: string;             // Tailwind color class
  supportsLocation: boolean;
  supportsHierarchy: boolean;
  supportsTelemetry: boolean;
}

export const ASSET_TYPE_REGISTRY: Record<string, AssetTypeInfo> = {
  // Parcels
  AgriParcel: {
    type: 'AgriParcel',
    category: 'parcels',
    icon: 'map-pin',
    color: 'green',
    supportsLocation: true,
    supportsHierarchy: true,
    supportsTelemetry: false,
  },
  Vineyard: {
    type: 'Vineyard',
    category: 'parcels',
    icon: 'grape',
    color: 'purple',
    supportsLocation: true,
    supportsHierarchy: true,
    supportsTelemetry: false,
  },
  OliveGrove: {
    type: 'OliveGrove',
    category: 'parcels',
    icon: 'trees',
    color: 'green',
    supportsLocation: true,
    supportsHierarchy: true,
    supportsTelemetry: false,
  },
  AgriParcelZone: {
    type: 'AgriParcelZone',
    category: 'parcels',
    icon: 'grid-2x2',
    color: 'green',
    supportsLocation: true,
    supportsHierarchy: false,
    supportsTelemetry: false,
  },
  AgriSoil: {
    type: 'AgriSoil',
    category: 'parcels',
    icon: 'layers',
    color: 'amber',
    supportsLocation: true,
    supportsHierarchy: false,
    supportsTelemetry: false,
  },
  AgriSoilExtended: {
    type: 'AgriSoilExtended',
    category: 'parcels',
    icon: 'layers',
    color: 'amber',
    supportsLocation: true,
    supportsHierarchy: false,
    supportsTelemetry: false,
  },
  AgriFarm: {
    type: 'AgriFarm',
    category: 'parcels',
    icon: 'warehouse',
    color: 'green',
    supportsLocation: true,
    supportsHierarchy: true,
    supportsTelemetry: false,
  },
  
  // Sensors
  AgriSensor: {
    type: 'AgriSensor',
    category: 'sensors',
    icon: 'gauge',
    color: 'teal',
    supportsLocation: true,
    supportsHierarchy: false,
    supportsTelemetry: true,
  },
  Device: {
    type: 'Device',
    category: 'sensors',
    icon: 'cpu',
    color: 'cyan',
    supportsLocation: true,
    supportsHierarchy: false,
    supportsTelemetry: true,
  },
  
  // Fleet
  AutonomousMobileRobot: {
    type: 'AutonomousMobileRobot',
    category: 'fleet',
    icon: 'bot',
    color: 'indigo',
    supportsLocation: true,
    supportsHierarchy: false,
    supportsTelemetry: true,
  },
  ManufacturingMachine: {
    type: 'ManufacturingMachine',
    category: 'fleet',
    icon: 'tractor',
    color: 'amber',
    supportsLocation: true,
    supportsHierarchy: false,
    supportsTelemetry: true,
  },
  
  // Infrastructure
  AgriBuilding: {
    type: 'AgriBuilding',
    category: 'infrastructure',
    icon: 'building-2',
    color: 'slate',
    supportsLocation: true,
    supportsHierarchy: false,
    supportsTelemetry: false,
  },
  AgriGreenhouse: {
    type: 'AgriGreenhouse',
    category: 'infrastructure',
    icon: 'warehouse',
    color: 'slate',
    supportsLocation: true,
    supportsHierarchy: false,
    supportsTelemetry: false,
  },
  IrrigationSystem: {
    type: 'IrrigationSystem',
    category: 'infrastructure',
    icon: 'droplets',
    color: 'blue',
    supportsLocation: true,
    supportsHierarchy: false,
    supportsTelemetry: true,
  },
  AgriEnergyTracker: {
    type: 'AgriEnergyTracker',
    category: 'infrastructure',
    icon: 'sun',
    color: 'yellow',
    supportsLocation: true,
    supportsHierarchy: false,
    supportsTelemetry: true,
  },
  PhotovoltaicInstallation: {
    type: 'PhotovoltaicInstallation',
    category: 'infrastructure',
    icon: 'sun',
    color: 'yellow',
    supportsLocation: true,
    supportsHierarchy: false,
    supportsTelemetry: true,
  },
  
  // Vegetation
  AgriCrop: {
    type: 'AgriCrop',
    category: 'vegetation',
    icon: 'leaf',
    color: 'emerald',
    supportsLocation: true,
    supportsHierarchy: true,
    supportsTelemetry: false,
  },
  AgriTree: {
    type: 'AgriTree',
    category: 'vegetation',
    icon: 'tree-deciduous',
    color: 'green',
    supportsLocation: true,
    supportsHierarchy: true,
    supportsTelemetry: false,
  },
  OliveTree: {
    type: 'OliveTree',
    category: 'vegetation',
    icon: 'tree-deciduous',
    color: 'green',
    supportsLocation: true,
    supportsHierarchy: true,
    supportsTelemetry: false,
  },
  Vine: {
    type: 'Vine',
    category: 'vegetation',
    icon: 'grape',
    color: 'purple',
    supportsLocation: true,
    supportsHierarchy: true,
    supportsTelemetry: false,
  },
  FruitTree: {
    type: 'FruitTree',
    category: 'vegetation',
    icon: 'apple',
    color: 'orange',
    supportsLocation: true,
    supportsHierarchy: true,
    supportsTelemetry: false,
  },
  
  // Livestock
  LivestockAnimal: {
    type: 'LivestockAnimal',
    category: 'livestock',
    icon: 'beef',
    color: 'amber',
    supportsLocation: true,
    supportsHierarchy: true,
    supportsTelemetry: true,
  },
  LivestockGroup: {
    type: 'LivestockGroup',
    category: 'livestock',
    icon: 'users',
    color: 'amber',
    supportsLocation: true,
    supportsHierarchy: true,
    supportsTelemetry: false,
  },
  
  // Water
  WaterSource: {
    type: 'WaterSource',
    category: 'water',
    icon: 'droplet',
    color: 'blue',
    supportsLocation: true,
    supportsHierarchy: false,
    supportsTelemetry: true,
  },
  Well: {
    type: 'Well',
    category: 'water',
    icon: 'circle-dot',
    color: 'blue',
    supportsLocation: true,
    supportsHierarchy: false,
    supportsTelemetry: true,
  },
  Pond: {
    type: 'Pond',
    category: 'water',
    icon: 'waves',
    color: 'blue',
    supportsLocation: true,
    supportsHierarchy: false,
    supportsTelemetry: false,
  },
  
  // Weather
  // WeatherObserved stations reach the asset list as the api.getWeatherStations() projection
  WeatherStation: {
    type: 'WeatherStation',
    category: 'weather',
    icon: 'cloud-sun',
    color: 'sky',
    supportsLocation: true,
    supportsHierarchy: false,
    supportsTelemetry: true,
  },
};

// =============================================================================
// Category Metadata
// =============================================================================

export interface CategoryInfo {
  id: AssetCategory;
  icon: string;
  color: string;
}

export const CATEGORY_REGISTRY: Record<AssetCategory, CategoryInfo> = {
  parcels: {
    id: 'parcels',
    icon: 'map',
    color: 'green',
  },
  sensors: {
    id: 'sensors',
    icon: 'gauge',
    color: 'teal',
  },
  fleet: {
    id: 'fleet',
    icon: 'truck',
    color: 'indigo',
  },
  infrastructure: {
    id: 'infrastructure',
    icon: 'building',
    color: 'slate',
  },
  vegetation: {
    id: 'vegetation',
    icon: 'trees',
    color: 'emerald',
  },
  livestock: {
    id: 'livestock',
    icon: 'beef',
    color: 'amber',
  },
  water: {
    id: 'water',
    icon: 'droplets',
    color: 'blue',
  },
  weather: {
    id: 'weather',
    icon: 'cloud-sun',
    color: 'sky',
  },
};

// =============================================================================
// Utility Functions
// =============================================================================

/**
 * Extract name from NGSI-LD entity
 */
function extractName(entity: any): string {
  if (!entity) return '';
  if (typeof entity.name === 'string') return entity.name;
  if (entity.name?.value != null) return String(entity.name.value);
  return entity.id ?? '';
}

type Translate = (key: string) => string;

/** Outside React the shared i18next instance translates, in the active language. */
const translateCommon: Translate = key => String(i18n.t(key, { ns: 'common' }) ?? key);

export function assetTypeLabelKey(type: string): string {
  return `entities.assets.types.${type}`;
}

export function categoryLabelKey(category: AssetCategory): string {
  return `entities.categories.${category}`;
}

/** Label of an asset type in the user's language; a type without one shows its NGSI-LD type. */
export function assetTypeLabel(type: string, t: Translate = translateCommon): string {
  const key = assetTypeLabelKey(type);
  const label = t(key);
  return label && label !== key ? label : type;
}

/** Label of an asset category in the user's language. */
export function categoryLabel(category: AssetCategory, t: Translate = translateCommon): string {
  const key = categoryLabelKey(category);
  const label = t(key);
  return label && label !== key ? label : category;
}

const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;

/**
 * Name for entities without one (module-generated zones, soils, crops): the type
 * label plus the last id segment, with any uuid in it shortened to its first block.
 */
function fallbackName(entity: any, typeLabel: string): string {
  const tail = String(entity.id ?? '').split(':').pop() ?? '';
  const shortTail = tail.replace(UUID, uuid => uuid.slice(0, 8));
  return shortTail ? `${typeLabel} · ${shortTail}` : typeLabel;
}

function hasName(entity: any): boolean {
  return typeof entity?.name === 'string' || entity?.name?.value != null;
}

/**
 * Extract coordinates from NGSI-LD location
 */
function extractCoordinates(entity: any): [number, number] | undefined {
  const location = entity.location?.value || entity.location;
  if (!location) return undefined;
  
  const coords = location.coordinates;
  if (!coords) return undefined;
  
  // Point: [lon, lat]
  if (location.type === 'Point' && Array.isArray(coords) && coords.length >= 2) {
    return [coords[0], coords[1]];
  }
  
  // Polygon: Calculate centroid from first ring
  if (location.type === 'Polygon' && Array.isArray(coords) && coords.length > 0) {
    const ring = coords[0];
    if (Array.isArray(ring) && ring.length > 0) {
      let sumLon = 0, sumLat = 0;
      ring.forEach((point: any) => {
        if (Array.isArray(point) && point.length >= 2) {
          sumLon += point[0];
          sumLat += point[1];
        }
      });
      return [sumLon / ring.length, sumLat / ring.length];
    }
  }
  
  return undefined;
}

/**
 * Determine asset status from various entity properties
 */
function extractStatus(entity: any): AssetStatus {
  const status = entity.status?.value || entity.status;
  
  if (!status) return 'unknown';
  
  switch (status.toLowerCase()) {
    case 'active':
    case 'online':
    case 'connected':
    case 'working':
    case 'healthy':
      return 'active';
    case 'inactive':
    case 'offline':
    case 'disconnected':
    case 'idle':
      return 'inactive';
    case 'maintenance':
    case 'charging':
      return 'maintenance';
    case 'error':
    case 'failed':
    case 'critical':
      return 'error';
    default:
      return 'unknown';
  }
}

/**
 * Convert any NGSI-LD entity to UnifiedAsset
 */
function extraParcelCount(entity: any): number | undefined {
  const extra = readParcelLinks(entity).length - 1;
  return extra > 0 ? extra : undefined;
}

export function normalizeToAsset(entity: any): UnifiedAsset {
  const typeInfo = ASSET_TYPE_REGISTRY[entity.type] || {
    type: entity.type,
    category: 'sensors' as AssetCategory,
    icon: 'box',
    color: 'gray',
    supportsLocation: true,
    supportsHierarchy: false,
    supportsTelemetry: false,
  };
  
  const coordinates = extractCoordinates(entity);
  const location = entity.location?.value || entity.location;
  
  return {
    id: entity.id,
    type: entity.type,
    name: hasName(entity) ? extractName(entity) : fallbackName(entity, assetTypeLabel(entity.type)),
    category: typeInfo.category,
    status: extractStatus(entity),
    statusLabel: entity.status?.value || entity.status || 'Desconocido',
    lastSeen: entity.lastHeartbeat?.value 
      ? new Date(entity.lastHeartbeat.value) 
      : entity.dateModified?.value 
        ? new Date(entity.dateModified.value) 
        : undefined,
    healthScore: entity.batteryLevel?.value || undefined,
    hasLocation: !!coordinates,
    coordinates,
    geometryType: location?.type as any,
    municipality: entity.municipality || entity.address?.municipality,
    parentId: resolveParentRelationship(entity)?.parentId,
    extraParentCount: extraParcelCount(entity),
    parentName: undefined, // Will be populated by hook if needed
    childCount: entity.children?.length || 0,
    createdAt: entity.dateCreated?.value ? new Date(entity.dateCreated.value) : undefined,
    updatedAt: entity.dateModified?.value ? new Date(entity.dateModified.value) : undefined,
    description: entity.description?.value || entity.description,
    tags: entity.tags?.value || entity.tags || [],
    icon: entity.icon?.value || entity.icon || typeInfo.icon,
    model3d: entity.ref3DModel?.value || entity.ref3DModel || entity.model3d,
    color: typeInfo.color,
    rawEntity: entity,
    telemetrySummary: typeInfo.supportsTelemetry ? {
      lastValue: entity.temperature?.value 
        || entity.moisture?.value 
        || entity.batteryLevel?.value 
        || undefined,
      unit: entity.temperature?.value ? '°C' 
        : entity.moisture?.value ? '%' 
        : entity.batteryLevel?.value ? '%' 
        : undefined,
      trend: 'stable',
    } : undefined,
  };
}

/**
 * Normalize multiple entities of different types
 */
export function normalizeEntities(entities: Record<string, any[]>): UnifiedAsset[] {
  const assets: UnifiedAsset[] = [];
  
  Object.entries(entities).forEach(([type, items]) => {
    if (Array.isArray(items)) {
      items.forEach(item => {
        try {
          assets.push(normalizeToAsset({ ...item, type: item.type || type }));
        } catch (e) {
          logger.warn(`[Assets] Failed to normalize entity:`, item, e);
        }
      });
    }
  });
  
  return assets;
}

// =============================================================================
// Filter Types
// =============================================================================

export interface AssetFilters {
  search: string;
  categories: AssetCategory[];
  types: string[];
  statuses: AssetStatus[];
  hasLocation: boolean | null;
  municipality: string | null;
  parentId: string | null;
}

export const DEFAULT_FILTERS: AssetFilters = {
  search: '',
  categories: [],
  types: [],
  statuses: [],
  hasLocation: null,
  municipality: null,
  parentId: null,
};

/**
 * Apply filters to asset list
 */
export function filterAssets(assets: UnifiedAsset[], filters: AssetFilters): UnifiedAsset[] {
  return assets.filter(asset => {
    // Search filter (name, type, id)
    if (filters.search) {
      const search = filters.search.toLowerCase();
      const matchesSearch = 
        asset.name.toLowerCase().includes(search) ||
        asset.type.toLowerCase().includes(search) ||
        asset.id.toLowerCase().includes(search) ||
        (asset.municipality?.toLowerCase().includes(search) ?? false);
      if (!matchesSearch) return false;
    }
    
    // Category filter
    if (filters.categories.length > 0 && !filters.categories.includes(asset.category)) {
      return false;
    }
    
    // Type filter
    if (filters.types.length > 0 && !filters.types.includes(asset.type)) {
      return false;
    }
    
    // Status filter
    if (filters.statuses.length > 0 && !filters.statuses.includes(asset.status)) {
      return false;
    }
    
    // Location filter
    if (filters.hasLocation !== null && asset.hasLocation !== filters.hasLocation) {
      return false;
    }
    
    // Municipality filter
    if (filters.municipality && asset.municipality !== filters.municipality) {
      return false;
    }
    
    // Parent filter
    if (filters.parentId && asset.parentId !== filters.parentId) {
      return false;
    }
    
    return true;
  });
}

// =============================================================================
// Sort Types
// =============================================================================

export type SortField = 'name' | 'type' | 'status' | 'lastSeen' | 'municipality';
export type SortDirection = 'asc' | 'desc';

export interface SortConfig {
  field: SortField;
  direction: SortDirection;
}

export function sortAssets(assets: UnifiedAsset[], sort: SortConfig): UnifiedAsset[] {
  return [...assets].sort((a, b) => {
    let comparison = 0;
    
    switch (sort.field) {
      case 'name':
        comparison = a.name.localeCompare(b.name);
        break;
      case 'type':
        comparison = a.type.localeCompare(b.type);
        break;
      case 'status':
        comparison = a.status.localeCompare(b.status);
        break;
      case 'lastSeen': {
        const aTime = a.lastSeen?.getTime() || 0;
        const bTime = b.lastSeen?.getTime() || 0;
        comparison = aTime - bTime;
        break;
      }
      case 'municipality':
        comparison = (a.municipality || '').localeCompare(b.municipality || '');
        break;
    }
    
    return sort.direction === 'desc' ? -comparison : comparison;
  });
}

