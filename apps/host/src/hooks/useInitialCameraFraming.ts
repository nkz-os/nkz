import * as Cesium from 'cesium';
import { EU_RECTANGLE, boundingSphereForParcels } from '@/utils/cameraFraming';

export type FramingDecision =
  | { kind: 'parcels'; centroids: Array<[number, number]> }
  | { kind: 'eu' };

export function decideInitialFraming(parcelCentroids: Array<[number, number]>): FramingDecision {
  return parcelCentroids.length ? { kind: 'parcels', centroids: parcelCentroids } : { kind: 'eu' };
}

/** Apply the decision to a live viewer. Fail-safe: any error → EU view. */
export function applyInitialFraming(viewer: Cesium.Viewer, parcelCentroids: Array<[number, number]>): void {
  try {
    const d = decideInitialFraming(parcelCentroids);
    if (d.kind === 'parcels') {
      const sphere = boundingSphereForParcels(d.centroids);
      if (sphere) { viewer.camera.flyToBoundingSphere(sphere, { duration: 1.2 }); return; }
    }
    viewer.camera.flyTo({ destination: EU_RECTANGLE, duration: 1.2 });
  } catch {
    viewer.camera.flyTo({ destination: EU_RECTANGLE, duration: 0 });
  }
}
