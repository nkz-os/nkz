/**
 * Parent relationships: weather stations keep the parcel they belong to, and
 * relationship writes use the broker operations that can create and remove an
 * attribute (merge-patch on the entity, DELETE on the attribute).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const { get, patch, del } = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn(), del: vi.fn() }));

vi.mock('@/utils/logger', () => ({
  logger: { warn: vi.fn(), error: vi.fn(), debug: vi.fn(), info: vi.fn(), log: vi.fn() },
}));

vi.mock('axios', () => {
  const client = {
    get,
    post: vi.fn(),
    patch,
    put: vi.fn(),
    delete: del,
    request: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  };
  return { default: { create: () => client }, create: () => client };
});

import { api, clearApiCache } from '../api';

const PARCEL = 'urn:ngsi-ld:AgriParcel:p1';
const STATION = 'urn:ngsi-ld:WeatherObserved:t:parcel-p1';

beforeEach(() => {
  get.mockReset();
  patch.mockReset();
  del.mockReset();
  clearApiCache();
});

describe('getWeatherStations', () => {
  it('keeps the parcel relationship and hides the closed-day series', async () => {
    const refParcel = { type: 'Relationship', object: PARCEL };
    get.mockResolvedValue({
      data: [
        { id: STATION, type: 'WeatherObserved', refParcel, dailySummary: { type: 'Property', value: false } },
        { id: `${STATION}-daily`, type: 'WeatherObserved', refParcel, dailySummary: { type: 'Property', value: true } },
      ],
    });

    const stations = await api.getWeatherStations();

    expect(stations.map(s => s.id)).toEqual([STATION]);
    expect((stations[0] as unknown as { refParcel: unknown }).refParcel).toEqual(refParcel);
  });
});

describe('relationship writes', () => {
  it('merges a fragment into the entity, which creates missing attributes', async () => {
    patch.mockResolvedValue({ status: 204 });
    const fragment = { hasAgriParcel: { type: 'Relationship', object: PARCEL } };

    await api.mergeSDMEntity('urn:ngsi-ld:AgriCrop:c1', fragment);

    expect(patch).toHaveBeenCalledWith('/ngsi-ld/v1/entities/urn:ngsi-ld:AgriCrop:c1', fragment, {
      headers: { 'Content-Type': 'application/json' },
    });
  });

  it('removes a single attribute with DELETE', async () => {
    del.mockResolvedValue({ status: 204 });

    await api.deleteSDMEntityAttribute('urn:ngsi-ld:AgriCrop:c1', 'hasAgriParcel');

    expect(del).toHaveBeenCalledWith('/ngsi-ld/v1/entities/urn:ngsi-ld:AgriCrop:c1/attrs/hasAgriParcel');
  });
});
