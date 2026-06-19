import * as Cesium from 'cesium';
import type { GeocodeResult } from '@/types/geocode';

// [westLon, southLat, eastLon, northLat] — mainland EU framing (tune in manual verification).
export const EU_RECTANGLE_DEGREES: [number, number, number, number] = [-12, 34, 32, 62];

export const EU_RECTANGLE = Cesium.Rectangle.fromDegrees(...EU_RECTANGLE_DEGREES);

const HEIGHT_BY_TYPE: Record<GeocodeResult['type'], number> = {
  country: 1_200_000, region: 500_000, county: 250_000, city: 60_000,
  town: 30_000, village: 12_000, street: 3_000, house: 1_200, other: 50_000,
};

export function heightForResult(r: GeocodeResult): number {
  return HEIGHT_BY_TYPE[r.type] ?? HEIGHT_BY_TYPE.other;
}

export function bboxCenter(b: [number, number, number, number]): [number, number] {
  return [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2];
}

/** Fly camera to a geocode result: use bbox if present, else point+height-by-type. */
export function flyToForResult(viewer: Cesium.Viewer, r: GeocodeResult): void {
  if (r.bbox) {
    viewer.camera.flyTo({ destination: Cesium.Rectangle.fromDegrees(...r.bbox) });
  } else {
    viewer.camera.flyTo({ destination: Cesium.Cartesian3.fromDegrees(r.lon, r.lat, heightForResult(r)) });
  }
}

/** BoundingSphere over parcel centroids ([lon,lat] pairs). Empty array → null. */
export function boundingSphereForParcels(centroids: Array<[number, number]>): Cesium.BoundingSphere | null {
  if (!centroids.length) return null;
  const pts = centroids.map(([lon, lat]) => Cesium.Cartesian3.fromDegrees(lon, lat));
  return Cesium.BoundingSphere.fromPoints(pts);
}
