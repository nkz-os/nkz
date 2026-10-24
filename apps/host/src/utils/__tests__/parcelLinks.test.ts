import { describe, expect, it } from 'vitest';
import {
  deviceLinks,
  parcelLinksFragment,
  parkOptionsFor,
  readControlledLinks,
  readLinkOwner,
  readParcelLinks,
  toParkOption,
} from '../parcelLinks';

const A = 'urn:ngsi-ld:AgriParcel:a';
const B = 'urn:ngsi-ld:AgriParcel:b';
const K = 'urn:ngsi-ld:AgriSolarPark:k';

describe('readParcelLinks', () => {
  it('reads a plain string (single-element list unwrapped by the broker)', () => {
    expect(readParcelLinks({ controlledAsset: A })).toEqual([A]);
  });
  it('reads {object: string}', () => {
    expect(readParcelLinks({ controlledAsset: { object: A } })).toEqual([A]);
  });
  it('reads a Relationship with a list', () => {
    expect(
      readParcelLinks({ controlledAsset: { type: 'Relationship', object: [A, B] } }),
    ).toEqual([A, B]);
  });
  it('reads a list of objects', () => {
    expect(readParcelLinks({ controlledAsset: [{ object: A }, { object: B }] })).toEqual([A, B]);
  });
  it('returns [] when absent or invalid', () => {
    expect(readParcelLinks({})).toEqual([]);
    expect(readParcelLinks(null)).toEqual([]);
  });
  it('drops the NGSI-LD null urn', () => {
    expect(readParcelLinks({ controlledAsset: { object: ['urn:ngsi-ld:null', A] } })).toEqual([A]);
  });
});

describe('parcelLinksFragment', () => {
  it('dedupes keeping first-appearance order', () => {
    expect(parcelLinksFragment([A, B, A])).toEqual({
      controlledAsset: { type: 'Relationship', object: [A, B] },
    });
  });
  it('writes a list even for one parcel', () => {
    expect(parcelLinksFragment([A]).controlledAsset.object).toEqual([A]);
  });
  it('throws on an empty list', () => {
    expect(() => parcelLinksFragment([])).toThrow();
  });
});

describe('park ownership helpers', () => {
  const owned = { controlledAsset: { type: 'Relationship', object: [K, A, B] } };

  it('readParcelLinks drops the park, readControlledLinks keeps everything', () => {
    expect(readParcelLinks(owned)).toEqual([A, B]);
    expect(readControlledLinks(owned)).toEqual([K, A, B]);
  });

  it('readLinkOwner reads the first entry', () => {
    expect(readLinkOwner(owned)).toEqual({ urn: K, kind: 'park' });
    expect(readLinkOwner({ controlledAsset: [A, K] })).toEqual({ urn: A, kind: 'parcel' });
    expect(readLinkOwner({ controlledAsset: 'urn:ngsi-ld:Other:x' })).toBeNull();
    expect(readLinkOwner({})).toBeNull();
  });

  it('deviceLinks puts the park first, dedupes and refuses no parcels', () => {
    expect(deviceLinks(K, [A, B])).toEqual([K, A, B]);
    expect(deviceLinks(null, [A])).toEqual([A]);
    expect(deviceLinks(K, [A, A])).toEqual([K, A]);
    expect(() => deviceLinks(K, [])).toThrow();
  });

  it('parkOptionsFor keeps only parks on a selected parcel', () => {
    const parks = [
      toParkOption({ id: K, name: { value: 'Park K' }, hasAgriParcel: { object: A } }),
      toParkOption({ id: 'urn:ngsi-ld:AgriSolarPark:j', name: 'Park J', hasAgriParcel: { object: B } }),
    ];
    expect(parks[0]).toEqual({ id: K, name: 'Park K', parcelUrns: [A] });
    expect(parkOptionsFor(parks, [A]).map(p => p.id)).toEqual([K]);
    expect(parkOptionsFor(parks, [])).toEqual([]);
  });
});
