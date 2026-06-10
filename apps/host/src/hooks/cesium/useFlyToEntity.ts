import { useEffect, useRef } from 'react';
import { logger } from '@/utils/logger';

/**
 * Camera framing only when `listCameraNonce` bumps (left entity list + cameraFrame).
 * Map picks and module UI do not bump the nonce, so the camera is left unchanged.
 * Parcels: `viewer.zoomTo(entity)` so Cesium frames the polygon hull (avoids ground-level punch-in).
 */
export function useFlyToEntity(
  viewerRef: React.MutableRefObject<any>,
  selectedEntity: any,
  listCameraNonce: number
) {
  const selectedRef = useRef(selectedEntity);
  selectedRef.current = selectedEntity;

  useEffect(() => {
    if (!viewerRef.current || listCameraNonce === 0) return;

    const Cesium = window.Cesium;
    if (!Cesium) return;

    const latest = selectedRef.current;
    const entityId = latest?.id ?? null;
    if (!entityId) return;

    const entityKind = (latest?.type || latest?._type || '') as string;
    let cesiumEntityId = '';
    let range = 500;

    switch (entityKind) {
      case 'AgriParcel':
      case 'parcel':
        cesiumEntityId = `parcel-${entityId}`;
        range = 1000;
        break;
      case 'AutonomousMobileRobot':
      case 'robot':
        cesiumEntityId = `robot-${entityId}`;
        range = 50;
        break;
      case 'AgriSensor':
      case 'sensor':
        cesiumEntityId = `sensor-${entityId}`;
        range = 50;
        break;
      case 'ManufacturingMachine':
      case 'machine':
        cesiumEntityId = `machine-${entityId}`;
        range = 100;
        break;
      case 'LivestockAnimal':
      case 'livestock':
        cesiumEntityId = `livestock-${entityId}`;
        range = 100;
        break;
      case 'WeatherObserved':
      case 'weather':
        cesiumEntityId = `weather-${entityId}`;
        range = 1000;
        break;
      case 'AgriCrop':
      case 'crop':
        cesiumEntityId = `crop-${entityId}`;
        range = 200;
        break;
      case 'AgriBuilding':
      case 'building':
        cesiumEntityId = `building-${entityId}`;
        range = 300;
        break;
      case 'Device':
      case 'device':
        cesiumEntityId = `device-${entityId}`;
        range = 50;
        break;
      default:
        cesiumEntityId = entityId;
    }

    const isParcel = entityKind === 'AgriParcel' || entityKind === 'parcel';

    const attemptFly = (): boolean => {
      const v = viewerRef.current;
      if (!v || v.isDestroyed?.()) return true;

      if (isParcel) {
        const e = v.entities.getById(cesiumEntityId);
        if (!e) return false;
        logger.debug('[useFlyToEntity] zoomTo parcel', { cesiumEntityId, listCameraNonce });
        Promise.resolve(v.zoomTo(e)).catch((err: unknown) => {
          logger.warn('[useFlyToEntity] zoomTo parcel failed:', err);
        });
        return true;
      }

      const entity = v.entities.getById(cesiumEntityId);
      if (!entity) return false;
      logger.debug('[useFlyToEntity] flyTo entity', { cesiumEntityId, range, listCameraNonce });
      v.flyTo(entity, {
        duration: 1.5,
        offset: new Cesium.HeadingPitchRange(0, -Cesium.Math.PI_OVER_FOUR, range),
      });
      return true;
    };

    if (attemptFly()) return;

    let frames = 0;
    const maxFrames = 15;
    const tick = () => {
      if (attemptFly() || frames++ >= maxFrames) return;
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }, [listCameraNonce]);
}
