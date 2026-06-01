import { describe, it, expect } from 'vitest';
import { parseFieldPhotos, FieldPhotoRecord } from '../fieldPhotos';

describe('parseFieldPhotos', () => {
  it('maps normalized NGSI-LD AgriParcelRecord entities', () => {
    const out = parseFieldPhotos([
      {
        id: 'urn:ngsi-ld:AgriParcelRecord:r1',
        type: 'AgriParcelRecord',
        imageUrl: { type: 'Property', value: '/api/field-images/t/abc.jpg' },
        location: { type: 'GeoProperty', value: { type: 'Point', coordinates: [-2.93, 43.26] } },
        dateObserved: { type: 'Property', value: '2026-05-30T10:15:00Z' },
        note: { type: 'Property', value: 'hoja amarilla' },
        accuracy: { type: 'Property', value: 3.4 },
        refAgriParcel: { type: 'Relationship', object: 'urn:ngsi-ld:AgriParcel:P1' },
      },
    ]);
    expect(out).toEqual<FieldPhotoRecord[]>([
      {
        id: 'urn:ngsi-ld:AgriParcelRecord:r1',
        imageUrl: '/api/field-images/t/abc.jpg',
        lng: -2.93, lat: 43.26,
        dateObserved: '2026-05-30T10:15:00Z',
        note: 'hoja amarilla', accuracy: 3.4,
        refAgriParcel: 'urn:ngsi-ld:AgriParcel:P1',
      },
    ]);
  });

  it('tolerates missing optionals and skips entries without imageUrl', () => {
    const out = parseFieldPhotos([
      { id: 'urn:r2', imageUrl: { value: '/api/field-images/t/x.jpg' } },
      { id: 'urn:r3' },
    ]);
    expect(out).toEqual([
      { id: 'urn:r2', imageUrl: '/api/field-images/t/x.jpg', lng: null, lat: null, dateObserved: '', note: '', accuracy: null, refAgriParcel: null },
    ]);
  });

  it('returns [] for non-array input', () => {
    expect(parseFieldPhotos(undefined as any)).toEqual([]);
  });
});
