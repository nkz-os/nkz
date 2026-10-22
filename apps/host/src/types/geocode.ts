import type { Parcel } from '@/types';

export type GeocodeType =
  | 'country' | 'region' | 'county' | 'city' | 'town' | 'village'
  | 'street' | 'house' | 'other';

export interface GeocodeResult {
  label: string;
  lat: number;
  lon: number;
  bbox: [number, number, number, number] | null;
  type: GeocodeType;
  countryCode: string;
}

export interface GeocodeResponse { results: GeocodeResult[]; }

export function isGeocodeResult(x: unknown): x is GeocodeResult {
  const r = x as Record<string, unknown>;
  return !!r && typeof r.label === 'string'
    && typeof r.lat === 'number' && Number.isFinite(r.lat)
    && typeof r.lon === 'number' && Number.isFinite(r.lon)
    && (r.bbox === null || (Array.isArray(r.bbox) && r.bbox.length === 4))
    && typeof r.type === 'string' && typeof r.countryCode === 'string';
}

// Polygon centroid helper — average of outer ring vertices for Polygons, direct coords for Points.
// For Polygons: geometry.coordinates is number[][][] (first ring = outer boundary)
// Handle closed rings (last vertex === first) by excluding the duplicate.
export function parcelCentroid(parcel: Parcel): [number, number] | null {
  const geo = parcel.location?.value ?? parcel.geometry;
  if (!geo) return null;
  if (geo.type === 'Point') {
    return geo.coordinates as [number, number];
  }
  // Polygon
  const ring = (geo.coordinates as number[][][])[0];
  if (!ring || ring.length < 3) return null;
  const isClosed = ring[ring.length - 1][0] === ring[0][0] && ring[ring.length - 1][1] === ring[0][1];
  const n = isClosed ? ring.length - 1 : ring.length;
  const lon = ring.slice(0, n).reduce((s, c) => s + c[0], 0) / n;
  const lat = ring.slice(0, n).reduce((s, c) => s + c[1], 0) / n;
  return [lon, lat];
}
