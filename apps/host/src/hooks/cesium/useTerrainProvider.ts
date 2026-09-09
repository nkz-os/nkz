import { useEffect } from 'react';
import { detectTerrainProviderFromParcels, TERRAIN_PROVIDERS, terrainProviderForRegion } from '@/utils/terrain';
import type { Parcel } from '@/types';
import type { RegionId } from '@/utils/regions';
import { logger } from '@/utils/logger';

/**
 * Manages Cesium terrain provider switching (IDENA/IGN/ellipsoid).
 * Extracted from CesiumMap.tsx terrain update useEffect.
 *
 * When layerAutoMode is true and currentTerrainProvider === 'auto',
 * uses the camera-position region signal (currentRegion) instead of
 * parcel-based detection. For 'eu' region, an active eu-elevation module
 * still wins (its provider is respected via the module-managed guard);
 * otherwise the host falls back to the tokenless global terrarium provider
 * (AWS Open Data) instead of flat ellipsoid terrain.
 *
 * @param currentRegion - Resolved region from camera (for region-based auto)
 * @param layerAutoMode - Whether auto layer switching is active
 */

// =============================================================================
// Tokenless global providers
// =============================================================================

/**
 * AWS Open Data terrarium tiles (Mapzen terrain, no token, CORS *).
 * Decodes RGB-encoded PNG heightmaps for Cesium's CustomHeightmapTerrainProvider.
 * Dataset max level: 15 — deeper levels are upsampled from the z15 ancestor.
 */
const TERRARIUM_BASE_URL = 'https://elevation-tiles-prod.s3.amazonaws.com/terrarium';
const TERRARIUM_MAX_LEVEL = 15;
const TERRARIUM_GRID_SIZE = 65; // heightmap samples per tile edge

function createTerrariumProvider(Cesium: any): any {
  let ctx: CanvasRenderingContext2D | null = null;

  return new Cesium.CustomHeightmapTerrainProvider({
    width: TERRARIUM_GRID_SIZE,
    height: TERRARIUM_GRID_SIZE,
    callback: async (x: number, y: number, level: number): Promise<Float32Array> => {
      // Clamp to dataset max level; request the ancestor tile and upsample its quadrant.
      const over = Math.max(0, level - TERRARIUM_MAX_LEVEL);
      const tileLevel = level - over;
      const tx = x >> over;
      const ty = y >> over;

      const res = await fetch(`${TERRARIUM_BASE_URL}/${tileLevel}/${tx}/${ty}.png`);
      if (!res.ok) {
        throw new Error(`terrarium tile ${tileLevel}/${tx}/${ty} failed: HTTP ${res.status}`);
      }
      const bitmap = await createImageBitmap(await res.blob());

      if (!ctx) {
        const canvas = document.createElement('canvas');
        canvas.width = TERRARIUM_GRID_SIZE;
        canvas.height = TERRARIUM_GRID_SIZE;
        ctx = canvas.getContext('2d', { willReadFrequently: true })!;
      }

      // Source quadrant within the ancestor tile (terrarium tiles are 256x256 px).
      const quad = 1 << over;
      const sx = ((x - (tx << over)) / quad) * bitmap.width;
      const sy = ((y - (ty << over)) / quad) * bitmap.height;
      ctx.drawImage(
        bitmap,
        sx, sy, bitmap.width / quad, bitmap.height / quad,
        0, 0, TERRARIUM_GRID_SIZE, TERRARIUM_GRID_SIZE,
      );
      const data = ctx.getImageData(0, 0, TERRARIUM_GRID_SIZE, TERRARIUM_GRID_SIZE).data;
      bitmap.close();

      // Terrarium encoding: elevation = (R * 256 + G + B / 256) - 32768
      const heights = new Float32Array(TERRARIUM_GRID_SIZE * TERRARIUM_GRID_SIZE);
      for (let i = 0, j = 0; i < heights.length; i++, j += 4) {
        heights[i] = data[j] * 256 + data[j + 1] + data[j + 2] / 256 - 32768;
      }
      return heights;
    },
  });
}

/**
 * Esri World Elevation (Terrain3D, LERC tiles, no token required).
 * Uses the async fromUrl() factory when available, legacy constructor otherwise.
 */
const ESRI_TERRAIN_URL =
  'https://elevation3d.arcgis.com/arcgis/rest/services/WorldElevation3D/Terrain3D/ImageServer';

function createEsriProvider(Cesium: any): Promise<any> {
  if (typeof Cesium.ArcGISTiledElevationTerrain?.fromUrl === 'function') {
    return Cesium.ArcGISTiledElevationTerrain.fromUrl(ESRI_TERRAIN_URL);
  }
  return Promise.resolve(new Cesium.ArcGISTiledElevationTerrain(ESRI_TERRAIN_URL));
}

export function useTerrainProvider(
  viewerRef: React.MutableRefObject<any>,
  enable3DTerrain: boolean,
  currentTerrainProvider: string,
  parcels: Parcel[],
  currentRegion?: RegionId,
  layerAutoMode?: boolean,
) {
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !enable3DTerrain) {
      if (viewer && !enable3DTerrain) {
        const Cesium = window.Cesium;
        if (Cesium) viewer.terrainProvider = new Cesium.EllipsoidTerrainProvider();
      }
      return;
    }

    const Cesium = window.Cesium;
    if (!Cesium) return;

    // If a module (elevation, lidar, etc.) has already set a real terrain provider,
    // don't override it. Only interfere if the current provider is Ellipsoid (flat),
    // a host-managed provider (tagged __nkzHostManaged: idena/ign/esri/terrarium),
    // or if the user explicitly selected a host-managed provider.
    const currentProvider = viewer.terrainProvider;
    const isModuleManaged = currentProvider &&
        !(currentProvider instanceof Cesium.EllipsoidTerrainProvider) &&
        !(currentProvider as any).__nkzHostManaged &&
        currentTerrainProvider === 'auto';
    if (isModuleManaged) {
        logger.debug('[CesiumMap] Terrain already set by module, skipping host override');
        return;
    }

    try {
      let terrainUrlToUse: string | null = null;
      let providerFactory: (() => any) | null = null; // class-based providers (esri/terrarium)
      let providerName = 'custom';

      const applyProvider = (providerInstance: any) => {
        if (viewer.isDestroyed()) return;
        (providerInstance as any).__nkzHostManaged = true;
        providerInstance.errorEvent?.addEventListener?.((error: any) => {
          logger.warn('[CesiumMap] Terrain provider error:', providerName, error);
          if (!viewer.isDestroyed()) viewer.terrainProvider = new Cesium.EllipsoidTerrainProvider();
        });
        viewer.terrainProvider = providerInstance;
        logger.debug('[CesiumMap] Terrain provider activated:', providerName);
      };

      const failProvider = (error: any) => {
        logger.error('[CesiumMap] Failed to load terrain provider:', providerName, error);
        if (!viewer.isDestroyed()) viewer.terrainProvider = new Cesium.EllipsoidTerrainProvider();
      };

      if (currentTerrainProvider === 'idena') {
        terrainUrlToUse = TERRAIN_PROVIDERS.idena;
        providerName = 'IDENA';
      } else if (currentTerrainProvider === 'ign') {
        terrainUrlToUse = TERRAIN_PROVIDERS.ign;
        providerName = 'IGN';
      } else if (currentTerrainProvider === 'esri') {
        providerName = 'Esri World';
        providerFactory = () => createEsriProvider(Cesium);
      } else if (currentTerrainProvider === 'terrarium') {
        providerName = 'Terrarium';
        providerFactory = () => createTerrariumProvider(Cesium);
      } else if (currentTerrainProvider === 'auto') {
        // Region-based auto (Sub-feature B) — when layerAutoMode is active
        if (layerAutoMode && currentRegion) {
          const regionTerrain = terrainProviderForRegion(currentRegion);
          if (regionTerrain === 'terrarium') {
            // EU/world → tokenless global open-data terrain. An active eu-elevation
            // module still wins via the isModuleManaged guard above; this is the
            // fallback when no module provider has been set (previously: flat terrain).
            providerName = 'Terrarium (auto EU/world)';
            providerFactory = () => createTerrariumProvider(Cesium);
            logger.debug('[CesiumMap] Region-based terrain: terrarium (EU/world fallback)');
          } else {
            // navarra → idena, spain → ign
            terrainUrlToUse = TERRAIN_PROVIDERS[regionTerrain];
            providerName = regionTerrain.toUpperCase();
            logger.debug('[CesiumMap] Region-based terrain:', regionTerrain);
          }
        } else {
          // Fallback to parcel-based auto (legacy mode, when no region signal)
          const parcelsForDetection = parcels.map(p => ({
            geometry: p.location?.value || undefined
          }));
          const detected = detectTerrainProviderFromParcels(parcelsForDetection);
          if (detected === 'cesium_world') {
            if (import.meta.env.VITE_CESIUM_ION_TOKEN || (window as any).__ENV__?.VITE_CESIUM_TOKEN) {
              providerName = 'Cesium World';
              try {
                if (typeof Cesium.createWorldTerrain === 'function') {
                  Cesium.Ion.defaultAccessToken = import.meta.env.VITE_CESIUM_ION_TOKEN || (window as any).__ENV__?.VITE_CESIUM_TOKEN;
                  viewer.terrainProvider = Cesium.createWorldTerrain({
                    requestVertexNormals: true,
                    requestWaterMask: false,
                  });
                  logger.debug('[CesiumMap] Terrain provider activated: Cesium World Terrain');
                } else {
                  viewer.terrainProvider = new Cesium.EllipsoidTerrainProvider();
                  logger.warn('[CesiumMap] createWorldTerrain not available, using ellipsoid');
                }
              } catch (e) {
                logger.warn('[CesiumMap] Failed to create Cesium World Terrain:', e);
                viewer.terrainProvider = new Cesium.EllipsoidTerrainProvider();
              }
              return;
            }
            logger.warn('[CesiumMap] No Cesium Ion token — falling back to IGN (España)');
            terrainUrlToUse = TERRAIN_PROVIDERS.ign;
            providerName = 'IGN (fallback)';
          } else {
            terrainUrlToUse = TERRAIN_PROVIDERS[detected];
            providerName = detected.toUpperCase();
          }
          logger.debug('[CesiumMap] Auto-detected terrain provider:', detected);
        }
      } else if (currentTerrainProvider && currentTerrainProvider.startsWith('http')) {
        terrainUrlToUse = currentTerrainProvider;
      } else if (currentTerrainProvider === 'cesium_world') {
        if (import.meta.env.VITE_CESIUM_ION_TOKEN || (window as any).__ENV__?.VITE_CESIUM_TOKEN) {
          providerName = 'Cesium World';
          try {
            if (typeof Cesium.createWorldTerrain === 'function') {
              Cesium.Ion.defaultAccessToken = import.meta.env.VITE_CESIUM_ION_TOKEN || (window as any).__ENV__?.VITE_CESIUM_TOKEN;
              viewer.terrainProvider = Cesium.createWorldTerrain({
                requestVertexNormals: true,
                requestWaterMask: false,
              });
              logger.debug('[CesiumMap] Terrain provider activated: Cesium World Terrain');
            } else {
              viewer.terrainProvider = new Cesium.EllipsoidTerrainProvider();
            }
          } catch (e) {
            viewer.terrainProvider = new Cesium.EllipsoidTerrainProvider();
          }
          return;
        }
        logger.warn('[CesiumMap] No Cesium Ion token — falling back to IGN (España)');
        terrainUrlToUse = TERRAIN_PROVIDERS.ign;
        providerName = 'IGN (fallback)';
      }

      if (providerFactory) {
        logger.debug('[CesiumMap] Activating terrain provider:', providerName);
        try {
          const instance = providerFactory();
          Promise.resolve(instance).then(applyProvider).catch(failProvider);
        } catch (e) {
          failProvider(e);
        }
        return;
      }

      if (terrainUrlToUse) {
        logger.debug('[CesiumMap] Activating terrain provider:', providerName);
        const baseUrl = terrainUrlToUse.replace('/layer.json', '');

        Cesium.CesiumTerrainProvider.fromUrl(baseUrl, {
          requestWaterMask: false,
          requestVertexNormals: true,
        })
          .then((terrainProviderInstance: any) => {
            if (viewer.isDestroyed()) return;

            if (!viewer.isDestroyed()) {
              terrainProviderInstance.errorEvent.addEventListener((error: any) => {
                logger.warn('[CesiumMap] Terrain provider error:', providerName, error);
                if (!viewer.isDestroyed()) viewer.terrainProvider = new Cesium.EllipsoidTerrainProvider();
              });
              (terrainProviderInstance as any).__nkzHostManaged = true;
              viewer.terrainProvider = terrainProviderInstance;
              logger.debug('[CesiumMap] Terrain provider activated:', providerName);
            }
          })
          .catch((error: any) => {
            logger.error('[CesiumMap] Failed to load terrain provider:', providerName, error);
            if (!viewer.isDestroyed()) viewer.terrainProvider = new Cesium.EllipsoidTerrainProvider();
          });
      }
    } catch (e) {
      logger.warn('[CesiumMap] Failed to configure terrain, using ellipsoid:', e);
      if (viewer && !viewer.isDestroyed()) viewer.terrainProvider = new Cesium.EllipsoidTerrainProvider();
    }
  }, [enable3DTerrain, currentTerrainProvider, parcels, currentRegion, layerAutoMode]);
}
