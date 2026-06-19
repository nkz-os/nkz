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
 * parcel-based detection. For 'eu' region, delegates terrain to the
 * eu-elevation module (does nothing).
 *
 * @param currentRegion - Resolved region from camera (for region-based auto)
 * @param layerAutoMode - Whether auto layer switching is active
 */
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
        // @ts-ignore
        const Cesium = window.Cesium;
        if (Cesium) viewer.terrainProvider = new Cesium.EllipsoidTerrainProvider();
      }
      return;
    }

    // @ts-ignore
    const Cesium = window.Cesium;
    if (!Cesium) return;

    // If a module (elevation, lidar, etc.) has already set a real terrain provider,
    // don't override it. Only interfere if the current provider is Ellipsoid (flat)
    // or if the user explicitly selected a host-managed provider (idena, ign).
    const currentProvider = viewer.terrainProvider;
    const isModuleManaged = currentProvider &&
        !(currentProvider instanceof Cesium.EllipsoidTerrainProvider) &&
        currentTerrainProvider === 'auto';
    if (isModuleManaged) {
        logger.debug('[CesiumMap] Terrain already set by module, skipping host override');
        return;
    }

    try {
      let terrainUrlToUse: string | null = null;
      let providerName = 'custom';

      if (currentTerrainProvider === 'idena') {
        terrainUrlToUse = TERRAIN_PROVIDERS.idena;
        providerName = 'IDENA';
      } else if (currentTerrainProvider === 'ign') {
        terrainUrlToUse = TERRAIN_PROVIDERS.ign;
        providerName = 'IGN';
      } else if (currentTerrainProvider === 'auto') {
        // Region-based auto (Sub-feature B) — when layerAutoMode is active
        if (layerAutoMode && currentRegion) {
          const regionTerrain = terrainProviderForRegion(currentRegion);
          if (regionTerrain === 'eu') {
            // EU/world → delegate to eu-elevation module.
            // The isModuleManaged guard above will skip host override
            // if the module has already set terrain. If no module is
            // present, leave ellipsoid (flat) terrain.
            logger.debug('[CesiumMap] EU/world region — delegating terrain to eu-elevation module');
            return;
          }
          // navarra → idena, spain → ign
          terrainUrlToUse = TERRAIN_PROVIDERS[regionTerrain];
          providerName = regionTerrain.toUpperCase();
          logger.debug('[CesiumMap] Region-based terrain:', regionTerrain);
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
