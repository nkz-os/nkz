import { describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

const PARCEL = 'urn:ngsi-ld:AgriParcel:tenant-a:p1';
const rel = { type: 'Relationship', object: PARCEL };

const robot = { id: 'urn:ngsi-ld:AutonomousMobileRobot:r1', type: 'AutonomousMobileRobot', hasAgriParcel: rel };
const tractor = {
  id: 'urn:ngsi-ld:ManufacturingMachine:m1',
  type: 'ManufacturingMachine',
  category: { type: 'Property', value: 'tractor' },
  hasAgriParcel: rel,
};
const animal = { id: 'urn:ngsi-ld:LivestockAnimal:a1', type: 'LivestockAnimal', hasAgriParcel: rel };

const land = ['AgriParcelZone', 'AgriSoil', 'AgriSoilExtended', 'AgriFarm', 'AgriGreenhouse'].map(type => ({
  id: `urn:ngsi-ld:${type}:x1`,
  type,
  refAgriParcel: rel,
}));

const sdm: Record<string, unknown[]> = {
  AutonomousMobileRobot: [robot],
  ManufacturingMachine: [tractor],
  LivestockAnimal: [animal],
  ...Object.fromEntries(land.map(entity => [entity.type, [entity]])),
};

const { api } = vi.hoisted(() => ({
  api: {
    getRobots: vi.fn(),
    getMachines: vi.fn(),
    getLivestock: vi.fn(),
    getWeatherStations: vi.fn(),
    getSDMEntityInstances: vi.fn(),
  },
}));

vi.mock('@/services/api', () => ({ default: api }));
vi.mock('@/services/parcelApi', () => ({
  parcelApi: { getParcels: vi.fn(async () => [{ id: PARCEL, type: 'AgriParcel', name: 'North field' }]) },
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/context/ToastContext', () => ({ useToastContext: () => ({}) }));
vi.mock('@/utils/logger', () => ({
  logger: { warn: vi.fn(), error: vi.fn(), debug: vi.fn(), info: vi.fn(), log: vi.fn() },
}));

import { useAssets } from '../useAssets';

describe('useAssets', () => {
  it('lists fleet and livestock as their SDM entities, once each, under their parcel', async () => {
    api.getRobots.mockResolvedValue([robot]);
    // Map-oriented projections: display types and no relationships.
    api.getMachines.mockResolvedValue([
      { id: robot.id, name: 'r1', type: 'Autonomous Robot' },
      { id: tractor.id, name: 'm1', type: 'Tractor' },
    ]);
    api.getLivestock.mockResolvedValue([{ id: animal.id, type: 'LivestockAnimal', name: 'a1' }]);
    api.getWeatherStations.mockResolvedValue([]);
    api.getSDMEntityInstances.mockImplementation(async (type: string) => sdm[type] ?? []);

    const { result } = renderHook(() => useAssets());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    const byId = (id: string) => result.current.assets.filter(a => a.id === id);
    for (const [entity, category] of [[robot, 'fleet'], [tractor, 'fleet'], [animal, 'livestock']] as const) {
      const found = byId(entity.id);
      expect(found).toHaveLength(1);
      expect(found[0]).toMatchObject({ type: entity.type, category, parentId: PARCEL });
      expect(found[0].rawEntity).toEqual(entity);
    }
  });

  it('lists soil, zones, farms and greenhouses under their parcel', async () => {
    api.getRobots.mockResolvedValue([]);
    api.getMachines.mockResolvedValue([]);
    api.getLivestock.mockResolvedValue([]);
    api.getWeatherStations.mockResolvedValue([]);
    api.getSDMEntityInstances.mockImplementation(async (type: string) => sdm[type] ?? []);

    const { result } = renderHook(() => useAssets());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    for (const entity of land) {
      expect(result.current.assets.find(a => a.id === entity.id)).toMatchObject({ type: entity.type, parentId: PARCEL });
    }
  });

  it('lists individual plants (AgriPlant) as vegetation under their parcel, named by code', async () => {
    api.getRobots.mockResolvedValue([]);
    api.getMachines.mockResolvedValue([]);
    api.getLivestock.mockResolvedValue([]);
    api.getWeatherStations.mockResolvedValue([]);
    const plant = {
      id: 'urn:ngsi-ld:AgriPlant:tenant-a:demo-parcel-001:T-1', type: 'AgriPlant',
      location: { type: 'GeoProperty', value: { type: 'Point', coordinates: [-1.6, 42.8] } },
      hasAgriParcel: { type: 'Relationship', object: PARCEL },
      plantCode: { type: 'Property', value: 'T-1' },
    };
    api.getSDMEntityInstances.mockImplementation(async (type: string) => (type === 'AgriPlant' ? [plant] : sdm[type] ?? []));

    const { result } = renderHook(() => useAssets());
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    const found = result.current.assets.find(a => a.id === plant.id);
    expect(found).toMatchObject({ type: 'AgriPlant', category: 'vegetation', parentId: PARCEL });
    expect(found?.name).toContain('T-1');
  });
});
