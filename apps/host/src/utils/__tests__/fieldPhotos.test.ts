import { describe, it, expect } from 'vitest';
import { parseFieldPhotos, FieldPhotoRecord, photosInWindow } from '../fieldPhotos';

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

const mk = (id: string, date: string): FieldPhotoRecord => ({
  id, imageUrl: `/i/${id}.jpg`, lng: 0, lat: 0, dateObserved: date, note: '', accuracy: null, refAgriParcel: null,
});

describe('photosInWindow', () => {
  const photos = [mk('a', '2026-05-01T00:00:00Z'), mk('b', '2026-05-20T00:00:00Z'), mk('c', '2026-06-10T00:00:00Z')];

  it('keeps photos within currentDate ± windowDays, sorted ascending', () => {
    const out = photosInWindow(photos, new Date('2026-05-18T00:00:00Z'), 7);
    expect(out.map(p => p.id)).toEqual(['b']); // only May 20 within ±7d of May 18
  });

  it('widens with a larger window', () => {
    const out = photosInWindow(photos, new Date('2026-05-18T00:00:00Z'), 30);
    expect(out.map(p => p.id)).toEqual(['a', 'b', 'c']);
  });

  it('windowDays null returns all dated photos sorted ascending', () => {
    const out = photosInWindow([mk('z', ''), ...photos], new Date(), null);
    expect(out.map(p => p.id)).toEqual(['a', 'b', 'c']); // undated dropped, rest sorted
  });

  it('excludes undated photos when windowed', () => {
    expect(photosInWindow([mk('z', '')], new Date('2026-05-18T00:00:00Z'), 7)).toEqual([]);
  });
});
