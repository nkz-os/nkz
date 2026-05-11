import { useEffect, useRef } from 'react';
import type { Parcel } from '@/types';
import { getEntityCoordinates, getEntityGeometryType } from '@/utils/ngsiEntityCoordinates';
import { logger } from '@/utils/logger';

/**
 * One-time camera framing when the parcel dataset identity changes.
 * Does not run while an entity is selected (deep links / list selection own the camera).
 * Separated from entity sync to avoid re-zoom on overlay/panel-driven re-renders.
 */
export function useInitialParcelsFit(
  viewerRef: React.MutableRefObject<any>,
  isViewerReady: boolean,
  parcelsIdentityKey: string,
  parcels: Parcel[],
  skipBecauseEntitySelected: boolean
) {
  const lastAppliedKeyRef = useRef<string | null>(null);

  useEffect(() => {
    if (!isViewerReady || !viewerRef.current || parcels.length === 0 || !parcelsIdentityKey) {
      return;
    }

    const Cesium = window.Cesium;
    if (!Cesium) return;

    if (skipBecauseEntitySelected) {
      lastAppliedKeyRef.current = parcelsIdentityKey;
      return;
    }

    if (lastAppliedKeyRef.current === parcelsIdentityKey) {
      return;
    }
    lastAppliedKeyRef.current = parcelsIdentityKey;

    const positions: any[] = [];
    for (const parcel of parcels) {
      try {
        const coordinates = getEntityCoordinates(parcel);
        if (!coordinates) continue;
        const gType = getEntityGeometryType(parcel);
        if (gType === 'Polygon' && Array.isArray(coordinates[0])) {
          (coordinates[0] as number[][]).forEach((coord: number[]) => {
            if (Array.isArray(coord) && coord.length >= 2) {
              const lon = Number(coord[0]);
              const lat = Number(coord[1]);
              if (!Number.isNaN(lon) && !Number.isNaN(lat)) {
                positions.push(Cesium.Cartesian3.fromDegrees(lon, lat, 0));
              }
            }
          });
        }
      } catch (e) {
        logger.warn('[useInitialParcelsFit] parcel geometry skip:', parcel?.id, e);
      }
    }

    if (positions.length < 2) {
      return;
    }

    const viewer = viewerRef.current;
    const bs = Cesium.BoundingSphere.fromPoints(positions);
    if (!bs || !Number.isFinite(bs.radius) || bs.radius <= 0) {
      return;
    }

    const range = Math.min(Math.max(bs.radius * 2.35, 260), 120_000);

    try {
      viewer.camera.flyToBoundingSphere(bs, {
        duration: 2.0,
        offset: new Cesium.HeadingPitchRange(0, -Cesium.Math.PI_OVER_FOUR, range),
      });
    } catch (e) {
      logger.warn('[useInitialParcelsFit] flyToBoundingSphere failed:', e);
    }
  }, [isViewerReady, parcelsIdentityKey, parcels, skipBecauseEntitySelected]);
}
