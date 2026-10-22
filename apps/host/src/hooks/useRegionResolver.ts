// =============================================================================
// useRegionResolver — debounced camera.moveEnd → region signal
// =============================================================================
// Binds to Cesium camera.moveEnd, resolves the camera centre against the
// region table, and calls onRegionChange when the region changes.
// Also mirrors the current region onto viewer.__nkzRegion for cross-repo
// consumption (eu-elevation module reads it from useViewer().cesiumViewer).
//
// NOTE: This hook does NOT depend on MapRegionContext — consumers manage
// their own currentRegion/layerAutoMode state and decide whether to apply
// the change (respecting layerAutoMode). The mirror onto __nkzRegion is
// done here for cross-repo convenience.

import { useEffect, useRef } from 'react';
import { resolveRegion, type RegionId } from '../utils/regions';

const DEBOUNCE_MS = 300;

/**
 * Pure testable function: determines whether the region should change
 * given a new camera centre position. Returns the new region if it differs
 * from current, or null if unchanged (so consumers can skip unnecessary updates).
 */
export function nextRegionOnMove(
  lon: number,
  lat: number,
  current: RegionId,
): RegionId | null {
  const next = resolveRegion(lon, lat, current);
  return next === current ? null : next;
}

/**
 * Get the camera centre lon/lat from a Cesium viewer.
 * Uses globe.pick at the screen centre for terrain-aware coordinates.
 */
function cameraCenterLonLat(
  viewer: any,
): [number, number] | null {
  const canvas = viewer.canvas;
  if (!canvas) return null;
  const Cesium = (window as any).Cesium;
  if (!Cesium) return null;
  const ray = viewer.camera.getPickRay(
    new Cesium.Cartesian2(canvas.clientWidth / 2, canvas.clientHeight / 2),
  );
  if (!ray) return null;
  const pos = viewer.scene.globe.pick(ray, viewer.scene);
  if (!pos) return null;
  const c = Cesium.Cartographic.fromCartesian(pos);
  return [
    Cesium.Math.toDegrees(c.longitude),
    Cesium.Math.toDegrees(c.latitude),
  ];
}

/**
 * React hook: listens to camera.moveEnd, debounces, resolves region,
 * calls onRegionChange when the region changes, and mirrors onto
 * viewer.__nkzRegion for cross-repo consumption (eu-elevation module).
 *
 * @param viewer - Cesium.Viewer instance (any-typed, Cesium is a global)
 * @param onRegionChange - Called with the new region id when it changes.
 *   The consumer should apply the change only if layerAutoMode is true.
 */
export function useRegionResolver(
  viewer: any | null,
  onRegionChange: (region: RegionId) => void,
): void {
  const currentRef = useRef<RegionId>('eu');

  useEffect(() => {
    if (!viewer) return;

    let timer: ReturnType<typeof setTimeout> | null = null;

    const onMoveEnd = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        const center = cameraCenterLonLat(viewer);
        if (!center) return;
        const next = nextRegionOnMove(
          center[0],
          center[1],
          currentRef.current,
        );
        if (next) {
          currentRef.current = next;
          onRegionChange(next);
          // Mirror for cross-repo (eu-elevation module) consumption.
          // Modules use useViewerOptional() → cesiumViewer → .__nkzRegion
          const existing = (viewer as Record<string, unknown>).__nkzRegion as
            | { currentRegion: string; layerAutoMode: boolean }
            | undefined;
          (viewer as Record<string, unknown>).__nkzRegion = {
            currentRegion: next,
            layerAutoMode: existing?.layerAutoMode ?? true,
          };
        }
      }, DEBOUNCE_MS);
    };

    viewer.camera.moveEnd.addEventListener(onMoveEnd);

    return () => {
      if (timer) clearTimeout(timer);
      viewer.camera.moveEnd.removeEventListener(onMoveEnd);
    };
  }, [viewer, onRegionChange]);
}
