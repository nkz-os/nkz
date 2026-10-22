import { describe, it, expect } from 'vitest';
import {
  ORION_LD_PREFIX,
  orionEntityPath,
  orionEntitiesPath,
  orionEntityAttrsPath,
} from '../src/runtime/orionPaths';

describe('orionPaths', () => {
  it('uses canonical /ngsi-ld prefix (not /api/ngsi-ld)', () => {
    expect(ORION_LD_PREFIX).toBe('/ngsi-ld');
    expect(orionEntityPath('urn:ngsi-ld:AgriParcel:abc')).toBe(
      '/ngsi-ld/v1/entities/urn%3Angsi-ld%3AAgriParcel%3Aabc',
    );
    expect(orionEntitiesPath(new URLSearchParams({ type: 'AgriParcel' }))).toBe(
      '/ngsi-ld/v1/entities?type=AgriParcel',
    );
    expect(orionEntityAttrsPath('urn:ngsi-ld:AgriParcel:abc')).toBe(
      '/ngsi-ld/v1/entities/urn%3Angsi-ld%3AAgriParcel%3Aabc/attrs',
    );
  });
});
