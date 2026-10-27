/**
 * Only entity-manager may write parcels (the gateway answers 403 to a parcel write
 * on the broker path), so deleting a parcel goes through its parcel route, which
 * also removes the parcel's zones. Other entities are deleted on the broker.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { del } = vi.hoisted(() => ({ del: vi.fn() }));

vi.mock('@/utils/logger', () => ({
  logger: { warn: vi.fn(), error: vi.fn(), debug: vi.fn(), info: vi.fn(), log: vi.fn() },
}));

vi.mock('axios', () => {
  const client = {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    put: vi.fn(),
    delete: del,
    request: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  };
  return { default: { create: () => client }, create: () => client };
});

import { api } from '../api';

beforeEach(() => {
  del.mockReset();
  del.mockResolvedValue({ status: 204 });
});

describe('deleteSDMEntity', () => {
  it('deletes a parcel through entity-manager', async () => {
    await api.deleteSDMEntity('AgriParcel', 'urn:ngsi-ld:AgriParcel:p1');

    expect(del).toHaveBeenCalledWith('/api/entities/parcels/urn%3Angsi-ld%3AAgriParcel%3Ap1');
  });

  it('deletes any other entity on the broker', async () => {
    await api.deleteSDMEntity('Device', 'urn:ngsi-ld:Device:d1');

    expect(del).toHaveBeenCalledWith('/ngsi-ld/v1/entities/urn:ngsi-ld:Device:d1');
    await api.deleteSDMEntity('WeatherObserved', 'urn:ngsi-ld:WeatherObserved:acme:w1');
    expect(del).toHaveBeenLastCalledWith('/ngsi-ld/v1/entities/urn:ngsi-ld:WeatherObserved:acme:w1');
  });

  it('deletes a tenant-scoped Device through the sensors API', async () => {
    await api.deleteSDMEntity('Device', 'urn:ngsi-ld:Device:tenant-a:probe_1');

    expect(del).toHaveBeenCalledWith('/api/sensors/devices/urn:ngsi-ld:Device:tenant-a:probe_1');
  });
});
