import { describe, expect, it } from 'vitest';
import { isGatewayDevice } from '../deviceIds';

const FULL_KEY = 'https://smartdatamodels.org/dataModel.Device/deviceCategory';
const device = (extra: Record<string, unknown>) => ({ id: 'urn:ngsi-ld:Device:tenant-a:x', type: 'Device', ...extra });

describe('isGatewayDevice', () => {
  it.each([
    ['a list value', { deviceCategory: { type: 'Property', value: ['network'] } }],
    ['one value', { deviceCategory: { type: 'Property', value: 'network' } }],
    ['a bare list', { deviceCategory: ['sensor', 'network'] }],
    ['a bare string', { deviceCategory: 'network' }],
    ['the full IRI key', { [FULL_KEY]: { type: 'Property', value: ['network'] } }],
    ['the full IRI key, bare', { [FULL_KEY]: 'network' }],
  ])('is a gateway: %s', (_label, extra) => {
    expect(isGatewayDevice(device(extra))).toBe(true);
  });

  it.each([
    ['no category', {}],
    ['a sensor', { deviceCategory: { type: 'Property', value: ['sensor'] } }],
    ['a sensor under the full IRI key', { [FULL_KEY]: { type: 'Property', value: ['sensor'] } }],
  ])('is not a gateway: %s', (_label, extra) => {
    expect(isGatewayDevice(device(extra))).toBe(false);
  });

  it('is not a gateway when the type is not Device', () => {
    expect(isGatewayDevice({ type: 'AgriSensor', deviceCategory: 'network' })).toBe(false);
  });
});
