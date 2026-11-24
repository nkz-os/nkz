import { describe, expect, it } from 'vitest';
import { SKIP_KEYS } from '../Sensors';

describe('Sensors SKIP_KEYS', () => {
  it.each([
    'refDeviceModel', 'controlledProperty', 'deviceState', 'dateInstalled', 'deviceCategory',
    'category', 'profileCode', 'sensorType', 'externalId', 'installedAt',
  ])('hides %s from the dynamic measurements', (key) => {
    expect(SKIP_KEYS.has(key)).toBe(true);
  });
});
