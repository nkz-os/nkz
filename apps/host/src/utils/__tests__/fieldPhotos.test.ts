import { describe, it, expect } from 'vitest';
import { parseFieldPhotos, FieldPhotoRecord, photosInWindow, photosByTag, PHOTO_TAGS, PhotoTag } from '../fieldPhotos';

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
        category: { type: 'Property', value: 'pest' },
      },
    ]);
    expect(out).toEqual<FieldPhotoRecord[]>([
      {
        id: 'urn:ngsi-ld:AgriParcelRecord:r1',
        imageUrl: '/api/field-images/t/abc.jpg',
        lng: -2.93, lat: 43.26,
        dateObserved: '2026-05-30T10:15:00Z',
        note: 'hoja amarilla', accuracy: 3.4,
        parcelId: 'urn:ngsi-ld:AgriParcel:P1',
        tag: 'pest',
      },
    ]);
  });

  it('tolerates missing optionals and skips entries without imageUrl', () => {
    const out = parseFieldPhotos([
      { id: 'urn:r2', imageUrl: { value: '/api/field-images/t/x.jpg' } },
      { id: 'urn:r3' },
    ]);
    expect(out).toEqual([
      { id: 'urn:r2', imageUrl: '/api/field-images/t/x.jpg', lng: null, lat: null, dateObserved: '', note: '', accuracy: null, parcelId: null, tag: null },
    ]);
  });

  it('reads hasAgriParcel first, then refAgriParcel', () => {
    const base = { imageUrl: { value: '/i.jpg' } };
    const both = parseFieldPhotos([{ id: 'a', ...base,
      hasAgriParcel: { object: 'urn:new' }, refAgriParcel: { object: 'urn:old' } }]);
    expect(both[0].parcelId).toBe('urn:new');
    const legacy = parseFieldPhotos([{ id: 'b', ...base, refAgriParcel: { object: 'urn:old' } }]);
    expect(legacy[0].parcelId).toBe('urn:old');
  });

  it('reads category compact or as full IRI key; unknown -> other; absent -> null', () => {
    const base = { imageUrl: { value: '/i.jpg' } };
    const out = parseFieldPhotos([
      { id: 'a', ...base, category: { value: 'weed' } },
      { id: 'b', ...base, 'https://smartdatamodels.org/dataModel.Agrifood/category': { value: 'soil_water' } },
      { id: 'c', ...base, category: { value: 'bogus' } },
      { id: 'd', ...base },
    ]);
    expect(out.map(p => p.tag)).toEqual(['weed', 'soil_water', 'other', null]);
  });

  it('still skips records without imageUrl even with a category', () => {
    expect(parseFieldPhotos([{ id: 'w', category: { value: 'pest' } }])).toEqual([]);
  });

  it('returns [] for non-array input', () => {
    expect(parseFieldPhotos(undefined as any)).toEqual([]);
  });
});

const mk = (id: string, date: string): FieldPhotoRecord => ({
  id, imageUrl: `/i/${id}.jpg`, lng: 0, lat: 0, dateObserved: date, note: '', accuracy: null, parcelId: null, tag: null,
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

describe('photosByTag', () => {
  const tagged = (id: string, tag: PhotoTag | null): FieldPhotoRecord => ({ ...mk(id, '2026-05-01T00:00:00Z'), tag });
  const photos = [tagged('a', 'pest'), tagged('b', 'weed'), tagged('c', null)];

  it('null returns all, including untagged', () => {
    expect(photosByTag(photos, null).map(p => p.id)).toEqual(['a', 'b', 'c']);
  });

  it('keeps only selected tags (untagged excluded)', () => {
    expect(photosByTag(photos, new Set<PhotoTag>(['pest', 'soil_water'])).map(p => p.id)).toEqual(['a']);
  });

  it('exposes the vocabulary', () => {
    expect(PHOTO_TAGS).toEqual(['pest', 'disease', 'weed', 'damage', 'crop_status', 'soil_water', 'other']);
  });
});
