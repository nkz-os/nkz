/**
 * Regression: the viewer asked Orion-LD for `?type=X` with no limit and got its
 * default page of 20, so a list larger than the default page was silently
 * truncated. Every full-list service call must walk all pages.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const { get } = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('@/utils/logger', () => ({
  logger: { warn: vi.fn(), error: vi.fn(), debug: vi.fn(), info: vi.fn(), log: vi.fn() },
}));

vi.mock('axios', () => {
  const client = {
    get,
    post: vi.fn(),
    patch: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    request: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  };
  return { default: { create: () => client }, create: () => client };
});

import { logger } from '@/utils/logger';
import { api, clearApiCache } from '../api';

type Params = { type: string; limit?: number; offset?: number };

/** Orion-LD stand-in: `total` entities per type, honours limit/offset, default page 20. */
function serveEntities(totals: Record<string, number>) {
  get.mockImplementation(async (url: string, cfg: { params: Params }) => {
    expect(url).toBe('/ngsi-ld/v1/entities');
    const { type, limit = 20, offset = 0 } = cfg.params;
    const total = totals[type] ?? 0;
    const end = Math.min(offset + limit, total);
    const data = [];
    for (let i = offset; i < end; i++) data.push({ id: `urn:ngsi-ld:${type}:${i}`, type });
    return { data };
  });
}

describe('host entity list calls page through Orion-LD', () => {
  beforeEach(() => {
    get.mockReset();
    vi.mocked(logger.warn).mockClear();
    clearApiCache();
  });

  it('getSDMEntityInstances returns all 45 entities, not the first 20', async () => {
    serveEntities({ AgriParcelRecord: 45 });
    const out = await api.getSDMEntityInstances('AgriParcelRecord');
    expect(out).toHaveLength(45);
    expect(new Set(out.map((e: { id: string }) => e.id)).size).toBe(45);
  });

  it('getSDMEntityInstances sends limit=1000 and an advancing offset, keeping its params', async () => {
    serveEntities({ AgriCrop: 2500 });
    const out = await api.getSDMEntityInstances('AgriCrop');
    expect(out).toHaveLength(2500);
    const sent = get.mock.calls.map(([, cfg]) => cfg.params);
    expect(sent).toEqual([
      { type: 'AgriCrop', limit: 1000, offset: 0 },
      { type: 'AgriCrop', limit: 1000, offset: 1000 },
      { type: 'AgriCrop', limit: 1000, offset: 2000 },
    ]);
  });

  it('getSDMEntityInstances caches the complete list under entities:<type>', async () => {
    serveEntities({ AgriCrop: 5 });
    await api.getSDMEntityInstances('AgriCrop');
    const calls = get.mock.calls.length;
    await api.getSDMEntityInstances('AgriCrop');
    expect(get.mock.calls.length).toBe(calls);
  });

  it('getParcels returns every AgriParcel and keeps the context Link header', async () => {
    serveEntities({ AgriParcel: 1001 });
    const out = await api.getParcels();
    expect(out).toHaveLength(1001);
    const [, cfg] = get.mock.calls[0];
    expect(cfg.params).toEqual({ type: 'AgriParcel', limit: 1000, offset: 0 });
    expect(cfg.headers.Link).toContain('rel="http://www.w3.org/ns/json-ld#context"');
  });

  it('getMachines pages robots and machines independently', async () => {
    serveEntities({ AutonomousMobileRobot: 25, ManufacturingMachine: 30 });
    const out = await api.getMachines();
    expect(out).toHaveLength(55);
  });

  it('getMachines logs a failing type and still returns the other one', async () => {
    const boom = new Error('orion down');
    get.mockImplementation(async (_url: string, cfg: { params: Params }) => {
      if (cfg.params.type === 'ManufacturingMachine') throw boom;
      const data = [];
      for (let i = 0; i < 3; i++) data.push({ id: `urn:ngsi-ld:robot:${i}`, type: cfg.params.type });
      return { data };
    });
    const out = await api.getMachines();
    expect(out).toHaveLength(3);
    expect(logger.warn).toHaveBeenCalledWith(expect.stringContaining('ManufacturingMachine'), boom);
  });

  it('getRobots, getLivestock and getWeatherStations return everything', async () => {
    serveEntities({ AutonomousMobileRobot: 21, LivestockAnimal: 22, WeatherObserved: 23 });
    expect(await api.getRobots()).toHaveLength(21);
    expect(await api.getLivestock()).toHaveLength(22);
    expect(await api.getWeatherStations()).toHaveLength(23);
  });
});
