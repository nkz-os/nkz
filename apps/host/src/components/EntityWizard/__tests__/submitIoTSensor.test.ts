import { beforeEach, describe, expect, it, vi } from 'vitest';

const { createSDMIoTEntity, invalidateEntityCache } = vi.hoisted(() => ({
  createSDMIoTEntity: vi.fn(),
  invalidateEntityCache: vi.fn(),
}));
vi.mock('@/services/api', () => ({ default: { createSDMIoTEntity }, invalidateEntityCache }));

import { submitIoTSensor } from '../submission/submitIoTSensor';
import { parcelLinksFragment } from '@/utils/parcelLinks';
import { validateIoTConfig } from '../iotValidation';
import type { IoTSensorFormData } from '../types';

const P1 = 'urn:ngsi-ld:AgriParcel:p1';
const P2 = 'urn:ngsi-ld:AgriParcel:p2';
const parcel = (id: string) => ({ id, type: 'AgriParcel', name: id, geometry: null });

function form(over: Partial<IoTSensorFormData> = {}): IoTSensorFormData {
  return {
    macroCategory: 'sensors',
    name: 'S1',
    description: '',
    geometry: { type: 'Point', coordinates: [1, 2] },
    geometryType: 'Point',
    deviceProfileId: 'p1',
    parcels: [parcel(P1), parcel(P2)],
    additionalAttributes: {},
    modelScale: 1,
    modelRotation: [0, 0, 0],
    ...over,
  } as IoTSensorFormData;
}

describe('submitIoTSensor', () => {
  beforeEach(() => {
    createSDMIoTEntity.mockReset();
    invalidateEntityCache.mockReset();
    createSDMIoTEntity.mockResolvedValue({});
  });

  it('sends hasDeviceProfile and controlledAsset for a Device', async () => {
    await submitIoTSensor('Device', form());
    const body = createSDMIoTEntity.mock.calls[0][1];
    expect(body.hasDeviceProfile).toEqual({ type: 'Relationship', object: 'urn:ngsi-ld:DeviceProfile:p1' });
    expect(body.refDeviceProfile).toBeUndefined();
    expect(body.controlledAsset).toEqual(parcelLinksFragment([P1, P2]).controlledAsset);
  });

  it('omits controlledAsset for other sensor types', async () => {
    await submitIoTSensor('WeatherObserved', form({ parcels: [] }));
    expect(createSDMIoTEntity.mock.calls[0][1].controlledAsset).toBeUndefined();
  });

  it('invalidates the entity type cache after a successful create', async () => {
    await submitIoTSensor('Device', form());
    expect(invalidateEntityCache).toHaveBeenCalledWith('Device');
  });

  it('propagates a provisioning failure (no credentials result)', async () => {
    createSDMIoTEntity.mockRejectedValue(new Error('502'));
    await expect(submitIoTSensor('Device', form())).rejects.toThrow('502');
    expect(invalidateEntityCache).not.toHaveBeenCalled();
  });
});

describe('validateIoTConfig', () => {
  it('requires a parcel for Device', () => {
    expect(validateIoTConfig('Device', form({ parcels: [] }))).toBe('wizard.validation.parcel_required');
    expect(validateIoTConfig('Device', form())).toBeNull();
  });
  it('does not require parcels for WeatherObserved', () => {
    expect(validateIoTConfig('WeatherObserved', form({ parcels: [] }))).toBeNull();
  });
});
