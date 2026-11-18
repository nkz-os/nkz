import { beforeEach, describe, expect, it, vi } from 'vitest';

const { createSDMEntity } = vi.hoisted(() => ({ createSDMEntity: vi.fn() }));
vi.mock('@/services/api', () => ({ default: { createSDMEntity } }));

import { submitFleet } from '../submission/submitFleet';
import type { FleetFormData } from '../types';

const form = (extra: Partial<FleetFormData> = {}): FleetFormData => ({
  macroCategory: 'fleet', name: 'M', description: '', geometry: null, geometryType: 'Point',
  additionalAttributes: {}, modelScale: 1, modelRotation: [0, 0, 0], ...extra,
} as FleetFormData);

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- loose view of the posted NGSI-LD payload
const sent = () => createSDMEntity.mock.calls[0][1] as Record<string, any>;

beforeEach(() => createSDMEntity.mockReset().mockResolvedValue(undefined));

describe('submitFleet machine geometry', () => {
  it('writes the role (tractor by default) and only the dimensions that were given, in metres', async () => {
    await submitFleet('ManufacturingMachine', form({ machineGeometry: { minTurningRadius: 4.5, steeringType: 'ackermann' } }));
    const e = sent();
    expect(e.category).toEqual({ type: 'Property', value: 'tractor' });
    expect(e.minTurningRadius).toEqual({ type: 'Property', value: 4.5, unitCode: 'MTR' });
    expect(e.steeringType).toEqual({ type: 'Property', value: 'ackermann' });
    expect(e.trackWidth).toBeUndefined();
    expect(e.gpsOffsetX).toBeUndefined();
  });

  it('keeps a stored zero and the implement role', async () => {
    await submitFleet('ManufacturingMachine', form({ category: 'implement',
      machineGeometry: { implementOffsetY: 0, transportWidth: 3, mountPosition: 'rear' } }));
    const e = sent();
    expect(e.category.value).toBe('implement');
    expect(e.implementOffsetY).toEqual({ type: 'Property', value: 0, unitCode: 'MTR' });
    expect(e.transportWidth).toEqual({ type: 'Property', value: 3, unitCode: 'MTR' });
    expect(e.mountPosition).toEqual({ type: 'Property', value: 'rear' });
  });

  it('drops geometry that does not belong to the chosen role', async () => {
    await submitFleet('ManufacturingMachine', form({ category: 'implement',
      machineGeometry: { minTurningRadius: 4.5, implementWidth: 6 } }));
    const e = sent();
    expect(e.minTurningRadius).toBeUndefined();
    expect(e.implementWidth.value).toBe(6);
  });

  it('writes no machine role for other fleet types', async () => {
    await submitFleet('AgriOperation', form());
    expect(sent().category).toBeUndefined();
  });
});
