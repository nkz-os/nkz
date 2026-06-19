// =============================================================================
// useRegionResolver — debounced camera.moveEnd → region signal
// =============================================================================
// Binds to Cesium camera.moveEnd, resolves the camera centre against the
// region table, and updates MapRegionContext when the region changes.
// Also mirrors the current region onto viewer.__nkzRegion for cross-repo
// consumption (eu-elevation module reads it from useViewer().cesiumViewer).

import { useEffect, useRef } from 'react';
import { useMapRegion } from '../context/MapRegionContext';
import { resolveRegion, type RegionId } from '../utils/regions';

const DEBOUNCE_MS = 300;

/**
 * Pure testable function: determines whether the region should change
 * given a new camera centre position. Returns the new region if it differs
 * from current, or null if unchanged (so the hook can skip unnecessary updates).
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
 * updates MapRegionContext + mirrors onto viewer.__nkzRegion.
 *
 * Must be called inside a <MapRegionProvider>.
 * `viewer` is the Cesium.Viewer instance (any-typed because Cesium is a global).
 */
export function useRegionResolver(viewer: any | null): void {
  const { currentRegion, layerAutoMode, setRegion } = useMapRegion();
  const ref = useRef({ currentRegion, layerAutoMode });
  ref.current = { currentRegion, layerAutoMode };

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
          ref.current.currentRegion,
        );
        if (next) {
          setRegion(next);
          // Mirror for cross-repo (eu-elevation module) consumption.
          // Modules use useViewerOptional() → cesiumViewer → .__nkzRegion
          (viewer as Record<string, unknown>).__nkzRegion = {
            currentRegion: next,
            layerAutoMode: ref.current.layerAutoMode,
          };
        }
      }, DEBOUNCE_MS);
    };

    viewer.camera.moveEnd.addEventListener(onMoveEnd);

    return () => {
      if (timer) clearTimeout(timer);
      viewer.camera.moveEnd.removeEventListener(onMoveEnd);
    };
  }, [viewer, setRegion]);
}
