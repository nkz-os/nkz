// =============================================================================
// MapRegionContext — single source of truth for camera-position region
// =============================================================================
// Published by the host for both internal (imagery controller, terrain) and
// cross-repo consumption (eu-elevation module). Modules read the value from
// viewer.__nkzRegion (mirrored in useRegionResolver).

import { createContext, useContext, useMemo, useState, type ReactNode } from 'react';
import type { RegionId } from '../utils/regions';

export interface MapRegionContextValue {
  /** The currently active region, driven by camera position or manual override. */
  currentRegion: RegionId;
  /** When true, imagery + terrain auto-follow currentRegion.
   *  Set to false when the user explicitly picks a layer. */
  layerAutoMode: boolean;
  /** Called by useRegionResolver when camera moveEnd changes the region. */
  setRegion: (r: RegionId) => void;
  /** User picked a layer manually → freeze auto-switching. */
  setManual: () => void;
  /** Re-enable automatic region-based switching. */
  enableAuto: () => void;
}

const MapRegionCtx = createContext<MapRegionContextValue | null>(null);

export function MapRegionProvider({ children }: { children: ReactNode }) {
  const [currentRegion, setCurrentRegion] = useState<RegionId>('eu');
  const [layerAutoMode, setAuto] = useState(true);

  const value = useMemo<MapRegionContextValue>(
    () => ({
      currentRegion,
      layerAutoMode,
      setRegion: setCurrentRegion,
      setManual: () => setAuto(false),
      enableAuto: () => setAuto(true),
    }),
    [currentRegion, layerAutoMode],
  );

  return <MapRegionCtx.Provider value={value}>{children}</MapRegionCtx.Provider>;
}

export function useMapRegion(): MapRegionContextValue {
  const v = useContext(MapRegionCtx);
  if (!v) throw new Error('useMapRegion must be used within MapRegionProvider');
  return v;
}
