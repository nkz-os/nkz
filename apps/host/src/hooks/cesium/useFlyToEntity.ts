import { useEffect, useRef } from 'react';
import { getEntityCoordinates, getEntityGeometryType } from '@/utils/ngsiEntityCoordinates';
import { logger } from '@/utils/logger';

/**
 * Camera focus when the selected entity id/type changes (not when parent re-renders).
 * Runs after entity sync: caller must register this hook after the entities useEffect.
 * Parcels use a bounding-sphere distance derived from geometry (tighter framing).
 */
export function useFlyToEntity(
  viewerRef: React.MutableRefObject<any>,
  selectedEntity: any
) {
  const entityId = selectedEntity?.id ?? null;
  const entityKind = (selectedEntity?.type || selectedEntity?._type || '') as string;
  const selectedRef = useRef(selectedEntity);
  selectedRef.current = selectedEntity;

  useEffect(() => {
    if (!viewerRef.current || !entityId) return;

    const Cesium = window.Cesium;
    if (!Cesium) return;

    const viewer = viewerRef.current;
    const latestSelected = selectedRef.current;
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

    if (isParcel && latestSelected) {
      try {
        const coordinates = getEntityCoordinates(latestSelected);
        const gType = getEntityGeometryType(latestSelected);
        if (gType === 'Polygon' && coordinates?.[0] && Array.isArray(coordinates[0])) {
          const positions: any[] = [];
          (coordinates[0] as number[][]).forEach((coord: number[]) => {
            if (Array.isArray(coord) && coord.length >= 2) {
              const lon = Number(coord[0]);
              const lat = Number(coord[1]);
              if (!Number.isNaN(lon) && !Number.isNaN(lat)) {
                positions.push(Cesium.Cartesian3.fromDegrees(lon, lat, 0));
              }
            }
          });
          if (positions.length >= 3) {
            const bs = Cesium.BoundingSphere.fromPoints(positions);
            if (bs && Number.isFinite(bs.radius) && bs.radius > 0) {
              const offsetRange = Math.min(Math.max(bs.radius * 2.05, 95), 45_000);
              logger.debug('[useFlyToEntity] Parcel bounding sphere fly', { entityId, offsetRange });
              viewer.camera.flyToBoundingSphere(bs, {
                duration: 1.5,
                offset: new Cesium.HeadingPitchRange(0, -Cesium.Math.PI_OVER_FOUR, offsetRange),
              });
              return;
            }
          }
        }
      } catch (e) {
        logger.warn('[useFlyToEntity] Parcel geometry fly failed, falling back:', e);
      }
    }

    const entity = viewer.entities.getById(cesiumEntityId);
    if (entity) {
      logger.debug('[useFlyToEntity] flyTo entity', { cesiumEntityId, range });
      viewer.flyTo(entity, {
        duration: 1.5,
        offset: new Cesium.HeadingPitchRange(0, -Cesium.Math.PI_OVER_FOUR, range),
      });
    } else {
      logger.warn('[useFlyToEntity] Entity not found:', cesiumEntityId);
    }
  }, [entityId, entityKind]);
}
