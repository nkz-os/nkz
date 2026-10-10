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

describe('normalizeToAsset land entities', () => {
  const rel = { type: 'Relationship', object: PARCEL };

  it.each([
    ['AgriParcelZone', 'parcels'],
    ['AgriSoil', 'parcels'],
    ['AgriSoilExtended', 'parcels'],
    ['AgriFarm', 'parcels'],
    ['AgriGreenhouse', 'infrastructure'],
  ])('classifies %s as %s and nests it under its parcel', (type, category) => {
    const asset = normalizeToAsset({ id: `urn:ngsi-ld:${type}:x1`, type, refAgriParcel: rel });
    expect(asset.category).toBe(category);
    expect(asset.parentId).toBe(PARCEL);
  });

  it('names an unnamed entity after its type and the last id segment', () => {
    const zone = normalizeToAsset({ id: 'urn:ngsi-ld:AgriParcelZone:p1:twi-very-high', type: 'AgriParcelZone' });
    expect(zone.name).toBe('Zona de parcela · twi-very-high');
  });

  it('shortens a uuid id segment in the fallback name', () => {
    const soil = normalizeToAsset({
      id: 'urn:ngsi-ld:AgriSoilExtended:da36ccd2-85d2-4c76-b552-c5c835a987c1',
      type: 'AgriSoilExtended',
    });
    expect(soil.name).toBe('Suelo · da36ccd2');
  });

  it('shortens a uuid inside the last id segment', () => {
    const crop = normalizeToAsset({
      id: 'urn:ngsi-ld:AgriCrop:tenant-a:da36ccd2-85d2-4c76-b552-c5c835a987c1-default',
      type: 'AgriCrop',
    });
    expect(crop.name).toBe('Cultivo · da36ccd2-default');
  });

  it('keeps a real name', () => {
    expect(normalizeToAsset({ id: 'urn:ngsi-ld:AgriParcelZone:z', type: 'AgriParcelZone', name: 'North quadrant' }).name)
      .toBe('North quadrant');
  });
});
