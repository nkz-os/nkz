import { useEffect } from 'react';
import { logger } from '@/utils/logger';

/**
 * Ref to hold the host-managed tileset so we can remove it selectively
 * without destroying module-managed primitives.
 */
let hostTileset: any = null;

/**
 * Manages 3D Tiles tileset primitives on the Cesium viewer.
 * Only removes the previous host tileset — never calls primitives.removeAll(),
 * which would destroy tilesets added by external modules via the slot system.
 */
export function use3DTiles(
  viewerRef: React.MutableRefObject<any>,
  enable3DTiles: boolean,
  tilesetUrl: string
) {
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer) return;

    const Cesium = window.Cesium;
    if (!Cesium) return;

    // Remove only the previous host tileset (not all primitives)
    if (hostTileset) {
      try {
        viewer.scene.primitives.remove(hostTileset);
      } catch (e) {
        logger.warn('[CesiumMap] Error removing previous 3D Tiles:', e);
      }
      hostTileset = null;
    }

    if (enable3DTiles && tilesetUrl) {
      try {
        if (viewer.isDestroyed()) return;

        logger.log('[CesiumMap] Adding 3D Tiles from:', tilesetUrl);
        const tileset = viewer.scene.primitives.add(
          new Cesium.Cesium3DTileset({
            url: tilesetUrl,
          })
        );
        hostTileset = tileset;

        if (tileset.readyPromise) {
          tileset.readyPromise.then(() => {
            if (!viewer.isDestroyed()) {
              logger.log('[CesiumMap] 3D Tiles loaded successfully');
            }
          }).catch((error: any) => {
            logger.error('[CesiumMap] Error loading 3D Tiles:', error);
          });
        }
      } catch (error: any) {
        logger.error('[CesiumMap] Error adding 3D Tiles:', error);
      }
    }
  }, [enable3DTiles, tilesetUrl]);
}
