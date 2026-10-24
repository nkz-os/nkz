import { describe, expect, it } from 'vitest';
import { parcelLinksFragment, readParcelLinks } from '../parcelLinks';

describe('readParcelLinks', () => {
  it('reads a plain string (single-element list unwrapped by the broker)', () => {
    expect(readParcelLinks({ controlledAsset: 'u1' })).toEqual(['u1']);
  });
  it('reads {object: string}', () => {
    expect(readParcelLinks({ controlledAsset: { object: 'u1' } })).toEqual(['u1']);
  });
  it('reads a Relationship with a list', () => {
    expect(
      readParcelLinks({ controlledAsset: { type: 'Relationship', object: ['u1', 'u2'] } }),
    ).toEqual(['u1', 'u2']);
  });
  it('reads a list of objects', () => {
    expect(readParcelLinks({ controlledAsset: [{ object: 'u1' }, { object: 'u2' }] })).toEqual(['u1', 'u2']);
  });
  it('returns [] when absent or invalid', () => {
    expect(readParcelLinks({})).toEqual([]);
    expect(readParcelLinks(null)).toEqual([]);
  });
  it('drops the NGSI-LD null urn', () => {
    expect(readParcelLinks({ controlledAsset: { object: ['urn:ngsi-ld:null', 'u1'] } })).toEqual(['u1']);
  });
});

describe('parcelLinksFragment', () => {
  it('dedupes keeping first-appearance order', () => {
    expect(parcelLinksFragment(['u1', 'u2', 'u1'])).toEqual({
      controlledAsset: { type: 'Relationship', object: ['u1', 'u2'] },
    });
  });
  it('writes a list even for one parcel', () => {
    expect(parcelLinksFragment(['u1']).controlledAsset.object).toEqual(['u1']);
  });
  it('throws on an empty list', () => {
    expect(() => parcelLinksFragment([])).toThrow();
  });
});
