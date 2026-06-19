// =============================================================================
// Region resolution — camera-position bbox hierarchy
// =============================================================================
// Provides a single region signal based on camera centre position.
// Imagery and terrain both follow this region, with manual override support.

export type RegionId = 'navarra' | 'spain' | 'eu' | 'world';

export interface RegionDef {
  id: RegionId;
  bbox: [number, number, number, number]; // [w, s, e, n]
  priority: number;                        // higher = more specific
  imagery: 'pnoa' | 'esri';
  terrain: 'idena' | 'ign' | 'eu';
}

/**
 * Region table ordered by specificity.
 *
 * Navarra and Spain share 'pnoa' imagery (IGN PNOA orthophoto covers all Spain).
 * IDENA is terrain-only (Navarra MDT05 5m).
 * EU/world use ESRI satellite imagery + eu-elevation module terrain.
 *
 * Bboxes aligned with existing terrain.ts constants (NAVARRA_BOUNDS, SPAIN_BOUNDS).
 * Spain includes Canary Islands (-18.5 lon).
 * 'world' is the catch-all fallback (not in table — resolveRegion returns 'world'
 * when no entry matches).
 */
export const REGION_TABLE: RegionDef[] = [
  { id: 'navarra', bbox: [-2.5, 42.0, -1.0, 43.5],   priority: 3, imagery: 'pnoa', terrain: 'idena' },
  { id: 'spain',   bbox: [-18.5, 27.0, 5.0, 44.0],   priority: 2, imagery: 'pnoa', terrain: 'ign' },
  { id: 'eu',      bbox: [-12.0, 34.0, 32.0, 62.0],  priority: 1, imagery: 'esri', terrain: 'eu' },
];

/** Hysteresis margin in degrees — prevents flicker at borders. */
const HYSTERESIS_DEG = 0.15;

function inBbox(
  lon: number, lat: number,
  b: [number, number, number, number],
  margin = 0
): boolean {
  return (
    lon >= b[0] - margin &&
    lon <= b[2] + margin &&
    lat >= b[1] - margin &&
    lat <= b[3] + margin
  );
}

/**
 * Resolve the most specific region containing (lon, lat).
 *
 * Regions are checked by priority (highest first). If `current` matches a region,
 * a hysteresis margin is applied so small camera movements near a border don't
 * cause flips. Outside all defined regions → 'world'.
 */
export function resolveRegion(
  lon: number,
  lat: number,
  current: RegionId
): RegionId {
  const ordered = [...REGION_TABLE].sort((a, b) => b.priority - a.priority);
  for (const r of ordered) {
    const margin = r.id === current ? HYSTERESIS_DEG : 0;
    if (inBbox(lon, lat, r.bbox, margin)) return r.id;
  }
  return 'world';
}

/** Get the full region definition for a given region id. */
export function regionDef(id: RegionId): RegionDef | undefined {
  return REGION_TABLE.find(r => r.id === id);
}
