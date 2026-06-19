import { describe, it, expect } from 'vitest';
import { isGeocodeResult, parcelCentroid } from '@/types/geocode';
import type { Parcel } from '@/types';

describe('isGeocodeResult', () => {
  it('accepts well-formed result with bbox array', () => {
    const result: unknown = {
      label: 'Madrid',
      lat: 40.4168,
      lon: -3.7038,
      bbox: [-3.8895, 40.312, -3.5179, 40.6437],
      type: 'city',
      countryCode: 'ES',
    };
    expect(isGeocodeResult(result)).toBe(true);
  });

  it('accepts null bbox', () => {
    const result: unknown = {
      label: 'Small Village',
      lat: 42.1234,
      lon: -2.5678,
      bbox: null,
      type: 'village',
      countryCode: 'ES',
    };
    expect(isGeocodeResult(result)).toBe(true);
  });

  it('rejects missing lat', () => {
    const result: unknown = {
      label: 'No Lat',
      lon: -3.7038,
      bbox: null,
      type: 'other',
      countryCode: 'XX',
    };
    expect(isGeocodeResult(result)).toBe(false);
  });

  it('rejects missing lon', () => {
    const result: unknown = {
      label: 'No Lon',
      lat: 40.4168,
      bbox: null,
      type: 'other',
      countryCode: 'XX',
    };
    expect(isGeocodeResult(result)).toBe(false);
  });

  it('rejects non-object input', () => {
    expect(isGeocodeResult(null)).toBe(false);
    expect(isGeocodeResult(undefined)).toBe(false);
    expect(isGeocodeResult('string')).toBe(false);
    expect(isGeocodeResult(42)).toBe(false);
  });
});

describe('parcelCentroid', () => {
  it('returns centroid for Polygon with location GeoProperty (closed ring)', () => {
    const parcel: Parcel = {
      id: 'parcel-1',
      type: 'AgriParcel',
      name: 'Test Parcel',
      location: {
        type: 'GeoProperty',
        value: {
          type: 'Polygon',
          coordinates: [
            [
              [-3.8, 40.4],
              [-3.7, 40.4],
              [-3.7, 40.5],
              [-3.8, 40.5],
              [-3.8, 40.4], // closed
            ],
          ] as unknown as number[][],
        },
      },
    };
    const [lon, lat] = parcelCentroid(parcel);
    // Average of unique vertices: ([-3.8,-3.7,-3.7,-3.8]/4 = -3.75, [40.4,40.4,40.5,40.5]/4 = 40.45)
    expect(lon).toBeCloseTo(-3.75, 10);
    expect(lat).toBeCloseTo(40.45, 10);
  });

  it('returns centroid for Polygon with geometry (open ring)', () => {
    const parcel: Parcel = {
      id: 'parcel-2',
      type: 'AgriParcel',
      geometry: {
        type: 'Polygon',
        coordinates: [
          [
            [2.0, 41.0],
            [2.1, 41.0],
            [2.1, 41.1],
            [2.0, 41.1],
            [2.0, 41.0], // closed
          ],
        ],
      },
    };
    const [lon, lat] = parcelCentroid(parcel);
    expect(lon).toBeCloseTo(2.05, 10);
    expect(lat).toBeCloseTo(41.05, 10);
  });

  it('returns centroid for Point geometry', () => {
    const parcel: Parcel = {
      id: 'parcel-3',
      type: 'AgriParcel',
      location: {
        type: 'GeoProperty',
        value: {
          type: 'Point',
          coordinates: [-0.5, 38.5] as [number, number],
        },
      },
    };
    const [lon, lat] = parcelCentroid(parcel);
    expect(lon).toBeCloseTo(-0.5, 10);
    expect(lat).toBeCloseTo(38.5, 10);
  });

  it('returns [0, 0] for parcel with no geometry', () => {
    const parcel: Parcel = {
      id: 'parcel-4',
      type: 'AgriParcel',
    };
    expect(parcelCentroid(parcel)).toEqual([0, 0]);
  });

  it('returns [0, 0] for Polygon with fewer than 3 vertices', () => {
    const parcel: Parcel = {
      id: 'parcel-5',
      type: 'AgriParcel',
      geometry: {
        type: 'Polygon',
        coordinates: [
          [
            [2.0, 41.0],
            [2.1, 41.0],
          ],
        ],
      },
    };
    expect(parcelCentroid(parcel)).toEqual([0, 0]);
  });
});
