import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import i18n from 'i18next';
import { beforeAll, describe, expect, it } from 'vitest';
import { controlledPropertyText, normalizeToAsset, readEntityStatus } from '../assets';

// Fallback names use the type label of the active language.
beforeAll(async () => {
  const common = JSON.parse(readFileSync(resolve(__dirname, '../../../public/locales/es/common.json'), 'utf8'));
  await i18n.init({ lng: 'es', ns: ['common'], defaultNS: 'common', resources: { es: { common } } });
});

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

describe('Device status', () => {
  const device = (attrs: Record<string, unknown>) => ({ id: 'urn:ngsi-ld:Device:acme:d1', type: 'Device', ...attrs });

  it('prefers deviceState over status for a Device', () => {
    const asset = normalizeToAsset(device({ deviceState: { type: 'Property', value: 'active' }, status: 'error' }));
    expect(asset.status).toBe('active');
    expect(asset.statusLabel).toBe('active');
  });

  it('falls back to status, or to deviceState alone', () => {
    expect(normalizeToAsset(device({ status: { type: 'Property', value: 'offline' } })).status).toBe('inactive');
    expect(normalizeToAsset(device({ deviceState: 'maintenance' })).status).toBe('maintenance');
  });

  it('ignores deviceState on other types', () => {
    const asset = normalizeToAsset({ id: 'urn:ngsi-ld:Other:x', type: 'AgriSensor', deviceState: 'active', status: 'error' });
    expect(asset.status).toBe('error');
    expect(readEntityStatus({ type: 'AgriSensor', deviceState: 'active' })).toBeNull();
  });

  it('treats a non-string status as unknown without throwing', () => {
    const asset = normalizeToAsset(device({ status: { type: 'Property', value: { a: 1 } } }));
    expect(asset.status).toBe('unknown');
    expect(asset.statusLabel).toBe('Desconocido');
    expect(normalizeToAsset(device({ deviceState: 5, status: 'working' })).status).toBe('active');
  });
});

describe('controlledPropertyText', () => {
  it('joins an array, keeps a string, and is null when empty or missing', () => {
    expect(controlledPropertyText({ controlledProperty: { type: 'Property', value: ['humidity', 'temperature'] } })).toBe('humidity, temperature');
    expect(controlledPropertyText({ controlledProperty: ['humidity', 'temperature'] })).toBe('humidity, temperature');
    expect(controlledPropertyText({ controlledProperty: 'humidity' })).toBe('humidity');
    expect(controlledPropertyText({ controlledProperty: { value: 'humidity' } })).toBe('humidity');
    expect(controlledPropertyText({ controlledProperty: [] })).toBeNull();
    expect(controlledPropertyText({})).toBeNull();
  });
});
