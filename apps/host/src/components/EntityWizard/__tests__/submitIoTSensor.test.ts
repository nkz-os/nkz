import { beforeEach, describe, expect, it, vi } from 'vitest';

const { createSDMIoTEntity, invalidateEntityCache, createDatalogger, registerSensor } = vi.hoisted(() => ({
  createSDMIoTEntity: vi.fn(),
  invalidateEntityCache: vi.fn(),
  createDatalogger: vi.fn(),
  registerSensor: vi.fn(),
}));
vi.mock('@/services/api', () => ({ default: { createSDMIoTEntity }, invalidateEntityCache }));
vi.mock('@/services/dataloggersApi', () => ({ createDatalogger, registerSensor }));

import { submitIoTSensor } from '../submission/submitIoTSensor';
import { parcelLinksFragment } from '@/utils/parcelLinks';
import { validateIoTConfig, validateDataloggerStep } from '../iotValidation';
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
    dataloggerMode: 'existing',
    dataloggerId: 'dl-1',
    externalId: 'probe_1',
    ...over,
  } as IoTSensorFormData;
}

describe('submitIoTSensor — Device registers through the sensors API', () => {
  beforeEach(() => {
    createSDMIoTEntity.mockReset();
    invalidateEntityCache.mockReset();
    createDatalogger.mockReset();
    registerSensor.mockReset();
    registerSensor.mockResolvedValue({ entity_id: 'urn:ngsi-ld:Device:tenant-a:probe_1', topic: '/k/probe_1/attrs', datalogger_id: 'dl-1' });
  });

  it('register posts datalogger_id and owner-first controlledAsset', async () => {
    const K = 'urn:ngsi-ld:AgriSolarPark:k';
    await submitIoTSensor('Device', form({ parcels: [parcel(P1), parcel(P2)], ownerParkId: K }));
    expect(registerSensor).toHaveBeenCalledTimes(1);
    expect(registerSensor.mock.calls[0][0]).toMatchObject({
      external_id: 'probe_1',
      name: 'S1',
      profile: 'p1',
      location: { lat: 2, lon: 1 },
      controlledAsset: [K, P1, P2],
      datalogger_id: 'dl-1',
    });
    expect(createSDMIoTEntity).not.toHaveBeenCalled();
    expect(createDatalogger).not.toHaveBeenCalled();
  });

  it('keeps the parcels in the given order when a parcel owns the sensor', async () => {
    await submitIoTSensor('Device', form());
    expect(registerSensor.mock.calls[0][0].controlledAsset).toEqual(
      parcelLinksFragment([P1, P2]).controlledAsset.object,
    );
  });

  it('existing datalogger: no password shown', async () => {
    const result = await submitIoTSensor('Device', form());
    expect(result.newDatalogger).toBeNull();
    expect(result.mqttCredentials).toBeNull();
  });

  it('shows credentials once after creating a datalogger', async () => {
    createDatalogger.mockResolvedValue({
      id: 'dl-new',
      mqtt: { configured: true, host: 'broker.example', port: 8883, protocol: 'mqtts', username: 'dl_ab', password: 'pw-once' },
      devices: [],
    });
    const result = await submitIoTSensor('Device', form({ dataloggerMode: 'new', dataloggerId: '', newDataloggerName: 'Field logger', newDataloggerKind: 'datak' }));
    expect(createDatalogger).toHaveBeenCalledWith('Field logger', 'datak');
    expect(registerSensor.mock.calls[0][0].datalogger_id).toBe('dl-new');
    expect(result.newDatalogger).toMatchObject({
      id: 'dl-new',
      name: 'Field logger',
      topic: '/k/probe_1/attrs',
      mqtt: { username: 'dl_ab', password: 'pw-once', host: 'broker.example', port: 8883 },
    });
  });

  it('mqtt.configured false carries the reason and no password', async () => {
    createDatalogger.mockResolvedValue({
      id: 'dl-new',
      mqtt: { configured: false, reason: 'No external endpoint', username: 'dl_ab', password: 'pw-hidden' },
      devices: [],
    });
    const result = await submitIoTSensor('Device', form({ dataloggerMode: 'new', dataloggerId: '', newDataloggerName: 'L', newDataloggerKind: 'generic_mqtt' }));
    expect(result.newDatalogger?.mqtt.configured).toBe(false);
    expect(result.newDatalogger?.mqtt.reason).toBe('No external endpoint');
    expect(result.newDatalogger?.mqtt.password).toBeUndefined();
  });

  it('a failed register after creating a datalogger reports the created id', async () => {
    createDatalogger.mockResolvedValue({ id: 'dl-new', mqtt: { configured: true, username: 'u', password: 'p' }, devices: [] });
    registerSensor.mockRejectedValue(Object.assign(new Error('502'), { response: { data: { error: 'boom' } } }));
    await expect(
      submitIoTSensor('Device', form({ dataloggerMode: 'new', dataloggerId: '', newDataloggerName: 'L', newDataloggerKind: 'datak' })),
    ).rejects.toMatchObject({ createdDataloggerId: 'dl-new' });
    expect(invalidateEntityCache).not.toHaveBeenCalled();
  });

  it('invalidates the Device cache after a successful register', async () => {
    await submitIoTSensor('Device', form());
    expect(invalidateEntityCache).toHaveBeenCalledWith('Device');
  });

  it('carries health and calibration config', async () => {
    await submitIoTSensor('Device', form({ healthConfig: { communicationTimeoutHours: 2 }, calibrationConfig: { x: { slope: 1 } } }));
    expect(registerSensor.mock.calls[0][0]).toMatchObject({
      healthConfig: { communicationTimeoutHours: 2 },
      calibrationConfig: { x: { slope: 1 } },
    });
  });
});

describe('submitIoTSensor — other sensor types keep the SDM flow', () => {
  beforeEach(() => {
    createSDMIoTEntity.mockReset().mockResolvedValue({});
    registerSensor.mockReset();
    invalidateEntityCache.mockReset();
  });

  it('omits controlledAsset and does not register', async () => {
    await submitIoTSensor('WeatherObserved', form({ parcels: [] }));
    expect(createSDMIoTEntity.mock.calls[0][1].controlledAsset).toBeUndefined();
    expect(createSDMIoTEntity.mock.calls[0][1].hasDeviceProfile).toEqual({
      type: 'Relationship',
      object: 'urn:ngsi-ld:DeviceProfile:p1',
    });
    expect(registerSensor).not.toHaveBeenCalled();
  });

  it('propagates a provisioning failure', async () => {
    createSDMIoTEntity.mockRejectedValue(new Error('502'));
    await expect(submitIoTSensor('WeatherObserved', form({ parcels: [] }))).rejects.toThrow('502');
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

describe('validateDataloggerStep', () => {
  it('requires a datalogger choice', () => {
    expect(validateDataloggerStep('Device', form({ dataloggerMode: 'existing', dataloggerId: '' }))).toBe('wizard.validation.datalogger_required');
    expect(validateDataloggerStep('Device', form({ dataloggerMode: 'new', newDataloggerName: ' ', newDataloggerKind: 'datak' }))).toBe('wizard.validation.datalogger_name_required');
    expect(validateDataloggerStep('Device', form({ dataloggerMode: 'new', newDataloggerName: 'L' }))).toBe('wizard.validation.datalogger_kind_required');
  });
  it('validates the external id', () => {
    expect(validateDataloggerStep('Device', form({ externalId: 'a/b' }))).toBe('wizard.validation.external_id_invalid');
    expect(validateDataloggerStep('Device', form({ externalId: '' }))).toBe('wizard.validation.external_id_invalid');
    expect(validateDataloggerStep('Device', form({ externalId: 'x'.repeat(65) }))).toBe('wizard.validation.external_id_invalid');
    expect(validateDataloggerStep('Device', form())).toBeNull();
  });
  it('does not apply to other types', () => {
    expect(validateDataloggerStep('WeatherObserved', form({ dataloggerId: '' }))).toBeNull();
  });
});
