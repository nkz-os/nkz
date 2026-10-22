/**
 * parcelApi.getParcels must page through Orion-LD (default page is 20).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const { get } = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('axios', () => {
  const client = {
    get,
    post: vi.fn(),
    patch: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  };
  return { default: { create: () => client }, create: () => client };
});

import { parcelApi } from '../parcelApi';

describe('parcelApi.getParcels', () => {
  beforeEach(() => {
    get.mockReset();
  });

  it('returns all 45 parcels and keeps the context Link header on every page', async () => {
    get.mockImplementation(async (_url: string, cfg: { params: { limit?: number; offset?: number } }) => {
      const { limit = 20, offset = 0 } = cfg.params;
      const end = Math.min(offset + limit, 45);
      const data = [];
      for (let i = offset; i < end; i++) {
        data.push({
          id: `urn:ngsi-ld:AgriParcel:${i}`,
          type: 'AgriParcel',
          location: { type: 'GeoProperty', value: { type: 'Polygon', coordinates: [] } },
        });
      }
      return { data };
    });

    const out = await parcelApi.getParcels();

    expect(out).toHaveLength(45);
    for (const [, cfg] of get.mock.calls) {
      expect(cfg.headers.Link).toContain('rel="http://www.w3.org/ns/json-ld#context"');
      expect(cfg.params.type).toBe('AgriParcel');
    }
  });
});
