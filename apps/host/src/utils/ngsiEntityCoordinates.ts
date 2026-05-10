// =============================================================================
// NGSI-LD / host entity geometry helpers (shared by Cesium hooks and map)
// =============================================================================

/** Safely extract GeoJSON coordinates from Parcel API objects or NGSI-LD entities */
export const getEntityCoordinates = (entity: any): any[] | undefined => {
  if (!entity) return undefined;
  if (entity.geometry?.coordinates) return entity.geometry.coordinates;
  if (entity.location?.value?.coordinates) return entity.location.value.coordinates;
  if (entity.location?.coordinates) return entity.location.coordinates;
  return undefined;
};

export const getEntityGeometryType = (entity: any): string | undefined => {
  if (!entity) return undefined;
  if (entity.geometry?.type) return entity.geometry.type;
  if (entity.location?.value?.type) return entity.location.value.type;
  if (entity.location?.type && entity.location.type !== 'GeoProperty') return entity.location.type;
  return undefined;
};
