// =============================================================================
// Terrain Provider Utilities
// =============================================================================
// Utilities for detecting and selecting terrain providers based on location

export type TerrainProviderType = 'idena' | 'ign' | 'cesium_world' | 'auto';

type PointCoordinates = [number, number];
type PolygonCoordinates = number[][] | number[][][];
type GeometryLike = {
  type?: 'Point' | 'Polygon' | string;
  coordinates?: PointCoordinates | PolygonCoordinates;
  value?: {
    type?: 'Point' | 'Polygon' | string;
    coordinates?: PointCoordinates | PolygonCoordinates;
  };
};

// Terrain provider URLs
export const TERRAIN_PROVIDERS: Record<string, string> = {
  idena: 'https://idena.navarra.es/cesiumTerrain/2017/epsg4326/5m/layer.json',
  ign: 'https://qm-mdt.idee.es/1.0.0/terrain/layer.json',
  cesium_world: 'cesium_world', // Special value — handled by Cesium.createWorldTerrain()
};

// Navarra bounding box (approximate)
const NAVARRA_BOUNDS = { minLon: -2.5, maxLon: -1.0, minLat: 42.0, maxLat: 43.5 };

// Spain bounding box (approximate — covers peninsula + islands)
const SPAIN_BOUNDS = { minLon: -18.5, maxLon: 5.0, minLat: 27.0, maxLat: 44.0 };

/**
 * Check if coordinates are within Navarra bounds
 */
export function isInNavarra(longitude: number, latitude: number): boolean {
  return (
    longitude >= NAVARRA_BOUNDS.minLon &&
    longitude <= NAVARRA_BOUNDS.maxLon &&
    latitude >= NAVARRA_BOUNDS.minLat &&
    latitude <= NAVARRA_BOUNDS.maxLat
  );
}

function isInSpain(longitude: number, latitude: number): boolean {
  return (
    longitude >= SPAIN_BOUNDS.minLon &&
    longitude <= SPAIN_BOUNDS.maxLon &&
    latitude >= SPAIN_BOUNDS.minLat &&
    latitude <= SPAIN_BOUNDS.maxLat
  );
}

/**
 * Detect terrain provider based on coordinates.
 * - Navarra → IDENA (5m)
 * - Spain → IGN
 * - Rest of the world → Cesium World Terrain (global, free)
 */
export function detectTerrainProvider(
  longitude: number,
  latitude: number
): TerrainProviderType {
  if (isInNavarra(longitude, latitude)) return 'idena';
  if (isInSpain(longitude, latitude)) return 'ign';
  return 'cesium_world';
}

/**
 * Detect terrain provider from parcel geometry or viewer camera position
 * @param parcels Array of parcels with geometry (supports both Parcel type and simple geometry objects)
 * @param cameraPosition Optional camera position [longitude, latitude]
 * @returns Detected terrain provider
 */
export function detectTerrainProviderFromParcels(
  parcels: Array<{ geometry?: GeometryLike }>,
  cameraPosition?: [number, number]
): TerrainProviderType {
  // If camera position provided, use it
  if (cameraPosition) {
    return detectTerrainProvider(cameraPosition[0], cameraPosition[1]);
  }

  // Otherwise, check parcels
  if (parcels && parcels.length > 0) {
    for (const parcel of parcels) {
      // Handle both Parcel type (with geometry.value) and simple geometry objects
      const geometry = parcel.geometry?.value || parcel.geometry;
      
      if (!geometry?.coordinates) {
        continue;
      }

      // Point geometry: [lon, lat]
      if (geometry.type === 'Point' && Array.isArray(geometry.coordinates) && geometry.coordinates.length >= 2) {
        const [lon, lat] = geometry.coordinates as PointCoordinates;
        if (typeof lon === 'number' && typeof lat === 'number') {
          return detectTerrainProvider(lon, lat);
        }
      }

      // Polygon geometry: first coordinate of first ring
      if (Array.isArray(geometry.coordinates) && geometry.coordinates.length > 0) {
        const first = geometry.coordinates[0] as unknown;
        if (Array.isArray(first) && first.length > 0 && Array.isArray(first[0])) {
          const [lon, lat] = first[0] as [number, number];
          if (typeof lon === 'number' && typeof lat === 'number') {
            return detectTerrainProvider(lon, lat);
          }
        }
      }
    }
  }

  // Default to Cesium World Terrain (global)
  return 'cesium_world';
}

/**
 * Get terrain provider URL
 */
export function getTerrainProviderUrl(provider: TerrainProviderType | string): string {
  if (provider in TERRAIN_PROVIDERS) {
    return TERRAIN_PROVIDERS[provider];
  }
  if (typeof provider === 'string' && provider.startsWith('http')) {
    return provider;
  }
  return TERRAIN_PROVIDERS.cesium_world;
}

/**
 * Get terrain provider display name
 */
export function getTerrainProviderName(provider: TerrainProviderType): string {
  if (provider === 'idena') return 'IDENA (Navarra)';
  if (provider === 'ign') return 'IGN (España)';
  if (provider === 'cesium_world') return 'Cesium World Terrain (Global)';
  return String(provider);
}

export function getTerrainProviderDescription(provider: TerrainProviderType): string {
  if (provider === 'idena') return 'Modelo Digital de Terreno de Navarra (5m resolución)';
  if (provider === 'ign') return 'Modelo Digital de Terreno del IGN (España completa)';
  if (provider === 'cesium_world') return 'Cesium World Terrain (~30m, global) — gratuito';
  return String(provider);
}

