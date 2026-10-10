import { describe, expect, it } from 'vitest';
import { normalizeToAsset } from '../assets';

const PARCEL = 'urn:ngsi-ld:AgriParcel:p1';

describe('normalizeToAsset parent resolution', () => {
  it('nests a crop under the parcel its relationship names', () => {
    const asset = normalizeToAsset({
      id: 'urn:ngsi-ld:AgriCrop:c1',
      type: 'AgriCrop',
      refAgriParcel: { type: 'Relationship', object: PARCEL },
    });
    expect(asset.parentId).toBe(PARCEL);
  });

  it('nests a weather station projection under its parcel', () => {
    const asset = normalizeToAsset({
      id: 'urn:ngsi-ld:WeatherObserved:parcel-p1',
      type: 'WeatherStation',
      refParcel: { type: 'Relationship', object: PARCEL },
    });
    expect(asset.parentId).toBe(PARCEL);
  });

  it('keeps an entity without parent relationship at root', () => {
    expect(normalizeToAsset({ id: 'urn:ngsi-ld:Device:d1', type: 'Device' }).parentId).toBeUndefined();
  });
});

describe('normalizeToAsset weather stations', () => {
  it('classifies the WeatherStation projection as weather', () => {
    expect(normalizeToAsset({ id: 'w', type: 'WeatherStation' }).category).toBe('weather');
  });
});
